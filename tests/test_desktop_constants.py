# SPDX-License-Identifier: MIT
"""
desktop/constants.py 常量语义钉扎测试
=====================================

本文件把「desktop 共享常量」的语义钉死，防止它退化成第二个事实源或
散落回各面板：

  1. 采样率常量与内核 ``mbdsdr_ai.sdr_backend.DEFAULT_SAMPLE_RATE_HZ`` 同值
     （单一事实来源）；
  2. mbdsdr_ai 缺失时的安全回退值必须与内核基准一致——回退不是"编个数"，
     回退分支本身要能被触发并断言（不是死代码）；
  3. desktop 侧任何面板都不得重新定义该常量，只能 from constants import；
  4. 常量模块保持"薄"：只放共享常量，不承载面板 id / tab 身份
     （身份的唯一事实源在 panel_registry + dock_layout_manager._tab_key），
     也不依赖 Qt（内核/脚本侧可无头导入）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_desktop_constants.py -v
"""
import importlib.util
import inspect
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_ROOT, "desktop")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _DESKTOP)

import constants  # noqa: E402
import panel_registry  # noqa: E402

KERNEL_RATE_MOD = "mbdsdr_ai.sdr_backend"
#: 项目统一基准采样率（与内核常量逐字一致；此处是钉扎用的期望值）
BASELINE_RATE_HZ = 2_048_000


def _load_constants_without_kernel():
    """在 mbdsdr_ai 不可用的环境下加载一份全新的 desktop/constants.py。"""
    spec = importlib.util.spec_from_file_location(
        "constants_no_kernel", os.path.join(_DESKTOP, "constants.py"))
    mod = importlib.util.module_from_spec(spec)
    saved = {k: sys.modules.get(k) for k in ("mbdsdr_ai", KERNEL_RATE_MOD)}
    sys.modules["mbdsdr_ai"] = None          # from-import 触发 ImportError
    sys.modules[KERNEL_RATE_MOD] = None
    try:
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod


def _desktop_sources():
    """desktop/*.py 的源码文本 {文件名: 文本}。"""
    out = {}
    for name in sorted(os.listdir(_DESKTOP)):
        if name.endswith(".py"):
            with open(os.path.join(_DESKTOP, name), encoding="utf-8") as fh:
                out[name] = fh.read()
    return out


# ----------------------------------------------------------------------
class TestSampleRateConstant:
    def test_value_is_project_baseline(self):
        assert constants.DEFAULT_SAMPLE_RATE_HZ == BASELINE_RATE_HZ

    def test_value_is_int_hz_not_float(self):
        """采样率是整数 Hz（面板档位表/下拉项按整数取值）。"""
        assert isinstance(constants.DEFAULT_SAMPLE_RATE_HZ, int)
        assert not isinstance(constants.DEFAULT_SAMPLE_RATE_HZ, bool)

    def test_matches_kernel_single_source_of_truth(self):
        """与内核基准同值：desktop 侧不另立一套基准。"""
        kernel = pytest.importorskip(
            KERNEL_RATE_MOD, reason="内核 mbdsdr_ai 不可用（无法对拍单一事实源）")
        assert constants.DEFAULT_SAMPLE_RATE_HZ == kernel.DEFAULT_SAMPLE_RATE_HZ

    def test_kernel_absent_fallback_is_reachable_and_consistent(self):
        """mbdsdr_ai 缺失 → 安全回退分支必须生效，且值与内核基准一致。

        回退是"保证 desktop 单独打包仍可导入"，不是"退回一个随便的数字"。
        """
        mod = _load_constants_without_kernel()
        assert mod.DEFAULT_SAMPLE_RATE_HZ == BASELINE_RATE_HZ
        kernel = pytest.importorskip(
            KERNEL_RATE_MOD, reason="内核 mbdsdr_ai 不可用（无法校验回退值一致性）")
        assert mod.DEFAULT_SAMPLE_RATE_HZ == kernel.DEFAULT_SAMPLE_RATE_HZ

    def test_fallback_does_not_raise_when_kernel_missing(self):
        """导入不得因内核缺失而炸（try/except 的意义所在）。"""
        mod = _load_constants_without_kernel()
        assert mod.DEFAULT_SAMPLE_RATE_HZ > 0


# ----------------------------------------------------------------------
class TestNoSecondSourceOfTruth:
    def test_module_exposes_only_the_shared_constant(self):
        """常量模块保持薄身：只许三条共享常量，不夹带别的公开符号。

        2026-10-30 契约演进：E3 频率范围收敛把 FREQ_MIN_HZ/FREQ_MAX_HZ
        收口进本模块（单一事实源），白名单随之从 1 条升到 3 条——
        仍是**封闭白名单**，多一个符号照样红。
        """
        public = {n for n in vars(constants) if not n.startswith("_")}
        assert public == {"DEFAULT_SAMPLE_RATE_HZ", "FREQ_MIN_HZ", "FREQ_MAX_HZ"}, \
            sorted(public)

    def test_no_module_redefines_the_constant(self):
        """desktop 侧只 import，不重新赋值（第二事实源会静默漂移）。"""
        offenders = []
        for name, text in _desktop_sources().items():
            if name == "constants.py":
                continue
            if re.search(r"^\s*DEFAULT_SAMPLE_RATE_HZ\s*=", text, re.M):
                offenders.append(name)
        assert offenders == [], f"这些模块自己定义了采样率常量: {offenders}"

    def test_panels_import_from_constants(self):
        """面板一律 from constants import（不写裸字面量、不各自 import 内核）。"""
        users = [name for name, text in _desktop_sources().items()
                 if name != "constants.py"
                 and re.search(r"from\s+constants\s+import\s+DEFAULT_SAMPLE_RATE_HZ",
                               text)]
        assert len(users) >= 5, f"引用 constants 的面板过少: {users}"
        for name in users:
            text = _desktop_sources()[name]
            assert "from mbdsdr_ai.sdr_backend import DEFAULT_SAMPLE_RATE_HZ" \
                not in text, f"{name} 绕过 constants 直接 import 内核常量"

    def test_module_has_no_qt_dependency(self):
        """常量模块不依赖 Qt：无头脚本/内核侧也能导入。"""
        assert "PySide6" not in _desktop_sources()["constants.py"]


# ----------------------------------------------------------------------
class TestPanelIdentityLivesElsewhere:
    """常量模块不承载面板 id / tab 身份：身份事实源在 panel_registry。"""

    def test_constants_carries_no_panel_id(self):
        """身份常量不得混进共享常量模块（译文/面板名会随语言漂）。"""
        text = _desktop_sources()["constants.py"]
        assert "panel" not in text.lower()
        assert "tab" not in text.lower()

    def test_registry_ids_are_stable_ascii(self):
        """面板 id 必须是稳定 ASCII 标识，不能是译文。"""
        ids = panel_registry.registry().ids()
        assert ids, "注册表为空"
        for pid in ids:
            assert pid and pid.isascii() and pid.isprintable(), pid
            assert not re.search(r"[一-鿿]", pid), f"面板 id 含译文: {pid}"

    def test_dock_layout_hardcoded_ids_are_registered(self):
        """dock_layout_manager 里硬编码的面板 id 必须能在注册表里查到。

        稳定 id 约定：布局落盘/恢复按 id 走，id 拼错 = 静默丢面板。
        """
        from dock_layout_manager import DockLayoutManager  # noqa: E402

        text = _desktop_sources()["dock_layout_manager.py"]
        known = set(panel_registry.registry().ids())
        # 源码里以稳定 id 形态出现的字符串字面量
        used = {pid for pid in known
                if re.search(rf"\"{re.escape(pid)}\"", text)}
        assert used, "未从 dock_layout_manager 里解析到面板 id"
        assert used <= known
        # _tab_key 必须优先取 tabData 里的稳定 id（不是译文）
        src = inspect.getsource(DockLayoutManager._tab_key)
        assert "tabData" in src and "tabText" in src
