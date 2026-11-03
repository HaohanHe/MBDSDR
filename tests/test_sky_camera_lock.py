# SPDX-License-Identifier: MIT
"""
tests/test_sky_camera_lock.py
=============================
A1 摄像机概念与「卫星锁定」坐标系（参照 Stellarium
StelMovementMgr::flagTracking 语义）+ B2 TLE 来源/历元透出。

机制核对（与 reference/stellarium/src/core/StelMovementMgr.cpp 对齐）：
  - 锁定开启要求目标可解算（setFlagTracking 要求有选中对象）；
  - 锁定期视图中心每帧贴住目标当前 az/el（updateMotion 每帧重瞄）；
  - 用户手动运动（拖拽/触屏/双击/重置）立即解锁（用户永远接管）；
  - 选中切换 → 锁定跟着选中走；反选/选中非卫星 → 解锁；
  - 目标落地/数据失效 → 诚实解锁并提示（不停在无意义角度假跟踪）。

坐标链真实性：锁定重算与卫星绘制同链（真实 TLE + sgp4，时间引擎
固定时刻定锚）；运动学用例用可控替身隔离时序，不许生产代码造假。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_camera_lock.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView, SkyObject  # noqa: E402
from mbdsdr_ai.decoders import BUILTIN_TLE  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai import orbit  # noqa: E402

# 长春（用户所在地，真实坐标作为测试夹具）
LAT, LON = 43.9, 125.3
ISS = "ISS (ZARYA)"
NOAA19 = "NOAA 19"
LINE1_ISS, LINE2_ISS = BUILTIN_TLE[ISS]
LINE1_N19, LINE2_N19 = BUILTIN_TLE[NOAA19]

_BASE_UNIX = datetime(2026, 9, 27, 12, 0,
                      tzinfo=timezone.utc).timestamp()


def _jd(unix_s: float) -> float:
    return unix_s / 86400.0 + 2440587.5


def _azalt_at(name: str, line1: str, line2: str, unix_s: float):
    """与锁定重算同链：真实 TLE + sgp4，固定时刻。"""
    from sgp4.api import Satrec
    sat = Satrec.twoline2rv(line1, line2)
    st = orbit._state_from_satrec(sat, name, _jd(unix_s), LAT, LON, 0.0)
    if st is None:
        return None
    return float(st["azimuth"]), float(st["elevation"])


def _unix_with_iss_above(min_el: float = 15.0) -> float:
    """确定性搜索：从固定基准时刻起找 ISS 仰角 > min_el 的时刻。"""
    for k in range(0, 24 * 12):          # 24h 内 5 分钟步进
        t = _BASE_UNIX + k * 300.0
        res = _azalt_at(ISS, LINE1_ISS, LINE2_ISS, t)
        if res is not None and res[1] > min_el:
            return t
    raise RuntimeError("24h 内未找到 ISS 高仰角时刻（测试锚定失败）")


def _catalog() -> list:
    """与 SatelliteTracker 喂入同构：目录条目 list（真实 TLE 夹具）。"""
    return [
        {"name": ISS, "line1": LINE1_ISS, "line2": LINE2_ISS,
         "freq_mhz": 145.8, "kind": "amateur",
         "tle_source": "Celestrak CATNR 25544（测试夹具）",
         "tle_epoch": orbit.tle_epoch_iso(LINE1_ISS)},
        {"name": NOAA19, "line1": LINE1_N19, "line2": LINE2_N19,
         "freq_mhz": 137.1, "kind": "weather",
         "tle_source": "Celestrak CATNR 33591（测试夹具）",
         "tle_epoch": orbit.tle_epoch_iso(LINE1_N19)},
    ]


def _new_view() -> RFSkyView:
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(LAT, LON)
    v.set_satellite_catalog(_catalog())
    return v


def _sat_obj(name: str, az: float = 100.0, el: float = 45.0) -> SkyObject:
    return SkyObject(name=name, azimuth_deg=az, elevation_deg=el,
                     obj_type="satellite", frequency_hz=145.8e6)


# ---------------------------------------------------------------------------
# 锁定语义
# ---------------------------------------------------------------------------
class TestLockEngage:
    def test_lock_requires_solvable_target(self):
        v = _new_view()
        assert v.set_camera_lock("不存在的星") is False
        assert v.camera_lock_target is None

    def test_lock_requires_observer(self):
        v = RFSkyView()
        v.set_satellite_catalog(_catalog())
        assert v._observer is None
        assert v.set_camera_lock(ISS) is False

    def test_lock_engages_on_real_chain(self):
        v = _new_view()
        assert v.set_camera_lock(ISS) is True
        assert v.camera_lock_target == ISS
        assert v._camera_btn.isChecked() is True


class TestLockFollowKinematics:
    def test_view_converges_to_target_no_steady_state_error(self, monkeypatch):
        """指数趋近（k=0.25/tick）：收敛后无稳态误差；目标移动再跟随。"""
        v = _new_view()
        pos = {"p": (100.0, 45.0)}
        monkeypatch.setattr(v, "_lock_target_azalt",
                            lambda _name: pos["p"])
        assert v.set_camera_lock(ISS) is True
        v.view_state.look_at(0.0, 90.0)
        for _ in range(120):                       # 120 tick ≈ 4s
            v._apply_camera_lock()
        assert abs(v.view_state.center_az - 100.0) < 1e-3
        assert abs(v.view_state.center_alt - 45.0) < 1e-3
        # 目标移动（新解算位置）→ 视图继续跟随（星空反向流动之源）
        pos["p"] = (110.0, 50.0)
        for _ in range(120):
            v._apply_camera_lock()
        assert abs(v.view_state.center_az - 110.0) < 1e-3
        assert abs(v.view_state.center_alt - 50.0) < 1e-3

    def test_azimuth_wrap_shortest_path(self, monkeypatch):
        """方位角跨 0/360 按最短路径（不绕远路）。"""
        v = _new_view()
        monkeypatch.setattr(v, "_lock_target_azalt",
                            lambda _name: (359.0, 45.0))
        assert v.set_camera_lock(ISS) is True
        v.view_state.look_at(1.0, 45.0)
        for _ in range(120):
            v._apply_camera_lock()
        # 收敛到 359 附近（允许 ±360 折叠等价位）
        err = abs(((v.view_state.center_az - 359.0 + 540.0) % 360.0) - 180.0)
        assert err < 1e-3

    def test_tick_drives_lock(self, monkeypatch):
        """33ms 节拍统一驱动：_on_refresh_tick 内部走 _apply_camera_lock。"""
        v = _new_view()
        monkeypatch.setattr(v, "_lock_target_azalt",
                            lambda _name: (200.0, 30.0))
        assert v.set_camera_lock(ISS) is True
        v.view_state.look_at(0.0, 90.0)
        v._on_refresh_tick()
        assert v.view_state.center_az != 0.0 or \
            v.view_state.center_alt != 90.0


class TestUnlockPaths:
    def test_unlock_on_drag(self):
        v = _new_view()
        assert v.set_camera_lock(ISS) is True
        v.on_drag_state_changed(True)
        assert v.camera_lock_target is None
        assert v._camera_btn.isChecked() is False

    def test_unlock_on_double_click(self):
        v = _new_view()
        assert v.set_camera_lock(ISS) is True
        ev = QMouseEvent(QMouseEvent.MouseButtonDblClick,
                         QPointF(350, 350), Qt.LeftButton,
                         Qt.LeftButton, Qt.NoModifier)
        v.mouseDoubleClickEvent(ev)
        assert v.camera_lock_target is None

    def test_unlock_on_reset_view(self):
        v = _new_view()
        assert v.set_camera_lock(ISS) is True
        v.reset_view()
        assert v.camera_lock_target is None
        # 天顶奇点守卫：look_at(0,90) 会夹到 90-1e-4（既有行为，不属本批改动）
        assert abs(v.view_state.center_alt - 90.0) < 1e-3

    def test_unlock_on_deselect(self):
        v = _new_view()
        assert v.set_camera_lock(ISS) is True
        v.on_object_picked(None)
        assert v.camera_lock_target is None
        assert v._camera_btn.isVisibleTo(v) is False

    def test_zoom_keeps_lock(self):
        """缩放不解锁（Stellarium 缩放下保持 tracking）。"""
        v = _new_view()
        assert v.set_camera_lock(ISS) is True
        v.view_state.zoom(1.0)
        assert v.camera_lock_target == ISS


class TestHonestExit:
    def test_exit_below_horizon(self, monkeypatch):
        v = _new_view()
        monkeypatch.setattr(v, "_lock_target_azalt",
                            lambda _name: (10.0, -5.0))
        v._camera_lock = ISS
        v._apply_camera_lock()
        assert v.camera_lock_target is None
        assert "地平线下" in v._camera_notice

    def test_exit_on_data_loss(self, monkeypatch):
        v = _new_view()
        monkeypatch.setattr(v, "_lock_target_azalt",
                            lambda _name: None)
        v._camera_lock = ISS
        v._apply_camera_lock()
        assert v.camera_lock_target is None
        assert "数据失效" in v._camera_notice
        # 画面停在最后位置，但锁定 HUD 已撤（不假跟踪）
        assert v._camera_btn.isChecked() is False


class TestSelectionBinding:
    def test_retarget_on_pick_another_sat(self):
        """锁着 A 星时选中 B 星 → 锁定换绑到 B（Stellarium 选中语义）。"""
        v = _new_view()
        assert v.set_camera_lock(ISS) is True
        v.on_object_picked(_sat_obj(NOAA19, 120.0, 30.0))
        assert v.camera_lock_target == NOAA19

    def test_unlock_on_pick_body(self):
        """选中行星/恒星（非卫星）→ 解锁。"""
        v = _new_view()
        assert v.set_camera_lock(ISS) is True
        v.on_body_picked({"kind": "planet", "key": "mars"})
        assert v.camera_lock_target is None

    def test_button_toggle_roundtrip(self):
        """按钮开关：on → 锁定当前选中；off → 解锁。"""
        v = _new_view()
        v._tune_sat_name = ISS
        v._camera_btn.setChecked(True)
        assert v.camera_lock_target == ISS
        v._camera_btn.setChecked(False)
        assert v.camera_lock_target is None

    def test_button_toggle_without_selection_fails_honest(self):
        """无选中星按锁定 → 按钮复位 + 诚实提示，不残留假锁定态。"""
        v = _new_view()
        v._tune_sat_name = None
        v._camera_btn.setChecked(True)
        assert v.camera_lock_target is None
        assert v._camera_btn.isChecked() is False
        assert "无法锁定" in v._camera_notice


# ---------------------------------------------------------------------------
# 真实链集成：固定时刻 ISS 高仰角，锁定后视图贴到真解算位置
# ---------------------------------------------------------------------------
class TestRealChainIntegration:
    def test_lock_tracks_real_sgp4_position(self):
        t = _unix_with_iss_above(15.0)
        eng = get_time_engine()
        eng.set_unix(t)
        eng.pause()
        try:
            v = _new_view()
            assert v.set_camera_lock(ISS) is True
            for _ in range(150):
                v._apply_camera_lock()
            az, el = _azalt_at(ISS, LINE1_ISS, LINE2_ISS, t)
            err_az = abs(((v.view_state.center_az - az + 540.0) % 360.0)
                         - 180.0)
            err_alt = abs(v.view_state.center_alt - el)
            assert err_az < 1e-3 and err_alt < 1e-3
            # 渲染冒烟：锁定 HUD + 全链 paint 不崩
            v.show()
            app.processEvents()
            v.repaint()
            img = v.grab().toImage()
            assert not img.isNull()
        finally:
            get_time_engine().set_time_now()


# ---------------------------------------------------------------------------
# B2：TLE 来源/历元透出
# ---------------------------------------------------------------------------
class TestTleTraceability:
    def test_tle_epoch_iso(self):
        iso = orbit.tle_epoch_iso(LINE1_ISS)
        assert iso.startswith("2026-") and "UTC" in iso
        # 坏输入诚实空串（不编造历元）
        assert orbit.tle_epoch_iso("garbage") == ""
        assert orbit.tle_epoch_iso("1 ") == ""

    def test_info_card_carries_tle_fields(self):
        v = _new_view()
        v.on_object_picked(_sat_obj(ISS))
        info = v._selected_info or {}
        assert info.get("tle_epoch", "").startswith("2026-")
        assert "Celestrak" in (info.get("tle_source") or "")
        # 卡片渲染冒烟（TLE 行进 paint 不崩）
        v.show()
        app.processEvents()
        v.repaint()

    def test_get_satellite_pass_has_tle_fields(self, monkeypatch,
                                               tmp_path):
        """PassInfo 透出 tle_epoch/tle_source（B2 工具面）。"""
        import types
        from mbdsdr_ai.core.controller import SDRController
        from mbdsdr_ai import sat_passes

        class _FakeT:
            def __init__(self, ts):
                self._ts = ts

            def utc_datetime(self):
                return datetime.fromtimestamp(self._ts, tz=timezone.utc)

        fake_pass = types.SimpleNamespace(
            sat_name=ISS,
            rise_time=_FakeT(_BASE_UNIX),
            set_time=_FakeT(_BASE_UNIX + 600),
            max_alt_time=_FakeT(_BASE_UNIX + 300),
            max_alt=42.0, duration=600.0, rise_az=10.0, set_az=180.0,
        )
        monkeypatch.setattr(sat_passes, "predict_passes",
                            lambda *a, **k: [fake_pass])
        ctrl = SDRController(backend=None, config_dir=str(tmp_path))
        out = ctrl.get_satellite_pass(25544)
        assert "error" not in out
        assert out["tle_epoch"].startswith("2026-")
        assert "builtin" in out["tle_source"]
