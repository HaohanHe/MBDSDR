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
import ast
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import json  # noqa: E402

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from settings import (DEFAULTS, DesktopSettings, _coerce,  # noqa: E402
                      volume_hw_to_pct, volume_pct_to_hw)

# 仅剩「负向守卫」类用例（不许出现某写法）仍需扫源码，见 V2 判定。
_MAIN_WINDOW_SRC = open(
    os.path.join(_ROOT, "desktop", "main_window.py"), encoding="utf-8").read()


def _app():
    app = QApplication.instance()
    return app if app is not None else QApplication([])


def _gui_config_path(home):
    """隔离 profile 下的 gui_config.json（HOME 已重定向到 tmp，不碰用户真实文件）。"""
    return os.path.join(str(home), ".mbdsdr", "gui_config.json")


def _write_gui_config(home, payload):
    path = _gui_config_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


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
    assert _coerce("rc_port", "9000") == 9000
    assert _coerce("web_port", 9001) == 9001
    assert _coerce("rc_enabled", 1) is True
    assert _coerce("web_enabled", 0) is False
    # 坏端口必须回默认值，而不是回 0 / 字符串（否则「任何输入返回 0」也会绿）
    assert _coerce("rc_port", "not-a-port") == DEFAULTS["rc_port"]


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


def _reset_canonical_settings(w):
    """把 settings 打回默认态，模拟「用户从未设置过」（迁移只在该前提发生）。"""
    # theme 默认值取权威源 DEFAULTS（出厂深色 dark_car），不写死旧浅色值：
    # 产品迁移守卫是 `theme == DEFAULT_THEME`，helper 必须打到真正的出厂默认
    # 才能模拟「用户从未设置过」，否则旧 gui_config 主题永远迁不进来。
    for key, val in (("gnss_port", ""), ("gnss_baudrate", 9600),
                     ("theme", DEFAULTS["theme"])):
        w.settings.set(key, val)


def test_gui_config_legacy_values_migrate_into_settings(main_window, isolated_profile):
    """旧 gui_config 残留值必须一次性迁移回写 settings（读旧键→迁→回写）。

    行为断言：喂一份真实旧配置给 `_load_gui_config()`，看 settings 与窗口成员
    是否真被迁移值接管；再次加载不重复覆盖（幂等）。
    """
    w = main_window
    _write_gui_config(isolated_profile, {
        "gnss_port": "COM7", "gnss_baudrate": 115200, "theme": "dark",
    })
    _reset_canonical_settings(w)
    w._load_gui_config()

    assert w.settings.get("gnss_port") == "COM7", \
        "旧 gui_config 的 gnss_port 没进 settings（改了重启仍读旧值）"
    assert w.settings.get("gnss_baudrate") == 115200, \
        "旧 gui_config 的 gnss_baudrate 没进 settings"
    assert w.settings.get("theme") == "dark", "旧 gui_config 的 theme 没进 settings"
    assert w._gnss_port == "COM7" and w._gnss_baudrate == 115200, \
        "迁移只写了 settings，窗口运行态没接管（连接时会退回默认口）"

    # 一次性：settings 已非默认后，旧键不许再覆盖
    _write_gui_config(isolated_profile, {
        "gnss_port": "COM8", "gnss_baudrate": 57600, "theme": "figma",
    })
    w._load_gui_config()
    assert w.settings.get("gnss_port") == "COM7", "迁移不幂等：第二次加载又覆盖了"
    assert w.settings.get("gnss_baudrate") == 115200
    assert w.settings.get("theme") == "dark"


def test_gnss_dialog_persists_through_settings(main_window, monkeypatch):
    """GNSS 对话框改完必须写 settings（只写内存/gui_config 等于改了白改）。

    行为断言：真构造对话框（拦下真模态，直接替 exec 填表单），确定后检查
    settings 落值 + 新串口参数真的下发给了 GNSS 驱动。
    """
    from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit
    w = main_window

    class _FakeGnss:
        def __init__(self):
            self.started = None

        def stop(self):
            pass

        def start(self, port, baud):
            self.started = (port, baud)
            return True

    gnss = _FakeGnss()
    monkeypatch.setattr(w, "_gnss", gnss, raising=False)

    def _accept_with_values(dlg):
        edits = dlg.findChildren(QLineEdit)
        combos = dlg.findChildren(QComboBox)
        assert edits and combos, "GNSS 对话框缺少串口/波特率控件"
        edits[0].setText("COM9")
        idx = combos[0].findData(115200)
        assert idx >= 0, "波特率下拉里没有 115200 档"
        combos[0].setCurrentIndex(idx)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", _accept_with_values, raising=False)
    w._open_gnss_dialog()

    assert w.settings.get("gnss_port") == "COM9", \
        "对话框确定后没写 settings（重启丢串口）"
    assert w.settings.get("gnss_baudrate") == 115200
    assert gnss.started == ("COM9", 115200), \
        f"新串口参数没下发到 GNSS 驱动：{gnss.started}"


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
def test_touch_mode_ui_entry_exists(main_window, monkeypatch):
    """touch_mode 三态必须有可点 UI 入口（没有入口 = 用户只能手改 JSON）。

    行为断言：扫菜单里互斥可勾选项，逐个触发并看 settings/touch_manager 是否
    真的被切到 auto/on/off —— 不再钉菜单文案与 `_set_touch_mode` 字面量。
    """
    from touch_manager import touch_manager
    w = main_window
    monkeypatch.setattr(w, "_restoring_settings", False, raising=False)

    groups = {}
    for top in w.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        for act in menu.actions():
            sub = act.menu()
            if sub is None:
                continue
            for leaf in sub.actions():
                grp = leaf.actionGroup()
                if leaf.isCheckable() and grp is not None and grp.isExclusive():
                    groups.setdefault(id(grp), (grp, []))[1].append(leaf)

    switched = {}
    for grp, leaves in groups.values():
        if len(leaves) != 3:
            continue
        for leaf in leaves:
            leaf.trigger()
            after = str(w.settings.get("touch_mode"))
            switched[after] = touch_manager().is_touch_mode()
    assert set(switched) == {"auto", "on", "off"}, (
        "视图菜单里没有完整的三态触屏模式入口，实际切换到的值："
        f"{sorted(switched)}")
    assert switched["on"] is True and switched["off"] is False, \
        f"触屏入口只写了 settings，没作用到 TouchManager：{switched}"
    w._set_touch_mode("auto")   # 复位，不影响同模块其它用例


def test_panel_has_no_dead_slots(tmp_path, monkeypatch):
    """端口槽必须真的接上线：改控件值 → settings 落盘（死槽则什么都不发生）。

    旧写法扫 `connect(self._on_rc_port_changed)` 字面量；这里直接点控件，
    用「值有没有落到 settings」证明信号确实连到了槽。
    """
    _app()
    import settings_panel as sp
    monkeypatch.setattr(sp, "RemoteControl", FakeRemoteControl)
    s = DesktopSettings(path=str(tmp_path / "s.json"))
    panel = sp.ServiceSettingsPanel(settings=s)
    try:
        panel.rc_port.setValue(9001)
        panel.web_port.setValue(8123)
        assert s.get("rc_port") == 9001, \
            "_on_rc_port_changed 是死槽：端口控件改了却没落盘"
        assert s.get("web_port") == 8123, \
            "_on_web_port_changed 是死槽：端口控件改了却没落盘"
    finally:
        panel.shutdown_all()


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


def test_drifting_keys_migrate_into_settings_once(main_window, isolated_profile):
    """旧 gui_config 残留键迁移进 settings（含 MHz→Hz 单位转换），且只迁一次。"""
    w = main_window
    _write_gui_config(isolated_profile, {
        "current_freq_mhz": 145.5, "current_mode": "USB",
        "volume": 42, "show_waterfall": False,
    })
    for key, val in (("frequency_hz", 98_000_000.0), ("demod_mode", "FM"),
                     ("volume", 48), ("show_waterfall", True)):
        w.settings.set(key, val)
    w._load_gui_config()

    assert w.settings.get("frequency_hz") == pytest.approx(145_500_000.0), (
        "current_freq_mhz 没有按 MHz→Hz 换算迁移，实际 "
        f"{w.settings.get('frequency_hz')}")
    assert w.settings.get("demod_mode") == "USB"
    assert w.settings.get("volume") == volume_hw_to_pct(42), \
        "旧 0-63 量纲 volume 没有换算成 0-100 人类量纲"
    assert w.settings.get("show_waterfall") is False

    # 一次性：settings 已非默认后，旧键不许再覆盖
    _write_gui_config(isolated_profile, {
        "current_freq_mhz": 433.0, "current_mode": "LSB",
        "volume": 0, "show_waterfall": True,
    })
    w._load_gui_config()
    assert w.settings.get("frequency_hz") == pytest.approx(145_500_000.0), \
        "迁移不幂等：第二次加载又覆盖了 frequency_hz"
    assert w.settings.get("demod_mode") == "USB"


def test_volume_and_waterfall_now_persist_through_settings(main_window, monkeypatch):
    """音量/瀑布改动必须写 settings（只写内存=改了重启丢）。"""
    w = main_window
    monkeypatch.setattr(w, "_restoring_settings", False, raising=False)

    w._on_volume_changed(37)
    assert w.settings.get("volume") == 37, "音量改动没写 settings（重启丢失）"

    before = bool(getattr(w.spectrum._state, "show_waterfall", None))
    w._toggle_waterfall()
    after = bool(getattr(w.spectrum._state, "show_waterfall", None))
    assert after is not before, "瀑布没真的切换（无法验证持久化）"
    assert w.settings.get("show_waterfall") is after, \
        "瀑布可见性改动没写 settings（重启回退）"
    w._toggle_waterfall()   # 复位


def _sky_obj(freq_hz):
    """天空图里的一颗卫星（只带联动需要的字段）。"""
    return SimpleNamespace(obj_type="satellite", azimuth_deg=210.0,
                           elevation_deg=35.0, name="ISS",
                           frequency_hz=freq_hz)


@pytest.fixture(scope="module")
def main_window(isolated_profile):
    """真实 MainWindow（offscreen）：把接线/持久化当行为测，而不是正则钉源码。"""
    _app()  # QWidget 必须先有 QApplication，否则 Qt 直接 abort
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001 - 收尾失败不该拖垮用例
        pass
    w.close()


@pytest.fixture(scope="module")
def sky_main_window(main_window):
    """天空图联动复用同一个窗口（构造一次，整模块共享）。"""
    return main_window


def test_satellite_tune_updates_control_panel(sky_main_window, monkeypatch):
    """调谐成功后，调谐后的真值必须回填控制面板。

    旧写法用正则钉 `float(obj.frequency_hz)` 字面量，天空线把中间变量改成
    局部 `freq_hz` + None 守卫就误红（语义没退化）。这里改成行为断言。
    """
    w = sky_main_window
    tuned = []

    class _Backend:
        def set_frequency(self, hz):
            tuned.append(hz)

    monkeypatch.setattr(w, "_active_sdr_backend", _Backend(), raising=False)
    monkeypatch.setattr(w, "_worker", None, raising=False)
    monkeypatch.setattr(w, "_update_main_vfo_offset", lambda: None)

    w._on_sky_object_clicked(_sky_obj(435_000_000.0))

    assert tuned == [435_000_000.0], "后端没收到调谐命令"
    assert w.control_panel.get_frequency_hz() == 435_000_000.0, \
        "调谐成功后控制面板没有回填（联动显示不更新）"
    assert "已调谐到 435.000 MHz" in w.statusBar().currentMessage()


def test_satellite_without_frequency_does_not_tune(sky_main_window, monkeypatch):
    """未登记下行频率：不许下发调谐，控制面板保持原值，状态栏如实说原因。"""
    w = sky_main_window
    tuned = []

    class _Backend:
        def set_frequency(self, hz):
            tuned.append(hz)

    monkeypatch.setattr(w, "_active_sdr_backend", _Backend(), raising=False)
    monkeypatch.setattr(w, "_worker", None, raising=False)
    monkeypatch.setattr(w, "_update_main_vfo_offset", lambda: None)

    before = w.control_panel.get_frequency_hz()
    w._on_sky_object_clicked(_sky_obj(None))

    assert tuned == [], "没有频率时仍下发了调谐"
    assert w.control_panel.get_frequency_hz() == before, "无频率时控制面板被改了"
    msg = w.statusBar().currentMessage()
    assert "未调谐频率" in msg and "没有配置下行频率" in msg, msg


class _FakeSkyWindow:
    """只承载 `_on_sky_object_clicked` 所需属性，用于跑变异后的函数副本。"""

    def __init__(self):
        self.tuned = []
        self.messages = []
        self.sky_view = SimpleNamespace(set_antenna=lambda a: None)
        self._worker = None
        self._vfo_center_hz = 0.0
        self._backend_center_hz = 0.0
        self.freq_label = SimpleNamespace(setText=lambda t: None)
        self.status_freq = SimpleNamespace(setText=lambda t: None)
        self.control_panel = SimpleNamespace(
            set_frequency_hz=lambda hz: self.tuned.append(hz))
        self._active_sdr_backend = SimpleNamespace(
            set_frequency=lambda hz: self.tuned.append(hz))

    def _update_main_vfo_offset(self):
        pass

    def statusBar(self):
        window = self
        return SimpleNamespace(
            showMessage=lambda text, *a: window.messages.append(text))


def _mutated_sky_handler(remove_guard: bool):
    """从 main_window 源码取 `_on_sky_object_clicked`（可选去掉 None 守卫）。

    反证用：不改生产文件，在内存里跑一份变异副本，证明守卫是"有牙"的。
    守卫定位走 AST（找到含 `is not None` 比较的 if 分支），不再钉字面量
    `src.count(guard)`——那会在守卫换写法时假红（本段已栽过三次）。
    """
    tree = ast.parse(_MAIN_WINDOW_SRC)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "_on_sky_object_clicked")
    if remove_guard:
        guards = [
            sub for sub in ast.walk(node)
            if isinstance(sub, ast.If) and any(
                isinstance(cmp, ast.Compare)
                and any(isinstance(op, ast.IsNot) for op in cmp.ops)
                for cmp in ast.walk(sub.test))
        ]
        assert len(guards) == 1, (
            "找不到唯一的 None 守卫 if 分支（实际 %d 个）："
            "反证需要按新的控制流重新定位" % len(guards))
        guards[0].test = ast.Constant(value=True)
        ast.fix_missing_locations(node)
    src = ast.unparse(node)
    import main_window as mw
    ns = {}
    exec(compile(src, "<sky-handler>", "exec"), vars(mw), ns)  # noqa: S102
    return ns["_on_sky_object_clicked"]


def test_removing_none_guard_breaks_the_unregistered_frequency_case():
    """反证：去掉 None 守卫后，同一场景不再给出「没有配置下行频率」的正确解释。

    正确实现里 `frequency_hz=None` 走 else 分支；变异后强行走调谐分支，
    报的是 float(None) 的 TypeError——test_satellite_without_frequency_does_not_tune
    里对这句文案的断言会因此变红，证明该断言确实钉住了守卫。
    """
    handler = _mutated_sky_handler(remove_guard=True)
    fake = _FakeSkyWindow()
    handler(fake, _sky_obj(None))
    assert fake.messages, "变异后连状态栏都没写"
    assert "没有配置下行频率" not in fake.messages[-1], \
        "去掉守卫后仍给出正确解释 = 反证失效"
    assert "TypeError" in fake.messages[-1]


def test_no_nonexistent_freq_spin_widget():
    """修前写 `freq_spin.setValue`——属性不存在，异常被 pass 吞掉 = 死路径。"""
    assert "freq_spin.setValue" not in _MAIN_WINDOW_SRC


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


class _FakeWorker:
    """只记录 `request_tool.emit(name, args)` 的假 worker。"""

    def __init__(self):
        self.calls = []
        self.request_tool = SimpleNamespace(
            emit=lambda name, args: self.calls.append((name, dict(args))))


class _FakeAudioPlayer:
    def __init__(self):
        self.gains = []

    def set_gain(self, gain):
        self.gains.append(gain)


def test_volume_wire_conversion_isolated_at_boundary(main_window, monkeypatch):
    """0-100 人类量纲只在 settings 两个纯函数里换算；下发/回填两端都验真值。"""
    w = main_window
    worker = _FakeWorker()
    audio = _FakeAudioPlayer()
    monkeypatch.setattr(w, "_worker", worker, raising=False)
    monkeypatch.setattr(w, "_audio_player", audio, raising=False)
    monkeypatch.setattr(w, "_restoring_settings", False, raising=False)

    w._on_volume_changed(30)
    assert worker.calls == [("set_volume", {"volume": volume_pct_to_hw(30)})], (
        "下发边界的 0-100→0-63 换算错误：" f"{worker.calls}（应为 19）")
    # 声卡增益走感知平方律 volume_pct_to_gain（SDR++ powf(v,2)，上限 1.0）：
    # 旧式 volume/100*2.0 在 72% 就把 20% 样本削成方波，是「过载」根因，已废。
    from mbdsdr_ai.audio_out import volume_pct_to_gain
    assert audio.gains[-1] == pytest.approx(volume_pct_to_gain(30)), (
        "声卡增益没按平方律 volume_pct_to_gain（0-100→0.0-1.0），实际 "
        f"{audio.gains[-1]}（应为 {volume_pct_to_gain(30):.4f}；旧式 ×2.0 已废）")
    assert audio.gains[-1] <= 1.0, "声卡增益超过 1.0 会硬削过载（平方律上限红线）"

    # 反向边界：硬件 0-63 量纲回填 UI 必须转回 0-100
    w.control_panel.set_volume(0)
    w._sync_ui_for_tool("set_volume", {"volume": 63})
    assert w.control_panel.volume_slider.value() == 100, \
        "硬件量纲 63 回填 UI 没换算成 100（0-100 人类量纲被破坏）"


class _ConnectBackend:
    """连接流程用的最小假后端（只记录下发，不开线程）。"""

    def __init__(self, name, max_gain):
        self.device = SimpleNamespace(name=name, max_gain=max_gain)
        self.status = SimpleNamespace(error="")
        self.calls = []

    def connect(self):
        return True

    def disconnect(self):
        pass

    def get_status(self):
        return SimpleNamespace(error="")

    def set_sample_rate(self, r):
        self.calls.append(("sample_rate", float(r)))

    def set_bandwidth(self, b):
        self.calls.append(("bandwidth", float(b)))

    def set_frequency(self, f):
        self.calls.append(("frequency", float(f)))

    def set_ppm(self, p):
        pass

    def set_offset_tuning(self, b):
        pass

    def set_agc(self, a):
        pass

    def set_gain(self, g):
        pass


def test_gain_dynamic_range_wired_on_connect(main_window, monkeypatch):
    """增益滑杆范围必须在连接成功后按设备能力收放（不再是死硬编码 0-49）。"""
    w = main_window
    monkeypatch.setattr(w, "_start_iq_streams", lambda: None, raising=False)
    monkeypatch.setattr(w, "_teardown_connection", lambda *a, **k: None)

    backend = _ConnectBackend("FakeSDR", 33.0)
    assert w._connect_backend(backend) is True

    assert w.control_panel.gain_slider.maximum() == 33, (
        "连接后控制面板增益上限没按 backend.device.max_gain 收放，实际 "
        f"{w.control_panel.gain_slider.maximum()}")
    assert w.module_panel.gain_slider.maximum() == 33, (
        "连接后模块面板增益上限没按 backend.device.max_gain 收放，实际 "
        f"{w.module_panel.gain_slider.maximum()}")

    # 复位到未连接兜底范围，避免影响后续用例
    w.control_panel.set_gain_range(0.0, 49.0)
    w.module_panel.set_gain_range(0.0, 49.0)
