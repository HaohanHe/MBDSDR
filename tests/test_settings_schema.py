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

from PySide6.QtWidgets import QApplication, QSpinBox  # noqa: E402

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
        return self.fft_size      # 与真实 generator 同契约：返回实际生效档

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
        self.wf_depth_rows = 256
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

    def set_waterfall_depth_rows(self, rows):
        self.wf_depth_rows = int(rows)
        return int(rows)


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

    def set_extinction_k(self, k):
        self.extinction_k = float(k)


class _FakeHost:
    def __init__(self):
        self.spectrum = _FakeSpectrum()
        self.sky_view = _FakeSky()
        self.control_panel = _FakeControlPanel()
        self.squelch_db = None
        self.auto_squelch = True
        self._observer_lat = None
        self._observer_lon = None
        self._observer_source = None
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

    def set_observer_site(self, lat, lon, alt_m: float = 0.0, *,
                          place=None, source="manual", persist=True):
        """与 main_window.set_observer_site 同语义的替身（唯一写入口）。"""
        has = lat is not None and lon is not None
        self._observer_lat = float(lat) if has else None
        self._observer_lon = float(lon) if has else None
        self._observer_source = source if has else None
        self.observer_last_source = source
        self.observer_apply += 1
        if persist:
            self.gui_saves += 1
        return True

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
    # 设置页是惰性构建的（首次显示才建 38 行）：本文件按"已打开的面板"检查
    # 控件，先把它物化。不 show()：那会让 reset_all(confirm=True) 弹出确认框。
    panel._ensure_ui_built()
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


def test_sample_rate_default_is_single_source_and_combo_matches(qapp):
    """采样率三值必须相等：schema/settings 默认 = constants = 控制面板下拉。"""
    import constants as desktop_constants
    from mbdsdr_ai.sdr_backend import DEFAULT_SAMPLE_RATE_HZ as backend_default

    assert desktop_constants.DEFAULT_SAMPLE_RATE_HZ == backend_default
    assert SCHEMA["sample_rate_hz"].default == float(desktop_constants.DEFAULT_SAMPLE_RATE_HZ)
    assert settings_mod.DEFAULTS["sample_rate_hz"] == SCHEMA["sample_rate_hz"].default

    from control_panel import ControlPanel
    cp = ControlPanel()
    assert float(cp.sample_rate_combo.currentData()) == \
        SCHEMA["sample_rate_hz"].default


def test_fft_size_exposes_only_kernel_supported_tiers():
    """档位与 C++/AI 工具对齐：只允许 1024/2048/4096。"""
    assert SCHEMA["fft_size"].choice_values == (1024, 2048, 4096)
    assert "不支持" in SCHEMA["fft_size"].help
    for bad in (8192, 16384):
        value, reason = validate_value(SCHEMA["fft_size"], bad)
        assert value == 2048 and reason, f"{bad} 必须回落 2048 并给原因"


def test_old_fft_size_falls_back_with_recorded_reason(tmp_path):
    """旧配置存了非法档位：启动回落 2048，并留下可读原因（不静默）。"""
    path = tmp_path / "desktop_settings.json"
    path.write_text(json.dumps({"fft_size": 8192, "theme": "dark"}),
                    encoding="utf-8")
    ds = DesktopSettings.load(path=str(path))
    assert ds.get("fft_size") == SCHEMA["fft_size"].default == 2048
    assert ds.get("theme") == "dark"          # 其他用户值不受影响
    assert ds.migration_notes, "非法档位回落必须记录原因"
    assert any("fft_size" in n and "回默认" in n for n in ds.migration_notes)


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


#: 「改了 A 不同时改 B 就不成立」的项对。两侧 help 必须互相点名对方 label，
#: 并带「联动」字样——面板每行 help 由 QLabel(item.help) 渲染，这就是用户看到
#: 的联动提示；没有这条断言，文案会随重构悄悄丢掉提示。
_DEPENDENCY_PAIRS = (
    ("show_low_elevation_passes", "pass_min_elevation_deg"),
    ("sky_extinction_k", "bortle"),
    ("fft_size", "fft_avg_frames"),
    ("show_waterfall", "waterfall_depth_rows"),
    ("show_peaks", "peak_threshold_db"),
    ("rc_enabled", "rc_port"),
    ("web_enabled", "web_port"),
    ("observer_lat", "observer_lon"),
    ("ai_temperature", "ai_top_p"),
    ("waterfall_smooth", "show_waterfall"),
)


def test_dependency_help_carries_cross_hint():
    """联动提示必须落在 help 文案里（面板自动渲染，不碰布局）。"""
    for a, b in _DEPENDENCY_PAIRS:
        ha, hb = SCHEMA[a].help, SCHEMA[b].help
        assert SCHEMA[b].label in ha, f"{a}.help 未点名「{SCHEMA[b].label}」"
        assert SCHEMA[a].label in hb, f"{b}.help 未点名「{SCHEMA[a].label}」"
        assert "联动" in ha, f"{a}.help 缺「联动」提示"
        assert "联动" in hb, f"{b}.help 缺「联动」提示"


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


#: 用户手改 JSON 的非法值路径：键 → 写入值 → 期望落地值 → 原因关键字。
#: 「启动即归一」由 DesktopSettings.load 里的 _coerce 负责，且原因必须进
#: migration_notes（不静默）。
_HAND_EDITED_OUTLIERS = (
    ("bortle", 99, 9, "上限"),
    ("sky_extinction_k", 9.0, 0.5, "上限"),
    ("waterfall_depth_rows", 384, 256, "回默认"),
    ("fft_size", 8192, 2048, "回默认"),
    ("pass_min_elevation_deg", -5, 0.0, "下限"),
    ("pass_window_hours", 999, 72, "上限"),
    ("peak_threshold_db", 100, 30.0, "上限"),
    ("squelch_default_db", 100, -20, "上限"),
    ("rc_port", 70000, 65535, "上限"),
    ("web_port", 0, 1, "下限"),
    ("ui_language", "fr", "zh", "回默认"),
    ("tuning_step_hz", 5000, 10000, "回默认"),
    ("show_low_elevation_passes", "maybe", False, "回默认"),
)


@pytest.mark.parametrize("key,raw,expected,reason_word", _HAND_EDITED_OUTLIERS)
def test_hand_edited_json_outliers_are_clamped_and_recorded(
        tmp_path, key, raw, expected, reason_word):
    """手改 JSON 写非法值 → 启动归一（夹取/回默认）并记录原因，其他键不受影响。"""
    path = tmp_path / "desktop_settings.json"
    path.write_text(json.dumps({key: raw, "theme": "dark"}), encoding="utf-8")
    ds = DesktopSettings.load(path=str(path))
    assert ds.get(key) == expected, f"{key}={raw!r} 应落到 {expected!r}"
    assert ds.get("theme") == "dark", "无关的用户值不得被连带改掉"
    notes = ds.migration_notes or []
    assert any(key in n and reason_word in n for n in notes), \
        f"{key} 的回落原因必须可见，实际 notes={notes!r}"


def test_hand_edited_bool_alias_is_legal_and_text_is_coerced(tmp_path):
    """合法别名不报原因；text 键的 JSON 数字按字符串收下（记录口径）。"""
    path = tmp_path / "desktop_settings.json"
    path.write_text(json.dumps({"show_waterfall": "yes", "record_dir": 123}),
                    encoding="utf-8")
    ds = DesktopSettings.load(path=str(path))
    assert ds.get("show_waterfall") is True
    assert ds.get("record_dir") == "123"
    assert not ds.migration_notes, \
        f"合法别名/文本不该产生回落原因：{ds.migration_notes!r}"


def test_internal_keys_from_hand_edited_json_are_not_bounded(tmp_path):
    """已知口径：内部键（无面板入口）没有上下限，手改值原样接受。

    它们由控制面板/设备层写入且 UI 侧自带范围；这里钉住现状，避免误以为
    schema 对 1e9 Hz 做过夹取。
    """
    path = tmp_path / "desktop_settings.json"
    path.write_text(json.dumps({"frequency_hz": 1e9, "gain_db": 999.0}),
                    encoding="utf-8")
    ds = DesktopSettings.load(path=str(path))
    assert ds.get("frequency_hz") == 1e9
    assert ds.get("gain_db") == 999.0
    assert not ds.migration_notes


def _load_payload(tmp_path, payload):
    path = tmp_path / "desktop_settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return DesktopSettings.load(path=str(path))


#: 全部枚举项。归一覆盖与落盘/读回等价性都按这张清单逐项跑，新增枚举自动纳入。
_ENUM_ITEMS = tuple(it for it in SCHEMA.values() if it.kind == "enum")


def _enum_spelling_variant(item):
    """构造「写法不同、值相同」的输入：大小写 + 首尾空白（数字档位用字符串）。"""
    canonical = item.choice_values[0]
    if isinstance(canonical, str):
        return " " + canonical.swapcase() + " ", canonical
    return " " + str(canonical) + " ", canonical


@pytest.mark.parametrize("item", _ENUM_ITEMS, ids=[it.key for it in _ENUM_ITEMS])
def test_every_enum_normalizes_spelling_before_judging(item):
    """全枚举统一三步：strip/大小写/数字字符串归一命中即采用，且必须留痕。"""
    raw, expected = _enum_spelling_variant(item)
    assert raw != expected
    value, reason = validate_value(item, raw)
    assert value == expected
    assert reason and "归一" in reason, f"{item.key} 归一未留痕：{reason!r}"


@pytest.mark.parametrize("item", _ENUM_ITEMS, ids=[it.key for it in _ENUM_ITEMS])
def test_every_enum_legal_value_is_not_rewritten(item):
    """7/5/4 项合法值逐个原样通过，不产生任何说明。"""
    for v in item.choice_values:
        value, reason = validate_value(item, v)
        assert value == v and reason is None, f"{item.key}={v!r} 被改写"


@pytest.mark.parametrize("item", _ENUM_ITEMS, ids=[it.key for it in _ENUM_ITEMS])
def test_every_enum_roundtrip_records_normalization_and_fallback(tmp_path, item):
    """落盘/读回等价：写法变体 → 规范值 + notes；非法值 → 默认值 + notes。"""
    norm_dir = tmp_path / "normalized"
    norm_dir.mkdir()
    raw, expected = _enum_spelling_variant(item)
    ds = _load_payload(norm_dir, {item.key: raw})
    assert ds.get(item.key) == expected
    notes = ds.migration_notes or []
    assert any(item.key in n and "归一" in n and repr(raw) in n for n in notes), \
        f"{item.key} 归一后 notes 缺原值/归一值：{notes!r}"

    bad_dir = tmp_path / "illegal"
    bad_dir.mkdir()
    bad = "ZZZ-非法档位"
    ds_bad = _load_payload(bad_dir, {item.key: bad})
    value = ds_bad.get(item.key)
    assert value == item.default, f"{item.key}={bad!r} 应回默认 {item.default!r}"
    assert value != bad, "非法值不许静默变成另一个合法值"
    notes = ds_bad.migration_notes or []
    assert any(item.key in n and repr(bad) in n and "回默认" in n for n in notes), \
        f"{item.key} 回落未留痕：{notes!r}"


@pytest.mark.parametrize("raw,expected_panel,notes_expected", [
    (1e12, 6.0e9, False),      # 高于 6 GHz → ControlPanel 夹到上限
    (-1.0, 98e6, True),        # 0/负值 = 「没设过」→ settings._coerce 回默认并留痕
    ("abc", 98e6, True),       # 非数字 → settings._coerce 回默认并记原因
])
def test_internal_frequency_three_inputs_are_consumer_clamped(
        tmp_path, qapp, raw, expected_panel, notes_expected):
    """frequency_hz 手改三种输入：不崩、不显示非法值，由消费方兜底。

    产品顺序 = ``DesktopSettings.load``（归一/记原因）→ 主窗口
    ``_apply_restored_settings`` → ``ControlPanel.set_frequency_hz``（夹取）。
    断连时 display 保持 "--"，连接后才显示夹取后的值。
    """
    from control_panel import ControlPanel

    ds = _load_payload(tmp_path, {"frequency_hz": raw})
    cp = ControlPanel()
    cp.set_frequency_hz(ds.get("frequency_hz"))
    assert cp._freq_hz == pytest.approx(expected_panel)
    assert bool(ds.migration_notes) is notes_expected


def test_internal_gain_bandwidth_sample_rate_consumers_clamp(tmp_path, qapp):
    """gain_db / bandwidth_hz / sample_rate_hz 的兜底落在 ControlPanel setter。"""
    from control_panel import ControlPanel

    cp = ControlPanel()

    # gain：999 → 49 dB 上限；负值 → 0；非数字 → 默认 20 并记原因
    ds = _load_payload(tmp_path, {"gain_db": 1e12})
    cp.set_gain(ds.get("gain_db"))
    assert cp.gain_slider.value() == 49
    ds = _load_payload(tmp_path, {"gain_db": -1.0})
    cp.set_gain(ds.get("gain_db"))
    assert cp.gain_slider.value() == 0
    ds = _load_payload(tmp_path, {"gain_db": "abc"})
    assert ds.get("gain_db") == 20.0 and ds.migration_notes
    cp.set_gain(ds.get("gain_db"))
    assert cp.gain_slider.value() == 20

    # 带宽：产品路径 ``if bw > 0`` 才显式覆盖（main_window 3260 附近），
    # 0/负数/非数字都保持「随模式自动」（当前 FM → 12.5 kHz）。
    ds = _load_payload(tmp_path, {"bandwidth_hz": 1e12})
    bw = float(ds.get("bandwidth_hz", 0.0) or 0.0)
    cp.set_vfo_bandwidth(bw)
    assert cp.vfo_bw_combo.currentData() == 180_000.0   # 最近档：180 kHz (WFM)
    for bad in (-1.0, "abc"):
        cp.set_mode("FM")
        ds = _load_payload(tmp_path, {"bandwidth_hz": bad})
        bw = float(ds.get("bandwidth_hz", 0.0) or 0.0)
        if bw > 0:
            cp.set_vfo_bandwidth(bw)
        assert cp.vfo_bw_combo.currentData() == 12_500.0, \
            f"bandwidth_hz={bad!r} 不该覆盖模式默认带宽"
        if bad == "abc":
            assert ds.get("bandwidth_hz") == 0.0 and ds.migration_notes

    # 采样率：主窗口没有 >0 守卫，setter 选最近档。
    ds = _load_payload(tmp_path, {"sample_rate_hz": 1e12})
    cp.set_sample_rate(ds.get("sample_rate_hz"))
    assert cp.sample_rate_combo.currentData() == 3_200_000.0  # 最高档
    ds = _load_payload(tmp_path, {"sample_rate_hz": -1.0})
    cp.set_sample_rate(ds.get("sample_rate_hz"))
    assert cp.sample_rate_combo.currentData() == 250_000.0    # 最低档
    ds = _load_payload(tmp_path, {"sample_rate_hz": "abc"})
    assert ds.get("sample_rate_hz") == 2_400_000.0 and ds.migration_notes
    cp.set_sample_rate(ds.get("sample_rate_hz"))
    assert cp.sample_rate_combo.currentData() == 2_400_000.0


def test_demod_mode_choices_are_ui_backend_intersection_exact(qapp):
    """合法集合 = 注册表 analog 模式 ∩ 后端 VALID_MODES，且与下拉项逐项相等。

    多一项（如只在后端的 DSB）或少一项都会红：多出的项落盘后 UI 无对应项、
    `set_mode` 后下拉又会空白，正是本次要消灭的入口。
    """
    from control_panel import ControlPanel
    from mode_registry import mode_names
    from mbdsdr_ai.core.controller import VALID_MODES

    expected = {m for m in mode_names("analog") if m in VALID_MODES}
    item = SCHEMA["demod_mode"]
    assert item.kind == "enum"
    assert set(item.choice_values) == expected
    assert len(item.choice_values) == len(expected)

    cp = ControlPanel()
    dropdown = {cp.mode_combo.itemText(i)
                for i in range(cp.mode_combo.count())}
    assert dropdown == expected, f"下拉项与合法集合不一致：{dropdown} vs {expected}"
    assert cp.mode_combo.isEditable() is False


@pytest.mark.parametrize("raw,expected", [("usb", "USB"), (" Usb ", "USB")])
def test_hand_edited_demod_mode_is_normalized_before_rejecting(
        tmp_path, qapp, raw, expected):
    """归一 → 判合法 → 才回落：大小写/空白差异归一后命中即采用。

    直接拿原值判合法会把用户本意的 `usb` 回落成另一个模式 `FM`；这里钉住
    先 strip + upper 再比对，且归一必须留痕（notes 写原值与归一值）。
    """
    from control_panel import ControlPanel

    ds = _load_payload(tmp_path, {"demod_mode": raw})
    assert ds.get("demod_mode") == expected
    notes = ds.migration_notes or []
    assert any("归一" in n and repr(raw) in n and expected in n for n in notes), \
        f"归一必须留痕（含原值与归一值），实际 notes={notes!r}"

    cp = ControlPanel()
    cp.set_mode(ds.get("demod_mode"))
    assert cp._current_mode == expected
    assert cp.mode_combo.currentText() == expected
    assert cp.mode_combo.currentIndex() >= 0


def test_hand_edited_demod_mode_falls_back_to_fm_with_reason(tmp_path, qapp):
    """手改不存在的 "NBEM" → 落 FM、migration_notes 写明原值与回落值、下拉可选。"""
    from control_panel import ControlPanel

    ds = _load_payload(tmp_path, {"demod_mode": "NBEM"})
    assert ds.get("demod_mode") == "FM"
    notes = ds.migration_notes or []
    assert any("demod_mode" in n and "NBEM" in n and "FM" in n for n in notes), \
        f"回落原因必须含原值与回落值，实际 notes={notes!r}"

    cp = ControlPanel()
    cp.set_mode(ds.get("demod_mode"))
    assert cp._current_mode == "FM"
    assert cp.mode_combo.isEditable() is False
    assert cp.mode_combo.currentText() == "FM"
    assert cp.mode_combo.currentIndex() == 0


def test_legal_demod_mode_is_not_rewritten_and_has_no_note(tmp_path, qapp):
    """合法值（含顺序不敏感的大小写一致项）原样保留，不产生 migration_notes。"""
    for mode in ("FM", "WFM", "NFM", "AM", "USB", "LSB", "CW"):
        ds = _load_payload(tmp_path, {"demod_mode": mode})
        assert ds.get("demod_mode") == mode
        assert not ds.migration_notes, f"{mode} 不该产生回落原因"


def test_settings_panel_shows_demod_migration_reason(tmp_path, qapp):
    """用户可见：设置面板状态行必须逐字显示数据层产生的修复原因。

    行为断言（不钉生产源码里的字面量）：
      * 非法值真的被收敛到 schema 的默认值（数据层行为）；
      * 数据层留下了以该键名开头的迁移原因；
      * 面板状态行原样带上这条原因（接线行为），而不是各自出现几个词。
    """
    from settings_panel import SettingsPanel

    ds = _load_payload(tmp_path, {"demod_mode": "NBEM"})
    assert ds.get("demod_mode") == SCHEMA["demod_mode"].default, \
        "非法解调模式没有被收敛到 schema 默认值"
    notes = [n for n in (ds.migration_notes or [])
             if n.startswith("demod_mode: ")]
    assert notes, f"回落原因必须以键名开头，实际 notes={ds.migration_notes!r}"

    panel = SettingsPanel(settings=ds)
    text = panel.status_label.text()
    assert notes[0] in text, (
        "面板状态行没有逐字显示数据层的迁移原因："
        f"note={notes[0]!r}, text={text!r}")
    panel.deleteLater()


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


def test_write_failure_calls_on_error(tmp_path, monkeypatch):
    # 目标先是可读的合法文件（读改写能过），只让最后一步落盘失败。
    # 旧写法把目标做成目录：读改写引入后，flush 在读阶段就报「配置读取
    # 失败」，走不到写盘分支，这条用例因此从未真正验到写失败提示。
    path = tmp_path / "desktop_settings.json"
    path.write_text("{}", encoding="utf-8")
    ds = DesktopSettings(path=str(path))
    seen = []
    ds.on_error = seen.append

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(settings_mod.os, "replace", _boom)
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
    ds.set("fft_size", 4096)
    ds.set("waterfall_palette", "sdrpp")
    ds.set("sky_pressure_hpa", 1002.5)
    ds.flush()
    again = DesktopSettings.load(path=str(path))
    assert again.get("fft_size") == 4096
    assert again.get("waterfall_palette") == "sdrpp"
    assert again.get("sky_pressure_hpa") == 1002.5


def test_reset_single_key_only(env):
    ds, host, panel, _ = env
    ds.set("theme", "dark")
    ds.set("fft_size", 1024)
    ds.set("frequency_hz", 145_500_000.0)
    # 出厂默认主题已是 dark_car，断言追权威源 SCHEMA 而非写死旧浅色值。
    assert ds.reset("theme") == SCHEMA["theme"].default
    assert ds.get("theme") == SCHEMA["theme"].default
    assert ds.get("fft_size") == 1024      # 单项只动一项

    # P0-07 反证：settings 层不得再暴露「一次改回全部 DEFAULTS」的入口。
    # 旧 ``reset_all()`` 会把频点/音量/解调/带宽等运行态键静默改回出厂值——
    # 界面不变、下次启动听到的东西却变了。恢复全部默认只允许走
    # SettingsPanel 的可见项白名单（RESETTABLE_SETTING_KEYS）。
    with pytest.raises(AttributeError):
        ds.reset_all()

    assert ds.get("frequency_hz") == 145_500_000.0
    assert ds.get("fft_size") == 1024


def test_panel_reset_all_restores_behavior(env):
    ds, host, panel, _ = env
    panel._on_changed("theme", "dark")
    panel._on_changed("fft_size", 4096)
    # 站址必须成对：只填纬度不生效（2026-11-09 唯一写入口的契约，
    # 否则"先输纬度"会被当成清空，用户以为保存失败）
    panel._on_changed("observer_lat", 39.9)
    assert host._observer_lat is None, "半对坐标不该落进主窗"
    panel._on_changed("observer_lon", 116.4)
    assert host._observer_lat == 39.9
    assert host._observer_lon == 116.4
    assert host.theme == "dark"
    assert host.spectrum.generator.fft_size == 4096
    assert host._observer_lat == 39.9

    assert panel.reset_all(confirm=False) is True
    assert ds.get("theme") == SCHEMA["theme"].default
    assert host.theme == SCHEMA["theme"].default
    assert host.spectrum.generator.fft_size == 2048
    assert host._observer_lat is None
    assert host.spectrum.generator.fft_size == SCHEMA["fft_size"].default


def _non_default_probe(item):
    """给每个暴露项造一个合法的非默认值（用于"全部恢复默认"覆盖钉扎）。"""
    if item.kind == "bool":
        return not bool(item.default)
    if item.kind == "enum":
        for value, _label in item.choices:
            if value != item.default:
                return value
        return item.default
    if item.kind == "int":
        step = int(item.step or 1)
        candidate = int(item.default) + step
        if item.maximum is not None and candidate > item.maximum:
            candidate = int(item.default) - step
        return candidate
    if item.kind == "float":
        if item.default is None:            # 观测站：默认 None，用可解析的经纬度
            return 39.9 if item.key.endswith("_lat") else 116.4
        step = float(item.step or 1.0)
        candidate = float(item.default) + step
        if item.maximum is not None and candidate > item.maximum:
            candidate = float(item.default) - step
        return candidate
    return "_reset_probe"


def test_reset_all_covers_every_exposed_item(env, monkeypatch):
    """全局「恢复全部默认」必须覆盖 schema 全部暴露项：漏一项就红。"""
    ds, host, panel, _ = env
    applied = []
    real_apply = panel._apply

    def spy(item, value):
        applied.append(item.key)
        return real_apply(item, value)

    monkeypatch.setattr(panel, "_apply", spy)

    # 每项都先改成非默认值（三种存储分别写）
    for item in EXPOSED_ITEMS:
        panel._store_value(item, _non_default_probe(item))
    for item in EXPOSED_ITEMS:
        assert panel._current_value(item) != item.default, \
            f"{item.key} 探针值等于默认，覆盖测试无效"

    assert panel.reset_all(confirm=False) is True

    # 1) 每一项都回到 schema 默认（gui_config 的观测站回到 None）
    drifted = [it.key for it in EXPOSED_ITEMS
               if panel._current_value(it) != it.default]
    assert drifted == [], f"恢复默认漏项：{drifted}"

    # 2) 非 gui_config 的每一项都真的走过 _apply（GUI 存储由 _refresh 回填）
    expect = {it.key for it in EXPOSED_ITEMS if it.storage != "gui_config"}
    assert set(applied) == expect, \
        f"未生效项：{sorted(expect - set(applied))}"
    assert len(applied) == len(expect)


def test_reset_all_does_not_touch_runtime_keys(env, monkeypatch):
    """反证：恢复全部默认只动面板可见设置；运行态四键配置与现场都不变。

    变异回 ``self._settings.reset_all()``（全量 DEFAULTS）→ 本测试必红。
    """
    ds, host, panel, _ = env
    runtime = {
        "frequency_hz": 145_500_000.0,
        "volume": 77,
        "demod_mode": "USB",
        "bandwidth_hz": 12_500.0,
    }
    for key, value in runtime.items():
        ds.set(key, value)

    applied = []
    real_apply = panel._apply

    def spy(item, value):
        applied.append(item.key)
        return real_apply(item, value)

    monkeypatch.setattr(panel, "_apply", spy)

    assert panel.reset_all(confirm=False) is True

    # 1) 白名单恰好是面板可见的 35 项 settings，运行态键一个都不在其中
    assert len(settings_panel.RESETTABLE_SETTING_KEYS) == 35
    assert not set(runtime) & set(settings_panel.RESETTABLE_SETTING_KEYS)
    assert set(settings_panel.RESETTABLE_SETTING_KEYS) <= set(settings_mod.DEFAULTS)

    # 2) 配置层四键保持现场（变异回 DEFAULTS 时这里会变回 98M/48/FM/0）
    for key, value in runtime.items():
        assert ds.get(key) == value, f"{key} 被恢复默认偷改"

    # 3) 现场未被推送：_apply 从没收到运行态键
    assert not set(applied) & set(runtime)

    # 4) 状态行必须明写"保持不变"，不能只说「已恢复默认」
    msg = panel.status_label.text()
    assert "当前频点、音量、解调方式、带宽保持不变" in msg
    assert "已恢复全部默认" not in msg


def test_reset_all_dialog_names_runtime_keys(env, monkeypatch):
    """反证：确认框文案必须明写运行态不变，不许只写「恢复默认」。"""
    ds, host, panel, _ = env
    seen = {}

    def _question(_parent, _title, text, *args, **kwargs):
        seen["text"] = text
        return settings_panel.QMessageBox.No

    monkeypatch.setattr(settings_panel.SettingsPanel, "isVisible", lambda self: True)
    monkeypatch.setattr(settings_panel.QMessageBox, "question",
                        staticmethod(_question))

    assert panel.reset_all(confirm=True) is False
    assert "当前频点、音量、解调方式、带宽保持不变" in seen["text"]
    # 点「否」不写任何配置
    assert ds.get("theme") == SCHEMA["theme"].default


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
    # 拒绝导入后 settings 不动，仍是出厂默认主题（追 SCHEMA 权威源）。
    assert ds.get("theme") == SCHEMA["theme"].default


def test_import_old_document_keeps_missing_fields(env):
    """旧格式文件（缺本轮新增键）→ 合并导入：缺失键保持现值，绝不回默认。"""
    ds, host, panel, tmp_path = env
    base = str(tmp_path / "base.json")
    panel.export_to(base)
    payload = json.loads((tmp_path / "base.json").read_text(encoding="utf-8"))

    # 模拟旧版本导出文件：本轮新增的键在旧文件里根本不存在
    new_keys = ("tuning_step_hz", "squelch_default_db", "waterfall_depth_rows",
                "sky_extinction_k", "pass_min_elevation_deg",
                "pass_window_hours", "show_low_elevation_passes")
    for key in new_keys:
        payload["settings"].pop(key, None)

    # 旧文件里带着用户自己调过的值，导入时一个都不许被覆盖
    user_values = {
        "theme": "high_contrast", "fft_size": 4096, "fft_avg_frames": 16,
        "bortle": 8, "peak_threshold_db": 12.0, "show_waterfall": False,
        "ui_language": "en", "sky_pressure_hpa": 1002.5,
    }
    payload["settings"].update(user_values)
    payload["gui_config"] = {"observer_lat": 39.9, "observer_lon": 116.4}
    # 旧文件里多出的字段也必须逐条列出名字，不静默丢
    payload["settings"]["future_a"] = 1
    payload["settings"]["future_b"] = "x"
    path = tmp_path / "old.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    # 文件里缺失的键在导入前被用户改过：合并语义下必须保持这个值
    ds.set("tuning_step_hz", 1000)      # 合法非默认档（七档之一）
    res = panel.import_from(str(path))
    assert res.ok, res.error
    assert set(new_keys) <= set(res.absent)
    assert ds.get("tuning_step_hz") == 1000, "缺键不得被当成改回默认"
    for key in new_keys:
        if key == "tuning_step_hz":
            continue
        assert ds.get(key) == SCHEMA[key].default, key
    assert not any("已按默认补齐" in w for w in res.warnings)
    assert "已按默认补齐" not in res.summary()

    # 用户原有值一个都没被覆盖（含 gui_config 的观测站坐标）
    for key, value in user_values.items():
        assert ds.get(key) == value, key
    assert host._observer_lat == 39.9
    assert host._observer_lon == 116.4

    # 多出的字段逐条列名字（不是只报个数）
    assert res.unknown == ["settings.future_a", "settings.future_b"]
    assert "忽略未知 2 项" in res.summary()


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
# 5.0 合并语义三条反证（2026-11-11 行为变更：旧语义是全量覆盖）
# ---------------------------------------------------------------------------
def _set_live_baseline(panel, ds):
    """造一份"用户已调好"的现场：频点/观测站/5 个 AI 参数全部偏离默认。"""
    ds.set("frequency_hz", 145_500_000.0)
    panel._on_changed("observer_lat", 31.23)
    panel._on_changed("observer_lon", 121.47)
    ai_values = {
        "ai_temperature": 1.5, "ai_top_p": 0.5,
        "ai_max_output_tokens": 4096, "ai_max_context_tokens": 65536,
        "ai_timeout": 120,
    }
    for key, value in ai_values.items():
        panel._on_changed(key, value)
    return ai_values


def test_import_theme_only_changes_exactly_one_item(env):
    """反证①：只含 theme 的文件 → 只改 1 项，其余（含观测站/5 个 AI/频点）不动。"""
    ds, host, panel, tmp_path = env
    ai_values = _set_live_baseline(panel, ds)

    path = tmp_path / "theme_only.json"
    path.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "settings": {"theme": "dark"},
    }), encoding="utf-8")

    res = panel.import_from(str(path))
    assert res.ok, res.error
    # 预览口径：只报 1 项修改、0 项清空；旧值 → 新值可读
    assert res.changes == [("theme", SCHEMA["theme"].default, "dark")]
    assert res.clears == []
    assert "修改 1 项" in res.summary()
    assert ds.get("theme") == "dark"

    # 文件没提到的项必须一个都不动
    assert ds.get("frequency_hz") == 145_500_000.0, "频点不得被缺键覆盖"
    assert (host._observer_lat, host._observer_lon) == (31.23, 121.47)
    for key, value in ai_values.items():
        item = SCHEMA[key]
        assert panel._current_value(item) == value, key
        assert panel._ai[panel._ai_file_key(item)] == value, key
    assert "frequency_hz" in res.absent
    assert "observer_lat" in res.absent
    assert len(res.absent) >= 45, "缺键应大量落在 absent（保持现值）"


def test_import_rejects_illegal_value_instead_of_falling_back(env):
    """反证②：非法值必须拒绝并进 warnings，不得静默回落默认后写盘。"""
    ds, host, panel, tmp_path = env
    ds.set("fft_size", 2048)

    path = tmp_path / "illegal.json"
    path.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "settings": {"theme": "dark", "fft_size": 9999},
    }), encoding="utf-8")

    res = panel.import_from(str(path))
    assert res.ok, res.error
    # 合法的 theme 照常导入
    assert ds.get("theme") == "dark"
    # 非法的 fft_size 被拒绝：不写入、不改盘、有用户可见说明
    assert [k for k, _reason in res.rejected] == ["fft_size"]
    assert any(w.startswith("fft_size:") for w in res.warnings)
    assert "fft_size" not in res.accepted
    assert ds.get("fft_size") == 2048, "非法值不得静默回落默认"
    assert "拒绝 1 项" in res.summary()
    assert [k for k, _old, _new in res.changes] == ["theme"]


def test_import_full_old_document_still_restores(env):
    """反证③：旧格式整份配置导入仍能还原（合并语义不削弱完整恢复）。"""
    ds, host, panel, tmp_path = env
    panel._on_changed("ai_temperature", 1.5)
    ds.set("frequency_hz", 145_500_000.0)
    panel._on_changed("observer_lat", 31.23)
    panel._on_changed("observer_lon", 121.47)
    base = tmp_path / "full.json"
    panel.export_to(str(base))

    # 改乱现值，模拟"导错一份想还原"
    ds.set("frequency_hz", 98_000_000.0)
    ds.set("theme", "default")
    panel._on_changed("observer_lat", 10.0)
    panel._on_changed("observer_lon", 20.0)
    panel._on_changed("ai_temperature", 0.2)

    res = panel.import_from(str(base))
    assert res.ok, res.error
    changed = {k: (old, new) for k, old, new in res.changes}
    assert changed["frequency_hz"] == (98_000_000.0, 145_500_000.0)
    assert changed["theme"] == ("default", SCHEMA["theme"].default)
    assert ds.get("frequency_hz") == 145_500_000.0
    assert ds.get("theme") == SCHEMA["theme"].default
    assert (host._observer_lat, host._observer_lon) == (31.23, 121.47)
    assert panel._current_value(SCHEMA["ai_temperature"]) == 1.5
    # 整份文件不缺键 → absent 只有密钥类
    assert "frequency_hz" not in res.absent
    assert "observer_lat" not in res.absent


def test_preview_import_does_not_write(env):
    """预览纯读：调用方拿差异后再决定写，预览本身一个字段都不许落盘。"""
    ds, host, panel, tmp_path = env
    path = tmp_path / "theme_only.json"
    path.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "settings": {"theme": "dark"},
    }), encoding="utf-8")

    before_theme = host.theme
    res = panel.preview_import(str(path))
    assert res.ok
    assert [k for k, _old, _new in res.changes] == ["theme"]
    assert ds.get("theme") == SCHEMA["theme"].default
    assert host.theme == before_theme, "预览不得推送到运行时"


def test_import_dialog_cancel_writes_nothing(env, monkeypatch):
    """对话框必须先给预览、用户确认后才落盘；取消一个字段都不写。"""
    ds, host, panel, tmp_path = env
    ds.set("frequency_hz", 145_500_000.0)

    path = tmp_path / "theme_only.json"
    path.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "settings": {"theme": "dark"},
    }), encoding="utf-8")

    seen = {}
    real_dialog = settings_panel.ImportPreviewDialog

    class _CancelDialog(real_dialog):
        def exec(self):
            seen["body"] = self.body_text()
            return settings_panel.QDialog.Rejected

    monkeypatch.setattr(
        settings_panel.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(path), "JSON (*.json)")))
    monkeypatch.setattr(settings_panel, "ImportPreviewDialog", _CancelDialog)

    panel._on_import_clicked()

    # 预览三段 + 不受影响段确实展示给用户后才等确认
    for tag in ("【变化】", "【缺失】", "【清空】", "【不受影响】"):
        assert tag in seen["body"]
    assert "主题" not in seen["body"] and "theme" in seen["body"]
    assert "保持现值" in seen["body"]
    assert "observer_lat" in seen["body"] and "ai_temperature" in seen["body"]
    # 取消 → 未写盘，且明确告知
    assert ds.get("theme") == SCHEMA["theme"].default
    assert ds.get("frequency_hz") == 145_500_000.0
    assert "已取消导入" in panel.status_label.text()


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


def test_squelch_copy_is_margin_not_absolute_dbfs(env):
    """A5 二次裁定：文案必须说「相对噪声基线的余量」，不能再讲绝对 dBFS 音频电平。"""
    ds, host, panel, _ = env
    help_text = SCHEMA["squelch_default_db"].help
    assert "余量" in help_text
    assert "dBFS 是音频电平" not in help_text
    assert "-50 ≈ 6 dB" in help_text and "-20 ≈ 16 dB" in help_text
    # 控件旁必须把旧刻度 -50 换算成「≈6 dB 余量」，用户才看得到真实含义
    spins = [w for w in panel.findChildren(QSpinBox) if "余量" in w.suffix()]
    assert spins, "静噪控件没有显示余量换算"
    w = spins[0]
    assert w.value() == -50
    assert "6" in w.suffix() and "余量" in w.suffix()


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


def test_waterfall_depth_reaches_generator_and_falls_back(env):
    """A7：设置改深度 → 消费点环形缓冲真的变；非六档值回落 256 而非夹取。"""
    ds, host, panel, _ = env
    panel._on_changed("waterfall_depth_rows", 2048)
    assert ds.get("waterfall_depth_rows") == 2048
    assert host.spectrum.wf_depth_rows == 2048, "消费点没收到深度 = 假设置"
    panel._on_changed("waterfall_depth_rows", 99999)
    assert ds.get("waterfall_depth_rows") == 256, (
        "非档位值必须回落默认 256（夹到 4096 会与 C++ 回看时长不一致）")
    assert host.spectrum.wf_depth_rows == 256
    panel._on_changed("waterfall_depth_rows", 384)
    assert ds.get("waterfall_depth_rows") == 256, "384 不是六档之一，必须回落"


def test_waterfall_depth_schema_is_six_tier_enum():
    """GUI/schema/AI/C++ 同一枚举：六档精确值，不再是任意 128–4096。"""
    item = SCHEMA["waterfall_depth_rows"]
    assert item.kind == "enum"
    assert tuple(item.choice_values) == (128, 256, 512, 1024, 2048, 4096)
    value, reason = validate_value(item, 384)
    assert value == 256 and reason and "不在可选值" in reason, (
        "非法档位必须回落 256 且给出可见原因，不许静默")


def test_waterfall_depth_real_buffer_memory_and_history(caplog):
    """A7 反证：真实缓冲截断到新深度、保留最近行，内存可核算。"""
    import logging
    import numpy as np
    from spectrum_widget import (
        SpectrumDataGenerator, WF_DEPTH_CHOICES, WF_DEPTH_DEFAULT_ROWS)

    gen = SpectrumDataGenerator(num_bins=512)
    assert gen.set_waterfall_depth(2048) == 2048
    assert gen._wf_buf.shape == (2048, 512)
    assert gen._wf_buf.nbytes == 2048 * 512 * 4      # 4 MiB，float32
    # 六档之外的值（384 曾被旧实现当真）必须回落 256，且留下原因。
    with caplog.at_level(logging.WARNING, logger="spectrum_widget"):
        assert gen.set_waterfall_depth(384) == WF_DEPTH_DEFAULT_ROWS
    assert "不在六档" in caplog.text
    assert gen.max_waterfall_lines == WF_DEPTH_DEFAULT_ROWS
    assert gen.set_waterfall_depth(4096) == 4096
    assert set(WF_DEPTH_CHOICES) == {128, 256, 512, 1024, 2048, 4096}

    # 写入 600 行（每行唯一值），再把深度缩到 256：只保留最近 256 行
    gen.set_waterfall_depth(2048)
    for i in range(600):
        gen._wf_buf[gen._wf_head] = np.full(512, float(i), dtype=np.float32)
        gen._wf_head = (gen._wf_head + 1) % gen.max_waterfall_lines
        gen._wf_count = min(gen._wf_count + 1, gen.max_waterfall_lines)
        gen.wf_rows_written += 1
    gen.set_waterfall_depth(256)
    assert gen.wf_available_rows() == 256
    assert gen.wf_rows_at(600, 1)[0, 0] == 599
    assert gen.wf_rows_at(gen.wf_rows_written - 255, 1)[0, 0] == 344
    # 旧行已丢：回看窗口不能越过保留范围
    assert len(gen.wf_rows_at(344, 5)) == 0


def test_extinction_k_reaches_sky(env):
    """A8：设置改 k → 天空视图消费点真的变；越界夹取。"""
    ds, host, panel, _ = env
    panel._on_changed("sky_extinction_k", 0.5)
    assert ds.get("sky_extinction_k") == 0.5
    assert host.sky_view.extinction_k == 0.5, "消费点没收到 k = 假设置"
    panel._on_changed("sky_extinction_k", -1.0)
    assert ds.get("sky_extinction_k") == 0.0
    assert host.sky_view.extinction_k == 0.0


def test_extinction_setting_end_to_end_changes_visible_star_count(env, qapp):
    """A8 端到端：面板改设置 → 真实 RFSkyView 可见星数变化（不只是 setter 被调用）。"""
    ds, host, panel, _ = env
    from datetime import datetime, timezone

    from mbdsdr_ai.celestial_geometry import GroundStation
    from mbdsdr_ai.new_spacetime import get_time_engine
    from mbdsdr_ai.skyengine import jtime
    from rf_sky_view import RFSkyView

    unix = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
    jd = jtime.unix_to_jd(unix)
    eng = get_time_engine()
    eng.set_unix(unix)
    eng.pause()
    view = RFSkyView()
    try:
        view.resize(700, 700)
        view.set_observer(40.0, 116.0, 0.0)
        view.set_bortle(3)
        view.view_state.look_at(180.0, 40.0)
        view.view_state.fov_deg = 120.0
        host.sky_view = view
        gs = GroundStation(116.0, 40.0, 0.0)
        panel._on_changed("sky_extinction_k", 0.2)
        n02 = len(view._star_selection(gs, jd))
        panel._on_changed("sky_extinction_k", 0.5)
        n05 = len(view._star_selection(gs, jd))
        assert n02 > n05, f"k=0.2 应比 k=0.5 见更多星：{n02} vs {n05}"
        assert n02 - n05 >= 50, f"星数差 {n02 - n05} 太小，设置链路可能没进选择"
    finally:
        view.close()
        # 全局时间引擎是单例：本用例把它钉在 2026-09-27 并 pause 过，
        # 不复位会把后续「实时」类断言（如首次打开不跳时间）全部带偏。
        eng.set_time_now()
        eng.set_rate(1.0)


def test_extinction_k_changes_star_count_but_not_bortle():
    """A8 反证：同 FOV 同时间 k=0 vs 0.5 可见星数显著不同，且不与 Bortle 重复。"""
    from mbdsdr_ai.celestial_geometry import GroundStation
    from mbdsdr_ai.skyengine.star_catalog import (
        effective_limit_mag, get_catalog, limit_magnitude, select_for_view)

    get_catalog()
    gs = GroundStation(0.0, 0.0, 0.0)
    jd = 2451545.0
    kw = dict(bortle=3, max_count=100000)
    n0 = len(select_for_view(gs, jd, 0.0, 20.0, 120.0,
                             extinction_k=0.0, **kw))
    n5 = len(select_for_view(gs, jd, 0.0, 20.0, 120.0,
                             extinction_k=0.5, **kw))
    assert n0 > n5, f"k=0 应看到更多星：0={n0}, 0.5={n5}"
    assert n0 - n5 >= n0 * 0.1, f"差异不够显著：0={n0}, 0.5={n5}"

    # 消光按仰角逐星算：低仰角受 k 影响远大于天顶，不是把 Bortle 重算一遍
    d_zenith = (effective_limit_mag(90.0, 120.0, 3, 0.5)
                - effective_limit_mag(90.0, 120.0, 3, 0.0))
    d_low = (effective_limit_mag(15.0, 120.0, 3, 0.5)
             - effective_limit_mag(15.0, 120.0, 3, 0.0))
    assert -0.55 <= d_zenith <= -0.45, f"天顶 Δm≈-k：{d_zenith}"
    assert abs(d_low) > abs(d_zenith) * 3, \
        f"低仰角消光应远大于天顶：{d_low} vs {d_zenith}"
    # Bortle 仍独立决定天顶极限星等；k=0 时与 limit_magnitude 完全一致
    assert effective_limit_mag(90.0, 120.0, 3, 0.0) == limit_magnitude(120.0, 3)
    assert effective_limit_mag(90.0, 120.0, 8, 0.0) == limit_magnitude(120.0, 8)


def test_pass_filter_readers_are_real_functions(tmp_path):
    """A1/A2/A9：读取入口必须是可用函数（不是文档里的「拟」）。

    反证：改 DesktopSettings → 函数读到新值；越界夹取；A9 开时门降到 5°。
    消费点（new_spacetime_panel / rf_sky_view）接上一行就生效。
    """
    from settings import (
        PASS_LOW_ELEVATION_FLOOR_DEG, effective_pass_min_elevation_deg,
        effective_pass_visibility_gate_deg, effective_pass_window_hours,
        effective_show_low_elevation_passes)

    ds = DesktopSettings.load(path=str(tmp_path / "pass_filters.json"))
    assert effective_pass_min_elevation_deg(ds) == 10.0
    assert effective_pass_window_hours(ds) == 24
    assert effective_show_low_elevation_passes(ds) is False
    assert effective_pass_visibility_gate_deg(ds) == 10.0

    ds.set("pass_min_elevation_deg", 30)
    ds.set("pass_window_hours", 48)
    ds.set("show_low_elevation_passes", True)
    assert effective_pass_min_elevation_deg(ds) == 30.0
    assert effective_pass_window_hours(ds) == 48
    assert effective_show_low_elevation_passes(ds) is True
    assert effective_pass_visibility_gate_deg(ds) == PASS_LOW_ELEVATION_FLOOR_DEG

    # schema 校验先把越界夹住，函数再兜一层（dict/坏值也不抛）
    ds.set("pass_min_elevation_deg", 90)
    ds.set("pass_window_hours", 0)
    assert effective_pass_min_elevation_deg(ds) == 45.0
    assert effective_pass_window_hours(ds) == 1
    assert effective_pass_min_elevation_deg(
        {"pass_min_elevation_deg": "abc"}) == 10.0
    assert effective_pass_window_hours({"pass_window_hours": None}) == 24
    assert effective_pass_visibility_gate_deg(
        {"pass_min_elevation_deg": 3, "show_low_elevation_passes": True}) == 3.0

    for key in ("pass_min_elevation_deg", "pass_window_hours",
                "show_low_elevation_passes"):
        assert SCHEMA[key].exposed, f"{key} 没暴露到设置面板"


def test_squelch_migration_notice_is_visible_on_old_config(tmp_path, qapp):
    path = str(tmp_path / "old_settings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"theme": "dark"}, f)
    ds = DesktopSettings.load(path=path)
    panel = settings_panel.SettingsPanel(settings=ds, host=_FakeHost())
    text = panel.status_label.text()
    assert "静噪默认值迁移" in text
    assert "余量" in text and "6 dB" in text
    assert "-80" not in text, "旧绝对口径的解释已废弃，不许再出现"
    # 提示同时把新键按默认值写入（旧文件没有该键，不算改用户值）
    ds.flush()
    assert ds.get("squelch_default_db") == -50
    assert ds.get("theme") == "dark", "迁移不得覆盖用户已有的值"
    # 下次启动文件里已有该键 → 不再提示
    ds2 = DesktopSettings.load(path=path)
    panel2 = settings_panel.SettingsPanel(settings=ds2, host=_FakeHost())
    assert "静噪默认值迁移" not in panel2.status_label.text()


# ---------------------------------------------------------------------------
# 5.2 录音目录：可配置、默认落用户目录（不随程序目录删除）、只影响新录音
# ---------------------------------------------------------------------------
def test_record_dir_default_is_in_user_dir():
    default = settings_mod.default_record_dir()
    project = settings_mod.project_root()
    assert default == os.path.join(os.path.expanduser("~"), "MBDSDR",
                                   "recordings")
    assert os.path.basename(default) == "recordings"
    # 不再使用旧的 ~/mbdsdr_recordings，也不塞进 ~/.mbdsdr 配置目录
    assert default != os.path.expanduser("~/mbdsdr_recordings")
    assert not default.startswith(os.path.expanduser("~/.mbdsdr"))
    # 关键红线：绿色包「删目录即卸载」不能连带删除录音
    assert not settings_mod.record_dir_inside_program(default)
    assert settings_mod.record_dir_inside_program(
        os.path.join(settings_mod.program_install_dir(), "recordings"))


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


def test_record_dir_missing_target_reports_actionable_error(tmp_path, monkeypatch, qapp):
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


def test_record_dir_panel_row_and_buttons(env, monkeypatch):
    """录音目录行的两个按钮必须真的接上：选择→持久化、打开→调系统打开器。

    行为断言（不按按钮文案找按钮）：取该行容器里的两个 QPushButton，
    逐个点击并观察副作用——其中一个必须把文件对话框返回值写进 settings
    并回填控件，另一个必须调 QDesktopServices 打开当前生效目录。
    """
    import os

    from PySide6.QtWidgets import QLineEdit, QPushButton

    ds, host, panel, tmp_path = env
    editor = panel._rows["record_dir"]
    assert isinstance(editor, QLineEdit)
    assert settings_mod.default_record_dir() in editor.placeholderText()

    box = editor.parentWidget()
    buttons = box.findChildren(QPushButton)
    assert len(buttons) == 2, \
        f"录音目录行必须恰有两个工具按钮，实际 {[b.text() for b in buttons]}"

    target = str(tmp_path / "new_recordings")
    os.makedirs(target)
    opened = []
    monkeypatch.setattr(
        "settings_panel.QFileDialog.getExistingDirectory",
        staticmethod(lambda *a, **k: target))
    monkeypatch.setattr(
        "settings_panel.QDesktopServices.openUrl",
        staticmethod(lambda url: (opened.append(url.toLocalFile()), True)[1]))

    buttons[0].click()
    if ds.get("record_dir") == target:
        browse_btn, open_btn = buttons[0], buttons[1]
    else:
        browse_btn, open_btn = buttons[1], buttons[0]

    assert ds.get("record_dir") == target, \
        "选择目录按钮没有把对话框返回值持久化到 settings"
    assert editor.text() == target, "选择目录后没有回填控件：%r" % editor.text()
    assert target in panel.status_label.text()

    open_btn.click()
    from PySide6.QtCore import QUrl
    expected_url = QUrl.fromLocalFile(target).toLocalFile()
    assert opened and opened[-1] == expected_url, \
        f"打开目录按钮没有调系统打开器，实际 opened={opened}"
    assert target in panel.status_label.text()


# ---------------------------------------------------------------------------
# 6. 面板结构 + 立即生效
# ---------------------------------------------------------------------------
def test_panel_has_required_groups_and_all_rows(env):
    ds, host, panel, _ = env
    # 2026-11-11 W51（D-10 裁定）：面板按「用途」切成 6 节，不再是 schema 的
    # 7 个数据分组——同一 schema 分组会被拆开（如「频谱与解码」拆成
    # 接收与频谱 / 瀑布与标注）。断言口径换成用途标题。
    titles = [title for title, _section in panel.group_sections]
    assert titles == ["外观与语言", "接收与频谱", "瀑布与标注",
                      "天文观测", "设备与接口", "数据与操作"]
    # 服务组 4 项并入「设备与接口」（不新增第二节 ServiceSettingsPanel）
    settings_keys = [it.key for it in EXPOSED_ITEMS]
    assert set(settings_keys) == set(panel._rows)
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
    panel._on_changed("fft_size", 4096)
    panel._on_changed("fft_window", "Hann")
    panel._on_changed("waterfall_palette", "soft")
    panel._on_changed("peak_threshold_db", 9.0)
    panel._on_changed("show_peaks", False)
    panel._on_changed("waterfall_smooth", True)
    panel._on_changed("sky_pressure_hpa", 1000.0)

    assert host.theme == "dark"
    assert host.touch == "on"
    assert host.spectrum._state.show_waterfall is False
    assert host.spectrum.generator.fft_size == 4096
    assert host.spectrum.generator.window == "Hann"
    assert host.spectrum.palette_idx == 1
    assert host.spectrum._state.peak_rel_db == 9.0
    assert host.spectrum._state.show_peaks is False
    assert host.spectrum._state.wf_smooth is True
    assert host.sky_view.pressure == 1000.0
    # 落盘值同样一致
    assert ds.get("fft_size") == 4096
    assert ds.get("sky_pressure_hpa") == 1000.0


def test_ui_widget_emits_and_applies(env):
    """真实控件路径：下拉框换项 → schema → 落盘 → 生效。"""
    ds, host, panel, _ = env
    combo = panel._rows["fft_size"]
    combo.setCurrentIndex(2)            # 4096（schema 只暴露 1024/2048/4096）
    assert ds.get("fft_size") == 4096
    assert host.spectrum.generator.fft_size == 4096


def test_observer_writes_existing_gui_config(env):
    ds, host, panel, _ = env
    panel._on_changed("observer_lat", 39.9042)
    panel._on_changed("observer_lon", 116.4074)
    assert host._observer_lat == 39.9042
    assert host._observer_lon == 116.4074
    # 2026-11-09：经纬度成对提交（不再每个字段各跑一遍同步/落盘），
    # 所以是 1 次而不是 2 次；多跑一遍只会让天空图闪两下。
    assert host.observer_apply >= 1
    assert host.gui_saves >= 1        # 沿用 gui_config 既有权威源


def test_ai_params_write_without_touching_secret(env):
    ds, host, panel, tmp_path = env
    panel._on_changed("ai_temperature", 0.2)
    data = json.loads((tmp_path / "ai_config.json").read_text(encoding="utf-8"))
    assert data["temperature"] == 0.2
    assert data["api_key"] == "sk-secret-should-never-export"
    assert data["model"] == "fake-model"


def test_restart_items_are_marked():
    restart = [it.key for it in EXPOSED_ITEMS if it.requires_restart]
    assert restart == ["ui_language", "log_level"]
    for key in restart:
        assert SCHEMA[key].restart_note


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

# ---------------------------------------------------------------------------
# 7. 启动回推：立即生效项全部还原，性能档不得覆盖 FFT
# ---------------------------------------------------------------------------
def test_startup_restores_immediate_items_without_perf_override(
        isolated_profile, qapp):
    """反证：fft_size=4096 + perf_profile=eco，启动后控件真实值必须是 4096。

    旧链路 MainWindow 启动先调 set_performance_mode(2) → generator.reconfigure
    (1024)，把配置里的 4096 盖成档位默认；waterfall_palette / peak_threshold_db
    也从不在启动回推清单里（实测缺口 S-01）。现在回推遍历所有立即生效项，
    且性能档不再碰 FFT。
    """
    import spectrum_widget
    from main_window import MainWindow

    ds = DesktopSettings.load()
    ds.set("fft_size", 4096)
    ds.set("perf_profile", "eco")
    ds.set("waterfall_palette", "soft")
    ds.set("peak_threshold_db", 21.0)
    ds.flush()

    w = MainWindow()
    try:
        sp = w.spectrum
        assert sp.generator.fft_size == 4096, (
            f"启动后真实 FFT={sp.generator.fft_size}（期望 4096，"
            "不是性能档默认 1024）")
        assert list(sp._state.wf_colors) == list(
            spectrum_widget.WATERFALL_COLORS_SOFT), "调色板未按配置还原"
        assert float(sp._state.peak_rel_db) == pytest.approx(21.0), \
            "峰值门限未按配置还原"
        # 启动后再切一次性能档：FFT 必须仍由 fft_size 决定（反证核心）
        sp.set_performance_mode(2)
        assert sp.generator.fft_size == 4096, (
            f"切档后真实 FFT={sp.generator.fft_size}：性能档又碰了 FFT")
    finally:
        try:
            w._disconnect()
        except Exception:  # noqa: BLE001 - 收尾失败不掩盖主断言
            pass
        w.close()


# ---------------------------------------------------------------------------
# 7. 导入预览：变化 / 缺失 / 清空 三段 + 「清空」逐项勾选
# ---------------------------------------------------------------------------
def test_import_preview_has_three_sections_and_unaffected_list(env):
    """预览文本必须同时出现三段 + 观测站/AI 参数的「不受影响」点名。"""
    ds, host, panel, tmp_path = env
    path = tmp_path / "theme_only.json"
    path.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "settings": {"theme": "dark"},
    }), encoding="utf-8")

    res = panel.preview_import(str(path))
    assert res.ok
    # 预览必须真的识别出 theme 这一项变化；否则「文案写 0 项」也能自洽通过
    assert [k for k, _old, _new in res.changes] == ["theme"], res.changes
    assert res.clears == []
    text = settings_panel.format_import_preview(res)
    assert f"将改 {len(res.changes)} 项，清空 {len(res.clears)} 项" in text
    for tag in ("【变化】", "【缺失】", "【清空】", "【不受影响】"):
        assert tag in text
    for key in settings_panel.UNAFFECTED_IMPORT_KEYS:
        assert key in text, f"{key} 必须显式标为不受影响"


def test_import_theme_only_leaves_other_files_byte_identical(env):
    """只导入 theme：观测站与 5 个 AI 参数一个都不动（磁盘字节比对）。"""
    ds, host, panel, tmp_path = env
    import settings_panel as sp_mod

    ai_path = sp_mod.AI_CONFIG_PATH
    gui_path = sp_mod.GUI_CONFIG_PATH
    with open(gui_path, "w", encoding="utf-8") as f:
        json.dump({"observer_lat": 31.2, "observer_lon": 121.5}, f)
    host.set_observer_site(31.2, 121.5)
    ds.set("frequency_hz", 145_500_000.0)
    ds.flush()

    ai_before = open(ai_path, "rb").read()
    gui_before = open(gui_path, "rb").read()

    path = tmp_path / "theme_only.json"
    path.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "settings": {"theme": "dark"},
    }), encoding="utf-8")

    res = panel.apply_import(panel.preview_import(str(path)))
    assert [k for k, _o, _n in res.changes] == ["theme"]
    assert "observer_lat" in res.absent and "ai_temperature" in res.absent

    assert open(ai_path, "rb").read() == ai_before, "AI 参数被导入改了"
    assert open(gui_path, "rb").read() == gui_before, "观测站坐标被导入改了"
    assert ds.get("frequency_hz") == 145_500_000.0, "频点被导入改了"
    assert ds.get("theme") == "dark"


def test_import_clear_only_happens_for_checked_keys(env):
    """「清空」默认不执行；用户勾了哪项才删哪项（不提供一键整体替换）。"""
    ds, host, panel, tmp_path = env
    import settings_panel as sp_mod

    gui_path = sp_mod.GUI_CONFIG_PATH
    with open(gui_path, "w", encoding="utf-8") as f:
        json.dump({"observer_lat": 31.2, "observer_lon": 121.5}, f)
    host.set_observer_site(31.2, 121.5)

    path = tmp_path / "nulls.json"
    path.write_text(json.dumps({
        "schema_version": CONFIG_SCHEMA_VERSION,
        "gui_config": {"observer_lat": None, "observer_lon": None},
    }), encoding="utf-8")

    preview = panel.preview_import(str(path))
    assert preview.ok
    assert sorted(k for k, _old in preview.clears) == ["observer_lat", "observer_lon"]

    # 对话框默认全部不勾选 → 一项都不清（现值原样）
    dlg = sp_mod.ImportPreviewDialog(None, preview)
    assert dlg.selected_clear_keys() == []
    res = panel.apply_import(preview, clear_keys=dlg.selected_clear_keys())
    assert res.clears == []
    assert (host._observer_lat, host._observer_lon) == (31.2, 121.5)

    # 逐项勾选：只勾 observer_lat → 只执行这一项（清空任一坐标 = 整对清空，
    # 是既有的成对语义；这里钉的是「没勾的项不产生任何动作」）
    preview2 = panel.preview_import(str(path))
    dlg2 = sp_mod.ImportPreviewDialog(None, preview2)
    dlg2._clear_boxes[0][1].setChecked(True)
    assert dlg2.selected_clear_keys() == ["observer_lat"]
    res2 = panel.apply_import(preview2, clear_keys=dlg2.selected_clear_keys())
    assert [k for k, _old in res2.clears] == ["observer_lat"]
    assert host._observer_lat is None, "勾选的清空项必须真的清掉"


# ---------------------------------------------------------------------------
# 8. 生效时机标注：重启级只有 2 项；6 项「非重启但延迟」必须有橙色提示
# ---------------------------------------------------------------------------
#: 逐项对着代码核实过的「不是下一帧、也不需要重启应用」的项：
#: gnss_port/gnss_baudrate → 下次连接 GNSS（main_window.py:12512 才开串口）；
#: perf_profile → 刷新/GNSS 轮询立即、采样率默认值下次连接（_apply_perf_profile）；
#: record_dir → 下次录音（settings_panel.py:1277 注释 + help）；
#: rc_port/web_port → 重启对应服务（服务面板 tooltip / help）。
DELAYED_APPLY_KEYS = {
    "gnss_port", "gnss_baudrate", "perf_profile",
    "record_dir", "rc_port", "web_port",
}


def test_requires_restart_is_only_language_and_log_level():
    """重启应用级只有界面语言与日志级别；其余项不得冒充重启级。"""
    marked = {it.key for it in EXPOSED_ITEMS if it.requires_restart}
    assert marked == {"ui_language", "log_level"}


def test_delayed_items_carry_apply_note_and_not_restart_flag():
    """6 项延迟生效必须有提示，且不能靠 requires_restart 撒谎。"""
    noted = {it.key for it in EXPOSED_ITEMS if getattr(it, "apply_note", "")}
    assert noted == DELAYED_APPLY_KEYS
    for key in DELAYED_APPLY_KEYS:
        item = SCHEMA[key]
        assert not item.requires_restart, f"{key} 不需要重启应用，别乱标"
        assert item.apply_note.strip(), f"{key} 缺生效时机提示"
        assert ("生效" in item.apply_note) or ("影响" in item.apply_note)


def test_apply_note_reaches_the_widget_tooltip(env):
    """提示必须真的挂到控件上（只在 schema 里写一句等于没写）。"""
    ds, host, panel, _ = env
    # 2026-11-11 W51 后服务开关并入「设备与接口」，6 项 apply_note 全在本页。
    in_panel = [k for k in DELAYED_APPLY_KEYS if k in panel._rows]
    assert set(in_panel) == {"gnss_port", "gnss_baudrate",
                             "perf_profile", "record_dir",
                             "rc_port", "web_port"}
    for key in in_panel:
        assert SCHEMA[key].apply_note in panel._rows[key].toolTip()

    # 端口项在独立「服务设置」面板里也有一份：它自己的 tooltip 明说需重启服务
    svc = getattr(host, "service_settings_panel", None)
    if svc is not None:
        for widget in (svc.rc_port, svc.web_port):
            assert "重启" in widget.toolTip()
