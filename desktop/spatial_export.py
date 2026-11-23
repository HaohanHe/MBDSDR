# SPDX-License-Identifier: MIT
"""桌面端「导出头显观测包」入口。

把一份 ``*.observation.json`` 导出成 ``mbdsdr.spatial/1`` 离线观测包。
**不在这里重实现导出逻辑**：直接调用 ``scripts/export_spatial_bundle.py``
的 ``main()``，与 CLI、现有测试共用同一份实现（单一来源）。

头显端只读这个包，不联网、不接 SDR；本模块也不给它造第二套数据格式。
"""
from __future__ import annotations

import importlib.util
import io
import os
from contextlib import redirect_stderr, redirect_stdout
from types import ModuleType
from typing import Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CLI_PATH = os.path.join(_ROOT, "scripts", "export_spatial_bundle.py")

_cli: Optional[ModuleType] = None


def _cli_module() -> ModuleType:
    """按路径加载导出 CLI 模块（``scripts/`` 不是包，只能走文件加载）。"""
    global _cli
    if _cli is None:
        spec = importlib.util.spec_from_file_location(
            "_mbdsdr_export_spatial_bundle", _CLI_PATH)
        if spec is None or spec.loader is None:  # pragma: no cover - 环境损坏
            raise ImportError(f"找不到导出脚本：{_CLI_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _cli = mod
    return _cli


def default_out_dir(record_path: str) -> str:
    """默认输出目录：档案同级的 ``<档案名>_spatial``。"""
    name = os.path.splitext(os.path.basename(record_path))[0]
    if name.endswith(".observation"):
        name = name[: -len(".observation")]
    return os.path.join(os.path.dirname(os.path.abspath(record_path)),
                        f"{name}_spatial")


def export_spatial_bundle(record_path: str, out_dir: str, *,
                          passes_hours: float = 0.0) -> Tuple[bool, str]:
    """导出观测包，返回 ``(是否成功, 给人看的一句话)``。

    失败时把 CLI 的 stderr 原文带回来，**不谎报「已导出」**；
    命令返回成功但没落地 ``bundle.json`` 同样判失败。
    """
    if not os.path.isfile(record_path):
        return False, f"档案不存在：{record_path}"

    argv = [record_path, "-o", out_dir]
    if passes_hours > 0:
        argv += ["--passes-hours", str(passes_hours)]

    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = _cli_module().main(argv)
    except Exception as exc:  # noqa: BLE001 - 原文交给界面显示
        return False, f"导出异常：{type(exc).__name__}: {exc}"

    if rc != 0:
        detail = (err_buf.getvalue() or out_buf.getvalue()).strip()
        return False, detail or f"导出失败（退出码 {rc}）"

    bundle_path = os.path.join(out_dir, "bundle.json")
    if not os.path.isfile(bundle_path):
        return False, f"导出命令返回成功，但没有生成 {bundle_path}（不谎报）"
    return True, (f"已导出：{bundle_path}\n"
                  "口径：头显只读离线包，不含实时接收。")
