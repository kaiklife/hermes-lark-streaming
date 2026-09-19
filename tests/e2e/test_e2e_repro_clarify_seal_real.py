"""真机复现（2026-09-19 17:13 那张卡）：clarify 打断 → 继续 → 封卡。

mock 单测（tests/test_repro_clarify_seal.py）是绿的 —— 封卡批次里 footer 和
完整正文都在。这里把同一时序打到**真飞书 API**，区分两条候选解释：

  (a) 插件发出去的封卡动作就是残的（少了尾部 / 少了 footer）
  (b) 插件发全了，飞书侧没落上

判定不看本用例的断言，看两样东西：插件实际发出的批次（本用例 dump 到
/tmp/hls_batch_dump.json）+ 飞书侧卡片实况（跑完拿 card_id 去群里回读）。
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from hermes_lark_streaming.cardkit import ANSWER_ELEMENT_ID

TAIL_MARK = "【尾标记-必须留在卡片里】"

SEG1 = "① 真机复现第一段：clarify 之前写进卡片的正文。\n"
SEG2 = "② 真机复现第二段：clarify 之后写进卡片的正文 —— " + TAIL_MARK


def _spy_batches(client, dump_path: str):
    """包住 cardkit_batch_update，把每次发出的 actions 落到文件。"""
    dump: list[dict] = []
    orig = client.cardkit_batch_update

    async def spy(card_id, actions, **kw):
        entry = {"card": card_id, "seq": kw.get("sequence"),
                 "actions": [a.get("action") for a in actions]}
        try:
            r = await orig(card_id, actions, **kw)
            entry["ok"] = True
        except Exception as e:  # noqa: BLE001 — 要看 API 到底报没报错
            entry["ok"] = False
            entry["error"] = f"{type(e).__name__}: {e}"
            dump.append(entry)
            with open(dump_path, "w") as f:
                json.dump(dump, f, ensure_ascii=False, indent=1)
            raise
        entry["full"] = actions
        dump.append(entry)
        with open(dump_path, "w") as f:
            json.dump(dump, f, ensure_ascii=False, indent=1)
        return r

    client.cardkit_batch_update = spy
    return dump


async def _flush(ctrl, session):
    await session.flush.flush_now(lambda s=session: ctrl._do_unified_flush(s))


@pytest.mark.asyncio
async def test_real_seal_after_clarify_interrupt(runner):
    """干净时序：一段 → pre-flush → 一段 → 封卡。"""
    if not runner.is_real_mode:
        pytest.skip("真机复现，只在真飞书模式跑")

    ctrl = runner.controller
    session = await runner.start_message("e2e real repro: clarify interrupt + seal")

    await runner.feed_answer(session, SEG1)
    await _flush(ctrl, session)          # clarify 打断前的 pre-flush
    await runner.feed_answer(session, SEG2)

    ctrl.on_completed(
        message_id=session.message_id,
        answer=SEG1 + SEG2,
        duration=379.3,
        model="deepseek-v4.1-flash",
    )
    for _ in range(30):
        await asyncio.sleep(0.5)
        if getattr(session, "_streaming_closed", False) or getattr(session, "is_terminal_phase", False):
            break

    runner.assert_card_created(session)
    print(f"\nE2E_RESULT clean card_id={session.card_id}")


@pytest.mark.asyncio
async def test_real_streamed_turn_keeps_tail_and_footer(runner):
    """生产形态时序：逐段流式 + 周期性 flush + clarify 打断 + 继续 + 封卡。

    生产那轮（17:13 建卡、17:20 封卡）是**流式**的：answer 分很多小 delta
    进来，中间每隔约 10s flush 一次，clarify 插在中间。本用例照这个形状驱动，
    并把插件实际发出的批次 dump 下来，用来判断尾部/footer 到底是没发还是没落。
    """
    if not runner.is_real_mode:
        pytest.skip("真机复现，只在真飞书模式跑")

    # 生产那轮的答案约 1.6KB；用 E2E_ANSWER_CHARS 把答案撑到指定规模来二分
    target = int(os.environ.get("E2E_ANSWER_CHARS", "0"))
    unit = "这是一段用于把答案撑到生产规模的填充正文，内容本身无意义。"
    chunks: list[str] = []
    if target:
        while sum(len(c) for c in chunks) < target:
            chunks.append(unit)
    else:
        chunks = ["A0 第一段正文。 ", "A1 第一段正文。 ", "A2 第一段正文。 ",
                  "A3 第一段正文。 ", "A4 第一段正文。 ", "A5 第一段正文。 ",
                  "A6 第一段正文。 ", "A7 第一段正文。 "]
    half = len(chunks) // 2
    part_a, part_b = chunks[:half], chunks[half:]

    ctrl = runner.controller
    dump_path = "/tmp/hls_batch_dump.json"
    _spy_batches(ctrl._client, dump_path)

    session = await runner.start_message("e2e real repro: streamed + clarify + seal")

    # ── 工具面板：生产那轮有 27 个工具步，封卡时面板要一起写（4 个动作）──
    # 生产的面板里每步都带长命令详情（多行 shell），卡片 JSON 因此大得多；
    # E2E_LONG_DETAIL=1 复刻这个规模，用来验证「面板把卡片撑大 → 封卡批次失效」。
    n_tools = int(os.environ.get("E2E_TOOLS", "25"))
    long_detail = (
        "do curl -s --compressed -m 25 -A \"$UA\" "
        "\"https://www.example.com/shop/x?parts.0=MG724CH/A\" -o t.html "
        "-w \"$slug → HTTP %{http_code} %{size_download}B\\n\" + 3 commands"
    )
    for i in range(n_tools):
        await runner.feed_tool_update(
            session,
            tool_name=f"tool_{i}",
            status="success",
            detail=long_detail if os.environ.get("E2E_LONG_DETAIL") else f"step {i}",
        )

    # ── 第一段：流式喂 + 周期性 flush ──
    for i, chunk in enumerate(part_a):
        await runner.feed_answer(session, chunk)
        if target and i and i % 8 == 0:
            await _flush(ctrl, session)

    # ── clarify 打断：pre-flush ──
    await _flush(ctrl, session)

    # ── 第二段：继续流式 ──
    for i, chunk in enumerate(part_b):
        await runner.feed_answer(session, chunk)
        if target and i and i % 8 == 0:
            await _flush(ctrl, session)
    await _flush(ctrl, session)

    full = "".join(part_a) + "".join(part_b) + TAIL_MARK

    ctrl.on_completed(
        message_id=session.message_id,
        answer=full,
        duration=379.3,
        model="deepseek-v4.1-flash",
    )
    for _ in range(30):
        await asyncio.sleep(0.5)
        if getattr(session, "_streaming_closed", False) or getattr(session, "is_terminal_phase", False):
            break

    # ── 插件侧：实际发出去的封卡批次里有什么 ──
    dump = json.load(open(dump_path))
    sent_tail = False
    sent_footer = False
    for b in dump:
        for a in b.get("full", []):
            if a.get("action") == "add_elements":
                sent_footer = True
            if (a.get("action") == "partial_update_element"
                    and a.get("params", {}).get("element_id") == ANSWER_ELEMENT_ID
                    and TAIL_MARK in str(a.get("params", {}).get("partial_element", {}).get("content", ""))):
                sent_tail = True
    print(f"\nE2E_RESULT streamed card_id={session.card_id} batches={len(dump)} "
          f"plugin_sent_tail={sent_tail} plugin_sent_footer={sent_footer}")
    for i, b in enumerate(dump):
        print(f"  batch[{i}] seq={b['seq']} actions={b['actions']} ok={b.get('ok')} "
              f"err={b.get('error', '-')}")
    print(f"E2E_DUMP {dump_path}")
    runner.assert_card_created(session)