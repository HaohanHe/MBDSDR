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


# ===========================================================================
# 1. module_panel：采样率 / 增益 必须随控件下发
# ===========================================================================
class TestModulePanelParamsReachBackend:
    """修前：sr_combo / gain_slider 改了只改内存，连接恒用默认值。"""

    def test_sample_rate_combo_carries_hz(self):
        from module_panel import ModulePanel
        p = ModulePanel()
        # 档位文本保持原样，Hz 挂在 itemData 上（不靠解析文本，避免精度坑）
        assert p.sr_combo.count() == 5
        assert p.sr_combo.itemText(1) == "1.44 MHz"
        p.sr_combo.setCurrentIndex(1)
        assert p.selected_sample_rate_hz() == pytest.approx(1_440_000.0)
        p.sr_combo.setCurrentIndex(4)
        assert p.selected_sample_rate_hz() == pytest.approx(3_200_000.0)

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
        p._devices = [{"label": "FakeSDR", "driver": "fake"}]
        p.device_combo.clear()
        p.device_combo.addItem("FakeSDR")
        p.device_combo.setCurrentIndex(0)

        p.sr_combo.setCurrentIndex(0)          # 1.024 MHz
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

    def test_delete_writes_to_store(self):
        from control_panel import ControlPanel
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

    def test_store_unavailable_does_not_raise(self):
        """仓库不可用（内核缺失 / 磁盘只读）时删除 UI 仍要完成，不抛异常。"""
        from control_panel import ControlPanel
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
    "new_spacetime_panel.py:self.passes_table": "只读过境表（NoEditTriggers）",
    # 对话框/表单：按下"确定"后统一读值（read-on-use），不需要逐控件 connect
    "ai_panel.py:api_key_edit": "配置对话框：Accepted 后统一读 text()",
    "ai_panel.py:base_url_edit": "配置对话框：Accepted 后统一读 text()",
    "ai_panel.py:model_edit": "配置对话框：Accepted 后统一读 text()",
    "device_panel.py:self.dev_combo": "连接按钮 _on_connect_clicked 读 currentIndex()",
    "device_panel.py:self.sr_edit": "连接按钮读 text() → dev['_sample_rate']",
    "device_panel.py:self.gain_edit": "连接按钮读 text() → dev['_gain']",
    "device_panel.py:self.ppm_edit": "连接按钮读 text() → dev['_ppm']",
    "doppler_panel.py:self.f0_edit": "开始定轨 / 加载 IQ 时读 text()",
    "doppler_panel.py:self.fs_edit": "加载 IQ 文件时读 text()",
    "doppler_panel.py:self.lat_edit": "_ground_station() 读 text()",
    "doppler_panel.py:self.lon_edit": "_ground_station() 读 text()",
    "doppler_panel.py:self.alt_edit": "_ground_station() 读 text()",
    "doppler_panel.py:self.ekf_radio": "_estimator() 读 isChecked()",
    "doppler_panel.py:self.rls_radio": "_estimator() 读 isChecked()",
    "doppler_panel.py:self.ls_radio": "_estimator() 读 isChecked()",
    "doppler_panel.py:self.est_group": "QButtonGroup 容器，状态经三个 radio 读取",
    "doppler_panel.py:self.tle_edit": "开始定轨读 toPlainText()",
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
    "satellite_image_panel.py:self.sat_combo": "开始解码读 currentText()",
    "new_spacetime_panel.py:self.lat_spin": "刷新过境读 value()",
    "new_spacetime_panel.py:self.lon_spin": "刷新过境读 value()",
    "new_spacetime_panel.py:self.alt_spin": "刷新过境读 value()",
    "weather_panel.py:self.freq_edit": "开始解码读 text()",
    "weather_panel.py:self.sym_edit": "开始解码读 text()",
    "weather_panel.py:self.sr_edit": "开始解码读 text()",
    "settings_panel.py:self.rc_port": "启动远程控制读 value()（端口不落盘另列 C 类台账）",
    "settings_panel.py:self.web_port": "启动 Web 服务读 value()（端口不落盘另列 C 类台账）",
    "main_window.py:left_tab": "QTabWidget 页签容器，不做业务接线",
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
    # 本轮审计确认的**真**死控件（未修，需后端/主窗口配合，见审计报告）
    "module_panel.py:self.bw_spin": (
        "【已知未修 A 类】带宽无 connect 且无人读取：连接协议 dev 里根本没有 "
        "带宽字段，main_window._connect_backend 也不接受 bandwidth；需主窗口侧 "
        "补下发链后再接，本轮只报不改"),
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
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "connect" and node.args:
            sig_node = node.func.value
            if isinstance(sig_node, ast.Subscript):
                sig_node = sig_node.value
            if isinstance(sig_node, ast.Attribute):
                owner = _dotted(sig_node.value)
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
def test_known_unfixed_module_bandwidth_is_documented():
    """module_panel 带宽旋钮（bw_spin）本轮未修：它是真的没接线，
    且连接协议里没有带宽字段。修它时请同步删除 ALLOWED_UNCONNECTED 里
    那条 '【已知未修 A 类】' 登记（AGENTS.md：不许删断言，只许随修复更新）。"""
    from module_panel import ModulePanel
    p = ModulePanel()
    assert p.bw_spin is not None
    assert "【已知未修 A 类】" in ALLOWED_UNCONNECTED["module_panel.py:self.bw_spin"]


def test_anr_strength_slot_empty_is_reported_not_silently_fixed():
    """main_window._on_anr_strength 是 `pass`（ANR 强度滑杆改了完全没反应）。
    main_window 本轮禁用改动，故只登记不断言修复；这里钉住'槽确实存在'，
    等解禁修好后本条应改为断言真实下发。"""
    src = open(os.path.join(_ROOT, "desktop", "main_window.py"),
               encoding="utf-8").read()
    assert "def _on_anr_strength" in src, "ANR 强度槽消失请核对台账"
