# SPDX-License-Identifier: MIT
"""启动自检（缺件硬门槛）单元测试。

覆盖 desktop/main.py 的 _startup_dependency_problems：
  * 必需依赖全在 → 空清单；
  * 必需依赖缺一项 → 清单里写明「缺什么 / 缺了会怎样 / 怎么修」；
  * scipy 系只做 find_spec 存在性检查，启动前不真 import（首次导入 1.0–1.3 s
    推迟到首帧之后，见 _start_deferred_imports）；
  * 必需数据件缺失 → 同样进清单；
  * 可选依赖缺失 → 只记日志，不进清单、不拦启动。
"""
import importlib
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_desktop_main = importlib.import_module("main")


def test_selfcheck_passes_when_required_items_present():
    assert _desktop_main._startup_dependency_problems() == [], \
        "当前开发环境必需项齐全，自检必须放行"


def test_selfcheck_reports_missing_required_with_fix_instructions(monkeypatch):
    monkeypatch.setattr(_desktop_main, "_probe_module_spec",
                        lambda name: name != "scipy.signal")
    problems = _desktop_main._startup_dependency_problems()
    assert len(problems) == 1
    text = problems[0]
    assert "scipy.signal" in text, "必须点名缺了哪个模块"
    assert "缺了会怎样" in text and "怎么修" in text, \
        "每条缺失项必须写清后果和修复方式"


def test_selfcheck_does_not_import_scipy_before_first_frame(monkeypatch):
    """反证：门槛只要碰 scipy 就真 import，这条必红（首次导入 ≈1.0–1.3 s）。"""
    seen = []

    def _boom(name):
        if name == "scipy" or name.startswith("scipy."):
            seen.append(name)
            raise AssertionError("启动前真 import scipy: " + name)
        return True

    monkeypatch.setattr(_desktop_main, "_probe_import", _boom)
    monkeypatch.setattr(_desktop_main, "_probe_module_spec", lambda name: True)
    assert _desktop_main._startup_dependency_problems() == []
    assert seen == [], "scipy 系必须在首帧之后才真 import"


def test_selfcheck_reports_missing_required_data_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        _desktop_main, "_required_data_path",
        lambda rel: str(tmp_path / "missing.json"))
    problems = _desktop_main._startup_dependency_problems()
    assert len(problems) == 1
    assert "dso_catalog.json" in problems[0]
    assert "缺了会怎样" in problems[0] and "怎么修" in problems[0]


def _make_pyc_only_pkg(tmp_path, name: str, legacy: bool) -> list[str]:
    """造一个包，编译后删源码；legacy=True 剩 pkg/mod.pyc，False 剩 __pycache__。"""
    import compileall
    pkg = tmp_path / name
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("P = 1\n", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("S = 1\n", encoding="utf-8")
    (pkg / "sub" / "mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    assert compileall.compile_dir(str(pkg), quiet=1, legacy=legacy)
    for src in pkg.rglob("*.py"):
        src.unlink()
    return [str(tmp_path)]


def test_probe_sees_pyc_only_package_without_importing_it(tmp_path):
    """冻结包 noarchive 落盘是 legacy .pyc：门槛必须看得见，且不能真 import。"""
    paths = _make_pyc_only_pkg(tmp_path, "mbdsdr_pyc_probe", legacy=True)
    name = "mbdsdr_pyc_probe.sub.mod"
    saved = list(sys.path)
    sys.path[:0] = paths
    try:
        assert _desktop_main._probe_module_spec(name) is True, \
            "只有 .pyc 的包被判缺件：产物上会误中止启动"
        assert name not in sys.modules, "存在性检查把包 import 进来了"
        assert "mbdsdr_pyc_probe" not in sys.modules
    finally:
        sys.path[:] = saved
        for mod in [m for m in list(sys.modules) if m.startswith("mbdsdr_pyc_probe")]:
            sys.modules.pop(mod, None)


def test_probe_rejects_pycache_only_package(tmp_path):
    """只剩 __pycache__/*.pyc、没有 legacy .pyc 的形态 Python 3 导入不了，判缺件是对的。"""
    paths = _make_pyc_only_pkg(tmp_path, "mbdsdr_pycache_probe", legacy=False)
    saved = list(sys.path)
    sys.path[:0] = paths
    try:
        assert _desktop_main._probe_module_spec(
            "mbdsdr_pycache_probe.sub.mod") is False
    finally:
        sys.path[:] = saved


def test_probe_handles_namespace_package_without_importing(tmp_path):
    """scipy._external 是 PEP 420 命名空间包：父包没 import 时 PathFinder 抛
    KeyError，门槛必须退回按目录判断，且不能真把父包导进来。"""
    pkg = tmp_path / "mbdsdr_ns_probe"
    (pkg / "sub").mkdir(parents=True)          # 故意不放 __init__.py
    (pkg / "sub" / "mod.py").write_text("V = 1\n", encoding="utf-8")
    saved = list(sys.path)
    sys.path.insert(0, str(tmp_path))
    try:
        assert _desktop_main._probe_module_spec("mbdsdr_ns_probe.sub.mod") is True
        assert "mbdsdr_ns_probe" not in sys.modules
    finally:
        sys.path[:] = saved


def test_probe_falls_back_to_stdlib_find_spec(monkeypatch):
    """目录遍历认不出的形态（zip/PYZ）必须交给标准 find_spec 复核，不能直接判缺件。"""
    monkeypatch.setattr(_desktop_main, "_module_exists_without_import",
                        lambda name: None)
    assert _desktop_main._probe_module_spec("json") is True
    assert _desktop_main._probe_module_spec("no_such_module_mbdsdr") is False


def test_optional_dependency_missing_does_not_block_startup(monkeypatch):
    monkeypatch.setattr(
        _desktop_main, "_probe_import",
        lambda name: name not in ("sounddevice", "sgp4"))
    problems = _desktop_main._startup_dependency_problems()
    assert problems == [], "可选依赖缺失只能记日志，不得拦启动"


def test_deferred_failure_reports_to_status_bar_and_log(monkeypatch, tmp_path):
    """「文件在但加载失败」：窗口已出，只能不阻塞地提示 + 写日志。

    曾有 MSG_IMPORTANT 未定义，异常被 Qt 槽吞掉、状态栏一个字都不显示；
    本用例钉住「状态栏真的拿到文案」与「文案含缺了会怎样 / 怎么修」。
    """
    shown = []

    class _Status:
        def showMessage(self, text, timeout):
            shown.append((text, timeout))

    class _Window:
        def statusBar(self):             # noqa: N802 (Qt 命名)
            return _Status()

    log = tmp_path / "startup.log"
    monkeypatch.setenv("MBDSDR_STARTUP_LOG", str(log))
    _desktop_main._report_deferred_import_failure(
        _Window(), "scipy._external.packaging_version", "ImportError: boom")

    assert len(shown) == 1, "失败必须落到状态栏"
    text, timeout = shown[0]
    assert "scipy._external.packaging_version" in text
    assert isinstance(timeout, int) and timeout > 0, "必须带超时：不阻塞、也不永久占位"

    written = log.read_text(encoding="utf-8")
    assert "延后导入失败" in written
    assert "缺了会怎样" in written and "怎么修" in written
