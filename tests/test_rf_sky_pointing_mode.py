# SPDX-License-Identifier: MIT
"""桌面 AR 指向模式接线（Task 55）的离屏契约。

守四件事：

1. 默认关闭：不生成 HUD，paintEvent 前后 view_state 与既有交互不变；
2. 开启 + 位姿：视图跟随，HUD 指令按级别生成；
3. 用户手动接管（L4）：后续位姿更新不再覆盖视角；
4. 无位姿：只显示「还没对准」，不画假目标。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402
from mbdsdr_ai import sky_calibration  # noqa: E402

POSE = {"valid": True, "az_deg": 33.0, "alt_deg": 52.0,
        "roll_deg": 0.0, "fov_deg": 2.3}
POSE_B = {"valid": True, "az_deg": 200.0, "alt_deg": 10.0,
          "roll_deg": 0.0, "fov_deg": 2.3}
TARGETS = [
    {"name": "角宿一", "az_deg": 33.2, "alt_deg": 52.1, "vmag": 1.0},
    {"name": "试验卫星", "az_deg": 32.8, "alt_deg": 51.9, "vmag": 3.5,
     "kind": "satellite"},
]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _view():
    from rf_sky_view import RFSkyView
    v = RFSkyView()
    v.resize(800, 600)
    return v


def _snapshot(v):
    return (float(v.view_state.center_az), float(v.view_state.center_alt),
            float(v.view_state.fov_deg), float(v.view_state.rotation))


# 固定历元与站址：只用于「准星自动识别」的确定性断言。
FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
FIXED_JD = _jtime.unix_to_jd(FIXED_UNIX)
OBS_LAT, OBS_LON = 45.0, 120.0


def _visible_named_star():
    """固定时刻/站址下，按星等从亮到暗找一个地平高度 > 30° 的专名星。"""
    from mbdsdr_ai.celestial_geometry import (
        GroundStation, azalt_from_vec, j2000_to_altaz, vec_from_radec)
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    cat = get_catalog()
    gs = GroundStation(OBS_LON, OBS_LAT, 0.0, "test")
    order = sorted(range(len(cat.vmag)), key=lambda i: float(cat.vmag[i]))
    for i in order[:400]:
        name = str(cat.name[i]).strip()
        if not name:
            continue
        az, alt = azalt_from_vec(j2000_to_altaz(
            vec_from_radec(float(cat.ra_deg[i]), float(cat.dec_deg[i])),
            gs, FIXED_JD))
        if float(alt) > 30.0:
            return name, float(az), float(alt)
    pytest.skip("固定时刻无可见亮星，环境星表异常")


def test_default_off_does_not_change_paint_or_interaction(qapp):
    v = _view()
    try:
        v.view_state.look_at(10.0, 20.0)
        v.view_state.zoom_to(60.0)
        before = _snapshot(v)

        assert v.sky_pointing_mode is False
        assert v.sky_hud is None
        # 关闭状态下喂位姿必须原样返回 False，且不产生 HUD。
        assert v.update_sky_pose(POSE, TARGETS, "L3") is False
        assert v.sky_hud is None

        v.grab()                     # 触发 paintEvent
        assert _snapshot(v) == before
        assert v.sky_hud is None

        # 既有交互仍按原语义工作（没有指向模式钩子介入）。
        v.view_state.look_at(100.0, 30.0)
        assert v.view_state.center_az == pytest.approx(100.0)
        v.view_state.zoom(1.0)
        assert v.view_state.fov_deg < 60.0
        v.view_state.pan(5.0, -2.0)
        assert v.view_state.center_az == pytest.approx(95.0)
    finally:
        v.close()


def test_enable_then_pose_follows_and_builds_hud(qapp):
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        assert v.sky_pointing_mode is True
        # 刚开启、还没有位姿：只有准星 +「还没对准」，不画假目标。
        hud = v.sky_hud
        assert hud is not None and hud["hint"] == "还没对准"
        assert hud["labels"] == []

        assert v.update_sky_pose(POSE, TARGETS, "L3") is True
        assert v.view_state.center_az == pytest.approx(33.0)
        assert v.view_state.center_alt == pytest.approx(52.0)
        assert v.view_state.fov_deg == pytest.approx(2.3)

        hud = v.sky_hud
        assert hud["level"] == "L3"
        assert hud["hint"] == "已锁定"
        assert hud["labels"], "L3 应生成精确标签"
        assert "角宿一" in [lab["name"] for lab in hud["labels"]]
        assert [m["name"] for m in hud["satellite_markers"]] == ["试验卫星"]
        assert hud["crosshair"]["x"] == pytest.approx(400.0)
        assert hud["crosshair"]["y"] == pytest.approx(300.0)

        v.grab()                     # HUD 绘制路径不崩
    finally:
        v.close()


def test_manual_override_blocks_later_pose(qapp):
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        v.update_sky_pose(POSE, TARGETS, "L3")
        v.set_manual_override(True)
        assert v.sky_manual_override is True
        assert v.sky_hud["level"] == "L4"
        assert v.sky_hud["hint"] == "手动"
        assert v.sky_hud["manual"] is True

        before = (float(v.view_state.center_az),
                  float(v.view_state.center_alt))
        assert v.update_sky_pose(POSE_B, TARGETS, "L3") is False
        after = (float(v.view_state.center_az),
                 float(v.view_state.center_alt))
        assert after == before, "L4 后位姿不得覆盖用户视角"
        assert v.sky_hud["level"] == "L4"

        # 放开手动接管后，位姿恢复驱动。
        v.set_manual_override(False)
        assert v.update_sky_pose(POSE_B, TARGETS, "L3") is True
        assert v.view_state.center_az == pytest.approx(200.0)
        assert v.view_state.center_alt == pytest.approx(10.0)
    finally:
        v.close()


def test_wheel_auto_enters_manual_l4(qapp):
    """用户滚轮微调后自动进入 L4，后续位姿不再把视角拽走。"""
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        v.update_sky_pose(POSE, TARGETS, "L3")
        assert v.sky_manual_override is False
        ev = QWheelEvent(QPointF(400.0, 200.0),
                         v.mapToGlobal(QPointF(400.0, 200.0)),
                         QPoint(0, 0), QPoint(0, 120),
                         Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        v.wheelEvent(ev)
        assert v.sky_manual_override is True
        assert v.sky_hud["level"] == "L4"
        assert v.sky_hud["hint"] == "手动"

        before = (float(v.view_state.center_az),
                  float(v.view_state.center_alt))
        assert v.update_sky_pose(POSE_B, TARGETS, "L3") is False
        assert (float(v.view_state.center_az),
                float(v.view_state.center_alt)) == before
    finally:
        v.close()


def test_no_pose_shows_not_aligned_and_no_fake_targets(qapp):
    v = _view()
    try:
        start = _snapshot(v)
        v.set_sky_pointing_mode(True)
        v.update_sky_pose({}, TARGETS, "L3")
        hud = v.sky_hud
        assert hud["level"] == "L0"
        assert hud["hint"] == "还没对准"
        assert hud["labels"] == []
        assert hud["satellite_markers"] == []
        assert _snapshot(v) == start, "无位姿不得改动视图"
        v.grab()
    finally:
        v.close()


def test_mode_off_never_auto_identifies(qapp):
    """关闭指向模式时 targets=None 仍是「保留上一帧」，绝不自动识别。"""
    v = _view()
    try:
        v._observer = {"lat": OBS_LAT, "lon": OBS_LON, "alt_m": 0.0}
        assert v.sky_pointing_mode is False
        assert v.update_sky_pose(POSE, None, "L1") is False
        assert v._sky_targets == []
        # 识别器一次都没跑过（成功会写缓存）。
        assert v._sky_identify_cache is None
    finally:
        v.close()


def test_auto_identify_at_crosshair_when_targets_none(qapp):
    """开启 + 有站址：targets=None 时按准星识别出已知亮星。"""
    star_name, star_az, star_alt = _visible_named_star()
    eng = get_time_engine()
    old_rate = eng.get_rate()
    v = _view()
    try:
        eng.set_unix(FIXED_UNIX)
        eng.pause()
        v._observer = {"lat": OBS_LAT, "lon": OBS_LON, "alt_m": 0.0}
        v.set_sky_pointing_mode(True)
        pose = {"valid": True, "az_deg": star_az, "alt_deg": star_alt,
                "roll_deg": 0.0, "fov_deg": 3.0}
        assert v.update_sky_pose(pose, None, "L1") is True

        names = [t.get("name") for t in v._sky_targets]
        assert names, "准星方向应识别出至少一个目标"
        assert star_name in names
        assert v._sky_identify_error == ""
        # 同键同量化参数的第二帧直接复用缓存（只存最近一帧）。
        cached = v._sky_targets
        v.update_sky_pose(pose, None, "L1")
        assert v._sky_targets == cached
    finally:
        v.close()
        eng.set_time_now()
        eng.resume(old_rate)


def test_auto_identify_includes_satellite_at_crosshair(qapp, monkeypatch):
    """卫星目标也进准星识别；显式喂 targets 时仍不被覆盖。"""
    from mbdsdr_ai import sky_satellites

    def _fake_visible(*_args, **_kwargs):
        return [{"satellite": "准星测试卫星", "azimuth": 33.0, "elevation": 52.0,
                 "range_km": 550.0, "range_rate_kms": -1.2,
                 "altitude_km": 420.0, "sunlit": True}]

    monkeypatch.setattr(sky_satellites, "visible_satellites", _fake_visible)

    eng = get_time_engine()
    old_rate = eng.get_rate()
    v = _view()
    try:
        eng.set_unix(FIXED_UNIX)
        eng.pause()
        v._observer = {"lat": OBS_LAT, "lon": OBS_LON, "alt_m": 0.0}
        v.set_sky_pointing_mode(True)
        pose = {"valid": True, "az_deg": 33.0, "alt_deg": 52.0,
                "roll_deg": 0.0, "fov_deg": 3.0}
        assert v.update_sky_pose(pose, None, "L1") is True

        names = [t.get("name") for t in v._sky_targets]
        kinds = [t.get("kind") for t in v._sky_targets]
        assert "准星测试卫星" in names
        assert "satellite" in kinds
        sat = next(t for t in v._sky_targets if t.get("kind") == "satellite")
        assert sat["angular_distance_deg"] < 0.01

        # 显式喂 targets 时行为不变，不被卫星/恒星自动识别覆盖。
        assert v.update_sky_pose(POSE, TARGETS, "L3") is True
        assert v._sky_targets == TARGETS
    finally:
        v.close()
        eng.set_time_now()
        eng.resume(old_rate)


def test_auto_identify_without_observer_is_honestly_empty(qapp):
    """没有站址：不猜站址/时刻，目标为空且 HUD 不画标签。"""
    v = _view()
    try:
        assert v._observer is None
        v.set_sky_pointing_mode(True)
        v.update_sky_pose(POSE, None, "L1")

        assert v._sky_targets == []
        assert v.sky_hud["labels"] == []
        assert v.sky_hud["satellite_markers"] == []
        assert v._sky_identify_cache is None
    finally:
        v.close()


def test_explicit_targets_win_over_auto_identify(qapp):
    """显式喂 targets 时行为不变，不被准星自动识别覆盖。"""
    eng = get_time_engine()
    old_rate = eng.get_rate()
    v = _view()
    try:
        eng.set_unix(FIXED_UNIX)
        eng.pause()
        v._observer = {"lat": OBS_LAT, "lon": OBS_LON, "alt_m": 0.0}
        v.set_sky_pointing_mode(True)
        assert v.update_sky_pose(POSE, TARGETS, "L3") is True

        assert v._sky_targets == TARGETS
        assert v._sky_identify_cache is None
        assert v.sky_hud["hint"] == "已锁定"
    finally:
        v.close()
        eng.set_time_now()
        eng.resume(old_rate)


def test_hud_narrative_is_plain_language(qapp):
    """开启 + 位姿 + 目标：HUD 自带一句不含工程术语的人话。"""
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        assert v.update_sky_pose(POSE, TARGETS, "L3") is True
        text = v._sky_hud["narrative"]
        assert text
        for term in ("az", "alt", "vmag", "GMST", "FOV"):
            assert term.lower() not in text.lower(), (text, term)
        v.grab()
    finally:
        v.close()


def test_hud_narrative_without_pose_is_not_aligned(qapp):
    """开启但无位姿：口语文本只说「还没对准」，旧键 hint 不变。"""
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        v.update_sky_pose({}, TARGETS, "L3")
        hud = v._sky_hud
        assert hud["narrative"] == "还没对准，先让设备看到星空"
        assert hud["hint"] == "还没对准"
        v.grab()
    finally:
        v.close()


# ---------------------------------------------------------------------------
# Task 5.3：桌面加载校准档案；界面只显示「已校准（日期）」
# ---------------------------------------------------------------------------
def _calibration_profile(**overrides):
    profile = {
        "schema_version": 1,
        "device_id": "示例设备",
        "fov_deg": 2.3,
        "boresight_az_deg": 1.0,
        "boresight_alt_deg": 0.5,
        "roll_zero_deg": 20.0,
        "created_utc": "2026-11-17T00:00:00Z",
        "verified_utc": "2026-11-20T12:34:56Z",
        "source": "two_star",
    }
    profile.update(overrides)
    return profile


def _saved_profile(tmp_path, **overrides):
    """用正式落盘入口在 tmp_path 造一份合法档案，返回路径。"""
    path = tmp_path / "sky_calibration.json"
    saved = sky_calibration.save_profile(_calibration_profile(**overrides),
                                         str(path))
    assert saved["ok"] is True, saved
    return str(path)


CALIBRATION_POSE = {"valid": True, "az_deg": 33.0, "alt_deg": 52.0,
                    "roll_deg": 0.0, "fov_deg": 8.0}


def test_load_sky_calibration_shows_date_only(qapp, tmp_path):
    """合法档案：加载成功，文案只含日期（取档案 verified_utc）。"""
    path = _saved_profile(tmp_path)
    v = _view()
    try:
        # 构造期不读盘：未显式加载时没有文案。
        assert v.sky_calibration_note == ""
        assert v.sky_calibration_error == ""
        assert v.load_sky_calibration(path) is True
        assert v.sky_calibration_note == "已校准（2026-11-20）"
        assert v.sky_calibration_error == ""
        # 绝不显示偏置数值。
        for value in ("1.0", "0.5", "20.0", "boresight"):
            assert value not in v.sky_calibration_note
        v.grab()
    finally:
        v.close()


def test_load_sky_calibration_default_path_follows_settings_dir(
        qapp, tmp_path, monkeypatch):
    """path=None：默认与 desktop_settings.json 同目录；环境变量可整条覆盖。"""
    import settings as settings_mod
    from rf_sky_view import default_sky_calibration_path

    default = default_sky_calibration_path()
    assert os.path.dirname(default) == os.path.dirname(
        settings_mod.default_config_path())
    assert os.path.basename(default) == "sky_calibration.json"

    # 该目录已被 conftest 隔离到临时目录，不会碰用户真实配置；但它是
    # 整个会话共享的，用完必须删掉——从 Task 5.4 起开启指向模式会自动读
    # 默认档案，留下的档案会污染后续用例的「未校准」基线。
    saved = sky_calibration.save_profile(_calibration_profile(), default)
    assert saved["ok"] is True, saved
    try:
        v = _view()
        try:
            assert v.load_sky_calibration() is True, v.sky_calibration_error
            assert v.sky_calibration_note == "已校准（2026-11-20）"
        finally:
            v.close()
    finally:
        os.remove(default)

    # 环境变量整条覆盖默认路径。
    override = tmp_path / "elsewhere.json"
    saved = sky_calibration.save_profile(
        _calibration_profile(verified_utc="2026-11-21T08:00:00Z"),
        str(override))
    assert saved["ok"] is True, saved
    monkeypatch.setenv("MBDSDR_SKY_CALIBRATION_PATH", str(override))
    v = _view()
    try:
        assert v.load_sky_calibration() is True, v.sky_calibration_error
        assert v.sky_calibration_note == "已校准（2026-11-21）"
    finally:
        v.close()


def test_load_sky_calibration_missing_or_corrupt_is_uncalibrated(
        qapp, tmp_path):
    """文件不存在 / JSON 损坏：返回 False、无文案、错误含中文原因。"""
    missing = str(tmp_path / "not_there.json")
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{ this is not json", encoding="utf-8")

    for path, reason_word in ((missing, "未找到"),
                              (str(corrupt), "损坏")):
        v = _view()
        try:
            assert v.load_sky_calibration(path) is False
            assert v.sky_calibration_note == ""
            assert reason_word in v.sky_calibration_error

            v.set_sky_pointing_mode(True)
            assert v.update_sky_pose(CALIBRATION_POSE, TARGETS, "L3") is True
            # 不静默采用半套偏置：视图就是未校准的位姿。
            assert v.view_state.center_az == pytest.approx(33.0)
            assert v.view_state.center_alt == pytest.approx(52.0)
            assert v.view_state.fov_deg == pytest.approx(8.0)
            assert v.sky_calibration_note == ""
            # 加载失败的诊断不被普通位姿帧抹掉。
            assert reason_word in v.sky_calibration_error
            v.grab()
        finally:
            v.close()


def test_loaded_profile_offsets_apply_and_explicit_calibration_wins(
        qapp, tmp_path):
    """未显式传 calibration 时用已加载档案；显式传参优先于它。"""
    path = _saved_profile(tmp_path)
    v = _view()
    try:
        assert v.load_sky_calibration(path) is True
        v.set_sky_pointing_mode(True)
        assert v.update_sky_pose(CALIBRATION_POSE, TARGETS, "L3") is True
        # 档案：az +1.0 / alt +0.5 / roll +20 / fov 2.3。
        assert v.view_state.center_az == pytest.approx(34.0)
        assert v.view_state.center_alt == pytest.approx(52.5)
        assert v.view_state.fov_deg == pytest.approx(2.3)
        assert v.view_state.rotation == pytest.approx(20.0)

        override = _calibration_profile(
            boresight_az_deg=-2.0, boresight_alt_deg=0.0,
            roll_zero_deg=0.0, fov_deg=5.0)
        assert v.update_sky_pose(CALIBRATION_POSE, TARGETS, "L3",
                                 calibration=override) is True
        assert v.view_state.center_az == pytest.approx(31.0)
        assert v.view_state.center_alt == pytest.approx(52.0)
        assert v.view_state.fov_deg == pytest.approx(5.0)
        assert v.view_state.rotation == pytest.approx(0.0)
        v.grab()
    finally:
        v.close()


def test_hud_never_leaks_calibration_offsets(qapp, tmp_path):
    """校准文案之外，HUD 的 hint/narrative/labels 不出现偏置数值。"""
    path = _saved_profile(tmp_path)
    v = _view()
    try:
        assert v.load_sky_calibration(path) is True
        v.set_sky_pointing_mode(True)
        assert v.update_sky_pose(CALIBRATION_POSE, TARGETS, "L3") is True

        hud = v.sky_hud
        assert v.sky_calibration_note == "已校准（2026-11-20）"
        texts = [hud["hint"], hud["narrative"]]
        texts += [str(lab.get("text") or "") for lab in hud["labels"]]
        for text in texts:
            for term in ("boresight", "roll_zero", "偏置", "校准"):
                assert term not in text.lower()
            # 校准文案只出现在 widget 的专用属性里，不混进 HUD 文案。
            assert v.sky_calibration_note not in text
        # HUD 指令里没有塞入偏置字段/数值的独立键。
        assert not any("boresight" in str(key) or "roll_zero" in str(key)
                       or "calibration" in str(key) for key in hud)
        v.grab()
    finally:
        v.close()


# ---------------------------------------------------------------------------
# Task 5.4：指向模式开启时自动读取默认校准档案（每段开启只读一次）
# ---------------------------------------------------------------------------
def _use_default_calibration_path(monkeypatch, path):
    """把默认档案路径重定向到 tmp_path（不碰用户真实配置目录）。"""
    monkeypatch.setenv("MBDSDR_SKY_CALIBRATION_PATH", str(path))
    return path


def test_pointing_mode_autoloads_default_calibration(qapp, tmp_path,
                                                     monkeypatch):
    """默认路径有合法档案：开启即显示日期，HUD 正常，且不每帧读盘。"""
    import rf_sky_view as rf_mod

    default = _use_default_calibration_path(
        monkeypatch, tmp_path / "sky_calibration.json")
    saved = sky_calibration.save_profile(_calibration_profile(), str(default))
    assert saved["ok"] is True, saved

    calls = []
    real_load = rf_mod.sky_load_calibration_profile

    def _counting_load(path):
        calls.append(path)
        return real_load(path)

    monkeypatch.setattr(rf_mod, "sky_load_calibration_profile", _counting_load)

    v = _view()
    try:
        # 构造期不读盘；开启这一刻才读一次默认档案。
        assert v.sky_calibration_note == ""
        assert calls == []
        v.set_sky_pointing_mode(True)
        assert calls == [str(default)]
        assert v.sky_calibration_note == "已校准（2026-11-20）"
        assert v.sky_calibration_error == ""
        assert v.sky_hud is not None

        assert v.update_sky_pose(CALIBRATION_POSE, TARGETS, "L3") is True
        # 档案偏置已生效：az +1.0 / alt +0.5 / fov 2.3。
        assert v.view_state.center_az == pytest.approx(34.0)
        assert v.view_state.center_alt == pytest.approx(52.5)
        assert v.view_state.fov_deg == pytest.approx(2.3)
        # 连续多帧位姿 + 重绘都不再读盘。
        for _ in range(3):
            v.update_sky_pose(CALIBRATION_POSE, TARGETS, "L3")
            v.grab()
        assert calls == [str(default)]
        assert v.sky_calibration_note == "已校准（2026-11-20）"
    finally:
        v.close()


def test_pointing_mode_without_default_calibration_is_uncalibrated(
        qapp, tmp_path, monkeypatch):
    """默认路径无档案：视为未校准，不报错；关闭再开启可重新读。"""
    default = _use_default_calibration_path(
        monkeypatch, tmp_path / "not_there.json")
    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        assert v.sky_calibration_note == ""
        assert v.sky_calibration_error == ""
        assert not default.exists()          # 不写文件、不自动创建

        assert v.update_sky_pose(CALIBRATION_POSE, TARGETS, "L3") is True
        assert v.view_state.center_az == pytest.approx(33.0)
        assert v.view_state.center_alt == pytest.approx(52.0)
        assert v.view_state.fov_deg == pytest.approx(8.0)
        assert v.sky_calibration_note == ""
        assert v.sky_calibration_error == ""

        # 「关闭 → 再开启」允许重新读默认档案（用户可能刚校准完）。
        v.set_sky_pointing_mode(False)
        saved = sky_calibration.save_profile(_calibration_profile(),
                                             str(default))
        assert saved["ok"] is True, saved
        v.set_sky_pointing_mode(True)
        assert v.sky_calibration_note == "已校准（2026-11-20）"
        assert v.sky_calibration_error == ""
        v.grab()
    finally:
        v.close()


def test_pointing_mode_corrupt_default_calibration_reports_reason(
        qapp, tmp_path, monkeypatch):
    """默认档案损坏：note 空、error 含「损坏」、位姿不被静默套偏置。"""
    default = _use_default_calibration_path(
        monkeypatch, tmp_path / "sky_calibration.json")
    original = "{ this is not json"
    default.write_text(original, encoding="utf-8")

    v = _view()
    try:
        v.set_sky_pointing_mode(True)
        assert v.sky_calibration_note == ""
        assert "损坏" in v.sky_calibration_error

        assert v.update_sky_pose(CALIBRATION_POSE, TARGETS, "L3") is True
        assert v.view_state.center_az == pytest.approx(33.0)
        assert v.view_state.center_alt == pytest.approx(52.0)
        assert v.view_state.fov_deg == pytest.approx(8.0)
        assert v.sky_calibration_note == ""
        # 加载失败诊断不被普通位姿帧抹掉。
        assert "损坏" in v.sky_calibration_error
        # 不写文件、不自动修复：原文件逐字不变。
        assert default.read_text(encoding="utf-8") == original
        v.grab()
    finally:
        v.close()


def test_explicit_calibration_wins_over_default_on_pointing_mode(
        qapp, tmp_path, monkeypatch):
    """显式加载自定义档案后，开关指向模式不被默认路径覆盖。"""
    custom = tmp_path / "custom.json"
    saved = sky_calibration.save_profile(
        _calibration_profile(verified_utc="2026-11-18T00:00:00Z"),
        str(custom))
    assert saved["ok"] is True, saved

    default = _use_default_calibration_path(
        monkeypatch, tmp_path / "default" / "sky_calibration.json")
    saved = sky_calibration.save_profile(
        _calibration_profile(verified_utc="2026-11-21T00:00:00Z"),
        str(default))
    assert saved["ok"] is True, saved

    v = _view()
    try:
        assert v.load_sky_calibration(str(custom)) is True
        assert v.sky_calibration_note == "已校准（2026-11-18）"

        v.set_sky_pointing_mode(True)
        assert v.sky_calibration_note == "已校准（2026-11-18）"
        v.set_sky_pointing_mode(False)
        v.set_sky_pointing_mode(True)
        assert v.sky_calibration_note == "已校准（2026-11-18）"

        assert v.update_sky_pose(CALIBRATION_POSE, TARGETS, "L3") is True
        assert v.sky_calibration_note == "已校准（2026-11-18）"
        assert v.sky_calibration_error == ""
        v.grab()
    finally:
        v.close()


# ---------------------------------------------------------------------------
# Task 7.1：2.3° 视场下只绘制视锥内目标，标签不抽稀丢目标
# ---------------------------------------------------------------------------
def test_2_3_deg_draws_only_targets_inside_cone(qapp):
    """2.3° 目镜级视场：锥外目标不进绘制，锥内具名目标都有精确标签。"""
    v = _view()
    try:
        pose = {"valid": True, "az_deg": 100.0, "alt_deg": 30.0,
                "roll_deg": 0.0, "fov_deg": 2.3}
        # 以视图中心为原点：两个约 0.5° 的锥内目标、两个约 5° 的锥外目标。
        targets = [
            {"name": "锥内甲", "az_deg": 100.5, "alt_deg": 30.0, "vmag": 2.0},
            {"name": "锥内乙", "az_deg": 100.0, "alt_deg": 30.5, "vmag": 3.0},
            {"name": "锥外甲", "az_deg": 105.0, "alt_deg": 30.0, "vmag": 1.0},
            {"name": "锥外乙", "az_deg": 95.0, "alt_deg": 30.0, "vmag": 1.0},
        ]
        v.set_sky_pointing_mode(True)
        assert v.update_sky_pose(pose, targets, "L3") is True
        assert v.view_state.fov_deg == pytest.approx(2.3)

        hud = v.sky_hud
        assert hud["targets_total"] == 4
        assert hud["targets_visible"] == 2
        names = [lab["name"] for lab in hud["labels"]]
        assert set(names) == {"锥内甲", "锥内乙"}
        # L3 精确标签不抽稀：锥内两条都画，锥外一条都不画。
        assert len(hud["labels"]) == 2
        v.grab()
    finally:
        v.close()
