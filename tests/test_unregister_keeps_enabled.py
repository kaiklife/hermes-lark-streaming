"""unload ≠ uninstall —— ``unregister`` 不能删掉自己的 ``plugins.enabled`` 条目。

``unregister`` 在每次插件卸载/重载（``discover_and_load(force=True)``）和进程收尾时都会跑。
若这里做 config 清理，条目会被删掉 → 下一次启动网关读到的就是「未启用」→ 插件被静默跳过：
卡片照发但没插件渲染（流式卡片/底栏全没了），而网关一切「看起来正常」。
2026-09-11 现场：09:45:42 的 config 备份里还有该条目，10:19 之前就没了，重启后卡片失去底栏。
"""

from __future__ import annotations

import yaml

from hermes_lark_streaming.plugin import unregister

_PLUGIN = "hermes-lark-streaming"


def _write_config(home, payload: dict):
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_unregister_keeps_the_enable_entry_and_injected_section(monkeypatch, tmp_path):
    home = tmp_path / "hermes"
    path = _write_config(home, {
        "plugins": {"enabled": [_PLUGIN, "ponytail", "web/tavily"], "disabled": []},
        "hermes_lark_streaming": {"enabled": True, "footer": {"fields": [["status", "elapsed"]]}},
    })
    monkeypatch.setenv("HERMES_HOME", str(home))

    unregister(ctx=None)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert _PLUGIN in raw["plugins"]["enabled"], (
        "unregister 删掉了自己的 plugins.enabled 条目：下次启动网关会把它当未启用跳过"
    )
    assert "hermes_lark_streaming" in raw, "unregister 删掉了注入的配置段"


def test_unregister_is_idempotent(monkeypatch, tmp_path):
    """收尾可能跑多次：重复调用也不能动 config。"""
    home = tmp_path / "hermes"
    path = _write_config(home, {"plugins": {"enabled": [_PLUGIN], "disabled": []}})
    monkeypatch.setenv("HERMES_HOME", str(home))

    unregister(ctx=None)
    unregister(ctx=None)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["plugins"]["enabled"] == [_PLUGIN]
