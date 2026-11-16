# SPDX-License-Identifier: MIT
"""
SigMF（Signal Metadata Format）元数据读写扩展
=============================================

本模块为基带录制流程提供 **额外** 的标准 SigMF ``.sigmf-meta`` 侧车元数据读写
能力。它与录制主流程已经写出的 ``.json`` sidecar 并行存在、互不替代：

- 录制时：在 IQ 数据文件同目录写一份符合 SigMF 1.0.0 标准的 ``.sigmf-meta``，
  供通用 SDR 工具链（SigDigger / inspectrum / GNU Radio 等）直接识别。
- 回放时：FileIQBackend 仍读原有 ``.json`` sidecar；本模块额外提供
  ``read_sigmf_meta`` / ``metadata_for_display``，把标准元数据拍平成 UI 友好
  的字段，供界面展示采样率、中心频率、增益、时长等信息。

设计约束：
- 仅依赖 Python 标准库（json / os / logging），不引入 Qt、不强制依赖 numpy。
- 所有文件写入异常一律吞掉并记 ``logging.warning``，元数据失败绝不影响录制
  主流程本身。
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# SigMF 核心命名空间前缀
_CORE = "core"
# 本项目私有扩展命名空间前缀
_EXT = "mbdsdr"

# 遵循的 SigMF 规范版本
SIGMF_VERSION = "1.0.0"


def sigmf_meta_path(iq_path: str) -> str:
    """返回与 IQ 文件同目录、同 basename 的 ``.sigmf-meta`` 路径。

    例如 ``/x/foo.cf32`` -> ``/x/foo.sigmf-meta``；
    ``/x/foo``  -> ``/x/foo.sigmf-meta``。
    """
    directory, basename = os.path.split(iq_path)
    stem, _ext = os.path.splitext(basename)
    return os.path.join(directory, stem + ".sigmf-meta")


def _validate_captures(captures: Any) -> List[Dict]:
    """写侧校验 captures[]：非法一律 raise ValueError（不静默写坏元数据）。

    规范口径：captures 非空列表；每条 dict 必带 ``core:sample_start``
    （非负整数、非 bool）与 ``core:frequency``（有限数字）；sample_start
    必须严格递增（乱序/重复即非法）。校验通过返回浅拷贝列表。
    """
    if not isinstance(captures, list) or not captures:
        raise ValueError("captures 必须是非空列表")
    out: List[Dict] = []
    last_start = -1
    for c in captures:
        if not isinstance(c, dict):
            raise ValueError("capture 必须是 dict")
        if f"{_CORE}:sample_start" not in c:
            raise ValueError("capture 缺 core:sample_start")
        if f"{_CORE}:frequency" not in c:
            raise ValueError("capture 缺 core:frequency")
        start = c[f"{_CORE}:sample_start"]
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise ValueError("core:sample_start 必须是非负整数")
        if start <= last_start:
            raise ValueError("captures 的 core:sample_start 必须严格递增")
        try:
            freq_f = float(c[f"{_CORE}:frequency"])
        except (TypeError, ValueError):
            raise ValueError("core:frequency 必须是数字")
        if not math.isfinite(freq_f):
            raise ValueError("core:frequency 必须有限")
        last_start = start
        out.append(dict(c))
    return out


def _build_captures_section(
    *,
    center_freq_hz: float,
    gain_db: float,
    start_time_iso: str,
    captures: Optional[List[Dict]],
) -> List[Dict]:
    """构造 captures 段：未显式给 captures 时退回单条默认（起始频点）。

    显式给 captures 时走 :func:`_validate_captures`（非法即 raise）。
    """
    if captures is None:
        capture: Dict[str, Any] = {
            f"{_CORE}:sample_start": 0,
            f"{_CORE}:frequency": center_freq_hz,
            f"{_EXT}:gain_db": gain_db,
        }
        if start_time_iso:
            capture[f"{_CORE}:datetime"] = start_time_iso
        return [capture]
    return _validate_captures(captures)


def build_sigmf_meta(
    *,
    sample_rate: float,
    center_freq_hz: float,
    gain_db: float = 0.0,
    start_time_iso: str = "",
    hardware: str = "",
    datatype: str = "cf32_le",
    num_samples: Optional[int] = None,
    author: str = "MBDSDR",
    description: str = "",
    captures: Optional[List[Dict]] = None,
    annotations: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """构造标准 SigMF 元数据字典（尚未落盘）。

    参数：
        sample_rate: 采样率（Hz）。
        center_freq_hz: 中心频率（Hz）。
        gain_db: 接收增益（dB），放入 capture 段的私有扩展字段。
        start_time_iso: 录制起始时间 ISO8601 字符串（可带时区）。
        hardware: 硬件描述字符串（如 ``RTL-SDR``）。
        datatype: SigMF 数据类型标识（如 ``cf32_le`` / ``cu8``）。
        num_samples: 样本总数；录制进行中传 None，结束时补全。
        author: 作者标识。
        description: 描述文本。
        captures: 多段调谐捕获清单（每条含 core:sample_start / core:frequency）。
            None 时退回单条默认（起始频点）；显式给出则严格校验，非法 raise
            ValueError（不静默写坏元数据）。
        annotations: 事件标注清单（原样透传）；None/空 → 空列表，不自造标注。

    返回：
        符合 SigMF 1.0.0 的字典，含 ``global`` / ``captures`` / ``annotations``
        三段。
    """
    global_sec: Dict[str, Any] = {
        f"{_CORE}:datatype": datatype,
        f"{_CORE}:sample_rate": sample_rate,
        f"{_CORE}:version": SIGMF_VERSION,
        f"{_CORE}:num_channels": 1,
        f"{_CORE}:frequency": center_freq_hz,
        f"{_CORE}:hw": hardware,
        f"{_CORE}:author": author,
        f"{_CORE}:description": description,
    }
    if num_samples is not None:
        global_sec[f"{_CORE}:num_samples"] = num_samples

    return {
        "global": global_sec,
        "captures": _build_captures_section(
            center_freq_hz=center_freq_hz, gain_db=gain_db,
            start_time_iso=start_time_iso, captures=captures),
        "annotations": list(annotations) if annotations else [],
    }


def write_sigmf_meta(iq_path: str, **kwargs: Any) -> str:
    """根据 IQ 路径构造并写入 ``.sigmf-meta``，返回 meta 文件路径。

    透传关键字参数给 :func:`build_sigmf_meta`。写文件失败时吞掉异常、
    记 warning，并仍返回预期的 meta 路径（调用方不应因元数据失败而中断录制）。
    """
    meta_path = sigmf_meta_path(iq_path)
    meta = build_sigmf_meta(**kwargs)
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # 元数据失败不影响录制主流程
        logger.warning("写入 SigMF meta 失败（已忽略）: %s: %s", meta_path, exc)
    return meta_path


def read_sigmf_meta(iq_path: str) -> Optional[Dict[str, Any]]:
    """读回 IQ 文件对应的 ``.sigmf-meta``。

    文件不存在或解析失败时返回 None（回放端据此判断无标准元数据）。
    """
    meta_path = sigmf_meta_path(iq_path)
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("读取 SigMF meta 失败（已忽略）: %s: %s", meta_path, exc)
        return None


def metadata_for_display(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把 SigMF 元数据拍平成 UI 友好的展示字段。

    返回字段：sample_rate_hz / center_freq_hz / gain_db / start_time /
    hardware / datatype / num_samples / duration_s。无法计算时长时
    duration_s 为 None。
    """
    empty = {
        "sample_rate_hz": None,
        "center_freq_hz": None,
        "gain_db": None,
        "start_time": None,
        "hardware": None,
        "datatype": None,
        "num_samples": None,
        "duration_s": None,
    }
    if not meta:
        return empty

    g = meta.get("global", {}) or {}
    caps = meta.get("captures", []) or []
    c = caps[0] if caps else {}

    sample_rate = g.get(f"{_CORE}:sample_rate")
    center_freq = g.get(f"{_CORE}:frequency")
    num_samples = g.get(f"{_CORE}:num_samples")

    duration = None
    if num_samples and sample_rate:
        try:
            duration = float(num_samples) / float(sample_rate)
        except (TypeError, ZeroDivisionError, ValueError):
            duration = None

    return {
        "sample_rate_hz": sample_rate,
        "center_freq_hz": center_freq,
        "gain_db": c.get(f"{_EXT}:gain_db"),
        "start_time": c.get(f"{_CORE}:datetime"),
        "hardware": g.get(f"{_CORE}:hw"),
        "datatype": g.get(f"{_CORE}:datatype"),
        "num_samples": num_samples,
        "duration_s": duration,
    }


class SigMFRecordingSession:
    """录制会话辅助类：随录制进程增量维护 ``.sigmf-meta``。

    用法::

        with SigMFRecordingSession(iq_path, sample_rate=sr, center_freq_hz=f,
                                   gain_db=g, start_time_iso=iso) as sess:
            # 录制进行中可随时 update 当前已落盘样本数
            sess.update(count)
        # 退出 with 块自动 finalize(最终样本数)

    所有落盘异常均吞掉记 warning，绝不抛出影响录制主循环。
    """

    def __init__(self, iq_path: str, **meta_kwargs: Any) -> None:
        self._iq_path = iq_path
        self._meta_kwargs = dict(meta_kwargs)
        self._meta_path = sigmf_meta_path(iq_path)
        self._captures: List[Dict] = []
        self._annotations: List[Dict] = []

    @property
    def meta_path(self) -> str:
        return self._meta_path

    def add_capture(self, sample_start: int, frequency_hz: float, **extra: Any) -> None:
        """录制中调谐：追加一条 capture（首条 sample_start=0 由 _dump 默认补）。

        只累积，不立即落盘；下一次 _dump（update/finalize）时连同默认首段
        一起写出。非法 sample_start/frequency 会在 _dump 走写侧校验时 raise。
        """
        cap: Dict[str, Any] = {
            f"{_CORE}:sample_start": int(sample_start),
            f"{_CORE}:frequency": float(frequency_hz),
        }
        cap.update(extra)
        self._captures.append(cap)

    def add_annotation(self, sample_start: int, sample_count: int, **extra: Any) -> None:
        """事件标注：追加一条 annotation（同样累积到下次 _dump 落盘）。"""
        ann: Dict[str, Any] = {
            f"{_CORE}:sample_start": int(sample_start),
            f"{_CORE}:sample_count": int(sample_count),
        }
        ann.update(extra)
        self._annotations.append(ann)

    def _dump(self, num_samples: Optional[int]) -> None:
        """按当前参数落盘一次 meta（异常吞掉）。"""
        kwargs = dict(self._meta_kwargs)
        kwargs["num_samples"] = num_samples
        if self._captures:
            # 有调谐段时，补一条 sample_start=0 的起始段（沿用录制起始频点/增益），
            # 再拼上累积的调谐段，交给 build_sigmf_meta 走写侧严格校验。
            first: Dict[str, Any] = {
                f"{_CORE}:sample_start": 0,
                f"{_CORE}:frequency": kwargs.get("center_freq_hz"),
                f"{_EXT}:gain_db": kwargs.get("gain_db", 0.0),
            }
            if kwargs.get("start_time_iso"):
                first[f"{_CORE}:datetime"] = kwargs["start_time_iso"]
            kwargs["captures"] = [first] + self._captures
        if self._annotations:
            kwargs["annotations"] = self._annotations
        write_sigmf_meta(self._iq_path, **kwargs)

    def start(self) -> None:
        """录制开始：立即写一份初始 meta（num_samples 暂缺）。"""
        self._dump(None)

    def update(self, num_samples: int) -> None:
        """录制进行中：原地刷新已写入文件的 num_samples。"""
        self._dump(num_samples)

    def finalize(self, num_samples: Optional[int] = None) -> None:
        """录制结束：写最终 meta。未显式传 num_samples 时沿用 update 的值。"""
        final = num_samples
        if final is None:
            final = self._meta_kwargs.get("num_samples")
        self._dump(final)

    def __enter__(self) -> "SigMFRecordingSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # 无论录制是否异常退出，都尽力补全最终 meta
        try:
            self.finalize(self._meta_kwargs.get("num_samples"))
        except Exception as exc:  # 防御性：会话退出绝不抛
            logger.warning("SigMF 会话 finalize 失败（已忽略）: %s", exc)
