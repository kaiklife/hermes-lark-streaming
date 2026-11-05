"""探针：验证「已 close_streaming 的卡片能否再被 cardkit_update / batch_update 写」。

revive 修法（v2.0.30）押在这个行为上，仓库里没有真机证据。本脚本只建卡 +
写元素 + 关流式 + 再更新，**不发送到任何会话**（不产生用户可见消息）。
"""
import asyncio
import importlib.util
import os
import sys

PLUGIN = os.path.expanduser("~/.hermes/plugins/hermes-lark-streaming")
spec = importlib.util.spec_from_file_location(
    "hermes_lark_streaming", os.path.join(PLUGIN, "__init__.py"),
    submodule_search_locations=[PLUGIN])
mod = importlib.util.module_from_spec(spec)
sys.modules["hermes_lark_streaming"] = mod
spec.loader.exec_module(mod)

from hermes_lark_streaming.cardkit.cards import build_streaming_card_v2  # noqa: E402
from hermes_lark_streaming.cardkit.elements import (  # noqa: E402
    ANSWER_ELEMENT_ID, _LOADING_ELEMENT_ID, _build_footer_elements)
from hermes_lark_streaming.feishu.client import FeishuClient, FeishuClientConfig  # noqa: E402


def creds() -> tuple[str, str]:
    env = os.path.expanduser("~/.hermes/.env")
    vals = {}
    for line in open(env, encoding="utf-8"):
        line = line.strip()
        if line.startswith("FEISHU_APP_ID=") or line.startswith("FEISHU_APP_SECRET="):
            k, v = line.split("=", 1)
            vals[k] = v.strip().strip('"').strip("'")
    return vals["FEISHU_APP_ID"], vals["FEISHU_APP_SECRET"]


async def main() -> None:
    app_id, app_secret = creds()
    client = FeishuClient(FeishuClientConfig(app_id=app_id, app_secret=app_secret))

    card = build_streaming_card_v2(status_lines=["📝 **Terminal (12 ms)**\nls -lt"], answer_text="")
    card_id = await client.cardkit_create(card)
    print("1 create          ->", card_id)

    await client.cardkit_stream_element(card_id, ANSWER_ELEMENT_ID, "第一段答案", sequence=1)
    print("2 stream_element  -> ok")

    await client.cardkit_close_streaming(card_id, sequence=2, summary="探针")
    print("3 close_streaming -> ok")

    try:
        await client.cardkit_stream_element(card_id, ANSWER_ELEMENT_ID, "第二段答案", sequence=3)
        print("4 stream_element after close -> OK（意外：不该成功）")
    except Exception as exc:  # noqa: BLE001
        print("4 stream_element after close -> 拒绝:", getattr(exc, "code", "?"), str(exc)[:90])

    rebuilt = build_streaming_card_v2(
        status_lines=["📝 **Terminal (12 ms)**\nls -lt", "📝 **Terminal (30 ms)**\nwhoami"],
        answer_text="第一段答案\n\n第二段答案（rebuild）")
    try:
        await client.cardkit_update(card_id, rebuilt, sequence=4)
        print("5 cardkit_update after close -> OK")
    except Exception as exc:  # noqa: BLE001
        print("5 cardkit_update after close -> 拒绝:", getattr(exc, "code", "?"), str(exc)[:90])

    footer = _build_footer_elements({"duration": 12.0, "model": "probe"}, fields=[["status", "elapsed", "model"]])
    try:
        await client.cardkit_batch_update(card_id, [{
            "action": "add_elements",
            "params": {"type": "insert_before", "target_element_id": _LOADING_ELEMENT_ID,
                       "elements": footer},
        }], sequence=5)
        print("6 batch_update footer after close -> OK")
    except Exception as exc:  # noqa: BLE001
        print("6 batch_update footer after close -> 拒绝:", getattr(exc, "code", "?"), str(exc)[:90])

    try:
        await client.cardkit_update(card_id, rebuilt, sequence=6)
        print("7 second cardkit_update -> OK")
    except Exception as exc:  # noqa: BLE001
        print("7 second cardkit_update -> 拒绝:", getattr(exc, "code", "?"), str(exc)[:90])


asyncio.run(main())
