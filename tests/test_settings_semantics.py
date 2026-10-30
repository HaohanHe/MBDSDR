# SPDX-License-Identifier: MIT
"""
设置语义钉扎测试 (tests/test_settings_semantics.py)
====================================================

背景（2026-10-30 设置面审计·第二轮「语义反人类」）：用户原话
"设置他们不符合人类很多"。本轮专治语义五病：

  E1 魔法值无说明 —— 0=自动 / 0-63 音量刻度 / 0-100→α0-10 强度映射
     只活在代码注释里，UI 上一个字没写。
  E2 单位/量纲缺失 —— VFO 带宽列裸显 "12.5k"（频率列却带 MHz/kHz/Hz）；
     静噪值 "-80" 无 dB。
  E3 范围与能力不符 —— device_panel 参数解析失败**静默改写**成默认值
     （用户以为连的是自己填的参数）；sat_track 下行频率 100–2000 MHz
     卡掉仓库自己的 LRO 2271 MHz；module_panel 频率上限 7.2 GHz 是
     SoapySDR 无范围兜底值，超过所有在列后端。
  E4 命名/文案反直觉 —— 两个"带宽"（硬件带宽 vs VFO 信道带宽）同名；
     "Auto"/"Offset 调谐" 混英文；NTRIP 表单 "Caster Host:/Mountpoint:"
     裸英文行标。
  E5 失败提示不说人话 —— 英文异常/堆栈直接漏进 UI（clean_exc 战役先例，
     docs/learn/capability-map.md）。

本文件断言**修后的行为**：tooltip/占位文本含说明、单位后缀存在、
越界被拒且提示（绝不静默改写）、命名区分、失败消息不含英文堆栈。

红线：offscreen，不碰真硬件真网络；不删/放宽任何既有断言。
运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_settings_semantics.py -v
"""
import os
import sys
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from constants import FREQ_MIN_HZ, FREQ_MAX_HZ  # noqa: E402


def _app():
    app = QApplication.instance()
    return app if app is not None else QApplication([])


def _process_events(ms: int = 10):
    _app().processEvents()


def _capture_warnings(monkeypatch):
    """把 QMessageBox.warning/info 改成记录器（offscreen 模态会挂死测试）。"""
    seen = []

    def _warn(_parent, _title, text, *a, **k):
        seen.append(str(text))
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "warning", _warn)
    monkeypatch.setattr(QMessageBox, "information", _warn)
    return seen


# ======================================================================
# E1 魔法值必须有说明
# ======================================================================
class TestE1MagicValuesExplained:
    def test_bw_spin_zero_is_explicit_auto(self):
        """硬件带宽 0=自动：必须能表达且写明（旧值域 100 起，'自动'无法表达）。"""
        _app()
        from module_panel import ModulePanel
        p = ModulePanel()
        assert p.bw_spin.minimum() == 0, "0=自动必须可设（值域含 0）"
        assert p.bw_spin.specialValueText() == "自动", \
            "0 档必须显式显示为「自动」，而不是让用户猜 0 是什么"
        tip = p.bw_spin.toolTip()
        assert "自动" in tip, f"tooltip 必须写明 0=自动，实际 {tip!r}"
        assert "VFO" in tip, "tooltip 必须与 VFO 带宽区分（两套带宽同名）"
        assert "硬件" in tip, "tooltip 必须写明这是硬件带宽"

    def test_bw_spin_auto_still_reaches_payload_as_zero(self):
        """「自动」档下发载荷仍是 0.0（与后端 bandwidth_hz 0=自动语义一致）。"""
        _app()
        from PySide6.QtWidgets import QMessageBox as MB
        from module_panel import ModulePanel
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(MB, "information", lambda *a, **k: MB.Ok)
            p = ModulePanel()
            got = []
            p.device_selected.connect(lambda d: got.append(dict(d)))
            p._devices = [{"label": "FakeSDR", "driver": "fake"}]
            p.device_combo.clear()
            p.device_combo.addItem("FakeSDR")
            p.device_combo.setCurrentIndex(0)
            p.bw_spin.setValue(0)          # 「自动」
            p._on_connect()
        finally:
            monkeypatch.undo()
        assert len(got) == 1
        assert got[0]["_bandwidth"] == 0.0, "自动档必须以 0=自动 语义进载荷"

    def test_freq_input_documents_bare_number_semantics(self):
        """裸数字解析（FM→MHz / AM→kHz / >10000→Hz）必须写在 UI 上。"""
        _app()
        from control_panel import ControlPanel
        cp = ControlPanel()
        tip = cp.freq_input.toolTip()
        for token in ("MHz", "kHz", "Hz"):
            assert token in tip, f"频率输入 tooltip 必须写明单位语义（缺 {token}）"
        assert "10000" in tip or "10 000" in tip, \
            "裸数字 >10000 视为 Hz 的魔法阈值必须写明"
        ph = cp.freq_input.placeholderText()
        assert ph and ph != "输入频率", \
            f"占位文本必须给出输入示例（旧占位'输入频率'无示例），实际 {ph!r}"

    def test_volume_scale_documents_0_63(self):
        """音量 0–63 刻度（下游 volume/63×2.0）必须在 UI 说明。"""
        _app()
        from control_panel import ControlPanel
        cp = ControlPanel()
        tip = cp.volume_slider.toolTip()
        assert "63" in tip, f"音量 tooltip 必须写明 0–63 刻度，实际 {tip!r}"

    def test_anr_strength_documents_alpha_mapping(self):
        """ANR 强度 0–100 → α 0–10 映射必须写明；标签不许裸显 2.0。"""
        _app()
        from anr_panel import AnrPanel
        p = AnrPanel()
        tip = p.strength_slider.toolTip()
        assert "100" in tip and ("α" in tip or "alpha" in tip.lower()), \
            f"强度 tooltip 必须写明 0–100→α 映射，实际 {tip!r}"
        p.strength_slider.setValue(25)
        _process_events()
        assert p.strength_label.text().startswith("α"), \
            f"强度标签必须带 α 语义，实际 {p.strength_label.text()!r}"


# ======================================================================
# E2 单位/量纲
# ======================================================================
class TestE2UnitsPresent:
    def test_vfo_bandwidth_format_carries_unit(self):
        """VFO 带宽列不许裸显 12.5k（频率列有单位，带宽列也得有）。"""
        from vfo_panel import _fmt_bw_hz
        assert _fmt_bw_hz(12_500.0) == "12.5 kHz"
        assert _fmt_bw_hz(500.0) == "500 Hz"

    def test_squelch_value_carries_db_unit(self):
        """静噪值必须带 dB（信号语义 dBFS，旧显示裸 -80）。"""
        _app()
        from control_panel import ControlPanel
        cp = ControlPanel()
        cp._on_squelch_changed(-80)
        assert cp.squelch_val.text() == "-80 dB", \
            f"静噪值必须带 dB 单位，实际 {cp.squelch_val.text()!r}"

    def test_module_and_control_unit_suffixes(self):
        """既有单位后缀不许回退（Hz / dB / MHz 档位文本）。"""
        _app()
        from module_panel import ModulePanel
        from control_panel import ControlPanel
        m = ModulePanel()
        assert m.freq_spin.suffix().strip() == "Hz"
        assert m.bw_spin.suffix().strip() == "Hz"
        c = ControlPanel()
        assert "dB" in c.gain_val.text()
        # 采样率档位文本自带单位
        for i in range(c.sample_rate_combo.count()):
            txt = c.sample_rate_combo.itemText(i)
            assert ("MHz" in txt) or ("kHz" in txt), f"采样率档位缺单位: {txt!r}"


# ======================================================================
# E3 范围与能力
# ======================================================================
class TestE3RangeMatchesCapability:
    def test_device_panel_rejects_unparseable_without_rewrite(self, monkeypatch):
        """坏输入拒绝连接并提示；绝不静默改写成默认值（旧实现照连不误）。"""
        _app()
        from device_panel import DevicePanel
        seen = _capture_warnings(monkeypatch)
        p = DevicePanel()
        p._hotplug_timer.stop()
        got = []
        p.connect_requested.connect(lambda d: got.append(dict(d)))
        p._devices = [{"label": "FakeSDR", "driver": "fake"}]
        p.dev_combo.clear()
        p.dev_combo.addItem("FakeSDR")
        p.dev_combo.setCurrentIndex(0)

        p.sr_edit.setText("abc")
        p._on_connect_clicked()
        assert got == [], "坏采样率不得 emit 连接请求"
        assert p.sr_edit.text() == "abc", "不得改写用户输入框"
        assert seen and "采样率" in seen[-1], f"必须诚实提示采样率无效: {seen}"

    def test_device_panel_rejects_out_of_device_capability(self, monkeypatch):
        """超出所选设备 sample_rate_range / gain_range 必须拒绝并说明范围。"""
        _app()
        from device_panel import DevicePanel
        seen = _capture_warnings(monkeypatch)
        p = DevicePanel()
        p._hotplug_timer.stop()
        got = []
        p.connect_requested.connect(lambda d: got.append(dict(d)))
        p._devices = [{
            "label": "FakeSDR", "driver": "fake",
            "sample_rate_range": (250_000.0, 3_200_000.0),
            "gain_range": (0.0, 40.0),
        }]
        p.dev_combo.clear()
        p.dev_combo.addItem("FakeSDR")
        p.dev_combo.setCurrentIndex(0)

        p.sr_edit.setText("5000000")     # > 3.2M
        p._on_connect_clicked()
        assert got == [], "超设备能力采样率不得 emit"
        assert seen and "超出" in seen[-1] and "3200000" in seen[-1], \
            f"必须给出设备范围诚实提示: {seen}"

        seen.clear()
        p.sr_edit.setText("2048000")
        p.gain_edit.setText("99")        # > 40 dB
        p._on_connect_clicked()
        assert got == [], "超设备能力增益不得 emit"
        assert seen and "超出" in seen[-1] and "40" in seen[-1], \
            f"必须给出增益范围诚实提示: {seen}"

    def test_device_panel_rejects_non_integer_ppm(self, monkeypatch):
        _app()
        from device_panel import DevicePanel
        seen = _capture_warnings(monkeypatch)
        p = DevicePanel()
        p._hotplug_timer.stop()
        got = []
        p.connect_requested.connect(lambda d: got.append(dict(d)))
        p._devices = [{"label": "FakeSDR", "driver": "fake"}]
        p.dev_combo.clear()
        p.dev_combo.addItem("FakeSDR")
        p.dev_combo.setCurrentIndex(0)
        p.sr_edit.setText("2048000")
        p.gain_edit.setText("30")
        p.ppm_edit.setText("1.5")
        p._on_connect_clicked()
        assert got == [], "非法 PPM 不得 emit"
        assert seen and "PPM" in seen[-1], f"必须提示 PPM 无效: {seen}"

    def test_device_panel_valid_params_still_flow(self, monkeypatch):
        """合法输入仍进载荷（既有下发协议不许破坏）。"""
        _app()
        from device_panel import DevicePanel
        _capture_warnings(monkeypatch)
        p = DevicePanel()
        p._hotplug_timer.stop()
        got = []
        p.connect_requested.connect(lambda d: got.append(dict(d)))
        p._devices = [{
            "label": "FakeSDR", "driver": "fake",
            "sample_rate_range": (250_000.0, 3_200_000.0),
            "gain_range": (0.0, 40.0),
        }]
        p.dev_combo.clear()
        p.dev_combo.addItem("FakeSDR")
        p.dev_combo.setCurrentIndex(0)
        p.sr_edit.setText("2048000")
        p.gain_edit.setText("30")
        p.ppm_edit.setText("25")
        p._on_connect_clicked()
        assert len(got) == 1
        assert got[0]["_sample_rate"] == 2_048_000.0
        assert got[0]["_gain"] == 30.0
        assert got[0]["_ppm"] == 25

    def test_sat_track_range_covers_repo_lro_target(self):
        """下行频率值域不许卡掉合法值：仓库 LRO 目标 2271 MHz、S 波段 2401 MHz。"""
        _app()
        from sat_track_panel import SatTrackPanel
        p = SatTrackPanel()
        assert p.freq_spin.minimum() <= 2271.0 <= p.freq_spin.maximum(), \
            "LRO 2271 MHz（doppler_panel 默认目标）必须可输"
        assert p.freq_spin.maximum() >= 2401.0, "AO-40 S 波段 2401 MHz 必须可输"
        assert "设备" in p.freq_spin.toolTip(), \
            "tooltip 必须声明实际可调范围以设备为准"

    def test_module_freq_spin_matches_project_bounds(self):
        """module_panel 频率值域对齐项目可调范围（旧 7.2 GHz 是 Soapy 兜底值）。"""
        _app()
        from module_panel import ModulePanel
        p = ModulePanel()
        assert p.freq_spin.minimum() == FREQ_MIN_HZ
        assert p.freq_spin.maximum() == FREQ_MAX_HZ, \
            "上限必须是项目 6 GHz，而不是 Soapy 无范围兜底的 7.2 GHz"


# ======================================================================
# E4 命名/文案
# ======================================================================
class TestE4NamingHonest:
    def test_two_bandwidths_are_distinguished(self):
        """硬件带宽 vs VFO 信道带宽不许同名（审计残留项 3）。"""
        _app()
        from control_panel import ControlPanel
        cp = ControlPanel()
        assert cp.vfo_bw_label.text() == "VFO 带宽", \
            f"控制面板必须叫 VFO 带宽，实际 {cp.vfo_bw_label.text()!r}"
        assert "硬件" in cp.vfo_bw_combo.toolTip(), \
            "VFO 带宽 tooltip 必须与「硬件带宽」互相区分"
        mod_src = open(os.path.join(_ROOT, "desktop", "module_panel.py"),
                       encoding="utf-8").read()
        assert 'form.addRow("硬件带宽", self.bw_spin)' in mod_src, \
            "模块面板必须叫「硬件带宽」，不得与 VFO 带宽同名"

    def test_chinese_wording_no_stray_english(self):
        """「自动」「偏移调谐」中文化（Auto/Offset 混排是翻译腔）。"""
        _app()
        from control_panel import ControlPanel
        cp = ControlPanel()
        assert cp.auto_squelch_check.text() == "自动"
        assert "Auto" not in cp.auto_squelch_check.text()
        assert "偏移" in cp.offset_check.text()
        assert "Offset" not in cp.offset_check.text()

    def test_ntrip_labels_are_chinese_with_glossary_tips(self):
        """NTRIP 行标中文化；Caster/Mountpoint 术语必须有解释 tooltip。"""
        import ntrip_panel
        from PySide6.QtWidgets import QLabel

        class _Mgr:
            def stats(self):
                return {"connected": False, "bytes_received": 0}

        _app()
        dlg = ntrip_panel.NtripConfigDialog(manager=_Mgr())
        try:
            texts = {w.text() for w in dlg.findChildren(QLabel)}
            assert "Caster 主机:" in texts, f"行标应中文化: {texts}"
            assert "端口:" in texts
            assert "挂载点:" in texts
            assert "Mountpoint:" not in texts and "Port:" not in texts
            assert "Caster" in dlg.host_edit.toolTip(), "Caster 术语必须有解释"
            assert "挂载点" in dlg.mount_combo.toolTip(), "Mountpoint 术语必须有解释"
        finally:
            dlg._poll_timer.stop()
            dlg.close()


# ======================================================================
# E5 失败提示说人话（clean_exc 战役先例）
# ======================================================================
class TestE5FailureMessagesHumanized:
    def test_module_enum_failure_no_raw_english(self, monkeypatch):
        """设备枚举失败：英文异常不得裸奔 UI；失败 ≠ 没插设备，不许被空态盖掉。"""
        _app()
        import mbdsdr_ai.sdr_backend as sdr_backend
        from module_panel import ModulePanel

        def _boom():
            raise TimeoutError("timed out")

        monkeypatch.setattr(sdr_backend, "enumerate_all_sdr_devices", _boom)
        p = ModulePanel()
        p.refresh_devices()
        text = p.device_info.text()
        assert "timed out" not in text, f"英文异常泄漏到 UI: {text!r}"
        assert "超时" in text, f"必须给中文可操作说明: {text!r}"
        assert "枚举失败" in text, \
            f"失败必须与'没插设备'区分，不得被空态文案覆盖: {text!r}"

    def test_bookmark_import_failure_no_exception_class_leak(self, monkeypatch, tmp_path):
        """书签导入失败：不许 'FileNotFoundError: ...' 裸抛，要说人话。"""
        _app()
        from bookmark_panel import BookmarkPanel
        seen = _capture_warnings(monkeypatch)
        missing = str(tmp_path / "no_such_bookmarks_file.csv")
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getOpenFileName",
            staticmethod(lambda *a, **k: (missing, "CSV (*.csv)")))
        p = BookmarkPanel(config_dir=str(tmp_path))
        p._mgr = object()   # 只需非 None；open() 先于任何 mgr 方法失败
        p._on_import_csv()
        assert seen, "导入失败必须弹提示"
        msg = seen[-1]
        assert "FileNotFoundError" not in msg, f"异常类名泄漏: {msg!r}"
        assert "文件不存在" in msg, f"必须给中文可操作说明: {msg!r}"

    def test_ntrip_pull_network_error_is_chinese(self, monkeypatch):
        """拉取挂载点失败：英文异常不得裸奔，映射为中文诊断。"""
        import ntrip_panel
        _app()
        seen = _capture_warnings(monkeypatch)

        def _boom(*a, **k):
            # socket 读写超时的 str(e) 就是裸 "timed out"（clean_exc 有映射）
            raise TimeoutError("timed out")

        class _FakeTimer:
            @staticmethod
            def singleShot(_ms, fn):
                fn()

        class _Mgr:
            def stats(self):
                return {"connected": False, "bytes_received": 0}

        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        # 先用真 QTimer 构造（_poll_timer），再换掉模块名供 _pull_worker 的
        # singleShot 立即执行，避免 offscreen 排队不投递
        dlg = ntrip_panel.NtripConfigDialog(manager=_Mgr())
        monkeypatch.setattr(ntrip_panel, "QTimer", _FakeTimer)
        try:
            dlg._pull_worker("http://caster.invalid/")
        finally:
            dlg.close()
        assert seen, "网络失败必须弹提示"
        msg = seen[-1]
        assert "timed out" not in msg, f"英文异常泄漏: {msg!r}"
        assert "连接超时" in msg, f"必须给中文可操作诊断: {msg!r}"

    def test_ai_worker_failure_has_no_traceback(self):
        """AI 调用失败：堆栈不得漏进对话区（用户看不懂，还吓人）。"""
        _app()
        from ai_panel import AIWorker

        class _BoomAgent:
            def chat(self, text, on_delta=None):
                raise RuntimeError("boom")

        w = AIWorker(_BoomAgent(), "hi")
        msgs = []
        w.error.connect(msgs.append)
        w.run()
        assert msgs, "失败必须 emit error"
        assert "Traceback" not in msgs[0], f"堆栈泄漏: {msgs[0]!r}"
        assert "boom" in msgs[0], "细节（供排查）必须保留"
