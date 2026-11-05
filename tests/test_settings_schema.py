#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
设置 schema / 面板钉扎测试 (tests/test_settings_schema.py)
=========================================================

背景：用户反馈"设置里啥都没有"。本线把可配置面收敛到 config_schema.py 的
单一事实源，并由 SettingsPanel 只读 schema 渲染。本文件钉住这些红线：

  1. 默认值只有一处：settings.DEFAULTS 必须完全由 schema 派生；UI 不许
     出现第二份端口默认值（DEFAULT_RC_PORT 也取自 schema）。
  2. 校验不崩：越界夹范围、未知枚举回默认、坏类型回默认，且**给出原因**。
  3. 损坏/写失败都可见：读坏文件先备份 *.corrupt-* 且 last_error 非空；
     写盘失败 on_error 必须被调用（不许静默）。
  4. 恢复默认（单项 + 全部）后，界面值与 host 行为都回到默认。
  5. 导入导出带版本；版本不符拒绝；未知字段列出来，不静默丢弃；导出不含密钥。
  6. 改值立即生效：主题/触屏/频谱/星象仪/观测站都调用 host 既有入口。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

import config_schema  # noqa: E402
import settings as settings_mod  # noqa: E402
import settings_panel  # noqa: E402
from config_schema import (  # noqa: E402
    CONFIG_SCHEMA_VERSION, EXPOSED_ITEMS, GROUP_ORDER, SCHEMA, defaults,
    parse_import, validate_value,
)
from settings import DesktopSettings  # noqa: E402


# ---------------------------------------------------------------------------
# 假宿主：只记录调用，不碰真硬件/真网络
# ---------------------------------------------------------------------------
class _FakeGen:
    def __init__(self):
        self.fft_size = 2048
        self.window = "Nuttall"
        self.avg = 1

    def set_fft_size(self, n):
        self.fft_size = int(n)

    def set_window(self, w):
        self.window = str(w)

    def set_avg_frames(self, n):
        self.avg = int(n)


class _FakeCombo:
    def __init__(self, items):
        self._items = list(items)
        self._cur = 0

    def setCurrentText(self, text):
        if text in self._items:
            self._cur = self._items.index(text)
        self._text = text

    def itemText(self, i):
        return self._items[i] if 0 <= i < len(self._items) else ""

    def blockSignals(self, _):
        pass

    def setChecked(self, _):
        pass

    def setValue(self, _):
        pass


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *args):
        for fn in list(self._slots):
            fn(*args)


class _FakeSlider:
    def __init__(self):
        self.value = -50
        self.blocked = False

    def blockSignals(self, flag):
        self.blocked = bool(flag)

    def setValue(self, value):
        self.value = int(value)


class _FakeStepCombo:
    VALUES = (10, 100, 1000, 10000, 100000, 1000000)

    def __init__(self):
        self.index = 3
        self.blocked = False

    def blockSignals(self, flag):
        self.blocked = bool(flag)

    def findData(self, value):
        try:
            return list(self.VALUES).index(int(value))
        except ValueError:
            return -1

    def setCurrentIndex(self, idx):
        self.index = int(idx)


class _FakeCheckbox:
    def __init__(self, checked=True):
        self._checked = bool(checked)

    def isChecked(self):
        return self._checked

    def setChecked(self, v):
        self._checked = bool(v)


class _FakeLabel:
    def __init__(self):
        self.text_value = ""

    def setText(self, text):
        self.text_value = str(text)


class _FakeControlPanel:
    def __init__(self):
        self.squelch_slider = _FakeSlider()
        self.squelch_val = _FakeLabel()
        self.auto_squelch_check = _FakeCheckbox(True)
        self.step_combo = _FakeStepCombo()
        self.squelch_changed = _FakeSignal()
        self.step_changed = _FakeSignal()


class _FakeSpectrum:
    def __init__(self):
        self.generator = _FakeGen()
        self.snap_interval = 10000.0
        self._state = type("S", (), {
            "show_waterfall": True, "show_peaks": True,
            "show_bookmarks": True, "show_bandplan": True,
            "wf_smooth": False, "peak_rel_db": 6.0,
        })()
        self._fft_combo = _FakeCombo(["1024", "2048", "4096", "8192", "16384"])
        self._win_combo = _FakeCombo(
            ["Nuttall", "Hann", "Hamming", "Blackman", "Flattop", "None"])
        self._avg_combo = _FakeCombo(["1", "4", "8", "16"])
        self._palette_combo = _FakeCombo(["默认", "经典 SDR++"])
        self._peak_spin = _FakeCombo([])
        self._wf_smooth_chk = _FakeCombo([])
        self._peak_chk = _FakeCombo([])
        self._bookmark_chk = _FakeCombo([])
        self._bandplan_chk = _FakeCombo([])
        self.palette_idx = 0
        self.redraws = 0

    def toggle_waterfall(self):
        self._state.show_waterfall = not self._state.show_waterfall

    def _on_palette_changed(self, idx):
        self.palette_idx = int(idx)

    def _on_wf_smooth_toggled(self, v):
        self._state.wf_smooth = bool(v)

    def _redraw(self):
        self.redraws += 1

    def set_snap_interval(self, hz):
        self.snap_interval = float(hz)


class _FakeSky:
    def __init__(self):
        self.pressure = 1013.25
        self.temperature = 10.0
        self.bortle = 3

    def set_refraction_params(self, pressure_hpa=None, temperature_c=None):
        if pressure_hpa is not None:
            self.pressure = float(pressure_hpa)
        if temperature_c is not None:
            self.temperature = float(temperature_c)

    def set_bortle(self, level):
        self.bortle = int(level)


class _FakeHost:
    def __init__(self):
        self.spectrum = _FakeSpectrum()
        self.sky_view = _FakeSky()
        self.control_panel = _FakeControlPanel()
        self.squelch_db = None
        self.auto_squelch = True
        self._observer_lat = None
        self._observer_lon = None
        self.observer_apply = 0
        self.gui_saves = 0
        self.theme = "default"
        self.touch = "auto"
        self.ai_panel = None
        self.service_settings_panel = None

    def _on_squelch_changed(self, dbfs):
        self.squelch_db = float(dbfs)
        self.auto_squelch = False          # 与 main_window 一致：手动门限关自动

    def _on_auto_squelch_toggled(self, on):
        self.auto_squelch = bool(on)

    def _apply_theme(self, name):
        self.theme = name

    def _set_touch_mode(self, mode):
        self.touch = mode

    def _apply_observer_location(self):
        self.observer_apply += 1

    def _save_gui_config(self):
        self.gui_saves += 1


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):   # 必须先有 QApplication 才能建 QWidget
    """把 AI 配置路径挪到 tmp，绝不碰用户真实 ~/.mbdsdr/config.json。"""
    ai_path = tmp_path / "ai_config.json"
    monkeypatch.setattr(settings_panel, "AI_CONFIG_PATH", str(ai_path))
    monkeypatch.setattr(settings_panel, "GUI_CONFIG_PATH", str(tmp_path / "gui.json"))
    ai_path.write_text(json.dumps({
        "api_key": "sk-secret-should-never-export",
        "base_url": "https://example.invalid/v1",
        "model": "fake-model",
    }), encoding="utf-8")
    ds = DesktopSettings(path=str(tmp_path / "desktop_settings.json"))
    host = _FakeHost()
    panel = settings_panel.SettingsPanel(settings=ds, host=host)
    return ds, host, panel, tmp_path


# ---------------------------------------------------------------------------
# 1. 单一事实源
# ---------------------------------------------------------------------------
def test_defaults_derive_from_schema_only():
    assert settings_mod.DEFAULTS == defaults("settings")
    for item in EXPOSED_ITEMS:
        if item.storage == "settings":
            assert item.key in settings_mod.DEFAULTS
            assert settings_mod.DEFAULTS[item.key] == item.default
        else:
            assert item.key not in settings_mod.DEFAULTS


def test_schema_keys_unique_and_sourced():
    keys = [it.key for it in config_schema.ALL_ITEMS]
    assert len(keys) == len(set(keys))
    for item in config_schema.ALL_ITEMS:
        assert item.source, f"{item.key} 缺默认值出处"
        if item.exposed:
            assert item.label and item.help, f"{item.key} 缺人话文案"
            assert item.group in GROUP_ORDER


def test_ui_does_not_hardcode_port_defaults():
    assert settings_panel.DEFAULT_RC_PORT == SCHEMA["rc_port"].default
    assert settings_panel.DEFAULT_WEB_PORT == SCHEMA["web_port"].default


# ---------------------------------------------------------------------------
# 2. 校验：越界不崩且给原因
# ---------------------------------------------------------------------------
def test_validate_clamps_and_explains():
    item = SCHEMA["fft_size"]
    v, reason = validate_value(item, 999999)
    assert v == item.default and "回默认" in reason

    item = SCHEMA["peak_threshold_db"]
    v, reason = validate_value(item, 999)
    assert v == item.maximum and "上限" in reason

    item = SCHEMA["ui_language"]
    v, reason = validate_value(item, "fr")
    assert v == "zh" and "回默认" in reason

    item = SCHEMA["show_waterfall"]
    v, reason = validate_value(item, "false")
    assert v is False and reason is None


def test_bad_types_fall_back_without_crash():
    for item in EXPOSED_ITEMS:
        try:
            validate_value(item, object())
        except (TypeError, ValueError, AttributeError, KeyError, RuntimeError) as e:
            pytest.fail(f"{item.key} 对坏类型抛异常：{e}")


# ---------------------------------------------------------------------------
# 3. 损坏 / 写失败可见
# ---------------------------------------------------------------------------
def test_corrupt_file_is_backed_up_and_error_visible(tmp_path):
    path = tmp_path / "desktop_settings.json"
    path.write_text("{not json", encoding="utf-8")
    ds = DesktopSettings.load(path=str(path))
    assert ds.corrupt_backup and os.path.exists(ds.corrupt_backup)
    assert ds.last_error and "读取失败" in ds.last_error
    assert ds.get("theme") == SCHEMA["theme"].default


def test_write_failure_calls_on_error(tmp_path):
    blocker = tmp_path / "cfg"
    blocker.mkdir()                      # 目标路径是目录 → os.replace 必失败
    ds = DesktopSettings(path=str(blocker))
    seen = []
    ds.on_error = seen.append
    ds.set("theme", "dark")
    ds.flush()
    assert ds.last_error and "保存失败" in ds.last_error
    assert seen and "保存失败" in seen[0]


# ---------------------------------------------------------------------------
# 4. 新键落盘 / 恢复默认
# ---------------------------------------------------------------------------
def test_new_keys_persist_roundtrip(tmp_path):
    path = tmp_path / "desktop_settings.json"
    ds = DesktopSettings(path=str(path))
    ds.set("fft_size", 8192)
    ds.set("waterfall_palette", "sdrpp")
    ds.set("sky_pressure_hpa", 1002.5)
    ds.flush()
    again = DesktopSettings.load(path=str(path))
    assert again.get("fft_size") == 8192
    assert again.get("waterfall_palette") == "sdrpp"
    assert again.get("sky_pressure_hpa") == 1002.5


def test_reset_single_and_all(env):
    ds, host, panel, _ = env
    ds.set("theme", "dark")
    ds.set("fft_size", 16384)
    assert ds.reset("theme") == "default"
    assert ds.get("theme") == "default"
    assert ds.get("fft_size") == 16384      # 单项只动一项

    ds.reset_all()
    assert ds.values() == settings_mod.DEFAULTS


def test_panel_reset_all_restores_behavior(env):
    ds, host, panel, _ = env
    panel._on_changed("theme", "dark")
    panel._on_changed("fft_size", 8192)
    panel._on_changed("observer_lat", 39.9)
    assert host.theme == "dark"
    assert host.spectrum.generator.fft_size == 8192
    assert host._observer_lat == 39.9

    assert panel.reset_all(confirm=False) is True
    assert ds.get("theme") == "default"
    assert host.theme == "default"
    assert host.spectrum.generator.fft_size == 2048
    assert host._observer_lat is None
    assert host.spectrum.generator.fft_size == SCHEMA["fft_size"].default


# ---------------------------------------------------------------------------
# 5. 导入 / 导出
# ---------------------------------------------------------------------------
def test_export_has_version_and_no_secrets(env):
    ds, host, panel, tmp_path = env
    payload = panel.export_to(str(tmp_path / "exp.json"))
    assert payload["schema_version"] == CONFIG_SCHEMA_VERSION
    assert payload["settings"]["fft_size"] == 2048
    raw = (tmp_path / "exp.json").read_text(encoding="utf-8")
    assert "sk-secret" not in raw
    assert "api_key" not in payload["ai_config"]


def test_import_roundtrip_and_unknown_reported(env):
    ds, host, panel, tmp_path = env
    path = str(tmp_path / "exp.json")
    panel.export_to(path)
    ds.set("theme", "dark")
    ds.set("fft_size", 1024)

    payload = json.loads((tmp_path / "exp.json").read_text(encoding="utf-8"))
    payload["settings"]["theme"] = "high_contrast"
    payload["settings"]["future_unknown_key"] = 42
    (tmp_path / "exp.json").write_text(json.dumps(payload), encoding="utf-8")

    res = panel.import_from(path)
    assert res.ok
    assert ds.get("theme") == "high_contrast"
    assert res.unknown == ["settings.future_unknown_key"]
    assert "future_unknown_key" not in ds.values()


def test_import_rejects_cross_major_version(env):
    ds, host, panel, tmp_path = env
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "schema_version": 999, "settings": {"theme": "dark"},
    }), encoding="utf-8")
    res = panel.import_from(str(path))
    assert not res.ok
    # 跨主版本才拒绝，且必须说明为什么 + 该怎么办
    assert "主版本" in res.error
    assert "怎么办" in res.error
    assert ds.get("theme") == "default"


def test_import_old_document_migrates_missing_fields(env):
    """同主版本旧文件（缺字段）→ 允许导入，缺的取默认并报告补齐项数。"""
    ds, host, panel, tmp_path = env
    path = tmp_path / "old.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "settings": {"theme": "dark", "fft_size": 4096},
    }), encoding="utf-8")

    res = panel.import_from(str(path))
    assert res.ok, res.error
    assert ds.get("theme") == "dark"
    assert ds.get("fft_size") == 4096
    # 文件里缺的字段按 schema 默认补齐，并逐项列出
    assert "bortle" in res.filled
    assert "record_dir" in res.filled
    assert ds.get("bortle") == SCHEMA["bortle"].default
    assert any("已按默认补齐" in w for w in res.warnings)
    assert "已按默认补齐" in res.summary()
    assert len(res.filled) >= 20

    # 未知字段仍在“忽略”里显式列出，不静默丢
    assert res.unknown == []


def test_import_same_major_newer_minor_accepts_and_lists_unknown(env):
    ds, host, panel, tmp_path = env
    path = tmp_path / "newer.json"
    path.write_text(json.dumps({
        "schema_version": "1.9",
        "settings": {"theme": "dark", "future_key": 1},
    }), encoding="utf-8")
    res = panel.import_from(str(path))
    assert res.ok
    assert res.unknown == ["settings.future_key"]
    assert any("较新" in w for w in res.warnings)


def test_parse_import_reports_section_mismatch():
    res = parse_import({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "settings": {"observer_lat": 39.9},
    })
    assert res.ok
    assert res.rejected and res.rejected[0][0] == "observer_lat"


# ---------------------------------------------------------------------------
# 5.1 Bortle：必须可设、立即生效、可见星数实测有差
# ---------------------------------------------------------------------------
def test_bortle_schema_is_human_and_ranged():
    item = SCHEMA["bortle"]
    assert item.exposed, "Bortle 是用户头顶的观测条件，必须暴露"
    assert item.minimum == 1 and item.maximum == 9
    assert item.default == 3
    assert "极暗乡村" in item.help and "市中心" in item.help
    assert "可见星" in item.help
    assert "DEFAULT_BORTLE" in item.source


def test_bortle_change_takes_effect_immediately(env):
    ds, host, panel, _ = env
    # 面板构造时把持久化值推给天空图（默认 3）
    assert host.sky_view.bortle == 3
    panel._on_changed("bortle", 8)
    assert ds.get("bortle") == 8
    assert host.sky_view.bortle == 8, "改了必须立即生效，不能等重启"
    panel._on_changed("bortle", 0)          # 越界夹到 1
    assert ds.get("bortle") == 1
    assert host.sky_view.bortle == 1


def test_bortle_persisted_value_is_applied_on_next_start(env):
    ds, host, panel, _ = env
    ds.set("bortle", 8)
    panel2 = settings_panel.SettingsPanel(settings=ds, host=host)
    assert host.sky_view.bortle == 8, "重启后仍应是用户设定的 8，不是开发者默认"


def test_bortle_visible_star_count_counter_evidence():
    """同一个 FOV、同一时间：Bortle 3 与 8 的可见星数必须显著不同。"""
    from mbdsdr_ai.celestial_geometry import GroundStation
    from mbdsdr_ai.skyengine.star_catalog import get_catalog, select_for_view

    get_catalog()
    gs = GroundStation(0.0, 0.0, 0.0)
    jd = 2451545.0
    n3 = len(select_for_view(gs, jd, 0.0, 60.0, 120.0,
                             bortle=3, max_count=100000))
    n8 = len(select_for_view(gs, jd, 0.0, 60.0, 120.0,
                             bortle=8, max_count=100000))
    assert n3 > n8, f"Bortle 3 应看到更多星：3={n3}, 8={n8}"
    assert n3 >= n8 * 3, f"差异不够显著：3={n3}, 8={n8}"
    assert n3 - n8 >= 200, f"差异不够显著：3={n3}, 8={n8}"


def test_sky_view_bortle_setter_clamps_and_rejects_garbage():
    from rf_sky_view import RFSkyView

    class _View:
        _bortle = 3

        def __init__(self):
            self.updated = 0

        def update(self):
            self.updated += 1

    v = _View()
    RFSkyView.set_bortle(v, 8)
    assert v._bortle == 8 and v.updated >= 1
    RFSkyView.set_bortle(v, 0)
    assert v._bortle == 1
    RFSkyView.set_bortle(v, 99)
    assert v._bortle == 9
    with pytest.raises(ValueError):
        RFSkyView.set_bortle(v, "不是数字")


# ---------------------------------------------------------------------------
# 5.1b 参数治理 A5/A11：真实消费点必须跟着设置值变
# ---------------------------------------------------------------------------
def test_squelch_schema_range_comes_from_kernel_constants():
    """A5 交付说明：默认/范围不许在 schema 再写一份字面量。"""
    from mbdsdr_ai.squelch import (
        DEFAULT_SQUELCH_DBFS, SQUELCH_MAX_DBFS, SQUELCH_MIN_DBFS)

    item = SCHEMA["squelch_default_db"]
    assert item.default == int(DEFAULT_SQUELCH_DBFS)
    assert item.minimum == int(SQUELCH_MIN_DBFS)
    assert item.maximum == int(SQUELCH_MAX_DBFS)


def test_squelch_setting_reaches_slider_and_gate(env):
    ds, host, panel, _ = env
    panel._on_changed("squelch_default_db", -70)
    assert ds.get("squelch_default_db") == -70
    assert host.control_panel.squelch_slider.value == -70
    assert host.control_panel.squelch_val.text_value == "-70 dB"
    assert host.squelch_db == -70.0, "消费点没收到新门限 = 假设置"
    # 「自动静噪」勾选时，应用默认门限不能把它偷偷关掉
    assert host.auto_squelch is True
    # 用户手动关掉自动后，再改默认值应保持手动模式
    host.control_panel.auto_squelch_check.setChecked(False)
    panel._on_changed("squelch_default_db", -60)
    assert host.auto_squelch is False and host.squelch_db == -60.0
    # 顶部滑杆改动反写 settings（单一权威源）
    host.control_panel.squelch_changed.emit(-80.0)
    assert ds.get("squelch_default_db") == -80


def test_tuning_step_reaches_spectrum_and_combo(env):
    ds, host, panel, _ = env
    panel._on_changed("tuning_step_hz", 1000)
    assert ds.get("tuning_step_hz") == 1000
    assert host.spectrum.snap_interval == 1000.0, "频谱吸附没变 = 假设置"
    assert host.control_panel.step_combo.index == 2
    # 顶部步进改动反写 settings
    host.control_panel.step_changed.emit(100000.0)
    assert ds.get("tuning_step_hz") == 100000.0


def test_squelch_migration_notice_is_visible_on_old_config(tmp_path, qapp):
    path = str(tmp_path / "old_settings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"volume": 30}, f)
    ds = DesktopSettings.load(path=path)
    panel = settings_panel.SettingsPanel(settings=ds, host=_FakeHost())
    text = panel.status_label.text()
    assert "静噪默认值迁移" in text
    assert "-80" in text and "-50" in text
    # 新文件已有该键 → 不再提示
    ds.set("squelch_default_db", -50)
    ds.flush()
    ds2 = DesktopSettings.load(path=path)
    panel2 = settings_panel.SettingsPanel(settings=ds2, host=_FakeHost())
    assert "静噪默认值迁移" not in panel2.status_label.text()


# ---------------------------------------------------------------------------
# 5.2 录音目录：可配置、默认不落 C 盘用户目录、只影响新录音
# ---------------------------------------------------------------------------
def test_record_dir_default_is_sibling_of_project_not_user_dir():
    default = settings_mod.default_record_dir()
    project = settings_mod.project_root()
    assert default == os.path.join(os.path.dirname(project), "recordings")
    assert os.path.basename(default) == "recordings"
    # 不再使用旧的 ~/mbdsdr_recordings，也不塞进 ~/.mbdsdr 配置目录
    assert default != os.path.expanduser("~/mbdsdr_recordings")
    assert not default.startswith(os.path.expanduser("~/.mbdsdr"))
    if os.name == "nt" and project.startswith("D:"):
        assert default == r"D:\mbdsdr\recordings"


def test_record_dir_blank_means_default_and_ensure_creates(tmp_path):
    ds = DesktopSettings(path=str(tmp_path / "s.json"))
    assert ds.get("record_dir") == ""
    assert ds.record_dir == settings_mod.default_record_dir()
    ds.set("record_dir", str(tmp_path / "rec"))
    assert ds.record_dir == str(tmp_path / "rec")
    path, err = ds.ensure_record_dir()
    assert err is None and os.path.isdir(path)
    ds.flush()
    again = DesktopSettings.load(path=str(tmp_path / "s.json"))
    assert again.record_dir == str(tmp_path / "rec")


def test_record_dir_missing_target_reports_actionable_error(tmp_path, monkeypatch):
    """盘拔了/路径被文件占了：返回可读文案，界面弹窗用同一段文字。"""
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    ds = DesktopSettings(path=str(tmp_path / "s.json"))
    ds.set("record_dir", str(blocker))
    path, err = ds.ensure_record_dir()
    assert path == str(blocker)
    assert err and "录音目录不可用" in err
    assert str(blocker) in err
    assert "请检查" in err          # 告诉用户下一步做什么，而不是只报 WinError
    # 值仍保存（可能只是暂时拔盘），但错误必须同时进状态栏与弹窗
    seen = []

    def _warning(parent, title, text):
        seen.append((title, text))
        return 0

    monkeypatch.setattr(
        settings_panel, "QMessageBox",
        type("_MB", (), {"warning": staticmethod(_warning)}))
    panel = settings_panel.SettingsPanel(settings=ds, host=_FakeHost())
    panel._on_changed("record_dir", str(blocker))
    assert ds.get("record_dir") == str(blocker)
    assert "录音目录不可用" in panel.status_label.text()
    assert seen and seen[0][0] == "录音目录不可用"
    assert "请检查" in seen[0][1]


def test_record_dir_panel_row_and_buttons(env):
    from PySide6.QtWidgets import QLineEdit, QPushButton

    ds, host, panel, tmp_path = env
    editor = panel._rows["record_dir"]
    assert isinstance(editor, QLineEdit)
    assert settings_mod.default_record_dir() in editor.placeholderText()
    texts = {b.text() for b in panel.findChildren(QPushButton)}
    assert "打开目录" in texts and "选择目录…" in texts

    target = str(tmp_path / "new_recordings")
    panel._on_changed("record_dir", target)
    assert ds.get("record_dir") == target
    assert target in panel.status_label.text()
    assert "只影响新录音" in panel.status_label.text()


# ---------------------------------------------------------------------------
# 6. 面板结构 + 立即生效
# ---------------------------------------------------------------------------
def test_panel_has_required_groups_and_all_rows(env):
    ds, host, panel, _ = env
    titles = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
    for group in ("通用", "显示与主题", "星象仪", "频谱与解码",
                  "卫星与多普勒", "AI", "存储与日志"):
        assert group in titles
    # 服务组由独立「服务设置」页签承载（避免双 ServiceSettingsPanel），不算本面板行
    settings_keys = [it.key for it in EXPOSED_ITEMS
                     if it.storage == "settings" and it.group != "服务与远程访问"]
    assert set(settings_keys) <= set(panel._rows)
    # 行标题来自 schema 文案，UI 不另写
    from PySide6.QtWidgets import QGroupBox
    titles = {w.title() for w in panel.findChildren(QGroupBox)}
    for key in ("fft_size", "theme", "ui_language", "sky_pressure_hpa"):
        assert SCHEMA[key].label in titles


def test_panel_change_immediately_applies(env):
    ds, host, panel, _ = env
    panel._on_changed("theme", "dark")
    panel._on_changed("touch_mode", "on")
    panel._on_changed("show_waterfall", False)
    panel._on_changed("fft_size", 8192)
    panel._on_changed("fft_window", "Hann")
    panel._on_changed("waterfall_palette", "sdrpp")
    panel._on_changed("peak_threshold_db", 9.0)
    panel._on_changed("show_peaks", False)
    panel._on_changed("waterfall_smooth", True)
    panel._on_changed("sky_pressure_hpa", 1000.0)

    assert host.theme == "dark"
    assert host.touch == "on"
    assert host.spectrum._state.show_waterfall is False
    assert host.spectrum.generator.fft_size == 8192
    assert host.spectrum.generator.window == "Hann"
    assert host.spectrum.palette_idx == 1
    assert host.spectrum._state.peak_rel_db == 9.0
    assert host.spectrum._state.show_peaks is False
    assert host.spectrum._state.wf_smooth is True
    assert host.sky_view.pressure == 1000.0
    # 落盘值同样一致
    assert ds.get("fft_size") == 8192
    assert ds.get("sky_pressure_hpa") == 1000.0


def test_ui_widget_emits_and_applies(env):
    """真实控件路径：下拉框换项 → schema → 落盘 → 生效。"""
    ds, host, panel, _ = env
    combo = panel._rows["fft_size"]
    combo.setCurrentIndex(3)            # 8192
    assert ds.get("fft_size") == 8192
    assert host.spectrum.generator.fft_size == 8192


def test_observer_writes_existing_gui_config(env):
    ds, host, panel, _ = env
    panel._on_changed("observer_lat", 39.9042)
    panel._on_changed("observer_lon", 116.4074)
    assert host._observer_lat == 39.9042
    assert host._observer_lon == 116.4074
    assert host.observer_apply >= 2
    assert host.gui_saves >= 2        # 沿用 gui_config 既有权威源


def test_ai_params_write_without_touching_secret(env):
    ds, host, panel, tmp_path = env
    panel._on_changed("ai_temperature", 0.2)
    data = json.loads((tmp_path / "ai_config.json").read_text(encoding="utf-8"))
    assert data["temperature"] == 0.2
    assert data["api_key"] == "sk-secret-should-never-export"
    assert data["model"] == "fake-model"


def test_restart_items_are_marked():
    restart = [it.key for it in EXPOSED_ITEMS if it.requires_restart]
    assert restart == ["ui_language"]
    item = SCHEMA["ui_language"]
    assert item.restart_note


def test_locked_items_documented():
    assert config_schema.LOCKED
    for name, reason in config_schema.LOCKED:
        assert name and reason
        assert len(reason) > 20, f"{name} 的锁死理由太敷衍"


# ---------------------------------------------------------------------------
# 7. 服务面板：外部边界失败必须可见（不再静默 pass）
# ---------------------------------------------------------------------------
def test_service_panel_shows_save_failure(qapp):
    class _BadSettings:
        def get(self, key, default=None):
            return default

        def set(self, key, value):
            raise TypeError("模拟序列化失败")

    panel = settings_panel.ServiceSettingsPanel(settings=_BadSettings())
    panel._persist("rc_port", 7356)
    assert "保存失败" in panel.error_label.text()
    assert "重启后丢失" in panel.error_label.text()


def test_service_panel_shows_start_failure(qapp, monkeypatch):
    class _BoomRemote:
        def __init__(self, backend=None, host="127.0.0.1", port=7356):
            self.host, self.port = host, port

        def start(self):
            raise RuntimeError("模拟端口被占用")

        def stop(self):
            pass

    monkeypatch.setattr(settings_panel, "RemoteControl", _BoomRemote)
    panel = settings_panel.ServiceSettingsPanel()
    panel._start_rc()
    assert "启动失败" in panel.rc_status.text()
    assert panel.rc_check.isChecked() is False
    assert panel._rc is None


def test_service_panel_shows_stop_failure(qapp):
    class _BadStop:
        def stop(self):
            raise OSError("模拟句柄已失效")

    panel = settings_panel.ServiceSettingsPanel()
    panel._rc = _BadStop()
    panel._stop_rc()
    assert "停止失败" in panel.rc_status.text()
    assert panel._rc is None
