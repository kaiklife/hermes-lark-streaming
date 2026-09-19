"""复现测试：clarify 打断一轮 → 文字回复继续 → 封卡。

生产现场（2026-09-19 17:13 那张卡，卡片 768717213663）：
  - 卡片没有封尾、没有 footer，正文断在半句
  - 时序：17:13 建卡 → 17:16 clarify 插入（适配器先 pre-flush）→
          17:18 用户用**文字**回 clarify → 同一轮继续 → 17:20 封卡

本测试按同一时序驱动 controller，断言封卡那批动作里必须有 footer、
且写回卡片的正文必须包含第二段（打断后的那段）的尾部。
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from hermes_lark_streaming.cardkit import (
    ANSWER_ELEMENT_ID,
    UNIFIED_PANEL_ELEMENT_ID,
    _LOADING_ELEMENT_ID,
)
from hermes_lark_streaming.controller.mixin import COMPLETING, STREAMING

from tests.test_controller import _make_session, _setup_ctrl

SEG1 = "① 接口真的存在，我们从 n100 打过去是 200，能拿到真数据。\n"
SEG2 = "② 改完了，9 个通知类任务全部改投到群里。\n最后一句必须留在卡片里。"


@pytest.mark.asyncio
async def test_seal_after_clarify_interrupt_keeps_footer_and_full_answer() -> None:
    ctrl = _setup_ctrl(linear=True)
    session = _make_session("msg_clarify", linear=True)
    session.state = STREAMING
    session.card_id = "card_clarify"
    session._creation_stages.update({"panel", "answer", "hint_removed"})
    session.existing_elements = {
        ANSWER_ELEMENT_ID,
        UNIFIED_PANEL_ELEMENT_ID,
        _LOADING_ELEMENT_ID,
    }
    session.flush.set_card_message_ready(True)
    ctrl._sessions["msg_clarify"] = session

    # ── 第一段：clarify 之前的回答 ──
    session.unified_state.on_answer_delta(SEG1)

    # ── clarify 打断：适配器在插 clarify 卡前做的 pre-flush ──
    await session.flush.flush_now(lambda s=session: ctrl._do_unified_flush(s))

    # ── 用户用文字回 clarify → 同一轮继续，第二段答案 ──
    session.unified_state.on_answer_delta(SEG2)

    # ── 回合结束 ──
    session.footer = {"duration": 379.3, "model": "deepseek-v4.1-flash"}
    session.state = COMPLETING
    assert await ctrl._do_linear_complete(session) is True

    footer_added = False
    answer_written = ""
    for call in ctrl._client.cardkit_batch_update.call_args_list:
        actions = call.args[1] if len(call.args) > 1 else call.kwargs.get("actions", [])
        for a in actions:
            if a.get("action") == "add_elements":
                footer_added = True
            if (
                a.get("action") == "partial_update_element"
                and a.get("params", {}).get("element_id") == ANSWER_ELEMENT_ID
            ):
                answer_written = a["params"]["partial_element"].get("content", "")

    assert footer_added, "封卡必须带 footer（add_elements）"
    assert "最后一句必须留在卡片里" in answer_written, (
        "封卡写回的正文必须包含第二段尾部，实际写回内容：%r" % answer_written
    )
    ctrl._client.cardkit_close_streaming.assert_called()
