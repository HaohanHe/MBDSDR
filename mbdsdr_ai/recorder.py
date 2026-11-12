# SPDX-License-Identifier: MIT
"""
录制器：IQ（complex64 raw + SigMF sidecar）+ 音频（WAV 48k/16bit）
================================================================

流式录制：边收边写，关闭时落 sidecar 元数据。对照上游：
- sdrpp/misc_modules/recorder/src/main.cpp:166-210
    start() 时开文件、配置采样率/位深/声道；handler_sink 边收边写；
    stop() 关文件。
- SigMF 规范 (https://en.wikipedia.org/wiki/Signal_Metadata_Format)：
    - 数据文件：<name>.sigmf-data（裸 complex64 = cf32_le）
    - 元数据文件：<name>.sigmf-meta（JSON）
        {
          "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": 4800000.0,
            "core:frequency": 100000000.0,
            "core:author": "...",
            "core:description": "..."
          },
          "captures": [
            {"core:sample_start": 0, "core:datetime": "2026-09-26T...Z"}
          ],
          "annotations": []
        }

与 mbdsdr_ai/baseband_io.py 的关系：
- baseband_io.save_iq() 是一次性（整段数组存盘）+ 自定义字段 sidecar，
  供 LLM/工具调用。
- recorder.py 是**流式**录制器（recorder 类），边收边写、可关断时落盘，
  且 sidecar 严格 SigMF 兼容（可被 siglib/SigDigger/Inspectrum 直接读）。
两者并存，互不破坏。
"""
from __future__ import annotations

import errno
import json
import logging
import os
import shutil
import time
import wave as _wave
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np

from .corrupt_evidence import REASON_SUFFIX, write_reason

logger = logging.getLogger(__name__)

from .error_catalog import user_message


# ---------------------------------------------------------------------------
# 文件名/路径约定
# ---------------------------------------------------------------------------
def _classify_os_error(e: OSError) -> str:
    """把磁盘/权限错误归类，供上层显示可操作原因（不吞成"写失败"）。"""
    if getattr(e, "errno", None) == errno.ENOSPC:
        return "disk_full"
    if getattr(e, "errno", None) in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "not_writable"
    return "io"


def _sigmf_paths(data_path: str) -> Dict[str, str]:
    """给定数据文件路径，返回 {data, meta} 两个 SigMF 文件路径。

    - data_path 以 .sigmf-data 结尾 → 直接用，meta 换扩展名 .sigmf-meta
    - data_path 以 .cf32/.iq/.raw 结尾 → 替换为 .sigmf-data / .sigmf-meta
    - 其他（无扩展名）→ 加 .sigmf-data / .sigmf-meta
    """
    if data_path.endswith(".sigmf-data"):
        root = data_path[: -len(".sigmf-data")]
    else:
        root = os.path.splitext(data_path)[0]
    return {
        "data": root + ".sigmf-data",
        "meta": root + ".sigmf-meta",
    }


# ---------------------------------------------------------------------------
# IQ 录制器（complex64 raw + SigMF sidecar）
# ---------------------------------------------------------------------------
class IQRecorder:
    """流式 IQ 录制：complex64 裸数据 + SigMF meta。

    Usage::

        rec = IQRecorder("/tmp/iq", sample_rate=2.4e6,
                          center_freq_hz=100e6, gain_db=20.0,
                          device="RTL-SDR", driver="rtlsdr")
        rec.open()
        for iq_block in rx_stream:        # 每块 complex64 (N,)
            rec.write(iq_block)
        rec.close()                        # 写 SigMF meta

    红线：
    - 真实写文件，不造假。open() 失败立即抛错/标 failed。
    - 不硬编码增益范围；gain_db 只是记进 sidecar 的数值。
    - 录制格式可被 SigMF 兼容工具读回（cf32_le）。
    """

    def __init__(self, data_path: str,
                 sample_rate: float,
                 center_freq_hz: float = 0.0,
                 gain_db: float = 0.0,
                 device: str = "",
                 driver: str = "",
                 author: str = "MBDSDR",
                 description: str = "",
                 overwrite: bool = False):
        paths = _sigmf_paths(data_path)
        self.data_path = paths["data"]
        self.meta_path = paths["meta"]
        self.sample_rate = float(sample_rate)
        self.center_freq_hz = float(center_freq_hz)
        self.gain_db = float(gain_db)
        self.device = str(device)
        self.driver = str(driver)
        self.author = str(author)
        self.description = str(description)
        # 录制是证据，不是缓存：默认绝不覆盖上一段录音。主窗口工具栏有
        # `_unique_record_path` 防呆，但 MCP/CLI 走的 controller.start_recording
        # 直接把调用方给的路径传进来，同名会把上一段证据静默抹掉。
        # 只有显式 overwrite=True 才允许，且 meta 与 data 一并判断。
        self.overwrite = bool(overwrite)

        self._fp = None            # 数据文件句柄
        self._n_samples: int = 0   # 已写 complex 样本数
        self._start_time: Optional[str] = None
        self.closed = True
        # 写入状态：closed/recording/failed；失败原因与错误码如实暴露
        self.state: str = "closed"
        self.last_error: str = ""
        self.error_code: str = ""

    # ------------------------------------------------------------------
    def open(self) -> bool:
        """打开数据文件准备写。已打开则幂等返回 True。"""
        if self._fp is not None:
            return True
        if not self.overwrite:
            existing = [p for p in (self.data_path, self.meta_path)
                        if os.path.exists(p)]
            if existing:
                # 不是 OSError，但同样是"打不开"：给出可操作的人话，
                # 不借用 io 码去假装是系统错误再让上层显示"写入失败"。
                self._fp = None
                self.state = "failed"
                self.error_code = "already_exists"
                self.last_error = (
                    "同名录制已存在，拒绝覆盖（" + "、".join(
                        os.path.basename(p) for p in existing)
                    + "）；请换一个路径，或显式指定 overwrite=True")
                logger.error("IQRecorder 拒绝覆盖: %s", existing)
                return False
        try:
            os.makedirs(os.path.dirname(self.data_path) or ".", exist_ok=True)
            self._fp = open(self.data_path, "wb")
        except OSError as e:
            logger.error("IQRecorder 打开 %s 失败: %s", self.data_path, e)
            self._fp = None
            self.state = "failed"
            self.error_code = _classify_os_error(e)
            self.last_error = user_message(self.error_code, detail=str(e))
            return False
        self._start_time = datetime.now(timezone.utc).isoformat()
        self._n_samples = 0
        self.closed = False
        self.state = "recording"
        self.last_error = ""
        self.error_code = ""
        logger.info("IQRecorder 开始录制: %s (%.3f MS/s, %.3f MHz)",
                    self.data_path, self.sample_rate / 1e6,
                    self.center_freq_hz / 1e6)
        return True

    # ------------------------------------------------------------------
    def write(self, iq: np.ndarray) -> int:
        """写入一块 complex IQ。返回写入的 complex 样本数。

        自动把任意 complex 数组归一为 complex64 裸交织 I/Q 写出。
        未 open() 时返回 0（不崩）。
        """
        if self._fp is None or self.state == "failed":
            return 0
        arr = np.asarray(iq)
        if not np.iscomplexobj(arr):
            # 实数输入：当成已交织 [re,im,re,im,...]，重塑成 complex
            flat = arr.astype(np.float32).ravel()
            if flat.size % 2 != 0:
                return 0
            arr = flat[0::2] + 1j * flat[1::2]
        arr64 = arr.astype(np.complex64, copy=False)
        blob = arr64.tobytes()
        try:
            written = self._fp.write(blob)
            if written != len(blob):
                raise OSError(errno.ENOSPC, "写入字节数不足（疑似磁盘写满）")
            self._n_samples += int(arr64.size)
            return int(arr64.size)
        except OSError as e:
            # 失败即停：truncate 回最后一个完整样本边界，保证文件里没有
            # "半截样本"冒充正常数据；后续 write 一律返回 0。
            self.state = "failed"
            self.error_code = _classify_os_error(e)
            self.last_error = user_message(self.error_code, detail=str(e))
            logger.error("IQRecorder 写失败: %s（已截断到 %d 样本）",
                         e, self._n_samples)
            try:
                self._fp.flush()
                os.ftruncate(self._fp.fileno(),
                             self._n_samples * 8)
            except OSError:
                pass
            return 0

    # ------------------------------------------------------------------
    def close(self) -> Optional[str]:
        """关闭数据文件并写 SigMF meta。返回 meta 路径（失败 None）。"""
        if self._fp is not None:
            try:
                self._fp.flush()
                os.fsync(self._fp.fileno())
            except OSError as e:
                self.state = "failed"
                self.error_code = _classify_os_error(e)
                self.last_error = user_message(self.error_code, detail=str(e))
                logger.error("IQRecorder fsync 失败: %s", e)
            try:
                self._fp.close()
            except OSError as e:
                self.state = "failed"
                self.error_code = _classify_os_error(e)
                self.last_error = user_message(self.error_code, detail=str(e))
            self._fp = None
        if self.closed:
            return None
        self.closed = True

        # ── 构造 SigMF meta（严格按规范）──
        meta: Dict[str, Any] = {
            "global": {
                "core:datatype": "cf32_le",     # complex float32 little-endian
                "core:sample_rate": self.sample_rate,
                "core:frequency": self.center_freq_hz,
                "core:author": self.author,
                "core:description": self.description,
                "core:total_num_samples": self._n_samples,
                "mbdsdr:gain_db": self.gain_db,
                "mbdsdr:device": self.device,
                "mbdsdr:driver": self.driver,
                # 只有无写入错误时才算完整；断电/磁盘满留下的文件不会冒充完整
                "mbdsdr:complete": self.state != "failed",
                "mbdsdr:error": self.last_error,
            },
            "captures": [
                {
                    "core:sample_start": 0,
                    "core:datetime": self._start_time or "",
                }
            ],
            "annotations": [],
        }
        try:
            tmp = self.meta_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.meta_path)
            logger.info("IQRecorder 结束: %d samples, meta=%s",
                        self._n_samples, self.meta_path)
            if self.state != "failed":
                self.state = "closed"
            return self.meta_path
        except OSError as e:
            self.state = "failed"
            self.error_code = _classify_os_error(e)
            self.last_error = user_message(self.error_code, detail=str(e))
            logger.error("IQRecorder 写 meta 失败: %s", e)
            return None

    # ------------------------------------------------------------------
    def __enter__(self) -> "IQRecorder":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def n_samples(self) -> int:
        return self._n_samples

    @property
    def duration_s(self) -> float:
        return self._n_samples / max(self.sample_rate, 1e-9)


def inspect_recording(data_path: str) -> Dict[str, Any]:
    """检查录制文件完整性，返回可判定的 integrity 状态（不做修复）。"""
    paths = _sigmf_paths(data_path)
    data, meta = paths["data"], paths["meta"]
    out: Dict[str, Any] = {
        "data_path": data, "meta_path": meta,
        "data_exists": os.path.exists(data),
        "meta_exists": os.path.exists(meta),
        "integrity": "missing", "error": "", "reason": "",
        "samples_on_disk": 0, "declared_samples": 0,
        "complete_flag": None,
    }
    if not out["data_exists"]:
        out["reason"] = "数据文件不存在"
        return out
    try:
        size = os.path.getsize(data)
    except OSError as e:
        out["integrity"] = "io_error"
        out["reason"] = str(e)
        return out
    if size == 0:
        out["integrity"] = "empty"
        out["reason"] = "数据文件为空"
        return out
    if size % 8 != 0:
        out["integrity"] = "corrupt"
        out["reason"] = f"{size} 字节不是 8 的整数倍（存在半截样本）"
        return out
    out["samples_on_disk"] = size // 8
    if not out["meta_exists"]:
        out["integrity"] = "missing-meta"
        out["reason"] = "只有数据文件（录制中断/断电），meta 缺失"
        return out
    try:
        with open(meta, "r", encoding="utf-8") as f:
            meta_d = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        out["integrity"] = "meta-corrupt"
        out["reason"] = str(e)
        return out
    g = meta_d.get("global", {}) or {}
    out["declared_samples"] = int(g.get("core:total_num_samples", 0) or 0)
    out["complete_flag"] = g.get("mbdsdr:complete")
    if out["declared_samples"] > out["samples_on_disk"]:
        out["integrity"] = "truncated"
        out["reason"] = (f"声明 {out['declared_samples']} 样本，"
                         f"实际 {out['samples_on_disk']}")
        return out
    if out["complete_flag"] is False:
        out["integrity"] = "incomplete"
        out["reason"] = str(g.get("mbdsdr:error", "") or "录制未完整结束")
        return out
    out["integrity"] = "ok"
    return out


class MetaRecoveryAborted(RuntimeError):
    """恢复中止：无法在覆盖前安全留证（或 meta 存在但不可读）。

    恢复流程会重写 ``.sigmf-meta``；如果损坏原件备份不下来还继续写，
    就等于用“恢复”这个动作销毁唯一的原始元数据。此时必须中止并让调用方
    把原因显示给用户，而不是返回一个会被当成“数据字节不完整”的 None。
    """


def _same_content_backup(path: str) -> Optional[str]:
    """同目录下是否已有一份与当前文件字节相同的 ``*.corrupt-*`` 备份。"""
    directory = os.path.dirname(os.path.abspath(path))
    prefix = os.path.basename(path) + ".corrupt-"
    try:
        names = sorted(
            (n for n in os.listdir(directory)
             if n.startswith(prefix) and not n.endswith(REASON_SUFFIX)),
            reverse=True)
        if not names:
            return None
        candidate = os.path.join(directory, names[0])
        if os.path.getsize(candidate) != os.path.getsize(path):
            return None
        with open(candidate, "rb") as a, open(path, "rb") as b:
            return candidate if a.read() == b.read() else None
    except OSError:
        return None


def _backup_corrupt_meta(path: str, reason: str) -> Optional[str]:
    """把损坏的 meta 复制成 ``*.corrupt-<时间戳>`` 并写理由。

    复制而非改名：原文件留到原子替换那一步，用户中途取消也不会少一个文件。
    返回备份路径；备份失败返回 None（调用方必须据此中止恢复）。
    """
    dst = _same_content_backup(path)
    if dst is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = f"{path}.corrupt-{stamp}"
        n = 0
        while os.path.exists(dst):
            n += 1
            dst = f"{path}.corrupt-{stamp}-{n}"
        try:
            shutil.copy2(path, dst)
        except OSError:
            return None
    write_reason(dst, reason, logger)
    return dst


def recover_recording_meta(data_path: str, sample_rate_hz: float = 0.0,
                           center_freq_hz: float = 0.0) -> Optional[str]:
    """为录制中断留下的孤立数据文件补写诚实的 meta（complete=false）。

    只在数据字节完整（8 的整数倍、非空）时执行；无法判定则返回 None。
    采样率未知时写 0 并在 meta 中标注 unknown，绝不编造一个速率。

    若已有 meta 但损坏：先复制一份 ``*.corrupt-*``（理由里注明“恢复流程
    触发，不是用户删除”）再重建；**备份失败则抛**
    :class:`MetaRecoveryAborted`，绝不“没留证也照样重建”。
    """
    info = inspect_recording(data_path)
    if info["integrity"] not in ("missing-meta", "meta-corrupt"):
        return None
    paths = _sigmf_paths(data_path)
    if info["integrity"] == "meta-corrupt":
        meta_path = paths["meta"]
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                json.load(f)
        except FileNotFoundError:
            pass                      # 竞态：判损坏后刚被删，等同没有 meta
        except OSError as exc:
            # 三分法：OSError 不是“内容损坏”，读不了就不写、不改名。
            raise MetaRecoveryAborted(
                f"恢复中止：meta 存在但不可读（{type(exc).__name__}: {exc}）："
                f"{meta_path}；不覆盖无法读取的文件") from exc
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            reason = (f"{meta_path}：恢复流程触发（不是用户删除）："
                      f"inspect 判定 meta 损坏（{type(exc).__name__}: {exc}）；"
                      "重建前已复制原件留证")
            backup = _backup_corrupt_meta(meta_path, reason)
            if backup is None:
                raise MetaRecoveryAborted(
                    f"恢复中止：损坏 meta 备份失败（{meta_path}）；"
                    "不重建，以免用恢复动作销毁唯一原始元数据") from exc
            logger.warning("恢复流程：损坏 meta 已备份到 %s，继续重建", backup)
    n = int(info["samples_on_disk"])
    meta: Dict[str, Any] = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": float(sample_rate_hz),
            "core:frequency": float(center_freq_hz),
            "core:author": "MBDSDR",
            "core:description": "recovered from interrupted recording",
            "core:total_num_samples": n,
            "mbdsdr:complete": False,
            "mbdsdr:recovered": True,
            "mbdsdr:recovered_from": "orphan-data",
            "mbdsdr:sample_rate_unknown": bool(sample_rate_hz <= 0),
            "mbdsdr:error": info.get("reason", ""),
        },
        "captures": [{"core:sample_start": 0, "core:datetime": ""}],
        "annotations": [],
    }
    try:
        tmp = paths["meta"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp, paths["meta"])
        return paths["meta"]
    except OSError as e:
        logger.error("recover_recording_meta 写 meta 失败: %s", e)
        return None


# ---------------------------------------------------------------------------
# 音频录制器（WAV, 48kHz, 16bit PCM）
# ---------------------------------------------------------------------------
class AudioRecorder:
    """流式音频录制：WAV PCM（单声道或立体声）。

    对照 sdrpp/misc_modules/recorder/src/main.cpp:178-181
        writer.setFormat(WAV); writer.setSampleType(Int16);
        writer.setSamplerate(samplerate);

    ``bit_depth`` 对齐 sdrpp recorder 的格式档位（8/16/24/32bit PCM；
    2026-10-14 之前本类钉死 16bit——SDR++ 审计缺口，本批补齐）：
    8=uint8 偏移 128、16=int16、24=int24（3 字节小端）、32=int32。
    量化一律 clip 不自动缩放（电平由 AGC/手动增益负责），32bit 输出
    为定点 PCM（非 float WAV，文件头清晰可读、下游工具零猜测）。

    Usage::

        arec = AudioRecorder("/tmp/voice.wav", sample_rate=48000,
                             bit_depth=24)
        arec.open()
        for audio_block in demod_stream:    # float32 (N,) 或 (N,2)
            arec.write(audio_block)
        arec.close()
    """

    #: 支持的位宽（bit_depth -> wave sampwidth 字节数）
    _BIT_DEPTHS = {8: 1, 16: 2, 24: 3, 32: 4}

    def __init__(self, path: str, sample_rate: int = 48000,
                 channels: int = 1, bit_depth: int = 16,
                 overwrite: bool = False):
        if bit_depth not in self._BIT_DEPTHS:
            raise ValueError(
                f"不支持的位宽 {bit_depth}，可用：{sorted(self._BIT_DEPTHS)}")
        self.path = str(path)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.bit_depth = int(bit_depth)
        # 与 IQRecorder 同口径：录音是证据，默认不覆盖已有 wav。
        self.overwrite = bool(overwrite)
        self._wf = None        # wave.Wave_write 句柄
        self._n_frames: int = 0
        self.closed = True
        self.state: str = "closed"   # closed/recording/failed
        self.last_error: str = ""
        self.error_code: str = ""

    def open(self) -> bool:
        if self._wf is not None:
            return True
        if not self.overwrite and os.path.exists(self.path):
            self.state = "failed"
            self.error_code = "already_exists"
            self.last_error = (
                f"同名录音已存在，拒绝覆盖（{os.path.basename(self.path)}）；"
                "请换一个路径，或显式指定 overwrite=True")
            logger.error("AudioRecorder 拒绝覆盖: %s", self.path)
            return False
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._wf = _wave.open(self.path, "wb")
            self._wf.setnchannels(self.channels)
            self._wf.setsampwidth(self._BIT_DEPTHS[self.bit_depth])
            self._wf.setframerate(self.sample_rate)
        except (OSError, _wave.Error) as e:
            logger.error("AudioRecorder 打开 %s 失败: %s", self.path, e)
            self._wf = None
            self.state = "failed"
            self.error_code = (_classify_os_error(e) if isinstance(e, OSError)
                               else "wav_error")
            self.last_error = user_message(self.error_code, detail=str(e))
            return False
        self._n_frames = 0
        self.closed = False
        self.state = "recording"
        self.last_error = ""
        self.error_code = ""
        return True

    def write(self, audio: np.ndarray) -> int:
        """写入一块 float32 音频。按 bit_depth 量化（clip 不自动缩放）。

        返回写入的帧数（单声道样本数；立体声按帧计）。
        """
        if self._wf is None:
            return 0
        x = np.asarray(audio, dtype=np.float32)
        if x.size == 0:
            return 0
        # 归一化到满幅（不自动缩放，clip 即可——录音电平由 AGC/手动增益控制）
        clipped = np.clip(x, -1.0, 1.0)
        if self.bit_depth == 8:
            # 8bit PCM 为无符号偏移 128（WAV 标准）
            pcm = ((clipped * 127.0) + 128.0).astype(np.uint8)
        elif self.bit_depth == 16:
            pcm = (clipped * 32767.0).astype(np.int16)
        elif self.bit_depth == 24:
            i24 = np.round(clipped * 8388607.0).astype(np.int32)
            pcm = self._to_int24(i24)
        else:  # 32bit 定点 PCM
            # float32 尾数只有 24 bit：2^31−1 在 float32 里被舍成 2^31，
            # +1.0 满幅样本再 cast 到 int32 直接溢出成 −2^31（正满幅变
            # 最小负数 = 爆音，且 numpy 会发 invalid value 警告）。
            # 升到 float64 再乘，并显式夹进 int32 定义域。
            pcm = np.clip(clipped.astype(np.float64) * 2147483647.0,
                          np.iinfo(np.int32).min,
                          np.iinfo(np.int32).max).astype(np.int32)
        # wave.writeframes 要交错 bytes
        try:
            self._wf.writeframes(pcm.tobytes())
            # 帧数：立体声时 pcm 长度 = frames*channels
            frames = pcm.size // self.channels
            self._n_frames += int(frames)
            return int(frames)
        except (OSError, _wave.Error) as e:
            logger.error("AudioRecorder 写失败: %s", e)
            self.state = "failed"
            self.error_code = (_classify_os_error(e) if isinstance(e, OSError)
                               else "wav_error")
            self.last_error = user_message(self.error_code, detail=str(e))
            return 0

    @staticmethod
    def _to_int24(i24: np.ndarray) -> np.ndarray:
        """int32 满幅值 -> 3 字节小端 PCM24 字节流（无 numpy view 技巧）。"""
        out = np.empty(i24.size * 3, dtype=np.uint8)
        v = i24.astype("<i4")
        out[0::3] = (v & 0xFF).astype(np.uint8)
        out[1::3] = ((v >> 8) & 0xFF).astype(np.uint8)
        out[2::3] = ((v >> 16) & 0xFF).astype(np.uint8)
        return out

    def close(self) -> None:
        if self._wf is not None:
            try:
                self._wf.close()
            except (OSError, _wave.Error) as e:
                self.state = "failed"
                self.error_code = "wav_error"
                self.last_error = user_message(self.error_code, detail=str(e))
            self._wf = None
        self.closed = True
        if self.state != "failed":
            self.state = "closed"

    def __enter__(self) -> "AudioRecorder":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def n_frames(self) -> int:
        return self._n_frames

    @property
    def duration_s(self) -> float:
        return self._n_frames / max(self.sample_rate, 1e-9)
