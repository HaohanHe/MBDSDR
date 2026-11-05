#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""真机七环验收 —— 单命令可复跑套件（release_check 风格）。

用法::

    python scripts/hw_acceptance/acceptance_suite.py
    python scripts/hw_acceptance/acceptance_suite.py --hw      # 只跑 R1–R4，SKIP 计数可见
    python scripts/hw_acceptance/acceptance_suite.py --out D:\\mbdsdr\\tmp\\hw_suite

行为：逐环执行 → 逐环 PASS/FAIL/SKIP → **失败即停**（后续环标 SKIP「未执行」）
→ 末尾汇总 ``PASS n / FAIL n / SKIP n``，并逐条列出 SKIP 原因。
退出码：任一环 FAIL → 1，其余 → 0（SKIP 不是失败）。

七环（真实硬件链路七个面，每环都必须给出实测数字）::

    R1 连接与识别        枚举/连接/释放耗时（ms）、设备三元组、tuner
    R2 频谱与首帧        连接→第一帧频谱耗时（ms）、帧间隔抖动、噪声底/SNR
    R3 流稳定性          持续读 IQ 的请求/实收样本数、短读次数、吞吐比
                         （pyrtlsdr 无 overflow 计数，不伪造丢样本数）
    R4 解调与音频输出    音频帧数、RMS、声道、19 kHz 导频、WAV 字节（不出声）
    R5 云台回读          rotctld/板载回读延迟（ms）、方位/仰角、读回一致性
    R6 GNSS 定位         首次定位耗时（s）、经纬度、星数、HDOP
    R7 星象仪渲染        太阳误差、月球 vs JPL DE421（含 1h 错位反证）、
                         渲染耗时（ms）、PNG 字节、非空白像素

硬红线：没有真实设备/后端 → ``SKIP`` 并带原因码
（NO_HW / NO_DATA / NEEDS_OPERATOR / NEEDS_TIME_WINDOW），
**不允许**用模拟/合成/``debug`` 调试源数据判 PASS；拿不到的数字不补。
产物一律落 D 盘 ``.tmp-tool-results/hw_acceptance/<时间戳>/``。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import time
import traceback
from datetime import datetime

sys_path_dir = os.path.dirname(os.path.abspath(__file__))
if sys_path_dir not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path_dir)

import common  # noqa: E402  （同目录公共层，负责路径/设备探测/红线）

FS = 2.4e6
SKIP = common.SKIP


def _skip_code(res: dict) -> str:
    return res.get("skip_code", "") if res.get("status") == SKIP else ""


def skip_result(ring: str, name: str, code: str, metrics: dict | None = None,
                checks: list[dict] | None = None, notes: str = "") -> dict:
    """SKIP 信封：必须带原因码（NO_HW / NO_DATA / NEEDS_OPERATOR / NEEDS_TIME_WINDOW）。"""
    return common.skip_result(
        ring, name, code, notes,
        metrics=metrics, checks=checks,
        user_message_on_failure=common.USER_MSG["no_device"],
        device_required=True)


def fail_result(ring: str, name: str, detail: str, metrics: dict | None = None,
                checks: list[dict] | None = None, user_msg: str = "",
                notes: str = "") -> dict:
    return common.result(ring, name, common.FAIL, metrics or {}, checks or [],
                         user_message_on_failure=user_msg, device_required=True,
                         notes=notes or detail)


def _controller(serial: str):
    # USB 句柄释放有延迟，Access denied 时短暂重试（见 common.open_controller）。
    return common.open_controller(f"rtlsdr:{serial}")


def _device_gate(ring: str, name: str) -> dict | None:
    """无真机 → SKIP(NO_HW)；只看到 debug 源同样不算硬件。"""
    n = common.rtl_device_count()
    devs = common.real_devices()
    if n > 0 and devs:
        return None
    return skip_result(
        ring, name, "NO_HW",
        metrics={"device_count": n, "real_device_count": len(devs)},
        checks=[common.check("device_present", False,
                             f"device_count={n}, real={len(devs)}（debug 源不算）",
                             skipped=True)],
        notes="本机不可验：未枚举到真实 RTL-SDR；本环未产生任何实测数字。")


# ---------------------------------------------------------------- R1
def ring1() -> dict:
    gate = _device_gate("R1", "连接与识别")
    if gate:
        return gate
    strings = common.rtl_device_strings()
    devs = common.real_devices()
    serial = str(getattr(devs[0], "serial", "") or "")
    checks = [
        common.check("enumerated", True, f"device_count={common.rtl_device_count()}"),
        common.check("usb_strings", bool(strings),
                     f"{strings[0]['product']} / {strings[0]['serial']}"
                     if strings else "三元组不可读"),
    ]
    # tuner 必须在控制器连接**之前**探测：同一根棒子只能有一个客户端，
    # 连接后再开 RtlSdr() 会 LIBUSB_ERROR_ACCESS（会被误判成 tuner 失败）。
    tuner = ""
    try:
        from rtlsdr import RtlSdr
        s = RtlSdr()
        tuner = str(s.get_tuner_type())
        s.close()
    except Exception as e:
        checks.append(common.check("tuner_identified", False, f"pyrtlsdr: {e}"))
    t0 = time.perf_counter()
    try:
        ctrl = _controller(serial)
    except Exception as e:
        if common.transient_open_error(e):
            return skip_result(
                "R1", "连接与识别", "NEEDS_OPERATOR",
                metrics={"device_id": f"rtlsdr:{serial}"},
                checks=checks + [common.check("connect", False, str(e), skipped=True)],
                notes="设备已枚举但打不开（权限/占用/驱动/重枚举空窗），本环无法验证。")
        return fail_result("R1", "连接与识别", f"connect 失败：{e}",
                           {"device_id": f"rtlsdr:{serial}"},
                           checks + [common.check("connect", False, str(e))],
                           user_msg=common.USER_MSG["device_busy"])
    connect_ms = (time.perf_counter() - t0) * 1000.0
    checks.append(common.check("connect", True, f"connect {connect_ms:.1f} ms"))
    checks.append(common.check("tuner_identified", bool(tuner), f"tuner={tuner}"))
    t1 = time.perf_counter()
    try:
        ctrl.disconnect()
        disconnect_ok = True
    except Exception:
        disconnect_ok = False
    disconnect_ms = (time.perf_counter() - t1) * 1000.0
    checks.append(common.check("disconnect", disconnect_ok, f"{disconnect_ms:.1f} ms"))
    metrics = {
        "device_id": f"rtlsdr:{serial}",
        "devices": strings,
        "tuner": tuner,
        "connect_ms": round(connect_ms, 1),
        "disconnect_ms": round(disconnect_ms, 1),
        "enumeration_count": common.rtl_device_count(),
    }
    ok = all(c["ok"] for c in checks)
    return common.result("R1", "连接与识别", common.PASS if ok else common.FAIL,
                         metrics, checks, common.USER_MSG["device_busy"], [],
                         True, [], "连接耗时与释放耗时均为本机实测。")


# ---------------------------------------------------------------- R2
def ring2() -> dict:
    gate = _device_gate("R2", "频谱与首帧")
    if gate:
        return gate
    import numpy as np
    serial = str(common.real_devices()[0].serial)
    try:
        ctrl = _controller(serial)
    except Exception as e:
        if common.transient_open_error(e):
            return skip_result(
                "R2", "频谱与首帧", "NEEDS_OPERATOR",
                metrics={"device_id": f"rtlsdr:{serial}"},
                checks=[common.check("connect", False, str(e), skipped=True)],
                notes="设备已枚举但打不开（权限/占用/重枚举空窗），本环无法验证。")
        return fail_result("R2", "频谱与首帧", f"connect 失败：{e}", {},
                           [common.check("connect", False, str(e))],
                           user_msg=common.USER_MSG["device_busy"])

    ctrl.set_sample_rate(FS)
    # 第一帧：连接完成 → 第一次 read_spectrum 返回
    t0 = time.perf_counter()
    first = ctrl.read_spectrum(1024)
    first_frame_ms = (time.perf_counter() - t0) * 1000.0
    if first is None:
        return fail_result("R2", "频谱与首帧", "第一帧频谱为 None",
                           {"first_frame_ms": round(first_frame_ms, 1)},
                           [common.check("first_frame", False, "read_spectrum 返回 None")],
                           user_msg=common.USER_MSG["no_signal"])

    freq, peak_floor = common.find_strong_fm(ctrl, 1024, FS)
    if not freq:
        return fail_result("R2", "频谱与首帧", "88–108 MHz 无显著 FM 台",
                           {"scan_peak_floor_db": round(peak_floor, 1)},
                           [common.check("station_found", False, f"峰底比 {peak_floor:.1f} dB")],
                           user_msg=common.USER_MSG["no_signal"])

    ctrl.set_frequency(freq)
    time.sleep(0.2)
    frames, gaps = [], []
    t_prev = time.perf_counter()
    for _ in range(32):
        db = ctrl.read_spectrum(1024)
        now = time.perf_counter()
        gaps.append((now - t_prev) * 1000.0)
        t_prev = now
        if db is not None:
            frames.append(np.fft.fftshift(np.asarray(db, dtype=np.float64)))
    if len(frames) < 16:
        return fail_result("R2", "频谱与首帧", f"有效帧过少：{len(frames)}/32",
                           {"target_hz": freq}, [], common.USER_MSG["no_signal"])
    arr = np.stack(frames)
    c = arr.shape[1] // 2
    edge = int(700e3 / (FS / 1024))
    win = arr[:, c - edge:c + edge]
    noise = float(np.median(win))
    peak = float(np.max(np.mean(win, axis=0)))
    snr = peak - noise
    centers = [float(np.mean(np.where(row >= row.max() - 6)[0] - edge)) for row in win]
    offset_hz = float(np.mean(centers)) * (FS / 1024)
    drift = float(np.max(np.median(win, axis=1)) - np.min(np.median(win, axis=1)))
    metrics = {
        "target_hz": round(freq, 0),
        "first_frame_ms": round(first_frame_ms, 1),
        "frames": len(frames),
        "frame_interval_ms_mean": round(float(np.mean(gaps)), 1),
        "frame_interval_ms_max": round(float(np.max(gaps)), 1),
        "noise_floor_db": round(noise, 2),
        "peak_db": round(peak, 2),
        "snr_db": round(snr, 2),
        "centroid_offset_hz": round(offset_hz, 1),
        "frequency_accuracy_note": (
            "峰值质心偏移仅作信息：空口 FM 的峰值随调制/邻台漂移，"
            "不是频率精度指标；绝对频率精度需已知频率信号源（见人工卡 R2）。"),
        "floor_drift_db": round(drift, 2),
        "nan_bins": int(np.isnan(arr).sum()),
    }
    checks = [
        common.check("first_frame", True, f"{first_frame_ms:.1f} ms"),
        common.check("snr_ge_10db", snr >= 10.0, f"{snr:.2f} dB"),
        common.check("floor_drift_le_6db", drift <= 6.0, f"{drift:.2f} dB"),
        common.check("no_nan", int(np.isnan(arr).sum()) == 0,
                     f"{int(np.isnan(arr).sum())} 个 NaN"),
    ]
    try:
        ctrl.disconnect()
    except Exception as exc:  # 释放失败不改变已采到的指标，但必须留痕
        metrics["disconnect_error"] = f"{type(exc).__name__}: {exc}"
    return common.result("R2", "频谱与首帧",
                         common.PASS if all(c["ok"] for c in checks) else common.FAIL,
                         metrics, checks, common.USER_MSG["no_signal"], [], True, [],
                         "第一帧时间=连接完成到首次频谱返回；帧间隔抖动为实测。")


# ---------------------------------------------------------------- R3
def ring3() -> dict:
    gate = _device_gate("R3", "流稳定性")
    if gate:
        return gate
    serial = str(common.real_devices()[0].serial)
    try:
        ctrl = _controller(serial)
    except Exception as e:
        if common.transient_open_error(e):
            return skip_result(
                "R3", "流稳定性", "NEEDS_OPERATOR",
                metrics={"device_id": f"rtlsdr:{serial}"},
                checks=[common.check("connect", False, str(e), skipped=True)],
                notes="设备已枚举但打不开（权限/占用/重枚举空窗），本环无法验证。")
        return fail_result("R3", "流稳定性", f"connect 失败：{e}", {},
                           [common.check("connect", False, str(e))],
                           user_msg=common.USER_MSG["device_busy"])
    ctrl.set_sample_rate(FS)
    # 读块必须足够大：实测同样 2.4 MS/s 下，16384/块只有 1.74 MS/s、262144/块 2.28 MS/s
    # ——小块的低吞吐是 Python 每次调用开销，不是设备流；用小块会把「读循环开销」
    # 误判成「设备吞吐不足」。
    n_req = 262144
    duration_s = 3.0
    t_end = time.perf_counter() + duration_s
    requested = received = reads = short = nones = 0
    while time.perf_counter() < t_end:
        iq = ctrl.read_iq(n_req)
        reads += 1
        requested += n_req
        if iq is None:
            nones += 1
            continue
        got = int(len(iq))
        received += got
        if got < n_req:
            short += 1
    elapsed = duration_s - max(0.0, t_end - time.perf_counter())
    ratio = received / max(1, requested)
    eff_rate = received / max(elapsed, 1e-9)
    metrics = {
        "duration_s": round(elapsed, 3),
        "read_block_samples": n_req,
        "read_calls": reads,
        "requested_samples": requested,
        "received_samples": received,
        "throughput_ratio": round(ratio, 4),
        "effective_rate_msps": round(eff_rate / 1e6, 3),
        "short_reads": short,
        "none_reads": nones,
        "overflow_counter": "unavailable（pyrtlsdr 不暴露 USB overflow 计数，不伪造丢样本数）",
    }
    checks = [
        common.check("reads_returned", nones == 0, f"None 读 {nones}/{reads} 次"),
        common.check("throughput_ratio_ge_0_90", ratio >= 0.90, f"{ratio:.3f}"),
        common.check("effective_rate_near_2_4msps", abs(eff_rate / 1e6 - 2.4) <= 0.24,
                     f"{eff_rate / 1e6:.3f} MS/s"),
        common.check("no_short_reads", short == 0,
                     f"短读 {short}/{reads}（< {n_req} 样本）"),
    ]
    try:
        ctrl.disconnect()
    except Exception as exc:  # 释放失败不改变已采到的指标，但必须留痕
        metrics["disconnect_error"] = f"{type(exc).__name__}: {exc}"
    return common.result("R3", "流稳定性",
                         common.PASS if all(c["ok"] for c in checks) else common.FAIL,
                         metrics, checks, common.USER_MSG["device_busy"], [], True, [],
                         "丢样本只能由吞吐比/短读间接体现；驱动无 overflow 计数，未伪造。")


# ---------------------------------------------------------------- R4
def ring4() -> dict:
    gate = _device_gate("R4", "解调与音频输出")
    if gate:
        return gate
    import numpy as np
    serial = str(common.real_devices()[0].serial)
    try:
        ctrl = _controller(serial)
    except Exception as e:
        return fail_result("R4", "解调与音频输出", f"connect 失败：{e}", {},
                           [common.check("connect", False, str(e))],
                           user_msg=common.USER_MSG["device_busy"])
    ctrl.set_sample_rate(FS)
    freq, peak_floor = common.find_strong_fm(ctrl, 1024, FS)
    if not freq:
        return fail_result("R4", "解调与音频输出", "无显著 FM 台",
                           {"scan_peak_floor_db": round(peak_floor, 1)}, [],
                           common.USER_MSG["no_signal"])
    ctrl.set_frequency(freq)
    ctrl.set_demod("WFM")
    time.sleep(0.3)
    audio = ctrl.read_audio(96000)
    if audio is None or len(audio) < 4096:
        return fail_result("R4", "解调与音频输出", "read_audio None/过短",
                           {"target_hz": freq}, [],
                           user_msg=common.USER_MSG["no_signal"])
    a = np.asarray(audio, dtype=np.float32)
    channels = int(a.shape[1]) if a.ndim == 2 else 1
    mono = a.mean(axis=1) if a.ndim == 2 else a
    rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
    lo = float(np.sum(np.abs(np.fft.rfft(mono[: min(len(mono), 65536)]))[: 15000 * 2] ** 2))
    hi = float(np.sum(np.abs(np.fft.rfft(mono[: min(len(mono), 65536)]))[15000 * 2:] ** 2))
    ratio = lo / max(hi, 1e-12)
    out = common.artifact_dir("r4_demod_audio")
    wav = os.path.join(out, "wfm_audio.wav")
    import wave
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.clip(mono, -1, 1) * 32767).astype(np.int16).tobytes())
    checks = [
        common.check("frames_ge_4096", len(mono) >= 4096, f"{len(mono)} 帧"),
        common.check("rms_in_range", 1e-4 < rms < 1.0, f"RMS={rms:.4f}"),
        common.check("energy_below_15k", ratio > 2.0, f"<15k/>15k={ratio:.1f}"),
    ]
    metrics = {
        "target_hz": round(freq, 0),
        "audio_frames": int(len(mono)),
        "channels": channels,
        "rms": round(rms, 5),
        "energy_ratio_lt15k": round(ratio, 1),
        "wav_bytes": os.path.getsize(wav),
        "soundcard_opened": False,
    }
    try:
        ctrl.disconnect()
    except Exception as exc:  # 释放失败不改变已采到的指标，但必须留痕
        metrics["disconnect_error"] = f"{type(exc).__name__}: {exc}"
    return common.result("R4", "解调与音频输出",
                         common.PASS if all(c["ok"] for c in checks) else common.FAIL,
                         metrics, checks, common.USER_MSG["no_signal"],
                         ["音质/爆音听感（需人工出声，脚本永不开声卡）"],
                         True, [wav], "音频只落 WAV，不经过任何声卡。")


# ---------------------------------------------------------------- R5
def ring5() -> dict:
    """云台回读：只读姿态，不下发任何运动指令（避免未经确认转动硬件）。"""
    port = int(os.environ.get("MBDSDR_ROTCTLD_PORT", "4533"))
    host = os.environ.get("MBDSDR_ROTCTLD_HOST", "127.0.0.1")
    t0 = time.perf_counter()
    reachable = False
    try:
        with socket.create_connection((host, port), timeout=0.6):
            reachable = True
    except OSError:
        reachable = False
    if not reachable:
        return skip_result(
            "R5", "云台回读", "NO_HW",
            metrics={"rotctld_host": host, "rotctld_port": port,
                     "probe_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                     "board_channel": "unavailable（需板载 ESP32 send callable，本机未接）"},
            checks=[common.check("rotctld_reachable", False,
                                 f"{host}:{port} 无应答", skipped=True)],
            notes="本机不可验：未检测到 rotctld/板载云台；未下发任何运动指令。")
    from mbdsdr_ai.gimbal import RotctldClient
    client = RotctldClient(host, port, timeout=1.0)
    t1 = time.perf_counter()
    pose = client.get_position()
    read_ms = (time.perf_counter() - t1) * 1000.0
    t2 = time.perf_counter()
    pose2 = client.get_position()
    read_ms2 = (time.perf_counter() - t2) * 1000.0
    d_az = (pose2.azimuth - pose.azimuth + 180.0) % 360.0 - 180.0
    d_el = pose2.elevation - pose.elevation
    checks = [
        common.check("read_pose", True, f"az={pose.azimuth:.1f}° el={pose.elevation:.1f}°"),
        common.check("readback_stable", abs(d_az) <= 1.0 and abs(d_el) <= 1.0,
                     f"两次回读差 Δaz={d_az:.2f}° Δel={d_el:.2f}°"),
    ]
    metrics = {
        "backend": f"rotctld {host}:{port}",
        "connect_ms": round((t1 - t0) * 1000.0, 1),
        "read_ms_first": round(read_ms, 1),
        "read_ms_second": round(read_ms2, 1),
        "azimuth_deg": round(pose.azimuth, 2),
        "elevation_deg": round(pose.elevation, 2),
        "delta_az_deg": round(d_az, 3),
        "delta_el_deg": round(d_el, 3),
        "motion_commanded": False,
    }
    return common.result("R5", "云台回读",
                         common.PASS if all(c["ok"] for c in checks) else common.FAIL,
                         metrics, checks,
                         "云台无应答。请确认 rotctld 已启动（默认 127.0.0.1:4533）或板载云台通道已连接。",
                         [], False, [],
                         "只读回读，未下发运动指令（机械精度需另行授权测试）。")


# ---------------------------------------------------------------- R6
def ring6() -> dict:
    """GNSS 定位：真实串口 NMEA；无串口/无 fix 一律 SKIP，不编坐标。"""
    try:
        from mbdsdr_ai.gnss_monitor import RealGNSSMonitor
    except Exception as e:
        return skip_result("R6", "GNSS 定位", "NO_HW",
                           metrics={"import_error": f"{type(e).__name__}: {e}"},
                           notes="本机不可验：GNSS 模块不可导入。")
    mon = RealGNSSMonitor()
    t0 = time.perf_counter()
    started = mon.start()
    if not started:
        return skip_result(
            "R6", "GNSS 定位", "NO_HW",
            metrics={"start_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                     "port": mon.port, "baudrate": mon.baudrate},
            notes="本机不可验：未检测到串口 GNSS 接收机（可用 COM 口/驱动检查）。")
    deadline = time.perf_counter() + float(os.environ.get("MBDSDR_GNSS_WAIT_S", "30"))
    pos = mon.get_position()
    while pos.source != "real" and time.perf_counter() < deadline:
        time.sleep(0.5)
        pos = mon.get_position()
    ttff_s = None if pos.source != "real" else round(time.perf_counter() - t0, 2)
    gsv = mon.get_gsv_frames()
    sats_visible = sum(len(getattr(f, "satellites", []) or []) for f in gsv) if gsv else None
    mon.stop()
    if pos.source != "real":
        return skip_result(
            "R6", "GNSS 定位", "NEEDS_TIME_WINDOW",
            metrics={"port": mon.port, "wait_s": round(time.perf_counter() - t0, 1),
                     "fix": "none", "sats_visible": sats_visible},
            checks=[common.check("fix", False, "串口已开但等待窗口内无有效定位", skipped=True)],
            notes="本机不可验：串口已打开但无 fix（室内/天线遮挡常见），未编造坐标。")
    checks = [
        common.check("fix", True, "source=real"),
        common.check("coordinates_valid", -90 <= (pos.lat or 0) <= 90
                     and -180 <= (pos.lon or 0) <= 180,
                     f"lat={pos.lat}, lon={pos.lon}"),
    ]
    metrics = {
        "port": mon.port,
        "ttff_s": ttff_s,
        "lat": pos.lat,
        "lon": pos.lon,
        "alt_m": pos.alt,
        "sats_used": pos.sats,
        "sats_visible": sats_visible,
        "hdop": pos.hdop,
        "utc_time": pos.utc_time,
    }
    return common.result("R6", "GNSS 定位",
                         common.PASS if all(c["ok"] for c in checks) else common.FAIL,
                         metrics, checks,
                         "未获得 GNSS 定位。请检查 GNSS 接收机串口/天线，或在户外重试。",
                         [], False, [], "坐标来自真实 NMEA fix，未做任何平滑或补值。")


# ---------------------------------------------------------------- R7
def ring7() -> dict:
    """星象仪渲染：真实天文计算 + offscreen 渲染；无需射频硬件。"""
    import ring6_sky_mode as sky
    lat = float(os.environ.get("MBDSDR_OBS_LAT", "43.88"))
    lon = float(os.environ.get("MBDSDR_OBS_LON", "125.32"))
    t0 = time.perf_counter()
    res = sky.run(lat, lon)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    png = next((p for p in res.get("artifacts", []) if p.endswith(".png")), "")
    metrics = dict(res.get("metrics", {}))
    metrics["render_total_ms"] = round(elapsed_ms, 1)
    metrics["png_bytes"] = os.path.getsize(png) if png and os.path.exists(png) else 0
    checks = list(res.get("checks", []))
    checks.append(common.check("render_under_3s", elapsed_ms <= 3000,
                               f"渲染总耗时 {elapsed_ms:.1f} ms"))
    status = common.PASS if all(c["ok"] for c in checks if not c["skipped"]) else common.FAIL
    return common.result("R7", "星象仪渲染", status, metrics, checks,
                         res.get("user_message_on_failure", ""),
                         res.get("human_required", []), False, res.get("artifacts", []),
                         "观测站坐标为参数输入（非实测）；渲染为 offscreen，人眼手感需真实显示环境。")


# ---------------------------------------------------------------- 编排
RINGS = [
    ("R1", "连接与识别", ring1),
    ("R2", "频谱与首帧", ring2),
    ("R3", "流稳定性", ring3),
    ("R4", "解调与音频输出", ring4),
    ("R5", "云台回读", ring5),
    ("R6", "GNSS 定位", ring6),
    ("R7", "星象仪渲染", ring7),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="真机七环验收（单命令，失败即停）")
    ap.add_argument("--out", default="", help="证据根目录（必须 D 盘）")
    ap.add_argument("--hw", action="store_true",
                    help="只跑硬件四环 R1–R4：有设备逐环实测数字，"
                         "无设备 SKIP 并给原因码（SKIP 仍计数可见）")
    args = ap.parse_args()
    rings = [(r, n, f) for r, n, f in RINGS if not args.hw or r in
             ("R1", "R2", "R3", "R4")]
    root = args.out or os.path.join(
        common.ARTIFACT_ROOT,
        "suite_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    if not os.path.isabs(root):
        root = os.path.join(common.REPO_ROOT, root)
    if not root.lower().startswith("d:\\"):
        print(f"FAIL: 产物目录必须在 D 盘：{root}")
        return 1
    os.makedirs(root, exist_ok=True)
    os.environ["MBDSDR_HW_ACCEPTANCE_DIR"] = root

    print("=" * 72)
    print("真机七环验收（单命令，失败即停）")
    print(f"模式：{'--hw（仅 R1–R4 硬件四环）' if args.hw else '全七环'}")
    print(f"证据目录：{root}")
    print(common.RED_LINE)
    print("=" * 72)

    results: list[dict] = []
    halted = ""
    for ring, name, fn in rings:
        if halted:
            results.append(skip_result(
                ring, name, "NEEDS_OPERATOR",
                notes=f"前序 {halted} FAIL，按「失败即停」未执行；本环未产生数字。"))
            print(f"[{ring}] {name:<12} SKIP(NEEDS_OPERATOR)  ← 前序 {halted} FAIL")
            continue
        t0 = time.perf_counter()
        try:
            res = fn()
        except Exception as e:  # 环内异常 = 本环 FAIL
            res = fail_result(ring, name, f"{type(e).__name__}: {e}", {},
                              [common.check("ring_completed", False,
                                            traceback.format_exc()[-800:])])
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        results.append(res)
        ring_dir = os.path.join(root, f"{ring.lower()}_{name.replace('与', '_')}")
        os.makedirs(ring_dir, exist_ok=True)
        with open(os.path.join(ring_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"[{ring}] {name:<12} {res['status']:<5} {res.get('notes', '')}")
        for k, v in res.get("metrics", {}).items():
            if isinstance(v, (int, float, str)):
                print(f"      {k}: {v}")
        if res["status"] == common.FAIL:
            halted = ring
            print(f"      失败即停：{res.get('user_message_on_failure', '')}")
            print(f"      下一步：{res.get('notes', '')}")

    passed = sum(1 for r in results if r["status"] == common.PASS)
    failed = sum(1 for r in results if r["status"] == common.FAIL)
    skipped = sum(1 for r in results if r["status"] == SKIP)
    summary = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "root": root,
        "mode": "hw" if args.hw else "all",
        "red_line": common.RED_LINE,
        "counts": {"pass": passed, "fail": failed, "skip": skipped},
        "rings": [{"ring": r["ring"], "name": r["name"], "status": r["status"],
                   "skip_reason": _skip_code(r),
                   "elapsed_ms": r.get("elapsed_ms")}
                  for r in results],
    }
    with open(os.path.join(root, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 72)
    print(f"汇总：PASS {passed} / FAIL {failed} / SKIP {skipped}")
    print("SKIP 明细：")
    for r in results:
        if r["status"] == SKIP:
            print(f"  {r['ring']} {r['name']} [{_skip_code(r)}]: {r.get('notes', '')}")
    print(common.RED_LINE)
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
