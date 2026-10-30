# SPDX-License-Identifier: MIT
"""
设置面收敛钉扎测试 (tests/test_settings_convergence.py)
======================================================

背景（2026-10-30 设置面审计）：桌面端曾存在三处"设置不符合人类"的断裂：

  1. 服务开关（远程控制 TCP / Web）改了**不落盘**——重启回到默认关闭。
  2. `theme` / `gnss_port` / `gnss_baudrate` 在 settings（desktop_settings.json）
     与 gui_config（~/.mbdsdr/gui_config.json）**双写双读**，GNSS 对话框只写
     gui_config，settings 那份永远停在默认值 → 改了可能重启读回旧值。
  3. `touch_mode` 三态（auto/on/off）在 settings 里登记、`_on_touch_mode_changed`
     也预留了适配路径，却**没有任何 UI 入口**，用户只能手改 JSON 文件。

收敛契约（本文件钉住，防止悄悄回退）：
  * 服务开关/端口：登记在 DEFAULTS 且类型归一正确（端口 int 不许被 str 化）。
  * 三重叠键的唯一权威源是 settings；gui_config 的 `_save_gui_config` 不再写它们。
  * gui_config 旧残留按"读旧键 → 迁移 → 回写 settings"一次性迁移（幂等）。
  * `ServiceSettingsPanel` 开关/端口改动必须持久化；`restore_enabled_services`
    只在持久化值为 True 时才起服务（默认全关红线不变）。
  * 触屏模式的 UI 入口存在（视图→触屏模式 + `_set_touch_mode`）。

红线：不碰真网络/真服务。RemoteControl 用假类注入验证**面板调用链**，
不启动真实 TCP 监听；断言的是"面板调了 start/stop 与持久化"，不是服务可用。
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import json  # noqa: E402

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from settings import DEFAULTS, DesktopSettings, _coerce  # noqa: E402

_MAIN_WINDOW_SRC = open(
    os.path.join(_ROOT, "desktop", "main_window.py"), encoding="utf-8").read()
_SETTINGS_PANEL_SRC = open(
    os.path.join(_ROOT, "desktop", "settings_panel.py"), encoding="utf-8").read()


def _app():
    app = QApplication.instance()
    return app if app is not None else QApplication([])


class FakeRemoteControl:
    """只记录 start/stop 的假服务类（验证调用链，不监听端口）。"""

    instances = []

    def __init__(self, backend=None, host="127.0.0.1", port=7356):
        self.host = host
        self.port = port
        self.started = False
        self.stopped = False
        FakeRemoteControl.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


# ---------------------------------------------------------------- 服务开关持久化
def test_service_keys_registered_with_correct_types():
    for key, expect in (("rc_enabled", bool), ("rc_port", int),
                        ("web_enabled", bool), ("web_port", int)):
        assert key in DEFAULTS, f"{key} 必须登记，否则会被静默忽略"
        assert isinstance(_coerce(key, DEFAULTS[key]), expect), (
            f"{key} 归一后类型应为 {expect.__name__}")


def test_port_keys_never_coerced_to_str():
    """端口键不登记进 _INT_KEYS 会被 _coerce 落成字符串，下游 int() 再炸一次。"""
    assert isinstance(_coerce("rc_port", "9000"), int)
    assert isinstance(_coerce("web_port", 9001), int)
    assert isinstance(_coerce("rc_enabled", 1), bool)


def test_service_switch_and_port_roundtrip(tmp_path):
    p = str(tmp_path / "s.json")
    s = DesktopSettings(path=p)
    s.set("rc_enabled", True)
    s.set("rc_port", 7357)
    s.set("web_enabled", True)
    s.set("web_port", 8123)
    s.flush()
    s2 = DesktopSettings.load(path=p)
    assert s2.get("rc_enabled") is True
    assert s2.get("rc_port") == 7357 and isinstance(s2.get("rc_port"), int)
    assert s2.get("web_enabled") is True
    assert s2.get("web_port") == 8123


# ---------------------------------------------------------------- 单一权威源
def test_gui_config_no_longer_writes_canonical_keys():
    """theme/gnss_* 的唯一权威源是 settings；gui_config 保存侧不得再写。"""
    save_block = _MAIN_WINDOW_SRC.split("def _save_gui_config", 1)[1] \
        .split("def _load_gui_config", 1)[0]
    for key in ("gnss_port", "gnss_baudrate"):
        assert f'config["{key}"]' not in save_block, \
            f"_save_gui_config 双写 {key}：改了重启会读回旧值"
        assert f'"{key}"' not in save_block, \
            f"_save_gui_config 不该出现 {key} 键（权威源是 settings）"
    assert '"theme"' not in save_block, \
        "_save_gui_config 双写 theme：改了重启会回退"


def test_gui_config_legacy_values_migrate_into_settings():
    """旧 gui_config 残留值必须一次性迁移回写 settings（读旧键→迁→回写）。"""
    load_block = _MAIN_WINDOW_SRC.split("def _load_gui_config", 1)[1]
    assert 'settings.set("gnss_port"' in load_block
    assert 'settings.set("gnss_baudrate"' in load_block
    assert 'settings.set("theme"' in load_block


def test_gnss_dialog_persists_through_settings():
    """GNSS 对话框改完必须写 settings（只写内存/gui_config 等于改了白改）。"""
    dlg_block = _MAIN_WINDOW_SRC.split("def _open_gnss_dialog", 1)[1] \
        .split("def _disconnect_gnss", 1)[0]
    assert 'settings.set("gnss_port"' in dlg_block
    assert 'settings.set("gnss_baudrate"' in dlg_block


# ---------------------------------------------------------------- 面板行为
def test_panel_persists_switch_and_port(tmp_path, monkeypatch):
    _app()
    import settings_panel as sp
    monkeypatch.setattr(sp, "RemoteControl", FakeRemoteControl)
    FakeRemoteControl.instances.clear()

    s = DesktopSettings(path=str(tmp_path / "s.json"))
    panel = sp.ServiceSettingsPanel(settings=s)
    try:
        panel.rc_check.setChecked(True)          # 触发 toggled → 起服务 + 落盘
        assert s.get("rc_enabled") is True
        assert panel._rc is not None and panel._rc.started
        panel.rc_port.setValue(9001)             # 触发 valueChanged → 落盘
        assert s.get("rc_port") == 9001
        panel.web_port.setValue(8123)
        assert s.get("web_port") == 8123
        panel.rc_check.setChecked(False)         # 关停并落盘
        assert s.get("rc_enabled") is False
        assert panel._rc is None
    finally:
        panel.shutdown_all()


def test_restore_only_starts_services_the_user_enabled(tmp_path, monkeypatch):
    """红线：默认全关。只恢复用户上次显式勾过的，绝不自行监听。"""
    _app()
    import settings_panel as sp
    monkeypatch.setattr(sp, "RemoteControl", FakeRemoteControl)
    FakeRemoteControl.instances.clear()

    s = DesktopSettings(path=str(tmp_path / "s.json"))
    s.set("rc_enabled", False)
    s.set("web_enabled", False)
    s.flush()
    s2 = DesktopSettings.load(path=str(tmp_path / "s.json"))
    panel = sp.ServiceSettingsPanel(settings=s2)
    try:
        panel.restore_enabled_services()
        assert panel._rc is None, "用户没勾过就不许起服务"
        assert panel._web is None
    finally:
        panel.shutdown_all()

    s.set("rc_enabled", True)
    s.flush()
    s3 = DesktopSettings.load(path=str(tmp_path / "s.json"))
    panel2 = sp.ServiceSettingsPanel(settings=s3)
    try:
        panel2.restore_enabled_services()
        assert panel2._rc is not None and panel2._rc.started
    finally:
        panel2.shutdown_all()


def test_panel_without_settings_does_not_crash():
    _app()
    import settings_panel as sp
    panel = sp.ServiceSettingsPanel()   # 不注入 settings：旧行为，不落盘、不崩
    try:
        panel.rc_check.setChecked(True)
        panel.rc_check.setChecked(False)
    finally:
        panel.shutdown_all()


# ---------------------------------------------------------------- 触屏入口
def test_touch_mode_ui_entry_exists():
    """touch_mode 三态必须有 UI 入口；没有入口 = 用户只能手改 JSON（反人类）。"""
    assert "触屏模式" in _MAIN_WINDOW_SRC
    assert "def _set_touch_mode" in _MAIN_WINDOW_SRC
    assert '_set_touch_mode(m)' in _MAIN_WINDOW_SRC or \
           "_set_touch_mode(m)" in _MAIN_WINDOW_SRC


def test_panel_has_no_dead_slots():
    """面板里定义的槽必须都接了线（防再出现"槽写好没 connect"的死槽）。"""
    for slot in ("_on_rc_port_changed", "_on_web_port_changed"):
        assert f"def {slot}" in _SETTINGS_PANEL_SRC
        assert f"connect(self.{slot})" in _SETTINGS_PANEL_SRC, \
            f"{slot} 是死槽（定义了但没 connect）"


# ---------------------------------------------------------------- 配置键漂移收敛
def test_volume_and_waterfall_registered():
    """音量/瀑布可见性必须进 settings（此前只有 gui_config 键，双源漂移）。"""
    assert "volume" in DEFAULTS
    assert "show_waterfall" in DEFAULTS
    assert isinstance(_coerce("volume", "21"), int)
    assert isinstance(_coerce("show_waterfall", 0), bool)


def test_gui_config_no_longer_writes_drifting_keys():
    """current_freq_mhz（MHz！）/current_mode/volume/show_waterfall 不再双写。"""
    save_block = _MAIN_WINDOW_SRC.split("def _save_gui_config", 1)[1] \
        .split("def _load_gui_config", 1)[0]
    for key in ("current_freq_mhz", "current_mode", "volume", "show_waterfall"):
        assert f'config["{key}"]' not in save_block, \
            f"_save_gui_config 双写 {key}：gui_config 恢复会覆盖 settings 值"


def test_drifting_keys_migrate_into_settings_once():
    """旧 gui_config 残留键迁移进 settings（含 MHz→Hz 单位转换）。"""
    load_block = _MAIN_WINDOW_SRC.split("def _load_gui_config", 1)[1]
    assert 'settings.set("frequency_hz"' in load_block
    assert "* 1e6" in load_block, "current_freq_mhz 是 MHz，必须换算成 Hz"
    assert 'settings.set("demod_mode"' in load_block
    assert 'settings.set("volume"' in load_block
    assert 'settings.set("show_waterfall"' in load_block


def test_volume_and_waterfall_now_persist_through_settings():
    """音量/瀑布改动必须写 settings（只写内存=改了重启丢）。"""
    assert 'settings.set("volume"' in _MAIN_WINDOW_SRC
    assert 'settings.set("show_waterfall"' in _MAIN_WINDOW_SRC


def test_satellite_tune_updates_control_panel():
    """卫星跟踪调谐必须真的更新控制面板频率（修前写 freq_spin——属性不存在，
    异常被 pass 吞掉 = 联动显示永不更新的死路径）。"""
    assert "freq_spin.setValue" not in _MAIN_WINDOW_SRC, \
        "freq_spin 不存在（真实控件是 freq_input），不许再调用它"
    assert "self.control_panel.set_frequency_hz(float(obj.frequency_hz))" in _MAIN_WINDOW_SRC


# ------------------------------------------- 音量量纲收敛（0-100 人类量纲，2026-10-30）
def test_volume_scale_helpers_roundtrip():
    """0-100 ↔ 0-63 换算只在 settings 两个纯函数里；越界输入夹进量纲，不炸。"""
    from settings import volume_pct_to_hw, volume_hw_to_pct
    assert volume_pct_to_hw(100) == 63
    assert volume_pct_to_hw(0) == 0
    assert volume_hw_to_pct(63) == 100
    assert volume_hw_to_pct(0) == 0
    assert volume_pct_to_hw(150) == 63
    assert volume_hw_to_pct(99) == 100


def test_volume_legacy_scale_migrates_once(tmp_path):
    """旧 desktop_settings.json（0-63 量纲、无 volume_scale_v2 标记）一次性换算且幂等。"""
    _app()
    from settings import DesktopSettings
    p = tmp_path / "desktop_settings.json"
    p.write_text(json.dumps({"volume": 42}), encoding="utf-8")
    s = DesktopSettings.load(path=str(p))
    assert s.get("volume") == 67, f"42(0-63) 应迁移到 67(0-100)，实际 {s.get(volume)}"
    s.flush()
    s2 = DesktopSettings.load(path=str(p))
    assert s2.get("volume") == 67, "迁移必须幂等（二次加载不许再换算）"


def test_volume_wire_conversion_isolated_at_boundary():
    """main_window 不许再散写 63/100 公式——换算走 settings 纯函数。"""
    assert "volume_pct_to_hw" in _MAIN_WINDOW_SRC
    assert "volume_hw_to_pct" in _MAIN_WINDOW_SRC
    assert "volume / 63.0" not in _MAIN_WINDOW_SRC, \
        "音频增益换算已改 0-100 基准（volume/100*2.0），不得回退 0-63 公式"


def test_gain_dynamic_range_wired_on_connect():
    """增益滑杆范围必须在连接成功后按设备能力收放（不再是死硬编码）。"""
    assert "set_gain_range" in _MAIN_WINDOW_SRC, \
        "_connect_backend 必须调 set_gain_range 按 backend.device.max_gain 收放"
    assert "max_gain" in _MAIN_WINDOW_SRC
