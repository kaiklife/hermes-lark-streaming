"""启动死锁回归 — 插件注册期绝不 import ``gateway.run``。

gateway.run 的模块体在导入期触发插件发现（gateway/run.py 的 config→env 桥），所以插件
注册恰好发生在 gateway.run 自己还没导入完的窗口里。此时一句 ``from gateway.run import …``
会去等该模块的导入锁，而持锁的线程正等我们注册完 → 启动死锁（启动看门狗 10 分钟后以
TEMPFAIL 退出，服务要等下一次重试才起得来）。

契约：注册期的解析只读 ``sys.modules``。gateway.run 还不在时就返回 None，由
``patching/__init__.py`` 的延迟轮询事后补齐。

上游 v1.8.3 用同一套思路把 8 处解析全改成 sys.modules 读取（+ ``_hls_wrapped`` 哨兵），
本文件只留两条不依赖实现细节的不变量：注册期不许 import、已完成的模块必须能被用上。
半导入窗口那条是上游有意不处理的（此时 ``getattr`` 拿不到类属性就返回 None，拿得到就是
已构造好的类），故不再断言。
"""

from __future__ import annotations

import builtins
import importlib
import sys
import types

import pytest

from hermes_lark_streaming.patching import hermes_adapter

HermesCompat = hermes_adapter.HermesCompat

_WATCHED = "gateway.run"


def _watch(monkeypatch, names: tuple[str, ...]) -> list[str]:
    """记录对 *names* 的 import 尝试：覆盖 ``from X import Y`` 和 ``import_module(X)`` 两个入口。"""
    attempts: list[str] = []

    def _hit(name: str) -> bool:
        return any(name == w or name.startswith(w + ".") for w in names)

    real_import = builtins.__import__

    def spy_import(name, *args, **kwargs):  # noqa: ANN001
        if _hit(name):
            attempts.append(name)
        return real_import(name, *args, **kwargs)

    real_import_module = importlib.import_module

    def spy_import_module(name, package=None):  # noqa: ANN001
        if _hit(name):
            attempts.append(name)
        return real_import_module(name, package)

    monkeypatch.setattr(builtins, "__import__", spy_import)
    monkeypatch.setattr(importlib, "import_module", spy_import_module)
    return attempts


@pytest.fixture
def gateway_run_imports(monkeypatch):
    """gateway.run 既不在 sys.modules 里，也不允许被 import —— 就是死锁那个窗口。"""
    monkeypatch.delitem(sys.modules, _WATCHED, raising=False)
    return _watch(monkeypatch, (_WATCHED,))


def test_resolution_never_imports_gateway_run(gateway_run_imports):
    """注册期解析不能触发 gateway.run 的 import —— 那就是死锁的入口。"""
    compat = HermesCompat()

    assert compat.gateway_runner_class is None
    assert compat.has_gateway_runner is False
    assert gateway_run_imports == [], (
        "解析期 import 了 gateway.run：会和正在导入 gateway.run 的线程互锁（启动挂死）"
    )


def test_finished_module_is_used(monkeypatch):
    """导入完成后能正常拿到类 —— 延迟补齐那条路必须仍然有效。"""
    sentinel = object()
    mod = types.ModuleType(_WATCHED)
    mod.GatewayRunner = sentinel
    monkeypatch.setitem(sys.modules, _WATCHED, mod)

    assert HermesCompat().gateway_runner_class is sentinel
