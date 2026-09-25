#!/usr/bin/env python3
"""卫星地面站自动化往返验证
================================

覆盖三个适配器的端到端正确性：

  1. r2cloud 调度器：已知 ISS TLE + 北京站 -> 24h 内生成非空过境任务
  2. satnogs 多普勒校正：已知视线速度 -> NCO 频率与解析公式误差 < 1 Hz
  3. OGN FLARM 帧解析：已知帧解出飞机 ID/经纬度
  4. FireDetector：合成热点 -> 正确聚类数量
  5. RemoteControl：JSON 命令往返 -> 状态正确更新
"""
from __future__ import annotations

import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai.r2cloud_adapter import (  # noqa: E402
    R2CloudStation, R2CloudScheduler, ObservationTask,
    SequentialTimetable, TimeSlot, build_recording_metadata,
    register_r2cloud_tools,
)
from mbdsdr_ai.satnogs_adapter import (  # noqa: E402
    DemodMode, DopplerCorrector, SatnogsNetworkClient,
    SatnogsObservation, ObservationPipeline, register_satnogs_tools,
)
from mbdsdr_ai.sdrangel_plugins import (  # noqa: E402
    encode_flarm_frame, decode_flarm_frame, FireDetector,
    RemoteControlProtocol, RemoteDeviceState, OGN_DEFAULT_FREQ_HZ,
    register_sdrangel_plugins_tools,
)
from mbdsdr_ai.gpredict_adapter import (  # noqa: E402
    TLEParser, GeoStation, SGP4Propagator, CoordinateConverter,
    unix_to_jd, C_LIGHT,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


# 经典 Vallado ISS TLE（与 gpredict_test.py 一致）
ISS_NAME = "ISS (ZARYA)"
ISS_L1 = ("1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
          ).ljust(69)
ISS_L2 = ("2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
          ).ljust(69)


# ---------------------------------------------------------------------------
def test_r2cloud_scheduler():
    print("\n== 1. r2cloud 调度器 (ObservationFactory.java:24-63, MinElevationHandler) ==")
    station = R2CloudStation(name="beijing", lat_deg=39.9, lon_deg=116.4,
                              alt_m=50.0, min_elevation_deg=10.0)
    sched = R2CloudScheduler(station)
    tle = TLEParser.parse(ISS_NAME, ISS_L1, ISS_L2)
    t0 = (tle.epoch_jd - 2440587.5) * 86400.0
    tasks = sched.schedule_satellite(ISS_NAME, ISS_L1, ISS_L2,
                                     transmitter_id="iss-vhf",
                                     frequency_hz=145.9e6,
                                     start_unix_s=t0, horizon_hours=24.0)
    check("调度器生成非空任务列表", len(tasks) > 0, f"(n={len(tasks)})")
    for t in tasks:
        check("任务时长 > 4min 阈值", t.duration_s >= 4 * 60 - 1,
              f"(dur={t.duration_s:.0f}s)")
        check("任务时长 <= 15min 裁剪", t.duration_s <= 15 * 60 + 1,
              f"(dur={t.duration_s:.0f}s)")
        check("任务 task_id 非空", bool(t.task_id))
        check("任务 max_el >= 站最小仰角", t.max_el_deg >= 10.0 - 1e-6,
              f"(max_el={t.max_el_deg:.1f})")
    # 时间表非重叠
    slots = sched.timetable.slots()
    check("时间表槽数 >= 1", len(slots) >= 1, f"(slots={len(slots)})")
    for i in range(len(slots) - 1):
        check("时间槽不重叠",
              slots[i].end_unix_s <= slots[i + 1].start_unix_s + 1e-6)
    # 录制元数据
    meta = build_recording_metadata(tasks[0], station)
    check("元数据含 SigMF global.core:frequency",
          meta["global"]["core:frequency"] == 145.9e6)
    check("元数据含 r2cloud:task_id",
          "r2cloud:task_id" in meta["annotations"][0])


# ---------------------------------------------------------------------------
def test_satnogs_doppler():
    print("\n== 2. satnogs 多普勒校正 (gr-satnogs doppler_correction_impl) ==")
    tle = TLEParser.parse(ISS_NAME, ISS_L1, ISS_L2)
    st = GeoStation(39.9, 116.4, 0.05)
    tx_freq = 435.0e6  # 435 MHz
    corr = DopplerCorrector(tle, st, tx_freq, sample_dt_s=1.0)
    t0 = (tle.epoch_jd - 2440587.5) * 86400.0 + 500.0  # 纪元后 500s
    curve = corr.build_curve(t0, t0 + 600.0)
    check("多普勒曲线采样点数 > 50", len(curve) > 50, f"(n={len(curve)})")
    # 独立解析：在某个采样点，用 SGP4 算 range_rate，再算 f*(1-v_r/c)
    prop = SGP4Propagator(tle)
    pt = curve[30]
    jd = unix_to_jd(pt.time_unix_s)
    tsince_min = (jd - tle.epoch_jd) * 1440.0
    r, v = prop.propagate(tsince_min)
    ob = CoordinateConverter.calculate_obs(jd, r, v, st)
    analytic_rx = tx_freq * (1.0 - ob.range_rate_kms / C_LIGHT)
    err_hz = abs(pt.rx_freq_hz - analytic_rx)
    check("NCO 频率与解析多普勒公式误差 < 1 Hz", err_hz < 1.0,
          f"(err={err_hz:.4f} Hz, range_rate={ob.range_rate_kms:.3f} km/s)")
    # 接近/远离符号：AOS 附近 range_rate<0 (蓝移)，LOS 附近 >0 (红移)
    check("多普勒频偏量级合理 (LEO ~ ±几 kHz)",
          abs(corr.max_doppler_hz) < 20000.0,
          f"(max|fd|={corr.max_doppler_hz:.1f} Hz)")
    # 解调模式枚举
    modes = {m.value: m for m in DemodMode}
    check("解调模式含 CW/AFSK/FSK/GMSK/LRPT",
          {"CW", "AFSK", "FSK", "GMSK", "LRPT"} <= set(modes))
    check("LRPT 带宽 ~80kHz", abs(modes["LRPT"].default_bandwidth_hz - 80000) < 1)


# ---------------------------------------------------------------------------
def test_ogn_flarm():
    print("\n== 3. OGN FLARM V6 帧解析 (24bit ICAO, 17bit lat/lon) ==")
    # 已知飞机：ICAO 0x4B1234 (某航)，位置 柏林 (52.52, 13.405)，高度 300m
    aircraft_id = 0x4B1234
    lat, lon = 52.5200, 13.4050
    frame = encode_flarm_frame(aircraft_id, lat, lon, alt_m=300.0,
                               speed_kts=120.0, heading_deg=270.0)
    check("帧长度 = 12 字节", len(frame) == 12, f"(len={len(frame)})")
    rep = decode_flarm_frame(frame)
    check("解出协议版本 = 6", rep.protocol_version == 6)
    check("解出飞机 ID = 0x4B1234", rep.aircraft_id == aircraft_id,
          f"(got=0x{rep.aircraft_id:06X})")
    check("解出纬度 ≈ 52.52°", abs(rep.lat_deg - lat) < 0.01,
          f"(got={rep.lat_deg:.4f})")
    check("解出经度 ≈ 13.405°", abs(rep.lon_deg - lon) < 0.01,
          f"(got={rep.lon_deg:.4f})")
    check("解出高度 ≈ 300m", abs(rep.alt_m - 300) < 2, f"(got={rep.alt_m})")
    check("OGN 默认频点 868.2MHz",
          abs(OGN_DEFAULT_FREQ_HZ - 868.2e6) < 1e3)


# ---------------------------------------------------------------------------
def test_firedetector():
    print("\n== 4. FireDetector 热点聚类 ==")
    # 两个簇：簇1 在 (45.0, 11.0) 附近 3 个热像点，簇2 在 (46.0, 12.0) 附近 2 个
    points = [
        (45.00, 11.00, 340.0),
        (45.01, 11.00, 350.0),
        (45.00, 11.01, 345.0),
        (46.00, 12.00, 380.0),
        (46.01, 12.01, 390.0),
        # 冷点应被阈值滤掉
        (50.00, 10.00, 290.0),
    ]
    det = FireDetector(threshold_k=320.0, cluster_km=5.0)
    clusters = det.detect(points)
    check("检出 2 个火灾簇", len(clusters) == 2, f"(got={len(clusters)})")
    check("簇1 含 3 个热点", any(c.size == 3 for c in clusters),
          f"(sizes={[c.size for c in clusters]})")
    check("簇2 含 2 个热点", any(c.size == 2 for c in clusters))
    check("簇最大温 390K", max(c.max_temp_k for c in clusters) == 390.0)


# ---------------------------------------------------------------------------
def test_remotecontrol():
    print("\n== 5. RemoteControl JSON 命令往返 ==")
    proto = RemoteControlProtocol(RemoteDeviceState("R0"))
    # 发一串命令
    wire1 = proto.encode_command("set_center_frequency",
                                 {"center_frequency": 145_000_000}, cmd_id=1)
    resp1 = proto.decode_and_apply(wire1)
    r1 = __import__("json").loads(resp1)
    check("set_center_frequency ok", r1["ok"] is True)
    check("设备中心频率 = 145MHz",
          r1["status"]["center_frequency"] == 145_000_000)
    wire2 = proto.encode_command("start_rx", {}, cmd_id=2)
    r2 = __import__("json").loads(proto.decode_and_apply(wire2))
    check("start_rx 后 running=true", r2["status"]["running"] is True)
    wire3 = proto.encode_command("set_doppler",
                                 {"rx_frequency": 145_100_000.0}, cmd_id=3)
    r3 = __import__("json").loads(proto.decode_and_apply(wire3))
    check("set_doppler 更新 rx_frequency",
          abs(r3["status"]["rx_frequency"] - 145_100_000.0) < 1.0)
    # 错误命令
    bad = proto.decode_and_apply('{"id":9,"command":"nope","args":{}}')
    rb = __import__("json").loads(bad)
    check("未知命令返回 ok=false", rb["ok"] is False)
    # JSON 往返：wire 字符串可被 json.loads 解析
    check("命令 wire 是合法 JSON",
          isinstance(__import__("json").loads(wire1), dict))


# ---------------------------------------------------------------------------
def test_registry_integration():
    print("\n== 6. register_*_tools 注册集成 ==")
    from mbdsdr_ai.tool_registry import ToolRegistry
    reg = ToolRegistry()
    register_r2cloud_tools(reg)
    register_satnogs_tools(reg)
    register_sdrangel_plugins_tools(reg)
    names = sorted(reg.tools.keys())
    print(f"    registered: {names}")
    check("r2cloud 注册 >= 2 工具",
          sum(1 for n in names if n.startswith("r2cloud_")) >= 2)
    check("satnogs 注册 >= 2 工具",
          sum(1 for n in names if n.startswith("satnogs_")) >= 2)
    check("sdrangel_plugins 注册 >= 2 工具",
          sum(1 for n in names if n.startswith("sdrangel_")) >= 2)
    # 调用 r2cloud_schedule 工具
    out = reg.tools["r2cloud_schedule"]["handler"]({
        "lat_deg": 39.9, "lon_deg": 116.4, "alt_m": 50.0,
        "min_elevation_deg": 10.0,
        "transmitters": [{
            "sat_name": ISS_NAME, "line1": ISS_L1, "line2": ISS_L2,
            "transmitter_id": "iss", "frequency_hz": 145.9e6,
        }],
        "start_unix_s": (TLEParser.parse(ISS_NAME, ISS_L1, ISS_L2).epoch_jd
                         - 2440587.5) * 86400.0,
        "horizon_hours": 24.0,
    })
    check("r2cloud_schedule 工具返回 success", out.success,
          f"(err={out.error})")
    check("r2cloud_schedule 工具产出任务", out.data["task_count"] > 0)


if __name__ == "__main__":
    test_r2cloud_scheduler()
    test_satnogs_doppler()
    test_ogn_flarm()
    test_firedetector()
    test_remotecontrol()
    test_registry_integration()
    print(f"\n==== 结果: {PASS} passed, {FAIL} failed ====")
    sys.exit(1 if FAIL else 0)
