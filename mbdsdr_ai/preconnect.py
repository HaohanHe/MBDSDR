# SPDX-License-Identifier: MIT
"""断连时的「下次怎么连」预配置（W44 行为组，FZ-04 允许的纯行为逻辑）。

四项：设备（``device_id``）、采样率（``sample_rate_hz``）、增益（``gain_db``）、
重连策略（``reconnect.attempts`` / ``reconnect.interval_s``）。

纪律：
- **无配置时 connect 行为与历史一致**：不指定设备、不重试、不额外下发参数；
- **重试有硬上限**（``MAX_ATTEMPTS`` / ``MAX_INTERVAL_S``）：任何写入/读取都
  被钳位并如实回报，不存在「无限静默重试」；
- 坏配置只影响本键：``ConfigStore`` 已做损坏备份与未知键保留，本模块不做
  整份写回，也不把非法字段猜测成默认值——非法即忽略，并把原因交回调用方。
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: config.json 里本配置的键（唯一写入口是 ConfigStore.update 的锁内合并）。
KEY = "sdr_preconnect"

#: 默认 1 次 = 不重试 = 历史行为。上限是硬约束，不是建议值。
DEFAULT_ATTEMPTS = 1
MAX_ATTEMPTS = 3
DEFAULT_INTERVAL_S = 1.0
MAX_INTERVAL_S = 5.0

#: 允许写入的字段（未知字段直接拒绝，不静默丢弃成"看起来保存成功"）。
WRITABLE = ("device_id", "sample_rate_hz", "gain_db", "attempts", "interval_s")


def _read_store(store) -> Tuple[Dict[str, Any], List[str]]:
    """读配置字典；配置不可读/疑似损坏时返回证据行，绝不抛给调用方。"""
    if store is None:
        return {}, ["配置存储不可用（ConfigStore 构造失败），预配置按未配置处理"]
    try:
        data = store.read() or {}
    except Exception as e:  # noqa: BLE001 - 读失败不能阻断连接
        return {}, [f"读取配置失败（{type(e).__name__}: {e}），预配置按未配置处理"]
    if not data:
        # ConfigStore.read() 对损坏文件会先备份并要求日志，但这里拿到的是空
        # 字典，无法区分「文件缺失」「合法空对象」与「文件坏了」。文件存在
        # 且确实解析不出来时，必须让用户看见，不能装作没配过。
        import json
        import os
        path = getattr(store, "path", "")
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    json.load(f)
            except (OSError, ValueError, UnicodeDecodeError):
                return {}, [f"配置文件 {path} 存在但读不出内容（可能已损坏，"
                            f"已备份为 *.corrupt-<时间戳>），预配置按未配置处理"]
    return data, []


def load(store) -> Tuple[Dict[str, Any], List[str]]:
    """读取并校验预配置。

    返回 ``(profile, issues)``：``issues`` 非空表示配置损坏或字段非法，
    **调用方必须把它展示给用户**（本函数只负责如实报告，不静默吞掉）。
    """
    data, issues = _read_store(store)
    raw = data.get(KEY)
    if raw is None:
        return {}, issues
    if not isinstance(raw, dict):
        issues.append(f"预配置 {KEY} 格式非法（{type(raw).__name__}），已忽略；"
                      f"原文件未被改写")
        return {}, issues

    profile: Dict[str, Any] = {}
    bad: List[str] = []

    dev = raw.get("device_id")
    if isinstance(dev, str) and dev.strip():
        profile["device_id"] = dev.strip()
    elif dev not in (None, ""):
        bad.append("device_id（需非空字符串）")

    sr = raw.get("sample_rate_hz")
    if sr is not None:
        try:
            v = float(sr)
        except (TypeError, ValueError):
            bad.append("sample_rate_hz（需正数）")
        else:
            if math.isfinite(v) and v > 0:
                profile["sample_rate_hz"] = v
            else:
                bad.append("sample_rate_hz（需正数）")

    gain = raw.get("gain_db")
    if gain is not None:
        try:
            g = float(gain)
        except (TypeError, ValueError):
            bad.append("gain_db（需非负数）")
        else:
            if math.isfinite(g) and g >= 0:
                profile["gain_db"] = g
            else:
                bad.append("gain_db（需非负数）")

    rec = raw.get("reconnect")
    if rec is not None:
        if not isinstance(rec, dict):
            bad.append("reconnect（需对象）")
        else:
            policy_rec: Dict[str, Any] = {}
            a = rec.get("attempts")
            if a is not None:
                try:
                    n = int(a)
                except (TypeError, ValueError):
                    bad.append("reconnect.attempts（需整数）")
                else:
                    if n < 1:
                        bad.append("reconnect.attempts（至少 1 次）")
                    elif n > MAX_ATTEMPTS:
                        issues.append(
                            f"预配置重试次数 {n} 超过上限 {MAX_ATTEMPTS}，"
                            f"按 {MAX_ATTEMPTS} 次执行")
                        policy_rec["attempts"] = MAX_ATTEMPTS
                    else:
                        policy_rec["attempts"] = n
            iv = rec.get("interval_s")
            if iv is not None:
                try:
                    f = float(iv)
                except (TypeError, ValueError):
                    bad.append("reconnect.interval_s（需非负数秒）")
                else:
                    if not math.isfinite(f) or f < 0:
                        bad.append("reconnect.interval_s（需非负数秒）")
                    elif f > MAX_INTERVAL_S:
                        issues.append(
                            f"预配置重试间隔 {f:g}s 超过上限 {MAX_INTERVAL_S:g}s，"
                            f"按 {MAX_INTERVAL_S:g}s 执行")
                        policy_rec["interval_s"] = MAX_INTERVAL_S
                    else:
                        policy_rec["interval_s"] = f
            if policy_rec:
                profile["reconnect"] = policy_rec

    if bad:
        issues.append("预配置字段非法已忽略：" + "、".join(bad) +
                      "（原文件未被改写）")
    return profile, issues


def policy(profile: Dict[str, Any]) -> Tuple[int, float]:
    """把重连策略归一为 ``(attempts, interval_s)``，并再次钳位。

    读取侧已钳位，这里再钳一次，保证**任何调用路径**都不可能拿到无上限的重试。
    """
    rec = (profile or {}).get("reconnect")
    if not isinstance(rec, dict):
        return DEFAULT_ATTEMPTS, DEFAULT_INTERVAL_S
    try:
        attempts = int(rec.get("attempts", DEFAULT_ATTEMPTS))
    except (TypeError, ValueError):
        attempts = DEFAULT_ATTEMPTS
    attempts = max(1, min(attempts, MAX_ATTEMPTS))
    try:
        interval = float(rec.get("interval_s", DEFAULT_INTERVAL_S))
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL_S
    if not math.isfinite(interval) or interval < 0:
        interval = DEFAULT_INTERVAL_S
    interval = min(interval, MAX_INTERVAL_S)
    return attempts, interval


def _validate_changes(changes: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """写入前严格校验；非法值直接拒绝（返回空字典 + 原因），不做猜测。"""
    clean: Dict[str, Any] = {}
    notes: List[str] = []
    for k, v in (changes or {}).items():
        if k not in WRITABLE:
            raise ValueError(f"未知字段 {k!r}（可写字段：{', '.join(WRITABLE)}）")
        if k == "device_id":
            if v in (None, ""):
                clean[k] = ""
            elif not isinstance(v, str):
                raise ValueError("device_id 需字符串")
            else:
                clean[k] = v.strip()
        elif k == "sample_rate_hz":
            if v is None:
                clean[k] = None
            else:
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    raise ValueError("sample_rate_hz 需正数") from None
                if not math.isfinite(f) or f <= 0:
                    raise ValueError("sample_rate_hz 需正数")
                clean[k] = f
        elif k == "gain_db":
            if v is None:
                clean[k] = None
            else:
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    raise ValueError("gain_db 需非负数") from None
                if not math.isfinite(f) or f < 0:
                    raise ValueError("gain_db 需非负数")
                clean[k] = f
        elif k == "attempts":
            if v is None:
                clean[k] = None
            else:
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    raise ValueError("attempts 需整数") from None
                if n < 1:
                    raise ValueError("attempts 至少 1 次（1 = 不重试）")
                if n > MAX_ATTEMPTS:
                    notes.append(f"重试次数 {n} 超过上限，已按 {MAX_ATTEMPTS} 次保存")
                    n = MAX_ATTEMPTS
                clean[k] = n
        elif k == "interval_s":
            if v is None:
                clean[k] = None
            else:
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    raise ValueError("interval_s 需非负数秒") from None
                if not math.isfinite(f) or f < 0:
                    raise ValueError("interval_s 需非负数秒")
                if f > MAX_INTERVAL_S:
                    notes.append(
                        f"重试间隔 {f:g}s 超过上限，已按 {MAX_INTERVAL_S:g}s 保存")
                    f = MAX_INTERVAL_S
                clean[k] = f
    return clean, notes


def save(store, changes: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """写入预配置（只提交本键，未知键由 ConfigStore 锁内合并保留）。"""
    try:
        clean, notes = _validate_changes(changes)
    except ValueError as e:
        return False, [f"未保存：{e}"]
    if store is None:
        return False, ["未保存：配置存储不可用（ConfigStore 构造失败）"]

    def _mutate(data: Dict[str, Any]) -> bool:
        cur = data.get(KEY)
        prof = dict(cur) if isinstance(cur, dict) else {}
        rec = prof.get("reconnect")
        rec = dict(rec) if isinstance(rec, dict) else {}
        for k, v in clean.items():
            if k in ("attempts", "interval_s"):
                if v is None:
                    rec.pop(k, None)
                else:
                    rec[k] = v
            else:
                if v in (None, ""):
                    prof.pop(k, None)
                else:
                    prof[k] = v
        if rec:
            prof["reconnect"] = rec
        else:
            prof.pop("reconnect", None)
        if not prof:
            data.pop(KEY, None)
        else:
            data[KEY] = prof
        return True

    try:
        ok = store.update(_mutate)
    except Exception as e:  # noqa: BLE001 - 写失败要可见，不阻断调用方
        return False, [f"未保存：{type(e).__name__}: {e}"]
    if not ok:
        try:
            from .config import last_error
        except ImportError:
            from config import last_error
        return False, [f"未保存：{last_error() or '写入未完成（锁等待超时或写盘失败）'}"]
    return True, notes


def clear(store) -> Tuple[bool, List[str]]:
    """清除整个预配置键（其他键原样保留）。"""
    if store is None:
        return False, ["未清除：配置存储不可用（ConfigStore 构造失败）"]

    existed = KEY in (store.read() or {})
    if not existed:
        return True, ["本来就没有预配置，无需清除"]

    def _mutate(data: Dict[str, Any]) -> bool:
        if KEY not in data:
            return False                 # 锁内重读后已被别人清掉，不写盘
        data.pop(KEY, None)
        return True

    try:
        ok = store.update(_mutate)
    except Exception as e:  # noqa: BLE001
        return False, [f"未清除：{type(e).__name__}: {e}"]
    if not ok:
        return False, ["未清除：写入未完成（锁等待超时或写盘失败）"]
    return True, []


def describe(profile: Dict[str, Any]) -> str:
    """把预配置渲染成一行人类可读文本（空配置返回空串）。"""
    if not profile:
        return ""
    parts = []
    if "device_id" in profile:
        parts.append(f"设备={profile['device_id']}")
    if "sample_rate_hz" in profile:
        parts.append(f"采样率={profile['sample_rate_hz'] / 1e6:.3f} MS/s")
    if "gain_db" in profile:
        parts.append(f"增益={profile['gain_db']:g} dB")
    if "reconnect" in profile:
        a, iv = policy(profile)
        parts.append(f"重连={a} 次/间隔 {iv:g}s")
    return "、".join(parts)


def apply_to(backend, profile: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """把预配置下发到已连接的后端。

    返回 ``(applied, warnings)``：``applied`` 是成功项的可读描述，``warnings``
    是「下发被拒绝/异常」的如实说明——**不许把未生效说成已生效**。
    """
    if backend is None or not profile:
        return [], []
    applied: List[str] = []
    warnings: List[str] = []

    if "sample_rate_hz" in profile:
        rate = profile["sample_rate_hz"]
        try:
            ok = backend.set_sample_rate(rate)
            err = ""
        except Exception as e:  # noqa: BLE001 - 设备异常不能中断连接流程
            ok = False
            err = f"{type(e).__name__}: {e}"
        if ok is False:
            reason = err or getattr(getattr(backend, "status", None), "error", "")
            warnings.append(f"预配置采样率 {rate / 1e6:.3f} MS/s 未生效"
                            + (f"（{reason}）" if reason else "（后端拒绝）"))
        else:
            applied.append(f"采样率={rate / 1e6:.3f} MS/s")

    if "gain_db" in profile:
        gain = profile["gain_db"]
        try:
            ok = backend.set_gain(gain)
            err = ""
        except Exception as e:  # noqa: BLE001
            ok = False
            err = f"{type(e).__name__}: {e}"
        if ok is False:
            reason = err or getattr(getattr(backend, "status", None), "error", "")
            warnings.append(f"预配置增益 {gain:g} dB 未生效"
                            + (f"（{reason}）" if reason else "（后端拒绝）"))
        else:
            applied.append(f"增益={gain:g} dB")

    return applied, warnings
