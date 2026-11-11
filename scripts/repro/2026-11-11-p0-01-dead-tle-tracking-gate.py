#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-01 独立复现脚本：dead TLE 是否仍能进入闭环跟踪（调谐下发）。

roadmap 断言（原文）：
    「过期星历无闸门，``dead`` TLE 仍可过境预报与闭环跟踪」
    证据等级：实测（probe_final.py）。本条复核的是 **desktop 闭环跟踪** 这一半。

做法（不需要 SDR、不联网）：
  1. 用 ``SatelliteTracker`` 载入一条历元为 2024 年第 1 天的 ISS TLE（dead），
     读 ``tracking_policy_for()``——确认数据层已经给出 ``allow_tune=False`` /
     ``command_allowed=False``（闸门数据存在）。
  2. 用 ``object.__new__(MainWindow)`` 造一个只提供 ``_poll_satellite_track``
     所需属性的桩，调**真实产品方法** ``MainWindow._poll_satellite_track``。
     位置字典里 ``policy`` 明确为 deny，仰角 45°（可见）。
  3. 统计 ``_on_tune_sdr`` 是否仍被调用。仍调用 = 闸门没接到下发路径。

退出码（三态）：
    0  复现成功   —— policy 为 deny，desktop 仍下发调谐
    1  未复现     —— policy 为 deny 且 desktop 不再下发
    2  无法判定   —— 数据层没有给出 deny（前置不成立）或导入失败

反证对照：同一桩上跑一份「带闸门」的等价实现（读 ``policy.allow_tune``），
必须得到 0 次下发——证明探针能区分「有闸门 / 无闸门」。判定句固定为：

    显式读 policy = 0 次 / 实际 = 1 次     ← 修前（复现）
    显式读 policy = 0 次 / 实际 = 0 次     ← 修后（未复现）

口径（D-7 方案 A）：**dead/unknown 才是 0 次；expired 是「确认后 1 次」**
（``TRACKING_MODES["expired"]["tune"] == "confirm"``，云端/云台另禁）。
本脚本用 dead TLE 作探针，不得拿 expired 当 dead 用。

用法：
    python scripts/repro/2026-11-11-p0-01-dead-tle-tracking-gate.py
    MBDSDR_REPRO_ROOT=D:\\path\\to\\snapshot python scripts/repro/...py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _dead_iss_tle():
    """取内置 ISS TLE，把历元改成 2024-01-01（dead），返回三行。"""
    from mbdsdr_ai import sat_tracker
    for block in getattr(sat_tracker, "_BUILTIN_TLES", []):
        if len(block) >= 3 and "ISS" in str(block[0]):
            l1, l2 = str(block[1]), str(block[2])
            if len(l1) >= 32:
                l1 = l1[:18] + "24001.00000000" + l1[32:]
            return [str(block[0]), l1, l2]
    # 兜底：一条已知格式的 ISS TLE（历元 2024-001）
    return [
        "ISS (ZARYA)",
        "1 25544U 98067A   24001.00000000  .00016717  00000-0  10270-3 0  9005",
        "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49512876 12345",
    ]


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-01-profile-", dir=TMP_ROOT)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    try:
        from mbdsdr_ai.sat_tracker import SatelliteTracker
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] 无法导入 SatelliteTracker: {exc!r}")
        return 2

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print("硬件    : 不需要（纯计算 + 桌面方法直调）")

    _hr("实测 A：数据层对 dead TLE 的闸门")
    tracker = SatelliteTracker(None)
    name, l1, l2 = _dead_iss_tle()
    loaded = tracker.load_tle([name, l1, l2])
    policy = tracker.tracking_policy_for(name)
    entry = next((c for c in tracker._catalog if c.get("name") == name), {})
    print(f"  TLE 历元行 : {l1}")
    print(f"  载入成功   : {loaded}")
    print(f"  tle_tier   : {entry.get('tle_tier')}（age={entry.get('tle_age_days')} 天）")
    print(f"  policy     : allow_tune={policy.get('allow_tune')} "
          f"command_allowed={policy.get('command_allowed')} "
          f"require_confirm={policy.get('require_confirm')}")
    deny = (policy.get("allow_tune") is False
            and policy.get("command_allowed") is False)
    print(f"  数据层已拒绝下发 : {deny}（期望 True，否则本条前置不成立）")
    if not deny:
        print("[无法判定] 数据层未给出 deny，无法验证 desktop 是否忽略闸门。")
        return 2

    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        import main_window as mw
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] Qt/MainWindow 导入失败：{type(exc).__name__}: {exc}")
        return 2

    _hr("实测 B：policy=deny 时 desktop 仍下发调谐（真实 _poll_satellite_track）")

    tunes = []

    class _FakeLoop:
        selected_name = name

        def current_position(self):
            return {
                "valid": True,
                "azimuth": 180.0,
                "elevation": 45.0,
                "policy": policy,          # ← 闸门数据就在位置字典里
            }

        def downlink_freq_hz(self):
            return 145.8e6

    class _Panel:
        def update_position(self, _pos):
            pass

    class _Spectrum:
        def compute_snr_db(self):
            return None

    class _Comp:
        state = types.SimpleNamespace(
            target_name=name, valid=True, doppler_hz=0.0,
            downlink_hz=145.8e6, corrected_freq_hz=145.8e6,
            enabled=False, reason="")
        def refresh(self, _pos):
            pass
        def rx_freq(self):
            return 145.8e6

    # 函数内部 ``from mbdsdr_ai.doppler_link import get_compensator``，补丁模块属性
    try:
        import mbdsdr_ai.doppler_link as dl
        dl.get_compensator = lambda: _Comp()
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] 无法补丁多普勒补偿器：{exc!r}")
        return 2

    def _make_stub(sink):
        s = types.SimpleNamespace()
        s._loop_sat_tracker = _FakeLoop()
        s.sat_track_panel = _Panel()
        s.spectrum = _Spectrum()
        s.sky_view = None
        s._gimbal_follow = False
        s._sat_track_mode = "FM"
        s._sat_signal_present = False
        s._update_doppler_chip = lambda _c: None
        s._on_tune_sdr = lambda f, m: (sink.append((f, m)), True)[1]
        return s

    stub = _make_stub(tunes)
    err = None
    try:
        mw.MainWindow._poll_satellite_track(stub)
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        err = exc
    print("  请求：policy=deny + 仰角 45° 跑一拍 _poll_satellite_track")
    print(f"  实际：_on_tune_sdr 调用 {len(tunes)} 次 {tunes[:1]}；异常={err!r}")
    print("  期望：0 次（策略 deny 不得下发）")

    _hr("反证对照：同一桩 + 显式读取 policy.allow_tune 的等价实现")
    tunes_fixed = []

    def _fixed_poll(self):
        pos = self._loop_sat_tracker.current_position()
        if not pos.get("valid"):
            return
        if not (pos.get("policy") or {}).get("allow_tune"):
            return
        if float(pos.get("elevation") or 0.0) < 5.0:
            return
        self._on_tune_sdr(145.8e6, self._sat_track_mode)

    _fixed_poll(_make_stub(tunes_fixed))
    print(f"  显式读 policy = {len(tunes_fixed)} 次 / 实际 = {len(tunes)} 次")
    print(f"  对照组可复现旧行为 : {len(tunes) > 0}")
    print(f"  对照组闸门有效     : {len(tunes_fixed) == 0}")
    if len(tunes_fixed) != 0:
        print("[无法判定] 反证对照失效（加闸门后仍下发），脚本不可用于判定。")
        return 2

    reproduced = len(tunes) > 0
    print(f"\n结论：当前实现复现 P0-01（dead TLE 仍闭环跟踪）: {reproduced}")
    if reproduced:
        print("[复现] tracking_policy 已给出 allow_tune=False，但 desktop "
              "_poll_satellite_track 完全没有读 policy，仍调用 _on_tune_sdr。")
        return 0
    print("[未复现] desktop 已在调谐前读取 policy.allow_tune。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
