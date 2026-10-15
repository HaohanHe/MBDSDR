# SPDX-License-Identifier: MIT
"""校准数据落盘/加载（第十四批）：误差盒 + 校准件定义件 JSON 序列化。

上游口径（Calibration.cpp / common.h saveCalibrationFile）：校准系数
随文件存盘复用。本实现独立 JSON 格式（schema versioned，坏文件显式
报错不静默回退）：

- save_error_box(path, freqs_hz, box)：e00/t/e11 逐频点复数 + 频率轴，
  含口径标注（单端口 SOLT 误差盒，上游 AN 1287-3 术语）。
- load_error_box(path) -> (freqs, ErrorBox)：schema 版本不符/字段缺失/
  长度不齐显式 ValueError。
- save_cal_kit / load_cal_kit：OSL 三件定义件参数（多项式系数 + 偏移
  段 + use_ideal），跨会话复用同一套定义件口径。

诚实边界：JSON 不藏二进制魔法数，任何字段语义变更必须 bump
schema_version 并在 loader 里显式拒绝旧版（本批只有 v1）。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Tuple

import numpy as np

from .vna_cal import ErrorBox, LoadStandard, OpenStandard, ShortStandard

__all__ = [
    "SCHEMA_VERSION",
    "save_error_box", "load_error_box",
    "save_cal_kit", "load_cal_kit",
]

SCHEMA_VERSION = 1
_FORMAT = "mbdsdr.vna_cal.error_box"
_KIT_FORMAT = "mbdsdr.vna_cal.kit"


def _complex_list(z) -> list:
    return [[float(v.real), float(v.imag)] for v in np.asarray(z)]


def _complex_back(pairs) -> np.ndarray:
    return np.array([complex(float(r), float(i)) for r, i in pairs],
                    dtype=np.complex128)


def save_error_box(path: str, freqs_hz, box: ErrorBox) -> None:
    """误差盒逐频点序列化；频率轴与误差项长度须一致。"""
    f = np.asarray(freqs_hz, dtype=np.float64)
    n = len(f)
    for name, arr in (("e00", box.e00), ("t", box.t), ("e11", box.e11)):
        if len(np.asarray(arr)) != n:
            raise ValueError(f"{name} 长度 {len(np.asarray(arr))} != "
                             f"频率轴 {n}")
    doc = {
        "schema_version": SCHEMA_VERSION,
        "format": _FORMAT,
        "口径": "单端口 SOLT 误差盒（e00=方向性, t=e10e01=反射跟踪, "
                "e11=源匹配），apply: Γ=(Γm−e00)/(t+e11(Γm−e00))",
        "freqs_hz": [float(v) for v in f],
        "e00": _complex_list(box.e00),
        "t": _complex_list(box.t),
        "e11": _complex_list(box.e11),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)


def load_error_box(path: str) -> Tuple[np.ndarray, ErrorBox]:
    """加载误差盒；版本/格式/长度校验全显式。"""
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != _FORMAT:
        raise ValueError(f"文件格式 {doc.get('format')!r} 非 {_FORMAT!r}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version {doc.get('schema_version')} 不受支持"
            f"（当前 {SCHEMA_VERSION}）")
    f = np.asarray(doc["freqs_hz"], dtype=np.float64)
    e00, t, e11 = (_complex_back(doc[k]) for k in ("e00", "t", "e11"))
    for name, arr in (("e00", e00), ("t", t), ("e11", e11)):
        if len(arr) != len(f):
            raise ValueError(f"{name} 长度 {len(arr)} != 频率轴 {len(f)}")
    if np.any(np.diff(f) < 0):
        raise ValueError("频率轴乱序")
    return f, ErrorBox(e00=e00, t=t, e11=e11)


def save_cal_kit(path: str, open_std: OpenStandard,
                 short_std: ShortStandard,
                 load_std: LoadStandard) -> None:
    """OSL 定义件参数落盘（系数单位按 Keysight 惯例 GHz 系）。"""
    doc = {
        "schema_version": SCHEMA_VERSION,
        "format": _KIT_FORMAT,
        "口径": "Open/Short 多项式系数（f 代 GHz）+ Load 集总模型 + "
                "偏移段（双程相移，只相位无损耗）",
        "open": asdict(open_std),
        "short": asdict(short_std),
        "load": asdict(load_std),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)


def load_cal_kit(path: str) -> Tuple[OpenStandard, ShortStandard,
                                     LoadStandard]:
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != _KIT_FORMAT:
        raise ValueError(f"文件格式 {doc.get('format')!r} 非 {_KIT_FORMAT!r}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version {doc.get('schema_version')} 不受支持"
            f"（当前 {SCHEMA_VERSION}）")
    try:
        return (OpenStandard(**doc["open"]),
                ShortStandard(**doc["short"]),
                LoadStandard(**doc["load"]))
    except TypeError as e:
        raise ValueError(f"定义件字段缺失/多余：{e}") from None
