"""启动死锁回归 — 插件注册期绝不 import ``gateway.run``。

gateway.run 的模块体在导入期触发插件发现（gateway/run.py 的 config→env 桥），所以插件
注册恰好发生在 gateway.run 自己还没导入完的窗口里。此时一句 ``from gateway.run import …``
会去等该模块的导入锁，而持锁的线程正等我们注册完 → 启动死锁（启动看门狗 10 分钟后以
TEMPFAIL 退出，服务要等下一次重试才起得来）。

契约：注册期的解析只读 ``sys.modules``，并且把「半导入」（``__spec__._initializing``）
当成「还没到」——延迟轮询那条路仍然负责事后补齐。

注意：本文件按模块导入、并且不直接 import ``_module_if_loaded``，这样即使有人把修复回退
掉，第 1 条也能收集成功并以「确实 import 了 gateway.run」的行为性断言变红，而不是只报一个
收集期 ImportError。
"""

from __future__ import annotations

import builtins
import importlib
import importlib.machinery
import sys
import types

import pytest

from hermes_lark_streaming.patching import hermes_adapter

HermesCompat = hermes_adapter.HermesCompat

_WATCHED = "gateway.run"


def _module_if_loaded(name):
    """修复后才存在的辅助函数（用 getattr 取，老代码上不让整个文件收集失败）。"""
    return hermes_adapter._module_if_loaded(name)


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


def test_half_imported_gateway_run_reads_as_unavailable(monkeypatch):
    """半导入的模块不能用：它的类属性可能还没挂上去。"""
    mod = types.ModuleType(_WATCHED)
    mod.__spec__ = importlib.machinery.ModuleSpec(_WATCHED, loader=None)
    mod.__spec__._initializing = True
    mod.GatewayRunner = object()  # 就算属性在也不认
    monkeypatch.setitem(sys.modules, _WATCHED, mod)

    assert _module_if_loaded(_WATCHED) is None
    assert HermesCompat().gateway_runner_class is None


def test_finished_module_is_used(monkeypatch):
    """导入完成后能正常拿到类 —— 延迟补齐那条路必须仍然有效。"""
    sentinel = object()
    mod = types.ModuleType(_WATCHED)
    mod.__spec__ = importlib.machinery.ModuleSpec(_WATCHED, loader=None)
    mod.GatewayRunner = sentinel
    monkeypatch.setitem(sys.modules, _WATCHED, mod)

    assert _module_if_loaded(_WATCHED) is mod
    assert HermesCompat().gateway_runner_class is sentinel


def test_spec_less_module_is_usable(monkeypatch):
    """没有 __spec__ 的模块（内置 / 手动注入）不存在半导入状态 → 视为已完成。"""
    mod = types.ModuleType(_WATCHED)
    monkeypatch.setitem(sys.modules, _WATCHED, mod)

    assert _module_if_loaded(_WATCHED) is mod
