"""v2.0.30（kai fork）回归 — 卡片生命周期三修.

三起事故（2026-11-05/06 生产日志取证）：

1. **一回合多卡**：hermes 在「先给一段文字、再继续调工具」时会发真终帧，
   旧逻辑进来就落定 → 后续帧 ``active_state()=None`` → 建新卡。实测
   「为什么昨晚有个空卡片」一个提问 3 张卡（07:14/07:15/07:18，answer
   554/1690/1291，页脚 1m15s/2m12s/1m33s）。修：同 turn_id 后继帧撤销
   落定窗口；已封的同 turn 卡复活（重建通道，streaming 已关）。
2. **正文堆命令回显**：native 进度 overlay 的裸 ```plain_text 围栏块混进
   answer 快照 → 正文 20+ 个命令块、面板只记 7 个工具（真实 54 次）。
   修：``_strip_command_echo`` 剥出并回灌面板。
3. **旧快照擦卡**：面板全量重建请求在网络里挂了 27s，定稿后被 300317
   自愈重发，按旧快照整卡重建 → 答案抹回占位、页脚消失（22:47 空卡）。
   修：``call_with_seq_recovery`` 的 guard——定稿后/内容翻版后拒绝重发。
"""

from __future__ import annotations

import asyncio

import pytest
from hermes_lark_streaming.cardkit.seq import call_with_seq_recovery
from hermes_lark_streaming.composer.engine import (
    CardTurnState,
    _detach_tool_echoes,
    _merge_resumed_answer,
)

from .test_v208_stream_quality import _settle
from .test_v213_native_streaming import _FullClient, _make_composer

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# 1. 正文命令回显剥离
# ---------------------------------------------------------------------------


class TestDetachToolEchoes:
    def test_strips_head_plus_fence_blocks(self):
        answer = (
            "结论先说：\n\n"
            "💻 terminal\n```\nls -lt ~/.hermes/logs/\n```\n"
            "💻 terminal\n```\ncat /etc/hosts\n```\n"
            "下面是细节。")
        clean, echo = _detach_tool_echoes(answer)
        assert clean == "结论先说：\n\n下面是细节。"
        assert echo == ["ls -lt ~/.hermes/logs/", "cat /etc/hosts"]

    def test_keeps_language_fenced_code(self):
        answer = "用法：\n```bash\nsherlock torvalds\n```\n完。"
        assert _detach_tool_echoes(answer) == (answer, [])

    def test_keeps_bare_fence_without_head(self):
        answer = "```\nplain block\n```"
        assert _detach_tool_echoes(answer) == (answer, [])

    def test_unterminated_fence_untouched(self):
        answer = "💻 terminal\n```\nno closing fence"
        assert _detach_tool_echoes(answer) == (answer, [])

    def test_plain_answer_untouched(self):
        assert _detach_tool_echoes("就是一句话") == ("就是一句话", [])

    def test_dedup_suffix_after_fence_dropped(self):
        answer = "💻 terminal\n```\nwhoami\n```\n(×2)"
        clean, echo = _detach_tool_echoes(answer)
        assert clean == "" and echo == ["whoami"]

    async def test_echo_blocks_go_to_panel_not_answer(self):
        client = _FullClient()
        comp = _make_composer(client)
        chat = "oc_echo"
        frame = "💻 terminal\n```\nls -lt ~/.hermes/logs/\n```\n\n🛠 正在排查"
        await comp.handle_stream_frame(chat, frame, finalize=False, turn_id="t1")
        st = comp._states[chat]
        assert "```" not in st.answer_text
        assert any("ls -lt ~/.hermes/logs/" in line for line in st.status_lines)


class TestResumeMergeIdempotent:
    def test_snapshot_including_base_not_duplicated(self):
        assert _merge_resumed_answer("A\n\nB", "A\n\nB C") == "A\n\nB C"

    def test_plain_segment_join_still_works(self):
        assert _merge_resumed_answer("A", "B") == "A\n\nB"


# ---------------------------------------------------------------------------
# 2. 一回合多卡
# ---------------------------------------------------------------------------


class TestSameTurnSingleCard:
    async def _seal_first_segment(self, comp, client, chat, turn="t1"):
        await comp.handle_stream_frame(chat, "", finalize=False, turn_id=turn)
        await comp.handle_stream_frame(chat, "第一段", finalize=True, turn_id=turn)
        assert client._n == 1
        comp._pause_due(chat)  # 窗口到期 → 真落定
        await _settle()
        return comp._states[chat]

    async def test_same_turn_revives_card(self):
        client = _FullClient()
        comp = _make_composer(client)
        chat = "oc_revive"
        st = await self._seal_first_segment(comp, client, chat)
        assert st.finalized is True

        ok = await comp.handle_stream_frame(chat, "第二段", finalize=False, turn_id="t1")
        assert ok is True
        assert client._n == 1, "同 turn 的后继帧必须续用同一张卡"
        st = comp._states[chat]
        assert st.rebuild_only is True
        assert "第二段" in st.answer_text

    async def test_new_turn_gets_new_card(self):
        client = _FullClient()
        comp = _make_composer(client)
        chat = "oc_newturn"
        await self._seal_first_segment(comp, client, chat)
        await comp.handle_stream_frame(chat, "另一回合", finalize=False, turn_id="t2")
        assert client._n == 2

    async def test_empty_finalize_frame_keeps_settle_window(self):
        """空终帧（关空气泡）不算续写，落定窗口必须留着。"""
        client = _FullClient()
        comp = _make_composer(client)
        comp._clarify_suspend_sec = 1.2
        chat = "oc_empty"
        await comp.handle_stream_frame(chat, "", finalize=False, turn_id="t1")
        await comp.handle_stream_frame(chat, "答案", finalize=True, turn_id="t1")
        await comp.handle_stream_frame(chat, "", finalize=False, turn_id="t1")
        st = comp._states[chat]
        assert st.pause_pending is True, "空帧不能拆掉落定窗口"
        assert chat in comp._pause_timers

    async def test_finalize_frame_is_cancelled_by_next_frame(self):
        client = _FullClient()
        comp = _make_composer(client)
        comp._clarify_suspend_sec = 1.2  # 生产默认窗口（config=None 时为 0）
        chat = "oc_pause"
        await comp.handle_stream_frame(chat, "", finalize=False, turn_id="t1")
        await comp.handle_stream_frame(chat, "中途文字", finalize=True, turn_id="t1")
        # 落定窗口未到期，卡还活着
        assert comp._states[chat].finalized is False
        await comp.handle_stream_frame(chat, "继续干活", finalize=False, turn_id="t1")
        assert comp._states[chat].pause_pending is False
        assert chat not in comp._pause_timers
        assert client._n == 1


# ---------------------------------------------------------------------------
# 3. 旧快照护栏
# ---------------------------------------------------------------------------


class TestStaleSnapshotGuard:
    async def test_guard_drops_stale_send(self):
        st = CardTurnState(chat_id="c", seq=5, rev=3)
        sent: list[int] = []

        async def _send(seq: int) -> None:
            sent.append(seq)

        await call_with_seq_recovery(st, _send, what="update:status",
                                     guard=lambda: st.rev == 2)
        assert sent == []

    async def test_guard_blocks_after_finalize(self):
        st = CardTurnState(chat_id="c", seq=5, rev=1)
        sent: list[int] = []

        async def _send(seq: int) -> None:
            sent.append(seq)

        st.finalized = True
        await call_with_seq_recovery(st, _send, what="update:status",
                                     guard=lambda: (not st.finalized) and st.rev == 1)
        assert sent == []

    async def test_guard_allows_current_rev(self):
        st = CardTurnState(chat_id="c", seq=5, rev=1)
        sent: list[int] = []

        async def _send(seq: int) -> None:
            sent.append(seq)

        await call_with_seq_recovery(st, _send, what="update:status",
                                     guard=lambda: (not st.finalized) and st.rev == 1)
        assert sent == [5]

    async def test_patch_status_bumps_rev(self):
        client = _FullClient()
        comp = _make_composer(client)
        chat = "oc_rev"
        await comp.handle_stream_frame(chat, "答案", finalize=False, turn_id="t1")
        st = comp._states[chat]
        before = st.rev
        await comp._patch_status(st)
        assert st.rev >= before
