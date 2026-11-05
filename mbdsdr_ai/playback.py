# SPDX-License-Identifier: MIT
"""
录制回放：IQPlayback（complex64 raw + SigMF meta）+ PlaybackSource
=====================================================================

把 :mod:`mbdsdr_ai.recorder` 录下的真实 IQ 数据按实时流重放出来，作为
无硬件时的**调试数据源**。红线：回放的是**真实录制文件**的逐样本重放，
不是合成假数据冒充接收；中心频率/采样率一律从 SigMF meta 读，不硬编码、
不造假。

对照上游：
- SDR++ recorder 模块回放（sdrpp/misc_modules/recorder/src/main.cpp）：
  录制时写 cf32_le + sidecar；回放端 file_source 按采样率顺序读出，可 loop。
- GQRX 录制功能（gqrx/src/applications/gqrx/mainwindow.cpp 录制动作 +
  gqrx/qtgui/ 保存 wav/raw）：录制文件带采样率/中心频率元数据，
  回放端据此还原频谱中心。
- SigMF 规范：数据 <name>.sigmf-data（cf32_le）+ 元数据 <name>.sigmf-meta(JSON)。

与 sdr_backend.FileIQBackend 的分工：
- FileIQBackend 是 sdr_backend.py 里既有的"整文件载入内存"回放，用自定义
  .json sidecar，面向 sdr_backend 管理器。
- 本文件是**独立、流式、SigMF 原生**的回放器：mmap 按需读（大文件不全量
  进内存），提供 seek(time_s)/tell()/get_duration()/loop/speed 语义，并再包
  一层 :class:`PlaybackSource` 使其与 SDRBackend 接口兼容，可直接替换真实
  后端做 UI 调试。两者并存，互不破坏。
"""
from __future__ import annotations

import json
import logging
import os
import weakref
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

from .error_catalog import user_message


class PlaybackViewsInUseError(RuntimeError):
    """``close()`` 时仍有 ``read_samples`` 发出的零拷贝视图未被释放。

    零拷贝路径把 memmap 映射地址借给调用方；若提前解除映射，这些 ndarray
    就成了悬空指针，下一次访问会直接触发进程级访问冲突（Windows
    0xC0000005，Python 层 try/except 拦不住）。所以这种情况下 ``close()``
    必须显式失败，而不是静默解除映射。处置：先消费/拷贝这些视图，或改用
    ``read_samples(n, copy=True)``，再重试 ``close()``。
    """


# ---------------------------------------------------------------------------
# SigMF meta 解析
# ---------------------------------------------------------------------------
def _resolve_sigmf(data_path: str) -> Dict[str, str]:
    """给定数据文件路径，返回 {data, meta} 路径（与 recorder._sigmf_paths 同约定）。"""
    if data_path.endswith(".sigmf-data"):
        root = data_path[: -len(".sigmf-data")]
    else:
        root = os.path.splitext(data_path)[0]
    return {
        "data": root + ".sigmf-data",
        "meta": root + ".sigmf-meta",
    }


def parse_sigmf_meta(meta_path: str) -> Dict[str, Any]:
    """解析 SigMF .sigmf-meta，抽取回放需要的字段。

    返回 dict:
        sample_rate_hz, center_freq_hz, datetime, time_source, total_samples,
        datatype, raw(原始 dict)
    缺字段时给安全默认值（sample_rate=0, freq=0, time_source=""），不抛异常。

    time_source 取自 captures[0].mbdsdr:time_source（"gnss"|"system"），
    由 mbdsdr_ai.gnss_timestamps 在录制端写入；旧文件无此字段时返回 ""（未知），
    绝不臆断。
    """
    out: Dict[str, Any] = {
        "sample_rate_hz": 0.0,
        "center_freq_hz": 0.0,
        "datetime": "",
        "time_source": "",
        "total_samples": 0,
        "datatype": "cf32_le",
        "raw": {},
        "meta_present": False,   # meta 文件是否存在
        "meta_valid": False,     # meta 是否存在且可解析（缺失≠损坏）
        "meta_error": "",        # 损坏原因；缺失时为空（由调用方按"未知"处理）
    }
    if not meta_path or not os.path.exists(meta_path):
        return out
    out["meta_present"] = True
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("SigMF meta 解析失败 %s: %s", meta_path, e)
        out["meta_error"] = f"meta_corrupt: {e}"
        return out
    out["meta_valid"] = True
    out["raw"] = meta
    g = meta.get("global", {}) or {}
    out["sample_rate_hz"] = float(g.get("core:sample_rate", 0.0) or 0.0)
    out["center_freq_hz"] = float(g.get("core:frequency", 0.0) or 0.0)
    out["datatype"] = str(g.get("core:datatype", "cf32_le"))
    out["total_samples"] = int(g.get("core:total_num_samples", 0) or 0)
    captures = meta.get("captures", []) or []
    if captures:
        out["datetime"] = str(captures[0].get("core:datetime", "") or "")
        out["time_source"] = str(captures[0].get("mbdsdr:time_source", "") or "")
    return out


# ---------------------------------------------------------------------------
# IQ 回放器
# ---------------------------------------------------------------------------
class IQPlayback:
    """从录制的 complex64(cf32_le) raw 文件 + SigMF meta 流式回放 IQ。

    - open(filepath) 后用 read_samples(n) 按块取 complex64，模拟实时流。
    - seek(time_s)/tell()/get_duration() 按时间定位。
    - loop=True 时到 EOF 自动回到开头；speed 为播放速率（存储供实时消费方 pacing）。
    - 大文件用 memmap 按需读，不一次性载入内存。

    Parameters
    ----------
    loop : bool
        到文件末尾是否循环。
    speed : float
        播放速率倍率（>1 加速，<1 减速）。read_samples 本身按块即时返回，
        真实实时 pacing 由消费方据此 sleep；此处保存该参数。
    """

    # 每个 complex64 样本字节数（cf32_le = 2 * float32）
    _BYTES_PER_SAMPLE = 8

    def __init__(self, loop: bool = False, speed: float = 1.0) -> None:
        self.loop = bool(loop)
        self.speed = float(speed) if speed > 0 else 1.0

        self.filepath: Optional[str] = None
        self.meta_path: Optional[str] = None
        self.sample_rate: float = 0.0
        self.center_freq_hz: float = 0.0
        self.datetime: str = ""
        self.time_source: str = ""
        self.meta_raw: Dict[str, Any] = {}

        self._mmap: Optional[np.memmap] = None  # complex64 视图
        self._n_samples: int = 0
        self._cursor: int = 0
        self._views: list = []  # read_samples(copy=False) 发出的视图（weakref.ref）
        self.opened: bool = False
        # ── 文件完整性/可恢复状态（closed/open/eof/failed/deleted）──
        self.state: str = "closed"
        self.last_error: str = ""
        self.error_code: str = ""   # missing/corrupt/truncated/empty/datatype/deleted
        self.meta_valid: bool = False
        self.declared_samples: int = 0

    # ------------------------------------------------------------------
    def open(self, filepath: str) -> bool:
        """打开录制数据文件。自动寻找同名 .sigmf-meta 并解析元数据。

        接受 .sigmf-data / .cf32 / .cfile / .raw / .iq 等 complex64 裸数据路径。
        """
        paths = _resolve_sigmf(filepath)
        data_path = paths["data"]
        meta_path = paths["meta"]
        self._views = []  # 新会话：旧文件遗留的视图由它们自己的引用链保活，不再跟踪
        if not os.path.exists(data_path):
            # 兜底：用户直接传了原始扩展名文件
            if os.path.exists(filepath):
                data_path = filepath
            else:
                return self._fail_open("missing", "找不到数据文件", data_path)

        # 解析 SigMF meta（存在则读真实采样率/中心频率，不造假）
        meta = parse_sigmf_meta(meta_path)
        self.filepath = data_path
        self.meta_path = meta_path if os.path.exists(meta_path) else None
        self.sample_rate = meta["sample_rate_hz"]
        self.center_freq_hz = meta["center_freq_hz"]
        self.datetime = meta["datetime"]
        self.time_source = meta["time_source"]
        self.meta_raw = meta["raw"]
        self.meta_valid = bool(meta["meta_valid"])
        self.declared_samples = int(meta["total_samples"])

        # 非法头：meta 存在但 JSON 坏了，不能按默认值继续（那会造出假时间轴）。
        if meta["meta_present"] and not meta["meta_valid"]:
            return self._fail_open("corrupt", "SigMF meta 损坏", data_path)
        datatype = str(meta.get("datatype", "cf32_le")).lower()
        if datatype not in ("cf32_le", "cf32", "complex64"):
            return self._fail_open(
                "datatype", f"不支持的数据类型 {datatype}（仅 cf32_le）", data_path)

        try:
            size = int(os.path.getsize(data_path))
        except OSError as e:
            return self._fail_open("missing", f"数据文件不可读: {e}", data_path)
        if size == 0:
            return self._fail_open("empty", "数据文件为空", data_path)
        if size % self._BYTES_PER_SAMPLE != 0:
            return self._fail_open(
                "truncated",
                f"数据长度 {size} 字节不是 {self._BYTES_PER_SAMPLE} 的整数倍",
                data_path)
        actual_samples = size // self._BYTES_PER_SAMPLE
        if self.declared_samples > 0 and actual_samples < self.declared_samples:
            return self._fail_open(
                "truncated",
                f"文件已截断：实际 {actual_samples} 样本 < meta 声明 "
                f"{self.declared_samples}", data_path)

        try:
            self._mmap = np.memmap(data_path, dtype=np.complex64, mode="r")
        except (OSError, ValueError) as e:
            return self._fail_open("corrupt", f"mmap 失败: {e}", data_path)

        self._n_samples = int(self._mmap.size)
        # 若 meta 没给采样率，用 0 标记未知（绝不编造一个采样率）
        if self.sample_rate <= 0:
            logger.warning("IQPlayback: SigMF meta 缺 core:sample_rate，"
                           "采样率未知（=0）。文件=%s", data_path)
        self._cursor = 0
        self.opened = True
        self.state = "open"
        self.last_error = ""
        self.error_code = ""
        logger.info("IQPlayback 打开: %s (%d samples, %.3f MS/s, %.3f MHz)",
                    data_path, self._n_samples,
                    self.sample_rate / 1e6, self.center_freq_hz / 1e6)
        return True

    def _fail_open(self, code: str, reason: str, data_path: str) -> bool:
        """打开失败时如实落状态，绝不返回"打开成功但零样本"。"""
        self.opened = False
        self._mmap = None
        self.state = "failed"
        self.error_code = code
        self.last_error = user_message(code, detail=reason, path=data_path)
        logger.error("IQPlayback 打开失败(%s) %s: %s", code, data_path, reason)
        return False

    # ------------------------------------------------------------------
    def _live_views(self) -> list:
        """仍存活的零拷贝视图（weakref 名单里尚未被调用方释放的项）。"""
        self._views = [r for r in self._views if r() is not None]
        return self._views

    def close(self) -> None:
        """关闭回放器并解除 memmap 映射。

        Raises
        --------
        PlaybackViewsInUseError
            仍有 :meth:`read_samples` 发出的零拷贝视图未释放。此时**不解除映射**，
            调用方消费/拷贝完这些视图后可重试 ``close()``。
        """
        live = self._live_views()
        if live:
            raise PlaybackViewsInUseError(
                f"IQPlayback.close(): 还有 {len(live)} 个视图未被使用完"
                "（read_samples 默认零拷贝，返回数组指向 memmap）。"
                "请先消费或拷贝这些视图（或改用 read_samples(n, copy=True)），"
                "再 close()；在此之前映射保持有效。")
        if self._mmap is not None:
            try:
                self._mmap._mmap.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._mmap = None
        self._cursor = 0
        self.opened = False

    def __enter__(self) -> "IQPlayback":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    def read_samples(self, n: int, copy: bool = False) -> np.ndarray:
        """读取接下来 n 个 complex64 样本。

        两条所有权路径（**默认零拷贝**）：

        - ``copy=False``（默认）：返回 **memmap 视图**，零拷贝、不额外占内存，
          但生命周期与回放器绑定——必须在 ``close()`` 之前消费完或丢掉；
          否则 ``close()`` 抛 :class:`PlaybackViewsInUseError`（而不是静默
          解除映射、让调用方在下次访问时吃进程级 0xC0000005）。
        - ``copy=True``：返回独立副本，``close()`` 后可继续持有。代价是每次
          调用多一份 ``n*8`` 字节；若调用方把整段录音逐块累加，峰值内存约为
          文件大小的 2 倍（1.85 GB 的录音再复制一份就可能 OOM，长录音优先
          走「零拷贝 + close 前消费」）。

        其它语义：
        - 未打开返回空数组。
        - 到 EOF：loop=True 回绕；loop=False 返回剩余样本并停在末尾（再读返回空）。
        """
        if not self.opened or self._mmap is None or n <= 0:
            return np.empty(0, dtype=np.complex64)
        # 读到一半被删/被移走：立即暴露 deleted，不继续回放陈旧映射冒充现况。
        if self.filepath and not os.path.exists(self.filepath):
            self.state = "deleted"
            self.error_code = "deleted"
            self.last_error = user_message("deleted")
            self.opened = False
            # 不在此处强制解除映射：外面可能仍有视图，提前 close 会让它们悬空。
            # 交出引用即可，映射随最后一个视图释放而回收。
            self._mmap = None
            return np.empty(0, dtype=np.complex64)
        total = self._n_samples
        if total == 0:
            return np.empty(0, dtype=np.complex64)

        out_parts = []
        remaining = int(n)
        while remaining > 0:
            if self._cursor >= total:
                if self.loop:
                    self._cursor = 0
                else:
                    break
            end = self._cursor + remaining
            if end <= total:
                out_parts.append(np.asarray(self._mmap[self._cursor:end],
                                            dtype=np.complex64))
                used = end - self._cursor
                self._cursor = end
                remaining -= used
            else:
                out_parts.append(np.asarray(self._mmap[self._cursor:total],
                                            dtype=np.complex64))
                used = total - self._cursor
                self._cursor = total
                remaining -= used
                # loop 时下一轮 while 会把 cursor 归 0
        if not out_parts:
            if not self.loop and self._cursor >= total:
                self.state = "eof"
                self.error_code = "eof"
                self.last_error = user_message("eof")
            return np.empty(0, dtype=np.complex64)
        if not self.loop and self._cursor >= total:
            self.state = "eof"
            self.error_code = "eof"
            self.last_error = user_message("eof")
        if copy:
            if len(out_parts) > 1:
                return np.concatenate(out_parts).astype(np.complex64, copy=False)
            return np.array(out_parts[0], dtype=np.complex64, copy=True)
        if len(out_parts) > 1:
            # 跨 EOF 回绕时本来就是 concatenate 出来的独立数组，无需跟踪。
            return np.concatenate(out_parts).astype(np.complex64, copy=False)
        view = out_parts[0]
        self._live_views()
        self._views.append(weakref.ref(view))
        return view

    # ------------------------------------------------------------------
    def seek(self, time_s: float) -> None:
        """按回放时间（秒）定位。超出范围钳位到 [0, duration]。"""
        if not self.opened or self._n_samples == 0:
            return
        idx = int(round(time_s * self.sample_rate)) if self.sample_rate > 0 else 0
        self._cursor = max(0, min(idx, self._n_samples - 1))

    def tell(self) -> float:
        """当前回放位置（秒）。采样率未知时返回 0。"""
        if not self.opened or self.sample_rate <= 0:
            return 0.0
        return self._cursor / self.sample_rate

    def get_duration(self) -> float:
        """录制总时长（秒）。采样率未知时返回 0。"""
        if not self.opened or self.sample_rate <= 0:
            return 0.0
        return self._n_samples / self.sample_rate

    @property
    def n_samples(self) -> int:
        return self._n_samples

    @property
    def eof(self) -> bool:
        """是否已到文件末尾（且未开 loop）。"""
        return self.opened and not self.loop and self._cursor >= self._n_samples


# ---------------------------------------------------------------------------
# PlaybackSource：包装成 SDRBackend 兼容接口，作为 UI 调试数据源
# ---------------------------------------------------------------------------
def transmit_iq_playback(
    filepath: str,
    *,
    backend: Any,
    freq_hz: Optional[float] = None,
    doppler_precomp: Optional[bool] = False,
    arm_manual: bool = False,
    tx_token: Optional[str] = None,
    gain_db: Optional[float] = None,
    bandwidth_hz: Optional[float] = None,
    caps: Any = None,
    regulatory: Any = None,
    cancel_event: Any = None,
    chunk_samples: int = 65536,
    rx_control: Any = None,
    rx_active: bool = False,
    duplex: Optional[str] = None,
) -> Dict[str, Any]:
    """把录制 IQ 文件经 :class:`TxSession` 受控发射（回放发射路径）。

    红线：
      * 人工门、越界防护、发射状态机全部复用 :mod:`mbdsdr_ai.tx_guard`，
        本函数不另写一套；
      * 回放文件缺失/损坏/截断/读到一半被删时如实拒绝或中止，绝不把坏
        文件当成"正常波形"发出去；
      * 未指定频点时使用 SigMF 记录的中心频率；元数据缺失（0）则拒绝，
        不假设一个频点；
      * **回放已知录音默认严格按标称频点发射**（``doppler_precomp=False``）：
        自动预偏会把录音本来要表达的频率改掉。发波频点仍走公共
        :func:`mbdsdr_ai.sdr_tools.resolve_tx_doppler` 仲裁，并把读数随
        结果返回，绝不默默改频。
    """
    from .tx_guard import TxSession, policy_from_env

    pb = IQPlayback(loop=False)
    if not pb.open(filepath):
        return {
            "state": "rejected",
            "reason_code": (f"playback_{pb.error_code}"
                            if pb.error_code else "playback_failed"),
            "reason": pb.last_error or user_message(
                "missing_error_detail", context="回放打开"),
            "sent_samples": 0,
            "playback": {"path": filepath, "state": pb.state,
                         "error_code": pb.error_code},
        }
    center = float(freq_hz) if freq_hz and float(freq_hz) > 0 else pb.center_freq_hz
    if not center or center <= 0:
        pb.close()
        return {
            "state": "rejected",
            "reason_code": "bad_parameter",
            "reason": user_message(
                "bad_parameter",
                detail="未指定发射频点，且 SigMF 元数据没有中心频率——"
                       "不假设频点"),
            "sent_samples": 0,
            "playback": {"path": filepath, "state": "closed",
                         "meta_valid": pb.meta_valid},
        }

    def _blocks() -> Any:
        try:
            while True:
                # 安全路径：块要穿过生成器交给 TxSession，其生命周期不受本函数
                # 控制（最后一块可能在 close 后仍被持有），必须用副本。
                block = pb.read_samples(chunk_samples, copy=True)
                if block is None or int(block.size) == 0:
                    break
                yield block
        finally:
            pb.close()

    # 回放已知录音：默认 strict nominal；仍走公共 TX 仲裁（不另写一套），
    # 读数随结果返回，调用方/UI 能看见「发的是什么频率」。
    from .sdr_tools import resolve_tx_doppler
    tx_freq, doppler_note = resolve_tx_doppler(center, doppler_precomp)

    duration = pb.get_duration() if pb.sample_rate > 0 else None
    session = TxSession(backend, cancel_event=cancel_event)
    result = session.transmit(
        _blocks(),
        freq_hz=tx_freq,
        arm_manual=arm_manual,
        tx_token=tx_token,
        gain_db=gain_db,
        bandwidth_hz=bandwidth_hz,
        caps=caps,
        regulatory=regulatory or policy_from_env(),
        duration_s=duration,
        chunk_samples=chunk_samples,
        rx_control=rx_control or backend,
        rx_active=rx_active,
        duplex=duplex,
    )
    # 读到一半文件被删/损坏：read_samples 会返回空并落 deleted/failed，
    # 此时绝不能把"样本提前结束"报告成正常完成。
    if pb.state in ("deleted", "failed"):
        result["state"] = "aborted"
        result["reason_code"] = ("tx_source_deleted" if pb.state == "deleted"
                                 else "tx_source_failed")
        result["reason"] = pb.last_error or user_message(
            "missing_error_detail", context="回放源失效")
    result["playback"] = {
        "path": filepath,
        "state": pb.state,
        "error_code": pb.error_code,
        "sample_rate_hz": pb.sample_rate,
        "meta_valid": pb.meta_valid,
        "total_samples": pb._n_samples,
    }
    result["doppler"] = doppler_note
    return result



class PlaybackSource:
    """把 :class:`IQPlayback` 包装成与 SDRBackend 兼容的只读源。

    实现 SDRBackend 的最小调试接口子集：
        connect()/disconnect()/read_samples(n)/
        set_frequency(hz)/get_frequency()/
        set_sample_rate(hz)/get_sample_rate()/
        set_gain/get_gain/set_agc/get_status()

    红线：
    - 这是**真实录制文件的重放**，不是合成数据。中心频率/采样率来自 SigMF meta。
    - set_frequency 只更新上报的元数据（回放文件不能真的变频），并在
      status.error 里标注"回放源：频率为录制时记录值"，绝不冒充实时可调硬件。
    - get_status() 返回 dict（与 SDRBackend.get_status 的 dict 消费方兼容）。
    """

    def __init__(self, filepath: str, loop: bool = True, speed: float = 1.0):
        self._pb = IQPlayback(loop=loop, speed=speed)
        self._filepath = filepath
        self._connected = False
        self._freq: float = 0.0
        self._rate: float = 0.0
        self._gain: float = 0.0
        self._agc: bool = True
        self._samples_read: int = 0
        self._last_error: str = ""
        self._error_code: str = ""

    # ------------------------------------------------------------------
    def connect(self) -> bool:
        if self._pb.open(self._filepath):
            self._connected = True
            self._freq = self._pb.center_freq_hz
            self._rate = self._pb.sample_rate
            self._last_error = ""
            self._error_code = ""
            return True
        self._connected = False
        self._error_code = self._pb.error_code or "open_failed"
        self._last_error = (self._pb.last_error
                            or user_message("open_failed",
                                            detail=f"状态 {self._pb.state}"))
        return False

    def disconnect(self) -> None:
        self._pb.close()
        self._connected = False

    # ------------------------------------------------------------------
    def read_samples(self, n: int) -> Optional[np.ndarray]:
        if not self._connected:
            return None
        # 安全路径：返回值直接交给消费方（UI/流水线），其生命周期可能超出
        # disconnect()，因此这里取副本，避免 close 后悬空。
        out = self._pb.read_samples(n, copy=True)
        if out.size == 0:
            self._error_code = self._pb.error_code or "no_data"
            self._last_error = (self._pb.last_error
                                or user_message(
                                    "no_data", detail=f"状态 {self._pb.state}"))
            if self._pb.state == "deleted":
                self._connected = False
            return None  # EOF / 文件被删 / 未打开
        self._samples_read += int(out.size)
        return out

    # ------------------------------------------------------------------
    def set_frequency(self, freq_hz: float) -> bool:
        if not self._connected:
            return False
        # 回放文件不能真的变频；只记录上报值（诚实标注）。
        self._freq = float(freq_hz)
        return True

    def get_frequency(self) -> float:
        return self._freq

    def set_sample_rate(self, rate_hz: float) -> bool:
        """回放源采样率由录制文件决定，不允许改写（改写=时间轴失真）。"""
        if not self._connected:
            return False
        actual = float(self._pb.sample_rate)
        if actual <= 0:
            self._error_code = "rate_unknown"
            self._last_error = user_message("rate_unknown")
            return False
        if abs(float(rate_hz) - actual) > 1.0:
            self._error_code = "rate_mismatch"
            self._last_error = user_message(
                "rate_mismatch", actual=f"{actual:g}",
                requested=f"{float(rate_hz):g}")
            return False
        self._rate = actual
        return True

    def get_sample_rate(self) -> float:
        return self._rate

    def set_gain(self, gain_db: float) -> bool:
        if not self._connected:
            return False
        self._gain = float(gain_db)
        return True

    def get_gain(self) -> float:
        return self._gain

    def set_agc(self, enabled: bool) -> bool:
        self._agc = bool(enabled)
        return True

    # ------------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            # 明确标注：这是录制回放源（真实数据重放），不是实时硬件
            "device": "IQRecordingPlayback",
            "device_label": "录制回放源（真实录制文件重放）",
            "frequency_hz": self._freq,
            "sample_rate_hz": self._rate,
            "gain_db": self._gain,
            "agc_enabled": self._agc,
            "samples_read": self._samples_read,
            "playback_cursor_s": self._pb.tell(),
            "duration_s": self._pb.get_duration(),
            "loop": self._pb.loop,
            "recording_datetime": self._pb.datetime,
            "recording_time_source": self._pb.time_source,
            "playback_state": self._pb.state,
            "error_code": self._error_code,
            "meta_valid": self._pb.meta_valid,
            "file_error": self._pb.last_error,
            "error": (self._last_error
                      if self._last_error else
                      ("" if self._connected else "未连接")),
        }
