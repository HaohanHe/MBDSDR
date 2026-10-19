# SPDX-License-Identifier: MIT
"""
optional_deps.py 可选依赖检测——确定性离线测试

不联网、无硬件；以当前解释器真实 import 能力为准（诚实口径）：
  - 返回 dict 覆盖全部五个已知可选依赖（scipy/skyfield/pyserial/PySide6/SoapySDR）；
  - available=True 时必带 version；False 时必带 fallback 降级文案；
  - 报告与实际可导入性一致（不谎报）；
  - print_deps_status 打印表头与降级方案，且 stdout 可捕获不炸。

运行：python3 -m pytest mbdsdr_ai/tests/test_optional_deps.py -v
"""
from __future__ import annotations

import importlib
import io
import os
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.optional_deps import check_optional_deps, print_deps_status  # noqa: E402

KNOWN = ["scipy", "skyfield", "pyserial", "PySide6", "SoapySDR"]

# 发行名 -> 导入名：pyserial 的模块名是 serial（其余发行名即模块名）。
# optional_deps.py 的 available 判据就是这些模块的真实 import。
IMPORT_NAME = {
    "scipy": "scipy",
    "skyfield": "skyfield",
    "pyserial": "serial",
    "PySide6": "PySide6",
    "SoapySDR": "SoapySDR",
}


def _actually_importable(name: str) -> bool:
    """当前解释器能否真正 import 该依赖（导入期任何异常都算不可用）。"""
    try:
        importlib.import_module(IMPORT_NAME[name])
        return True
    except Exception:
        return False


def test_covers_all_known_optional_deps():
    deps = check_optional_deps()
    assert set(deps) == set(KNOWN)


def test_available_entries_have_version():
    deps = check_optional_deps()
    for name, info in deps.items():
        assert isinstance(info["available"], bool)
        if info["available"]:
            assert "version" in info and info["version"]
        else:
            assert "fallback" in info and info["fallback"]


def test_report_matches_real_importability():
    """检测结果必须与当前解释器真实 import 能力一致（不谎报可用）。"""
    deps = check_optional_deps()
    for name in KNOWN:
        assert deps[name]["available"] == _actually_importable(name), (
            f"{name} 报告 {deps[name]['available']} 与实际不符")


def test_soapysdr_version_reported_unknown_when_available():
    # 已知口径：SoapySDR 无稳定 __version__，源码固定报 'unknown'（不许假装版本号）
    deps = check_optional_deps()
    if deps["SoapySDR"]["available"]:
        assert deps["SoapySDR"]["version"] == "unknown"


def test_print_deps_status_smoke(capsys):
    print_deps_status()
    out = capsys.readouterr().out
    assert "MBDSDR 依赖状态" in out
    assert "降级方案" in out or all(
        i["available"] for i in check_optional_deps().values())
    # 每个依赖名都出现在输出里
    for name in KNOWN:
        assert name in out


def test_print_deps_status_does_not_raise_when_captured():
    """即使 stdout 被替换成非 tty 缓冲区也不炸。"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_deps_status()
    assert buf.getvalue()
