# SPDX-License-Identifier: MIT
"""
桌面端 UI「有前端没后端」接线审计回归测试 (tests/test_ui_wiring_audit.py)
=========================================================================

背景：脚本化 AST 扫描 desktop/*.py 的控件创建 / signal.connect / 槽定义，
逐条肉眼复核后分成三类：

  A 有控件没接线（含"接了但槽不干事"）
  B 有槽没控件（死代码）
  C 改了不落盘 / 改了不下发

本文件钉住**本轮已修**的三处，并提供一条**死控件回归门禁**：

  1. module_panel：采样率下拉 sr_combo 与增益滑杆 gain_slider 改了必须进入
     连接载荷（dev["_sample_rate"] / dev["_gain"]）——修前这两个控件是死的，
     连接永远走默认 2.048 MHz / 20 dB。
  2. control_panel：删书签必须落到 FrequencyManager（修前只删内存列表，
     重启即复活）。测试注入假仓库，绝不碰用户真实的 ~/.mbdsdr/bookmarks.json。
  3. 死控件门禁：AST 扫 desktop/*.py，凡"创建了交互控件却既没 connect 也没被
     读取"的一律红；已在审计中确认合法（只读展示 / 对话框读用）的走白名单，
     白名单条目带原因，新增死控件必须显式登记。

运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_wiring_audit.py -v
"""
import ast
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _process_events(_unused=None):
    """只排一次 Qt 事件队列。

    刻意不跑 QEventLoop/QTimer：本仓已知 QTimer.singleShot(0) 在 offscreen
    平台不触发（Qt 6.5+ offscreen 无窗口系统唤醒源），测试会挂死；而本文件
    断言的都是同步信号（不跨线程），processEvents 足够。
    """
    app.processEvents()


@pytest.fixture(scope="module")
def mw_window(isolated_profile):
    """真实 MainWindow（隔离 profile）：接线类断言走行为，不扫源码文本。"""
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001 - 收尾失败不拖垮用例
        pass
    w.close()


# ===========================================================================
# 1. module_panel：采样率 / 增益 必须随控件下发
# ===========================================================================
class TestModulePanelParamsReachBackend:
    """修前：sr_combo / gain_slider 改了只改内存，连接恒用默认值。"""

    def test_sample_rate_combo_carries_hz(self):
        from constants import format_sample_rate
        from module_panel import ModulePanel
        p = ModulePanel()
        # 档位来自设备能力（不再有前端静态 5 档表）；显示文本按 U-03 统一
        # MS/s，Hz 仍挂 itemData 上（不靠解析文本，避免精度坑）
        p._apply_devices([{
            "label": "RTL-SDR", "driver": "rtlsdr",
            "sample_rate_range": (1_000_000, 2_560_000),
            # 16042e4b 起能力来源缺省按 unknown：只给数值不给来源，面板
            # 不再当「已知量程」，下拉会退化成一条「档位未知」。这里补的是
            # 设备**声明**来源（declared），不是放宽断言。
            "capability_status": {"sample_rate_range": "declared"},
        }])
        assert p.sr_combo.count() == 8
        assert p.sr_combo.itemText(0) == format_sample_rate(1_024_000)
        p.sr_combo.setCurrentIndex(0)
        assert p.selected_sample_rate_hz() == pytest.approx(1_024_000.0)
        p.sr_combo.setCurrentIndex(p.sr_combo.count() - 1)
        assert p.selected_sample_rate_hz() == pytest.approx(2_560_000.0)

    def test_gain_slider_value_readable(self):
        from module_panel import ModulePanel
        p = ModulePanel()
        p.gain_slider.setValue(37)
        _process_events(10)
        assert p.selected_gain_db() == pytest.approx(37.0)
        # 标签同步（原有行为，保留）
        assert p.gain_val.text() == "37 dB"

    def test_connect_payload_follows_widgets(self, monkeypatch):
        """点连接：dev 里必须带上当前采样率与增益，而不是默认常量。"""
        from PySide6.QtWidgets import QMessageBox
        from module_panel import ModulePanel
        # _on_connect 会弹"已选择设备"模态框；offscreen 下模态 exec 会挂死测试，
        # 这里打桩掉（本用例断言的是载荷，不是弹窗）。
        monkeypatch.setattr(QMessageBox, "information",
                            lambda *a, **k: QMessageBox.Ok)
        p = ModulePanel()
        got = []
        p.device_selected.connect(lambda d: got.append(dict(d)))
        # 注入一台"设备"（无硬件：面板枚举为空，这里直接塞内存表模拟选中）
        p._devices = [{
            "label": "FakeSDR", "driver": "rtlsdr",
            "sample_rate_range": (1_000_000, 2_560_000),
            # 同上：能力来源必须显式给出，否则档位表退化、选中项恒为兜底值。
            "capability_status": {"sample_rate_range": "declared"},
        }]
        p.device_combo.clear()
        p.device_combo.addItem("FakeSDR")
        p.device_combo.setCurrentIndex(0)      # 触发按设备能力重填采样率档位

        p.sr_combo.setCurrentIndex(0)          # 1.024 MHz（量程内第一档）
        p.gain_slider.setValue(42)
        p._on_connect()

        assert len(got) == 1, "点连接应发 device_selected"
        assert got[0]["_sample_rate"] == pytest.approx(1_024_000.0)
        assert got[0]["_gain"] == pytest.approx(42.0)
        assert got[0]["label"] == "FakeSDR"

    def test_no_device_no_emit(self):
        """无设备（真实空态）时点连接不得凭空造一台设备出来。"""
        from module_panel import ModulePanel
        p = ModulePanel()
        got = []
        p.device_selected.connect(lambda d: got.append(d))
        p._devices = []
        p.device_combo.clear()
        p._on_connect()
        assert got == [], "无设备时不得 emit（绝不假造设备）"

    def test_sample_rate_payload_has_single_write_entry(self, monkeypatch):
        """采样率进连接载荷只经 constants.set_connect_sample_rate 一个写入口。

        现状（修前）：``device_panel._on_connect_clicked`` 与
        ``module_panel._on_connect`` 各写一次 ``dev["_sample_rate"]``，主窗口
        读侧还带第三个默认值——同一份「没填」三处三个答案，改协议必须三处
        一起改，漏一处就是沉默错值。

        反证：把任一面板改回 ``dev["_sample_rate"] = ...`` 直写，spy 就少
        一次调用 → 本用例红。
        """
        import constants
        import device_panel as device_panel_mod
        import module_panel as module_panel_mod
        from PySide6.QtWidgets import QMessageBox

        calls = []

        def spy(payload, requested_hz=None):
            real = constants.set_connect_sample_rate
            calls.append(requested_hz)
            return real(payload, requested_hz)

        monkeypatch.setattr(device_panel_mod, "set_connect_sample_rate", spy)
        monkeypatch.setattr(module_panel_mod, "set_connect_sample_rate", spy)
        monkeypatch.setattr(QMessageBox, "information",
                            lambda *a, **k: QMessageBox.Ok)

        # 设备面板：输入框显式 2.048 MS/s
        dp = device_panel_mod.DevicePanel()
        dp._hotplug_timer.stop()
        got_dev = []
        dp.connect_requested.connect(lambda d: got_dev.append(dict(d)))
        dp._devices = [{
            "label": "FakeSDR", "driver": "fake",
            "sample_rate_range": (250_000.0, 3_200_000.0),
            "gain_range": (0.0, 40.0),
        }]
        dp.dev_combo.clear()
        dp.dev_combo.addItem("FakeSDR")
        dp.dev_combo.setCurrentIndex(0)
        dp.sr_edit.setText("2048000")
        dp.gain_edit.setText("30")
        dp.ppm_edit.setText("0")
        dp._on_connect_clicked()

        # 模块面板：能力下拉第一档 1.024 MS/s
        mp = module_panel_mod.ModulePanel()
        got_mod = []
        mp.device_selected.connect(lambda d: got_mod.append(dict(d)))
        mp._apply_devices([{
            "label": "RTL-SDR", "driver": "rtlsdr",
            "sample_rate_range": (1_000_000, 2_560_000),
            "capability_status": {"sample_rate_range": "declared"},
        }])
        mp.sr_combo.setCurrentIndex(0)
        mp._on_connect()

        assert len(calls) == 2, "两个面板都必须走唯一写入口（直写该键即少一次）"
        assert got_dev and got_mod
        assert got_dev[0][constants.CONNECT_SAMPLE_RATE_KEY] == pytest.approx(
            2_048_000.0)
        assert got_mod[0][constants.CONNECT_SAMPLE_RATE_KEY] == pytest.approx(
            1_024_000.0)


# ===========================================================================
# 2. control_panel：删书签必须落盘
# ===========================================================================
class _FakeBookmarkStore:
    """假持久化仓库：只记录调用，绝不写用户磁盘。"""

    def __init__(self, names=("98.500MHz FM",)):
        self.removed = []
        self._names = list(names)

    def remove(self, name: str) -> bool:
        self.removed.append(name)
        return name in self._names


class TestBookmarkDeletePersisted:
    """修前：_on_bookmark_del 只删 self._bookmarks，FrequencyManager 里的
    记录还在，下次启动 load_bookmarks_from_list 会把删掉的书签原样读回来。"""

    def test_delete_writes_to_store(self, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from control_panel import ControlPanel
        # 产品已加"删除确认"二次确认（默认 No）；测试里只桩确认结果，
        # 两条落盘断言保持原样（不放宽比较）。
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        cp = ControlPanel()
        store = _FakeBookmarkStore()
        cp.set_bookmark_store(store)
        cp.load_bookmarks_from_list([(98.5e6, "98.500MHz FM", "FM")])

        # 选中第 0 条后点删除（currentData 即列表下标）
        cp.bookmark_combo.setCurrentIndex(0)
        cp._on_bookmark_del()

        assert cp._bookmarks == [], "内存列表应已删除"
        assert store.removed == ["98.500MHz FM"], \
            f"删除必须落到持久化仓库，实际: {store.removed}"

    def test_delete_without_selection_is_noop(self):
        """占位条目（currentData 为 None）时删除必须静默返回，不落盘不崩。"""
        from control_panel import ControlPanel
        cp = ControlPanel()
        store = _FakeBookmarkStore()
        cp.set_bookmark_store(store)
        cp.load_bookmarks_from_list([])          # 只有"暂无书签"占位项
        cp.bookmark_combo.setCurrentIndex(0)
        cp._on_bookmark_del()
        assert store.removed == []

    def test_store_unavailable_does_not_raise(self, monkeypatch):
        """仓库不可用（内核缺失 / 磁盘只读）时删除 UI 仍要完成，不抛异常。"""
        from PySide6.QtWidgets import QMessageBox
        from control_panel import ControlPanel
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        cp = ControlPanel()

        class _Boom:
            def remove(self, name):
                raise OSError("disk read-only")

        cp.set_bookmark_store(_Boom())
        cp.load_bookmarks_from_list([(144.39e6, "APRS", "NFM")])
        cp.bookmark_combo.setCurrentIndex(0)
        cp._on_bookmark_del()                    # 不应抛
        assert cp._bookmarks == []


# ===========================================================================
# 3. 死控件回归门禁（AST 扫描）
# ===========================================================================
WIDGET_CTORS = {
    "QCheckBox", "QPushButton", "QComboBox", "QSlider", "QSpinBox",
    "QDoubleSpinBox", "QLineEdit", "QDial", "QRadioButton", "QToolButton",
}

#: 判定"被读取"的方法名（read-on-use 形态：这些控件的值在按下按钮/提交对话框
#: 时才读，本身不需要 connect，属合法设计）
READ_METHODS = {
    "text", "value", "currentText", "currentData", "currentIndex",
    "currentRow", "isChecked", "toPlainText", "checkedButton", "isEnabled",
    "itemData", "itemText", "currentItem", "selectedItems", "count",
    "currentWidget", "isVisible", "checkState",
}

#: 白名单：已肉眼复核确认"合法未接线"的控件。
#:   键 = "文件名:控件名"，值 = 复核结论（为什么不算缺陷）。
#: 新增条目必须写明理由；想修白名单里的真缺陷时，请连理由一起更新。
ALLOWED_UNCONNECTED = {
    # 只读展示控件（NoEditTriggers / readOnly / NoSelection），本来就不该有信号
    "adsb_map_panel.py:self.table": "只读飞机列表（NoEditTriggers+NoSelection）",
    "aprs_panel.py:self.detail": "只读报文详情 QTextEdit",
    "ai_panel.py:self.conversation_view": "只读对话历史 QTextEdit",
    # 2026-11-11 删（不许续期）：`new_spacetime_panel.py:self.passes_table`
    # 已被 cb3250f4 接上 itemClicked → _on_pass_clicked（行定位/联动），
    # 不再是「只读过境表」，白名单条目过期即删。
    # 对话框/表单：按下"确定"后统一读值（read-on-use），不需要逐控件 connect
    "ai_panel.py:api_key_edit": "配置对话框：Accepted 后统一读 text()",
    "ai_panel.py:base_url_edit": "配置对话框：Accepted 后统一读 text()",
    "ai_panel.py:model_edit": "配置对话框：Accepted 后统一读 text()",
    "device_panel.py:self.gain_edit": "连接按钮读 text() → dev['_gain']",
    "device_panel.py:self.ppm_edit": "连接按钮读 text() → dev['_ppm']",
    "ntrip_panel.py:self.host_edit": "get_config()/连接按钮读 text()",
    "ntrip_panel.py:self.port_spin": "get_config()/连接按钮读 value()",
    "ntrip_panel.py:self.mount_combo": "get_config()/连接按钮读 currentText()",
    "ntrip_panel.py:self.user_edit": "get_config()/连接按钮读 text()",
    "ntrip_panel.py:self.pass_edit": "get_config()/连接按钮读 text()",
    "scanner_panel.py:self.start_sp": "_on_start_clicked 读 value()",
    "scanner_panel.py:self.stop_sp": "_on_start_clicked 读 value()",
    "scanner_panel.py:self.step_sp": "_on_start_clicked 读 value()",
    "scanner_panel.py:self.thr_sp": "_on_start_clicked 读 value()",
    "sat_track_panel.py:self.freq_spin": "下行频率 getter 读 value()",
    # 2026-11-06 删：`satellite_image_panel.py:self.sat_combo` 已接
    # currentTextChanged（NOAA 音频采集 ↔ METEOR LRPT 离线 IQ 模式切换），
    # 不再是"合法未接线"控件。
    # 2026-11-09 删：`new_spacetime_panel.py:self.alt_spin` 已随经纬度三个
    # SpinBox 一起移除 —— 面板不再持有可编辑站址（第二个写入口），
    # 站址唯一写入口在主窗 set_observer_site()，面板只读显示 + 「更改地点…」。
    "weather_panel.py:self.freq_edit": "开始解码读 text()",
    "weather_panel.py:self.sym_edit": "开始解码读 text()",
    "weather_panel.py:self.sr_edit": "开始解码读 text()",
    "main_window.py:right_tab": "QTabWidget 页签容器，不做业务接线",
    "main_window.py:sr_combo": "连接对话框：Accepted 后统一读 currentData()",
    "main_window.py:gain_spin": "连接对话框：Accepted 后统一读 value()",
    "main_window.py:ppm_spin": "连接对话框：Accepted 后统一读 value()",
    "main_window.py:agc_chk": "连接对话框：Accepted 后统一读 isChecked()",
    "main_window.py:offset_chk": "连接对话框：Accepted 后统一读 isChecked()",
    "main_window.py:start_sp": "扫频对话框：Accepted 后统一读 value()",
    "main_window.py:stop_sp": "扫频对话框：Accepted 后统一读 value()",
    "main_window.py:step_sp": "扫频对话框：Accepted 后统一读 value()",
    "main_window.py:dwell_sp": "扫频对话框：Accepted 后统一读 value()",
    "main_window.py:port_edit": "GNSS 对话框：Accepted 后统一读 text()",
    "main_window.py:baud_combo": "GNSS 对话框：Accepted 后统一读 currentData()",
    "spectrum_widget.py:menu": "右键菜单：exec() 返回值直接判定，无需 connect",
    # 2026-10-30 已修（C 类"改了不下发"）：带宽进 dev["_bandwidth"] 载荷，
    # main_window._connect_backend 增加 bandwidth 参数并下发 backend.set_bandwidth。
    # 旋钮本身仍走"Accepted 后统一读 value()"（与 sr_combo/gain_spin 同款），故留本条。
    "module_panel.py:self.bw_spin": "连接对话框：Accepted 后统一读 value() → dev[\"_bandwidth\"]（已下发）",
}


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _scan_file(path):
    """返回 (widgets, connected_owners, read_owners)。"""
    src = open(path, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src, path)
    widgets, connected, reads = {}, set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            val = node.value
            if not isinstance(val, ast.Call):
                continue
            fname = val.func.attr if isinstance(val.func, ast.Attribute) \
                else getattr(val.func, "id", None)
            if fname not in WIDGET_CTORS:
                continue
            tgt = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                tgt = node.targets[0]
            elif isinstance(node, ast.AnnAssign):
                tgt = node.target
            if isinstance(tgt, ast.Name):
                widgets[tgt.id] = node.lineno
            elif isinstance(tgt, ast.Attribute):
                d = _dotted(tgt)
                if d:
                    widgets[d] = node.lineno
        # 接线判定：connect 与 setMenu 都算接线——QToolButton 挂菜单即"按下有动作"
        # （菜单项自身在别处 connect；此前的扫描器不认 setMenu，把菜单按钮误报成死控件）。
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("connect", "setMenu") and node.args:
            sig_node = node.func.value
            if node.func.attr == "setMenu":
                # 菜单直接挂在控件本体上：self._more_btn.setMenu(menu) → owner 是本控件
                owner = _dotted(sig_node)
            else:
                if isinstance(sig_node, ast.Subscript):
                    sig_node = sig_node.value
                owner = _dotted(sig_node.value) \
                    if isinstance(sig_node, ast.Attribute) else None
            if owner:
                connected.add(owner)
        if isinstance(node, ast.Attribute) and node.attr in READ_METHODS:
            owner = _dotted(node.value)
            if owner:
                reads.add(owner)
    return widgets, connected, reads


def test_no_new_dead_interactive_widgets():
    """死控件门禁：新出现的"既没接线也没人读"的交互控件一律红。"""
    desk = os.path.join(_ROOT, "desktop")
    dead = []
    for fn in sorted(os.listdir(desk)):
        if not fn.endswith(".py"):
            continue
        widgets, connected, reads = _scan_file(os.path.join(desk, fn))
        for name, lineno in sorted(widgets.items(), key=lambda kv: kv[1]):
            if name in connected:
                continue
            # 局部变量：connect 时可能只写短名，这里两种写法都认
            short = name.split(".")[-1]
            if any(c.split(".")[-1] == short for c in connected):
                continue
            if any(r.split(".")[-1] == short for r in reads):
                continue
            key = f"{fn}:{name}"
            if key in ALLOWED_UNCONNECTED:
                continue
            dead.append((fn, lineno, name))
    assert dead == [], (
        "发现未登记的死控件（既无 signal.connect 也无人读取其值）：\n"
        + "\n".join(f"  desktop/{f}:{ln}  {n}" for f, ln, n in dead)
        + "\n\n若是合法的只读展示/读用控件，请在 ALLOWED_UNCONNECTED 里登记理由；"
          "若是真缺陷，请接线后再跑本测试。"
    )


def test_allowlist_entries_still_unconnected():
    """白名单自洽性：登记为'合法未接线'的控件，若哪天被接上了就把条目删掉
    （防止白名单无限膨胀、掩盖真实接线情况）。"""
    desk = os.path.join(_ROOT, "desktop")
    stale = []
    for key in ALLOWED_UNCONNECTED:
        fn, name = key.split(":", 1)
        path = os.path.join(desk, fn)
        if not os.path.exists(path):
            stale.append((key, "文件不存在"))
            continue
        widgets, connected, reads = _scan_file(path)
        short = name.split(".")[-1]
        if any(c.split(".")[-1] == short for c in connected):
            stale.append((key, "该控件已有 connect，白名单条目应删除"))
    assert stale == [], f"白名单过期条目: {stale}"


# ===========================================================================
# 4. 台账哨兵：本轮"只报未修"的项（改修后请同步更新断言与理由）
# ===========================================================================
class _RecBackend:
    """只记录下发的假后端（不开线程、不碰硬件）。"""

    def __init__(self, name="FakeSDR", max_gain=33.0):
        self.device = SimpleNamespace(name=name, max_gain=max_gain)
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


def test_module_bandwidth_now_reaches_backend(monkeypatch, mw_window):
    """带宽旋钮必须真的走完「面板 → 载荷 → 通用连接 → backend.set_bandwidth」。

    2026-10-30 修复前：dev 载荷只有 _sample_rate/_gain，_connect_backend 也不收
    bandwidth —— 用户调带宽被静默丢弃。这里用假后端记录下发，端到端验行为。
    """
    from PySide6.QtWidgets import QMessageBox
    from module_panel import ModulePanel
    import mbdsdr_ai.sdr_backend as sb

    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: QMessageBox.Ok)
    p = ModulePanel()
    p._devices = [{"label": "FakeSDR", "driver": "fake"}]
    p.device_combo.clear()
    p.device_combo.addItem("FakeSDR")
    p.device_combo.setCurrentIndex(0)
    p.sr_combo.setCurrentIndex(0)
    p.gain_slider.setValue(42)
    p.bw_spin.setValue(200_000.0)

    got = []
    p.device_selected.connect(lambda d: got.append(dict(d)))
    p._on_connect()
    assert got and got[0]["_bandwidth"] == pytest.approx(200_000.0), \
        f"带宽旋钮没进连接载荷：{got[:1]}（用户设了白设）"

    be = _RecBackend()
    monkeypatch.setattr(sb, "build_backend_for_device", lambda dev: be)
    monkeypatch.setattr(mw_window, "_start_iq_streams", lambda: None,
                        raising=False)
    monkeypatch.setattr(mw_window, "_teardown_connection", lambda *a, **k: None)

    mw_window._on_panel_device_connect(got[0])
    assert ("bandwidth", 200_000.0) in be.calls, \
        f"带宽没下发到后端 set_bandwidth：{be.calls}"

    # 复位增益范围，避免影响同模块其它用例
    mw_window.control_panel.set_gain_range(0.0, 49.0)
    mw_window.module_panel.set_gain_range(0.0, 49.0)


def test_anr_strength_applies_through_panel_internal_write():
    """ANR 强度滑杆实际生效（面板内部直接写 cfg.alpha），不是死控件。

    AnrPanel._on_strength_changed 内部直接写 `self._anr.cfg.alpha = alpha`；
    main_window._on_anr_strength 的 pass 只是冗余通知槽。这里注入假 ANR，
    拨滑杆看 cfg.alpha 是否真的被写——不用再钉源码字面量。
    """
    from types import SimpleNamespace

    from anr_panel import AnrPanel
    from main_window import MainWindow

    class _FakeAnr:
        def __init__(self):
            self.cfg = SimpleNamespace(alpha=0.0)
            self.enabled = False

    panel = AnrPanel()
    emitted = []
    panel.strength_changed.connect(emitted.append)
    fake = _FakeAnr()
    panel._anr = fake
    panel.strength_slider.setValue(55)

    assert fake.cfg.alpha == pytest.approx(5.5), (
        "ANR 强度滑杆没写进 anr.cfg.alpha（滑杆白调），实际 "
        f"{fake.cfg.alpha}")
    assert emitted and emitted[-1] == pytest.approx(5.5), \
        f"strength_changed 没发出正确 α：{emitted}"

    slot = getattr(MainWindow, "_on_anr_strength", None)
    assert callable(slot), "主窗口 _on_anr_strength 通知槽消失（请核对台账）"
    slot(object(), 5.5)   # 冗余通知槽必须能被调用且不崩
