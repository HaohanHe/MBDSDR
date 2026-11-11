# SPDX-License-Identifier: MIT
"""
MBDSDR 调谐控制面板
====================
频率显示/调节、模式选择、音量、预设电台、录音控制。

控件布局参考：
- SDR++ rtl_sdr_source/main.cpp（设备配置菜单：PPM 整数输入、Offset Tuning /
  RTL AGC / Tuner AGC 垂直复选框列表、Gain 滑杆）。
- GQRX dockrxopt/dockinputctl（带宽档位下拉、AGC 预设、PPM 频偏校正）。
"""

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QComboBox, QLineEdit, QFrame, QGroupBox, QSizePolicy,
    QCheckBox, QSpinBox, QProgressBar, QInputDialog, QMessageBox,
)
from PySide6.QtGui import QFont, QIntValidator, QDoubleValidator

#: 书签名长度上限（字符）。依据：常见台名/中继名（呼号+频点+亚音，如
#: "BG5ABC 439.750 -5 88.5"）约 24 字符；64 留出地名/用途备注的余量，
#: 同时保证下拉框文本在 240px 内可读。超过即视为异常输入/脏数据。
BOOKMARK_NAME_MAX_CHARS = 64

# 本目录（desktop/）下的兄弟模块（tokens / mode_registry 等）在以包方式从
# 仓库根 import（如 import desktop.control_panel）时不在 sys.path 上，
# 这里把脚本所在目录补进 sys.path，与 main.py / main_window.py 的引导方式一致。
import logging
import math
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

logger = logging.getLogger(__name__)

from tokens import tokens

from constants import (
    DEFAULT_SAMPLE_RATE_HZ, DEFAULT_SR_UNCONFIRMED,
    FREQ_MIN_HZ, FREQ_MAX_HZ, format_sample_rate)

# 静噪门限的唯一口径（相对该模式噪声基线的余量）定义在内核侧，
# 桌面端不复制数字；见 mbdsdr_ai/squelch.py 的常量段。
from mbdsdr_ai.squelch import (
    DEFAULT_SQUELCH_DBFS,
    SQUELCH_MAX_DBFS,
    SQUELCH_MIN_DBFS,
)

# 大字体频率显示（按位编辑，SDR++/gqrx radio dial 手感）；
# 契约与 cpp/src/ui/freq_display_widget.h 一致。
from freq_display import FreqDisplay


class _CapabilitySampleRateCombo(QComboBox):
    """采样率下拉：每次展开前重新问后端能力，前端不保存档位副本。

    展开时才刷新（而不是连接时一次性写入），是为了让“可见档位”始终等于
    当前活跃后端的可接受集合；后端还没报告范围时只显示“未知”，不编档位。
    """

    def __init__(self, refresh, parent=None):
        super().__init__(parent)
        self._refresh = refresh

    def showPopup(self):  # noqa: N802 (Qt 虚函数名)
        try:
            self._refresh()
        except Exception as exc:
            # 刷新失败不能让下拉变成空的：保留既有档位，并留下可查原因
            logger.warning("采样率档位刷新失败，沿用当前列表: %s: %s",
                           type(exc).__name__, exc)
        super().showPopup()

# 解调模式元数据（默认带宽/显示名/解调函数）已收口到 mode_registry。
# 这里只取查询函数；旧字典 MODE_VFO_BANDWIDTH 作为向后兼容别名在下方导出。
from mode_registry import (
    default_bandwidth,
    mode_names,
    to_bandwidth_dict,
    get as get_mode,
)


# 预设电台（FM，MHz）
# 不预存地区性广播台：FM 频率随城市/地区不同，硬编码其他城市的电台没有意义。
# 本地电台请用“自动扫台”（sdr_fm_scan）动态发现，或在频率框手动输入后自行保存。
PRESET_FM_STATIONS: list[tuple[str, str]] = []

# 预设 AM 电台（kHz）
# 同样不预存地区性中波台：频率随地区不同。请自动扫台或手动输入。
PRESET_AM_STATIONS: list[tuple[str, str]] = []

#: 空预设占位文案。原句「（无预设：请用“自动扫台”或手动输入频率）」在
#: 右栏 320 宽的下拉框里被硬切成「…或手动输入频」——省略号都没机会出现。
#: 这里收短到 140px 内，完整解释放 tooltip。
_NO_PRESET_TEXT = "无预设：先扫台或直接输频"
_NO_PRESET_TIP = ("该频段暂无内置电台：用工具栏「自动扫台」发现，"
                  "或直接在频率框输入频率后调谐。")
# SDR 常用频段预设（频率MHz, 名称, 模式）
SDR_BAND_PRESETS = [
    # 航空
    ("118.000", "航空塔台", "AM"),
    ("121.500", "航空应急", "AM"),
    ("123.450", "航空通联", "AM"),
    ("132.000", "航空进近", "AM"),
    # 业余无线电 2m
    ("144.390", "APRS 144", "AFSK"),
    ("145.800", "ISS APRS", "AFSK"),
    ("145.500", "2m 呼叫", "FM"),
    ("146.520", "2m 全美呼叫", "FM"),
    # 业余无线电 70cm
    ("432.100", "70cm CW", "CW"),
    ("433.000", "70cm 呼叫", "FM"),
    ("435.000", "70cm 卫星", "FM"),
    # 气象卫星
    ("137.100", "NOAA 19 APT", "FM"),
    ("137.620", "NOAA 15 APT", "FM"),
    ("137.9125", "NOAA 18 APT", "FM"),
    ("137.100", "METEOR M2", "FM"),
    # 导航
    ("1575.42", "GPS L1", "PSK"),
    ("1561.098", "北斗 B1", "PSK"),
    ("1227.60", "GPS L2", "PSK"),
    # 广播
    ("87.500", "FM 广播起点", "WFM"),
    ("108.000", "FM 广播终点", "WFM"),
    # 工业/物联网
    ("433.920", "ISM 433", "ASK"),
    ("868.000", "ISM 868", "FSK"),
    ("915.000", "ISM 915", "FSK"),
    ("2400.00", "WiFi/BT 2.4G", "QAM"),
    # 短波
    ("3.500", "80m 业余", "LSB"),
    ("7.000", "40m 业余", "LSB"),
    ("14.000", "20m 业余", "USB"),
    ("21.000", "15m 业余", "USB"),
    ("28.000", "10m 业余", "USB"),
]

# 模式 → 默认 VFO 带宽（Hz）已收口到 desktop/mode_registry.py 的 DemodMode 描述符。
# 这里仅保留向后兼容别名：旧代码 `from control_panel import MODE_VFO_BANDWIDTH` 仍可用。
MODE_VFO_BANDWIDTH = to_bandwidth_dict()

# 面板模式下拉的展示顺序（业务 UX 顺序，与注册表内置顺序 WFM/NFM/FM/... 不同）。
# 用注册表过滤：仅展示已注册的模式，未注册的自动剔除。
_PANEL_MODE_ORDER = ["FM", "WFM", "NFM", "AM", "USB", "LSB", "CW"]

# 合法频率范围：10 kHz ~ 6 GHz（SDR 全频段，不再硬编码 FM/AM 广播段）。
# 常量收口到 constants.py（module_panel 同用），这里 re-export 保持旧导入可用。
# _set_freq_hz 对越界输入做钳位并在 freq_input tooltip 里事先声明钳位行为。


def _format_frequency_hz(hz: float) -> str:
    """频率的人读口径（提示文案用）：按量级选 GHz/MHz/kHz/Hz，``:g`` 去尾零。"""
    v = float(hz)
    if abs(v) >= 1e9:
        return f"{v / 1e9:g} GHz"
    if abs(v) >= 1e6:
        return f"{v / 1e6:g} MHz"
    if abs(v) >= 1e3:
        return f"{v / 1e3:g} kHz"
    return f"{v:g} Hz"


class ControlPanel(QWidget):
    """调谐控制面板。"""

    # ===== 既有信号（保持不变，仅新增不删除）=====
    tune_fm_requested = Signal(float)    # 请求调谐 FM (MHz)
    tune_am_requested = Signal(int)      # 请求调谐 AM (kHz)
    tune_sdr_requested = Signal(float, str)  # 请求调谐 SDR (Hz, 模式)
    volume_changed = Signal(int)          # 音量改变 (0-100 人类量纲)
    record_toggled = Signal(bool)         # 录音开关
    mode_changed = Signal(str)            # 模式改变
    gain_changed = Signal(int)             # 硬件增益 (dB, RTL-SDR LNA 0~49)
    squelch_changed = Signal(float)        # 静噪门限（相对该模式噪声基线的余量刻度，-100~-20）
    auto_squelch_toggled = Signal(bool)  # 自动静噪开关
    sample_rate_changed = Signal(float)    # 采样率改变 (Hz)

    # ===== 新增信号 =====
    step_changed = Signal(float)               # 频率步进改变 (Hz)
    vfo_bandwidth_changed = Signal(float)      # VFO 带宽改变 (Hz)
    agc_changed = Signal(bool)                 # AGC 自动增益开关
    agc_speed_changed = Signal(str)            # 音频 AGC 档位 (off/fast/medium/slow)
    ppm_changed = Signal(int)                  # PPM 频偏校正
    offset_tuning_changed = Signal(bool)       # Offset 调谐开关
    dc_removal_changed = Signal(bool)          # DC 偏移去除开关（默认开）
    iq_balance_changed = Signal(bool)          # I/Q 不平衡校正开关（默认开）
    bookmark_added = Signal(float, str, str)   # 收藏新增 (freq_hz, name, mode)，供主窗口持久化

    def __init__(self, parent=None):
        super().__init__(parent)
        # 未连接时无模式/无频率真值；连接后由后端或持久化配置回填。
        # 绝不硬编码 98.5 MHz / FM 这类演示默认值伪装成"已在收听某台"。
        self._current_mode = None
        self._freq_hz = None
        self._sample_rate_options: list[float] = []
        self._step_hz = 10_000.0   # 默认步进 10 kHz
        self._volume = 48
        self._recording = False
        self._record_seconds = 0
        #: 录音结果态：None=未收口；"cancelled"/"failed"/"completed" 由主窗口写入。
        #: 面板不得仅凭按钮取消就宣布「完成」——取消/失败必须有自己的说法。
        self._record_outcome: str | None = None
        # 收藏列表：内存中，初始为空，不预存任何地区性电台
        self._bookmarks: list[tuple[float, str, str]] = []

        self._build_ui()
        self._relax_combo_widths()
        self._update_freq_display()
        # 默认未连接 SDR：调谐/音量/模式/录音控件全部置灰，绝不暴露假可控状态
        self.set_sdr_connected(False)
        self._apply_touch_targets()

    def _relax_combo_widths(self) -> None:
        """下拉框的条目文本不得决定面板最小宽（右栏只有 320）。

        默认 QComboBox 的 minimumSizeHint 取最长条目文本：模式「WFM（宽带
        调频）」、AGC 速度这类条目把 ControlPanel 顶到 334，而右栏视口
        只有 310——右边被硬裁（用户 2026-11-10「显示不全」）。统一改成
        「按最少字符数算 + 水平 Ignored」，控件照常吃布局给它的宽。
        """
        from PySide6.QtWidgets import QComboBox
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(
                QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _apply_touch_targets(self) -> None:
        """触屏模式下把调谐/收藏/录音等按钮与下拉/输入框最小高度提到 ≥44px。

        apply_touch_target 内部检查 TouchManager.is_touch_mode()，鼠标模式零旁路；
        主窗口还会递归 apply_touch_targets(self) 二次覆盖，这里自包含一遍便于
        ControlPanel 单独使用。
        """
        try:
            from touch_helpers import apply_touch_target
        except Exception as exc:
            # 边界：touch_helpers 属可选平台辅助模块；缺失时按钮保持原尺寸
            logger.debug("touch_helpers 不可用，跳过触屏目标尺寸: %s: %s",
                         type(exc).__name__, exc)
            return
        for name in ("tune_button", "bookmark_add_btn", "bookmark_del_btn",
                     "record_button"):
            w = getattr(self, name, None)
            if w is not None:
                apply_touch_target(w)
        for name in ("mode_combo", "band_combo", "preset_combo", "step_combo",
                     "sample_rate_combo", "vfo_bw_combo", "bookmark_combo"):
            w = getattr(self, name, None)
            if w is not None:
                apply_touch_target(w)
                # 下拉列表触屏可按像素滚动
                try:
                    from PySide6.QtWidgets import QAbstractItemView
                    view = w.view()
                    if view is not None:
                        view.setVerticalScrollMode(
                            QAbstractItemView.ScrollMode.ScrollPerPixel)
                except Exception as exc:
                    # 边界：Qt 视图滚动模式设置失败不影响触屏可用性
                    logger.debug("下拉像素滚动设置失败: %s: %s",
                                 type(exc).__name__, exc)
        for name in ("freq_input",):
            w = getattr(self, name, None)
            if w is not None:
                apply_touch_target(w)

    def set_sdr_connected(self, connected: bool):
        """无真实 SDR 时禁用调谐/音量/模式/录音等操作控件。

        连接成功后由 main_window._panels_set_sdr_connected(True) 统一启用；
        断开时再置灰（freq_display 同置灰——按位编辑无设备不可发调谐）。
        收藏查看/删除始终可用，仅“收藏当前频率”按钮随连接状态禁用。
        """
        self._sdr_connected = bool(connected)
        widgets = [
            getattr(self, "mode_combo", None),
            getattr(self, "freq_input", None),
            getattr(self, "freq_display", None),
            getattr(self, "tune_button", None),
            getattr(self, "band_combo", None),
            getattr(self, "preset_combo", None),
            getattr(self, "volume_slider", None),
            getattr(self, "record_button", None),
            getattr(self, "sample_rate_combo", None),
            # 新增控件
            getattr(self, "step_combo", None),
            getattr(self, "vfo_bw_combo", None),
            getattr(self, "agc_check", None),
            getattr(self, "agc_speed_combo", None),
            getattr(self, "ppm_spin", None),
            getattr(self, "offset_check", None),
            getattr(self, "dc_removal_check", None),
            getattr(self, "iq_balance_check", None),
            getattr(self, "bookmark_add_btn", None),
            # 增益 / 静噪滑杆：无设备时禁用，不暴露假可控状态
            getattr(self, "gain_slider", None),
            getattr(self, "squelch_slider", None),
        ]
        widgets += self._step_buttons
        # 置灰必须给理由（诚实禁用）：以前「调谐 / DC 去除 / IQ 平衡」在无设备
        # 时静默置灰，用户只看到点了没反应，交互审计记为「理由未登记」。
        # 这里统一在原 tooltip 后追加一句真实原因，连上后原样还原。
        _why = ("未连接设备：需要真实 SDR 连接后才能操作"
                "（不是坏了，连上即可用）")
        for w in widgets:
            if w is None:
                continue
            w.setEnabled(self._sdr_connected)
            try:
                if not hasattr(w, "_base_tip"):
                    w._base_tip = w.toolTip()
                base = str(getattr(w, "_base_tip", "") or "")
                if self._sdr_connected:
                    w.setToolTip(base)
                elif not w.isEnabled():
                    w.setToolTip(f"{base}\n{_why}" if base else _why)
            except (AttributeError, RuntimeError):
                pass
        # 读数标签：断开即清（不让上一个数字冒充当前增益）；连接后等真实回执。
        # 滑杆位置保留配置意图，不随连接态归零。
        self.gain_val.setText("—")
        if self._sdr_connected:
            self.gain_val.setToolTip("已连接：等待增益下发（滑杆位置=配置意图）")
        else:
            self.gain_val.setToolTip(
                "未连接：没有真实增益读数；滑杆位置是上次配置，未施加")
        # 后端未报告采样率范围时，即便已连接也不能把说明项当成可选项
        if (self._sdr_connected and self.sample_rate_combo.count()
                and self.sample_rate_combo.itemData(0) is None):
            self.sample_rate_combo.setEnabled(False)
        # 连接后恢复频率显示（断连期间 set_frequency_hz 只存内部值不刷显示）
        if self._sdr_connected and self._freq_hz is not None:
            self._update_freq_display()
        # 频率未知时 ±步进/收藏保持禁用：连接成功不等于拿到了频率真值。
        # 放在最后统一收口，避免上面每处 setEnabled 各写一份判断。
        self._sync_freq_actions_enabled()
        # 采样率文案随连接态重标：未连接时必须写「默认 · 未确认」，
        # 不能把预填档位当成设备正在跑的速率。
        self._apply_sample_rate_unconfirmed_marker()

    def set_connected(self, connected: bool):
        """主窗口在连接/断开时调用（hasattr 守卫，方法名固定为 set_connected）。

        - connected=False：所有频率输入/模式下拉/增益/音量/静噪等射频控件
          setEnabled(False)，freq_display 显占位，频率与模式真值清空，
          绝不残留 98.5/FM 之类的演示状态。
        - connected=True：恢复控件使能；具体真实频率/模式由后端或持久化配置
          通过 set_frequency_hz()/set_mode() 等回填，本方法不编造数值。
        """
        self.set_sdr_connected(connected)
        if not connected:
            self._freq_hz = None
            self._current_mode = None
            self.freq_display.set_frequency(None)
            self.freq_input.clear()
            self.freq_input.setStyleSheet("")
            # 模式下拉回到"无选择"，避免残留 FM 等假象
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(-1)
            self.mode_combo.blockSignals(False)
            self.record_status.setText("未连接")
        elif not self._recording:
            self.record_status.setText("未录音")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- 频率显示区 ----
        freq_group = QGroupBox("频率")
        freq_layout = QVBoxLayout(freq_group)

        # 大字体频率显示：9 位数字网格按位可编辑（滚轮/点上下半 ±1，右键清零），
        # 未连接时显占位，绝不显示 98.500 这类假频率
        self.freq_display = FreqDisplay()
        # 用户原话：「右边那个频率点点圈点点干啥的？我没明白」——
        # 这是按位可编辑的频率显示器，不说清就只像一排灰点。
        self.freq_display.setToolTip(
            "当前收听频率。每一位数字都能单独点/滚轮加减，右键清零；"
            "改完点「调谐」才真正发给设备，没连接时不显示假频率。")
        self.freq_display.set_bounds(int(FREQ_MIN_HZ), int(FREQ_MAX_HZ))
        self.freq_display.frequency_edited.connect(self._on_digit_stepped)
        freq_layout.addWidget(self.freq_display)

        # 模式选择 + 频率输入
        mode_freq_row = QHBoxLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([m for m in _PANEL_MODE_ORDER if get_mode(m)])
        self.mode_combo.setMinimumWidth(60)
        self.mode_combo.setMaximumWidth(140)
        _mp_sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.mode_combo.setSizePolicy(_mp_sp)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        # 未连接时不预选任何模式（index=-1）；FM 仍在下拉里作为可选项，但不主动设为当前值
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(-1)
        self.mode_combo.blockSignals(False)
        mode_freq_row.addWidget(self.mode_combo)

        self.freq_input = QLineEdit("")
        self.freq_input.setPlaceholderText("如 98.5M / 145.5MHz / 7050k")
        self.freq_input.setToolTip(
            "输入频率：支持 M/MHz/k/kHz/G/GHz/Hz 后缀；纯数字按模式解析"
            "（FM/WFM/未选模式 = MHz，AM/其它 = kHz，绝对值 >10000 = Hz）。"
            f"可调范围 {FREQ_MIN_HZ/1e3:.0f} kHz – {FREQ_MAX_HZ/1e9:.0f} GHz；"
            "键入超出范围会拒绝调谐并提示（内部自动值仍限制到边界，"
            "实际范围以设备为准）")
        self.freq_input.setMinimumHeight(28)
        self.freq_input.returnPressed.connect(self._on_freq_input)
        # 恢复 setter 在这里追加/还原"配置请求被替换"的运行期提示，保留原静态说明
        self._freq_input_base_tip = self.freq_input.toolTip()
        mode_freq_row.addWidget(self.freq_input)

        self.tune_button = QPushButton("调谐")
        self.tune_button.setMinimumHeight(28)
        self.tune_button.clicked.connect(self._on_freq_input)
        mode_freq_row.addWidget(self.tune_button)

        freq_layout.addLayout(mode_freq_row)

        # 频率步进选择 + ±1 步按钮
        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("步进:"))
        self.step_combo = QComboBox()
        for txt, hz in [("1Hz", 1.0), ("10Hz", 10.0), ("100Hz", 100.0),
                        ("1kHz", 1_000.0), ("10kHz", 10_000.0),
                        ("100kHz", 100_000.0), ("1MHz", 1_000_000.0)]:
            self.step_combo.addItem(txt, hz)
        # 默认选中 10kHz
        _def_step = self.step_combo.findData(10_000.0)
        self.step_combo.setCurrentIndex(_def_step if _def_step >= 0 else 4)
        self.step_combo.setToolTip(
            "每次加减频率走多大一步（这里选的是 10 kHz）；"
            "旁边的 − / + 就按这个步长微调，不直接改设备。")
        self.step_combo.currentIndexChanged.connect(self._on_step_combo_changed)
        step_row.addWidget(self.step_combo, 1)

        self._step_buttons = []
        for text, direction in [("-", -1), ("+", +1)]:
            btn = QPushButton(text)
            btn.setMinimumSize(36, 24)
            btn.setMaximumSize(80, 40)
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            btn.clicked.connect(lambda checked, d=direction: self._apply_step(d))
            self._step_buttons.append(btn)
            step_row.addWidget(btn)
        freq_layout.addLayout(step_row)

        layout.addWidget(freq_group)

        # ---- 预设电台 ----
        preset_group = QGroupBox("频率预设")
        preset_layout = QVBoxLayout(preset_group)

        # 频段分类选择
        band_row = QHBoxLayout()
        self.band_combo = QComboBox()
        self.band_combo.addItems(["FM 广播", "AM 广播", "航空", "业余 2m", "业余 70cm", "气象卫星", "导航", "ISM/物联网", "短波"])
        self.band_combo.currentIndexChanged.connect(self._on_band_changed)
        band_row.addWidget(QLabel("频段:"))
        band_row.addWidget(self.band_combo)
        preset_layout.addLayout(band_row)

        self.preset_combo = QComboBox()
        # 尺寸策略：条目文本长短不得改变面板最小宽。占位文案
        # 「（无预设：请用“自动扫台”或手动输入频率）」曾经 AdjustToContentsOnFirstShow
        # 把「频率预设」组撑到 332，清空后该最小宽缓存又是 292↔101 两态，右栏随
        # 之在 356↔278 间跳（1000×600 下挤压 13↔15）。与 bookmark_combo 同一策略：
        # 最小宽只按最少字符数算，水平 Ignored 让布局用可用宽度。
        self.preset_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset_combo.setMinimumContentsLength(16)
        self.preset_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._populate_presets("FM 广播")
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        preset_layout.addWidget(self.preset_combo)

        layout.addWidget(preset_group)

        # ---- 收藏（书签）----
        bookmark_group = QGroupBox("收藏")
        bookmark_layout = QVBoxLayout(bookmark_group)

        self.bookmark_combo = QComboBox()
        # 不写 setMinimumHeight：QSS 给下拉/按钮的内边距（8px 上下）已经让
        # sizeHint 是 34~36px，显式把最小值压到 24 会让右栏纵向空间不够时
        # 「合法地」压到 24——文字被裁掉半行（2026-11-11 实拍「收藏当前频率」
        # 只剩上半截）。保持默认最小值 = 内容真实高度。
        # 尺寸策略：item 文本再长也不得把面板撑开。AdjustToMinimumContentsLength
        # 让 sizeHint 只按最少字符数算；水平 Ignored 让布局用可用宽度而不是
        # item 的 minimumSizeHint（100k 字符书签曾把面板撑到 1.3M px）。
        self.bookmark_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.bookmark_combo.setMinimumContentsLength(16)
        self.bookmark_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._refresh_bookmarks()
        self.bookmark_combo.currentIndexChanged.connect(self._on_bookmark_selected)
        bookmark_layout.addWidget(self.bookmark_combo)

        bm_btn_row = QHBoxLayout()
        self.bookmark_add_btn = QPushButton("收藏当前频率")
        self.bookmark_add_btn.clicked.connect(self._on_bookmark_add)
        bm_btn_row.addWidget(self.bookmark_add_btn)

        self.bookmark_del_btn = QPushButton("删除选中")
        self.bookmark_del_btn.clicked.connect(self._on_bookmark_del)
        bm_btn_row.addWidget(self.bookmark_del_btn)
        bookmark_layout.addLayout(bm_btn_row)

        layout.addWidget(bookmark_group)

        # ---- 音量 ----
        volume_group = QGroupBox("音量")
        volume_layout = QVBoxLayout(volume_group)

        vol_row = QHBoxLayout()
        self.volume_label = QLabel("48")
        self.volume_label.setObjectName("statusValue")
        self.volume_label.setMinimumWidth(30)
        self.volume_label.setMaximumWidth(100)
        vol_row.addWidget(self.volume_label)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(48)
        self.volume_slider.setToolTip(
            "音量 0–100（人类量纲；对应音频增益 0–2.0 倍，"
            "下发硬件 0–63 寄存器量纲时在 main_window 边界换算）")
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.volume_slider)

        volume_layout.addLayout(vol_row)
        layout.addWidget(volume_group)

        # ---- 接收：S-meter + 采样率 + 带宽 + 增益 + AGC + PPM + Offset + 静噪 ----
        rx_group = QGroupBox("接收")
        rx_layout = QVBoxLayout(rx_group)

        # 信号电平表（S-meter）：真实数据驱动，由 update_signal_level() 喂入
        smeter_row = QHBoxLayout()
        self.smeter_label = QLabel("--")
        self.smeter_label.setObjectName("statusValue")
        self.smeter_label.setMinimumWidth(30)
        self.smeter_label.setMaximumWidth(100)
        smeter_row.addWidget(self.smeter_label)
        self.smeter_bar = QProgressBar()
        self.smeter_bar.setRange(-120, 0)
        self.smeter_bar.setValue(-120)
        self.smeter_bar.setTextVisible(False)
        smeter_row.addWidget(self.smeter_bar, 1)
        rx_layout.addLayout(smeter_row)

        # 采样率下拉：档位只在后端能力里取，前端不留副本。
        # 连接前按默认 RTL 后端的声明区间预填；连接后每次展开按活跃后端刷新。
        sr_row = QHBoxLayout()
        self.sr_label = QLabel("采样率")
        self.sr_label.setObjectName("statusValue")
        self.sr_label.setMinimumWidth(30)
        self.sr_label.setMaximumWidth(100)
        sr_row.addWidget(self.sr_label)
        self.sample_rate_combo = _CapabilitySampleRateCombo(
            self.refresh_sample_rate_options_from_active_backend)
        # 这个下拉每次展开都会 clear() 后重建档位，文本长短由后端报告决定。
        # 默认的 AdjustToContentsOnFirstShow 会把「未知（设备未报告采样率范围…）」
        # 这类长文案在首帧冻进最小宽（实测 461 vs 清成短档位后的 93），
        # 与 preset_combo 同一形态。改稳定策略：最小宽只按最少字符数算。
        self.sample_rate_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sample_rate_combo.setMinimumContentsLength(16)
        self.sample_rate_combo.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.sample_rate_combo.setToolTip(
            "采样率档位来自设备能力。显示「未知…」时说明后端未报告档位，"
            "那一项只是说明，不代表设备能力。")
        self._populate_sample_rate_options(
            self._default_sample_rate_options(), DEFAULT_SAMPLE_RATE_HZ)
        self.sample_rate_combo.currentIndexChanged.connect(self._on_sample_rate_changed)
        sr_row.addWidget(self.sample_rate_combo)
        rx_layout.addLayout(sr_row)

        # 解调带宽选择（采样率下方一行）
        bw_row = QHBoxLayout()
        self.vfo_bw_label = QLabel("解调带宽")
        self.vfo_bw_label.setObjectName("statusValue")
        self.vfo_bw_label.setMinimumWidth(30)
        self.vfo_bw_label.setMaximumWidth(100)
        bw_row.addWidget(self.vfo_bw_label)
        self.vfo_bw_combo = QComboBox()
        self.vfo_bw_combo.setToolTip(
            "解调带宽（中频滤波宽度），随模式自动套默认。"
            "注意与模块面板的「硬件带宽」（设备射频带宽）不同")
        self._vfo_bw_base_tip = self.vfo_bw_combo.toolTip()
        # 档位与 MODE_VFO_BANDWIDTH 对齐：2.4 kHz(SSB) / 12.5 kHz(NBFM) 必须在列，
        # 避免切到 USB/LSB/NFM 时 findData 落空、回退到错误带宽。
        for txt, hz in [("500Hz (CW)", 500.0), ("2.4kHz (SSB)", 2_400.0),
                        ("6kHz (AM)", 6_000.0), ("12.5kHz (NBFM)", 12_500.0),
                        ("180kHz (WFM)", 180_000.0)]:
            self.vfo_bw_combo.addItem(txt, hz)
        # FM 默认 12.5 kHz
        _def_bw = self.vfo_bw_combo.findData(default_bandwidth("FM"))
        self.vfo_bw_combo.setCurrentIndex(_def_bw if _def_bw >= 0 else 3)
        self.vfo_bw_combo.currentIndexChanged.connect(self._on_vfo_bw_changed)
        bw_row.addWidget(self.vfo_bw_combo, 1)
        rx_layout.addLayout(bw_row)

        gain_row = QHBoxLayout()
        self.gain_label = QLabel("LNA")
        self.gain_label.setObjectName("statusValue")
        self.gain_label.setMinimumWidth(30)
        self.gain_label.setMaximumWidth(100)
        gain_row.addWidget(self.gain_label)
        self.gain_slider = QSlider(Qt.Horizontal)
        # 默认范围是 RTL-SDR 典型值（未连接时的兜底）；连接后 main_window 调
        # set_gain_range() 按所连设备能力（backend.device.max_gain）动态收放，
        # 不再一辈子顶着硬编码 0–49。
        self.gain_slider.setRange(0, 49)
        self.gain_slider.setValue(0)
        self.gain_slider.setToolTip(
            "LNA 增益（dB）。未连接时按 RTL-SDR 典型 0–49 显示；"
            "连接后随所连设备能力动态收放，实际档位以设备为准（后端钳位）")
        # 恢复/收放时追加"配置请求被替换"的运行期提示，保留原静态说明
        self._gain_base_tip = self.gain_slider.toolTip()
        self.gain_slider.valueChanged.connect(self._on_gain_changed)
        gain_row.addWidget(self.gain_slider)
        self.gain_val = QLabel("—")
        self.gain_val.setMinimumWidth(36)
        self.gain_val.setMaximumWidth(80)
        self.gain_val.setToolTip(
            "未连接：没有真实增益读数；滑杆位置是上次配置，未施加")
        gain_row.addWidget(self.gain_val)
        rx_layout.addLayout(gain_row)

        # AGC 自动增益开关（增益滑杆下方）
        self.agc_check = QCheckBox("AGC 自动增益")
        self.agc_check.setToolTip(
            "勾选后由设备自动调增益；信号忽强忽弱时开，想手动控增益就关")
        self.agc_check.toggled.connect(self._on_agc_changed)
        rx_layout.addWidget(self.agc_check)

        # 解调后音频 AGC 档位（gqrx 式 Fast/Medium/Slow + 关）
        agc_speed_row = QHBoxLayout()
        self.agc_speed_label = QLabel("音频 AGC")
        self.agc_speed_label.setObjectName("statusValue")
        agc_speed_row.addWidget(self.agc_speed_label)
        self.agc_speed_combo = QComboBox()
        for key, label in (("off", "关"), ("fast", "快"),
                           ("medium", "中"), ("slow", "慢")):
            self.agc_speed_combo.addItem(label, key)
        self.agc_speed_combo.setCurrentIndex(
            self.agc_speed_combo.findData("medium"))
        self.agc_speed_combo.setToolTip(
            "解调后音频 AGC 档位（gqrx Fast/Medium/Slow）：快档紧跟信号"
            "起伏，慢档增益稳定；「关」直通原声")
        self.agc_speed_combo.currentIndexChanged.connect(
            self._on_agc_speed_changed)
        agc_speed_row.addWidget(self.agc_speed_combo)
        agc_speed_row.addStretch(1)
        rx_layout.addLayout(agc_speed_row)

        # PPM 频偏校正（AGC 下方）
        ppm_row = QHBoxLayout()
        self.ppm_label = QLabel("PPM")
        self.ppm_label.setObjectName("statusValue")
        self.ppm_label.setMinimumWidth(30)
        self.ppm_label.setMaximumWidth(100)
        ppm_row.addWidget(self.ppm_label)
        self.ppm_spin = QSpinBox()
        self.ppm_spin.setRange(-1000, 1000)
        self.ppm_spin.setSingleStep(1)
        self.ppm_spin.setValue(0)
        self._ppm_base_tip = (
            "晶振频偏校正（PPM = 百万分率）。廉价棒典型偏差 20~50 ppm，"
            "对照频率计数器修正")
        self.ppm_spin.setToolTip(self._ppm_base_tip)
        self.ppm_spin.valueChanged.connect(self._on_ppm_changed)
        ppm_row.addWidget(self.ppm_spin)
        ppm_row.addStretch(1)
        rx_layout.addLayout(ppm_row)

        # Offset 调谐开关（PPM 下方）
        self.offset_check = QCheckBox("偏移调谐")
        self._offset_base_tip = (
            "Offset Tuning：把本振偏移半个信道，避开零中频的 DC 尖峰，"
            "改善强台附近的接收")
        self.offset_check.setToolTip(self._offset_base_tip)
        self.offset_check.toggled.connect(self._on_offset_tuning_changed)
        rx_layout.addWidget(self.offset_check)

        # IQ 前端校正：DC 去除 + I/Q 平衡（默认开，对标 SDR++/GQRX 前端校正）
        iq_cal_row = QHBoxLayout()
        self.dc_removal_check = QCheckBox("DC 去除")
        self.dc_removal_check.setChecked(True)
        self.dc_removal_check.toggled.connect(self._on_dc_removal_changed)
        iq_cal_row.addWidget(self.dc_removal_check)
        self.iq_balance_check = QCheckBox("IQ 平衡")
        self.iq_balance_check.setChecked(True)
        self.iq_balance_check.toggled.connect(self._on_iq_balance_changed)
        iq_cal_row.addWidget(self.iq_balance_check)
        rx_layout.addLayout(iq_cal_row)

        sq_row = QHBoxLayout()
        self.squelch_label = QLabel("静噪门限")
        self.squelch_label.setObjectName("statusValue")
        self.squelch_label.setMinimumWidth(30)
        self.squelch_label.setMaximumWidth(100)
        sq_row.addWidget(self.squelch_label)
        self.squelch_slider = QSlider(Qt.Horizontal)
        # 范围/默认值的唯一口径在 mbdsdr_ai/squelch.py 常量段，这里不写字面量。
        self.squelch_slider.setRange(int(SQUELCH_MIN_DBFS),
                                     int(SQUELCH_MAX_DBFS))
        self.squelch_slider.setValue(int(DEFAULT_SQUELCH_DBFS))
        self.squelch_slider.setToolTip(
            "静噪门限：相对该模式噪声基线的余量（不是绝对音频电平）。"
            f"默认 {int(DEFAULT_SQUELCH_DBFS)} ≈ 6 dB 余量；"
            f"{int(SQUELCH_MIN_DBFS)} ≈ 几乎常开；{int(SQUELCH_MAX_DBFS)} ≈ 只放行强信号。"
            "噪声基线自动标定：FM/WFM/NFM 与 AM 用解调后音频 300–5000 Hz 谱峰/谱中位，"
            "USB/LSB 用 300–3000 Hz，CW 用 300–5000 Hz；切换模式或增益变化时重测。"
            "「自动」勾选时此项置灰并使用默认值。")
        self.squelch_slider.valueChanged.connect(self._on_squelch_changed)
        sq_row.addWidget(self.squelch_slider)
        self.squelch_val = QLabel(f"{int(DEFAULT_SQUELCH_DBFS)} dB")
        self.squelch_val.setMinimumWidth(36)
        self.squelch_val.setMaximumWidth(80)
        sq_row.addWidget(self.squelch_val)
        rx_layout.addLayout(sq_row)

        # 「自动」独立成行（2026-11-06 窄排）：静噪行三项最小宽
        # 60+84+43 + 两段 spacing 已占满右栏内容宽 242，再把「自动」塞进来
        # 会把「静噪门限」压到 45、「-50 dB」压到 36（尺寸口径按
        # sizeHint > width+2 计为挤压）。拆行后两行各自最小宽都 < 242，
        # 文字不再被压缩；控件本体、信号接线、语义均未变。
        sq_auto_row = QHBoxLayout()
        self.auto_squelch_check = QCheckBox("自动")
        self.auto_squelch_check.setToolTip(
            "自动跟踪噪声底（基线在任何模式下都会自动标定；"
            "关闭此项后基线锁定在最近一次标定，滑杆余量仍然生效）")
        self.auto_squelch_check.setChecked(True)
        self.auto_squelch_check.toggled.connect(self._on_auto_squelch_toggled)
        sq_auto_row.addWidget(self.auto_squelch_check)
        sq_auto_row.addStretch(1)
        rx_layout.addLayout(sq_auto_row)

        layout.addWidget(rx_group)

        # ---- 录音 ----
        record_group = QGroupBox("录音")
        record_layout = QVBoxLayout(record_group)

        self.record_button = QPushButton("开始录音")
        self.record_button.setObjectName("recordButton")
        self.record_button.setCheckable(True)
        self.record_button.setMinimumHeight(32)
        self.record_button.setMaximumHeight(56)
        self.record_button.toggled.connect(self._on_record_toggled)
        record_layout.addWidget(self.record_button)

        self.record_status = QLabel("未录音")
        self.record_status.setAlignment(Qt.AlignCenter)
        record_layout.addWidget(self.record_status)

        layout.addWidget(record_group)

        # 弹性空间
        layout.addStretch()

    # ========================================================================
    # 频率解析 / 统一频率真值
    # ========================================================================

    def _parse_freq_text(self, text: str) -> float:
        """把用户输入解析为 Hz。

        支持：
        - 带 M/MHz/k/kHz/G 后缀，如 "98.5M"=98.5MHz, "144500k"=144.5MHz
        - 纯数字：FM/WFM 按 MHz，AM 按 kHz；绝对值 >10000 视为 Hz（如 144500000）
        解析失败抛 ValueError。
        """
        t = text.strip().replace(",", "").replace(" ", "")
        if not t:
            raise ValueError("empty")
        low = t.lower()
        suffix = None
        for suf in ("mhz", "khz", "hz", "ghz", "g", "m", "k"):
            if low.endswith(suf):
                suffix = suf
                num = t[: -len(suf)]
                break
        else:
            num = t
        if num in ("", "-", "+", "."):
            raise ValueError("no number")
        val = float(num)
        if suffix is None:
            if abs(val) > 10000:
                suffix = "hz"
            elif self._current_mode in ("FM", "WFM") or self._current_mode is None:
                # None = 未连接/未选模式；沿用历史默认按 MHz 解析（FM 广播段最常用）
                suffix = "m"
            else:
                suffix = "k"
        if suffix in ("m", "mhz"):
            return val * 1e6
        if suffix in ("g", "ghz"):
            return val * 1e9
        if suffix in ("k", "khz"):
            return val * 1e3
        return val  # hz

    def _set_freq_hz(self, freq_hz: float):
        """统一设置频率真值并更新显示、发射调谐信号。

        NaN/±Inf 一律视为"没有频率"：既不改内部真值，也不向硬件下发调谐。
        历史实现走 max(FREQ_MIN, min(FREQ_MAX, nan))——NaN 比较恒为 False，
        结果被静默夹到 FREQ_MAX（6 GHz）并真调谐过去，等于把无效输入伪装成
        一个真实的 6 GHz 中心频率。
        """
        freq_hz = float(freq_hz)
        if not math.isfinite(freq_hz):
            return
        freq_hz = max(FREQ_MIN_HZ, min(FREQ_MAX_HZ, freq_hz))
        self._freq_hz = freq_hz
        # 用户手动调谐即采纳新频率，清掉恢复期"配置请求被钳位"的提示
        self.freq_input.setToolTip(self._freq_input_base_tip)
        self._update_freq_display()
        self._emit_tune(freq_hz)
        # 有真值了：±步进/收藏可以放开（首次手动调谐也走这条路）
        self._sync_freq_actions_enabled()

    def _emit_tune(self, freq_hz: float):
        mode = self._current_mode
        # 兼容旧信号
        if mode in ("FM", "WFM"):
            self.tune_fm_requested.emit(freq_hz / 1e6)
        elif mode == "AM":
            self.tune_am_requested.emit(int(round(freq_hz / 1e3)))
        # SDR 主路径（Hz, 模式）
        self.tune_sdr_requested.emit(freq_hz, mode)

    def _populate_presets(self, band: str):
        """填充预设电台列表。"""
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        if band == "FM 广播":
            if PRESET_FM_STATIONS:
                for freq, name in PRESET_FM_STATIONS:
                    self.preset_combo.addItem(f"{freq} MHz - {name}", (float(freq), "FM"))
            else:
                self.preset_combo.addItem(_NO_PRESET_TEXT, None)
        elif band == "AM 广播":
            if PRESET_AM_STATIONS:
                for freq, name in PRESET_AM_STATIONS:
                    self.preset_combo.addItem(f"{freq} kHz - {name}", (float(freq) / 1000.0, "AM"))
            else:
                self.preset_combo.addItem(_NO_PRESET_TEXT, None)
        else:
            # SDR 频段预设，按频段过滤
            band_keywords = {
                "航空": ["航空"],
                "业余 2m": ["2m", "APRS 144", "ISS"],
                "业余 70cm": ["70cm"],
                "气象卫星": ["NOAA", "METEOR"],
                "导航": ["GPS", "北斗"],
                "ISM/物联网": ["ISM", "WiFi"],
                "短波": ["80m", "40m", "20m", "15m", "10m"],
            }
            keywords = band_keywords.get(band, [])
            for freq, name, mode in SDR_BAND_PRESETS:
                if any(k in name for k in keywords):
                    self.preset_combo.addItem(f"{freq} MHz - {name} ({mode})", (float(freq), mode))
        self.preset_combo.blockSignals(False)
        # 占位项没有频率可调：给它一句"怎么办"，别让用户点了个空。
        has_preset = self.preset_combo.currentData() is not None
        self.preset_combo.setToolTip("" if has_preset else _NO_PRESET_TIP)

    @Slot(int)
    def _on_band_changed(self, index: int):
        """频段分类切换。"""
        band = self.band_combo.itemText(index)
        self._populate_presets(band)

    def _update_freq_display(self):
        """更新频率显示（数字网格恒为 Hz 原值，无真值时显占位）。"""
        hz = self._freq_hz
        self.freq_display.set_frequency(hz)
        if hz is None:
            # 未连接 / 未取得真实频率：输入框留空（占位符提示）
            return
        if hz >= 1e6:
            self.freq_input.setText(f"{hz / 1e6:g}")
        elif hz >= 1e3:
            self.freq_input.setText(f"{hz / 1e3:g}")
        else:
            self.freq_input.setText(f"{hz:g}")

    # ========================================================================
    # 槽函数
    # ========================================================================

    @Slot(str)
    def _on_mode_changed(self, mode: str):
        self._current_mode = mode
        self.mode_changed.emit(mode)
        # 预设列表属于「频段」，不属于解调模式：切模式只换带宽，不重排电台。
        # 旧实现把模式名（"FM"）当频段名传进去，_populate_presets 无一支命中，
        # 结果 clear() 后列表为空，还留下一条内容相关的最小宽缓存。
        self._populate_presets(self.band_combo.currentText())
        self._update_freq_display()
        # 切换模式时自动套用该模式的典型 VFO 带宽，并 emit vfo_bandwidth_changed
        bw = default_bandwidth(mode)
        self.vfo_bw_combo.blockSignals(True)
        idx = self.vfo_bw_combo.findData(float(bw))
        if idx >= 0:
            self.vfo_bw_combo.setCurrentIndex(idx)
        self.vfo_bw_combo.blockSignals(False)
        self.vfo_bandwidth_changed.emit(float(bw))

    @Slot()
    def _on_freq_input(self):
        text = self.freq_input.text()
        try:
            freq_hz = self._parse_freq_text(text)
        except (ValueError, OSError):
            # 解析失败：输入框变红，稍后恢复原值，绝不崩溃
            self.freq_input.setStyleSheet(
                "background-color:" + tokens().COLORS["danger"] + ";")
            QTimer.singleShot(700, self._restore_freq_input)
            return
        # 防呆：键入值超出可调范围时**拒绝下发**并明确告知，而不是
        # 静默夹到边界再调谐（那样 7 GHz 会被伪装成 6 GHz 的“调谐成功”）。
        # 程序内部路径（步进/预设/回填）仍走 _set_freq_hz 的边界钳位。
        if not (FREQ_MIN_HZ <= freq_hz <= FREQ_MAX_HZ):
            self.freq_input.setStyleSheet(
                "background-color:" + tokens().COLORS["danger"] + ";")
            QTimer.singleShot(700, self._restore_freq_input)
            QMessageBox.warning(
                self, "频率超出可调范围",
                f"输入 {text.strip()} = {freq_hz / 1e6:.6f} MHz，超出可调范围 "
                f"{FREQ_MIN_HZ / 1e3:.0f} kHz – {FREQ_MAX_HZ / 1e9:.0f} GHz。\n"
                "未下发调谐。请检查单位后缀（M/MHz、k/kHz、G/GHz）后重试。")
            return
        self.freq_input.setStyleSheet("")
        self._set_freq_hz(freq_hz)

    def _restore_freq_input(self):
        self.freq_input.setStyleSheet("")
        self._update_freq_display()

    @Slot(int)
    def _on_digit_stepped(self, hz: int):
        """FreqDisplay 按位编辑（滚轮/点击/右键清零）→ 走统一调谐通道。"""
        self._set_freq_hz(float(hz))

    @Slot(int)
    def _on_step_combo_changed(self, index: int):
        hz = float(self.step_combo.itemData(index) or 10_000.0)
        self._step_hz = hz
        self.step_changed.emit(hz)

    def _sync_freq_actions_enabled(self) -> None:
        """「以当前频率为输入」的动作只在拿到频率真值后才可用。

        连接成功 ≠ 拿到频率真值：后端读回失败时界面按红线保持「—」，
        此时 `_freq_hz` 仍是 None。旧实现让 ±步进 / 收藏按钮跟着
        `set_sdr_connected(True)` 一起亮，点一下就是 `None + float` /
        `None / 1e6` 直接抛 TypeError（2026-11-09 真机接入时 UI 交互守卫
        抓到三处：ControlPanel 的 `−`、`+`、「收藏当前频率」）。
        「控件可用」必须等于「这个动作此刻真的能做」。
        """
        ready = bool(self._sdr_connected) and self._freq_hz is not None
        targets = list(getattr(self, "_step_buttons", []) or [])
        targets.append(getattr(self, "bookmark_add_btn", None))
        for w in targets:
            if w is not None:
                w.setEnabled(ready)
                # 置灰必须带理由：鼠标悬停就知道为什么点不动。
                w.setToolTip("" if ready else
                             "还没拿到频率真值（未连接，或后端没有回读成功）："
                             "先调到一个频率再用")

    def _apply_step(self, direction: int):
        """按当前选中的步进 ±1 步。

        频率未知时按钮本就应为禁用（`_sync_freq_actions_enabled`）；
        这里再守一道，避免任何内部/脚本调用撞上 None 直接崩。
        """
        if self._freq_hz is None:
            return
        self._set_freq_hz(self._freq_hz + direction * self._step_hz)

    @Slot(int)
    def _on_preset_selected(self, index: int):
        if index < 0:
            return
        data = self.preset_combo.itemData(index)
        if data is None:
            return
        # 新格式: (freq_mhz, mode)
        if isinstance(data, tuple):
            freq_mhz, mode = data
            self._current_mode = mode
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentText(mode)
            self.mode_combo.blockSignals(False)
            self._set_freq_hz(freq_mhz * 1e6)
        else:
            # 旧格式兼容
            if self._current_mode in ("FM", "WFM"):
                self._set_freq_hz(float(data) * 1e6)
            else:
                self._set_freq_hz(float(data) * 1e3)

    @Slot(int)
    def _on_volume_changed(self, value: int):
        self._volume = value
        self.volume_label.setText(str(value))
        self.volume_changed.emit(value)

    def _on_gain_changed(self, db: int):
        self.gain_val.setText(f"{db} dB")
        self.gain_val.setToolTip("已连接：增益请求已下发，等待设备回执")
        # 用户手动调增益即采纳当前值，清掉恢复/收放期"配置请求被替换"提示
        self.gain_slider.setToolTip(self._gain_base_tip)
        self.gain_changed.emit(db)

    @Slot(int)
    def _on_sample_rate_changed(self, index: int):
        """采样率档位切换 → emit sample_rate_changed(rate_hz)。"""
        rate = float(self.sample_rate_combo.itemData(index) or 0.0)
        if rate > 0:
            self.sample_rate_changed.emit(rate)

    @Slot(int)
    def _on_vfo_bw_changed(self, index: int):
        """VFO 带宽档位切换 → emit vfo_bandwidth_changed(bw_hz)。"""
        bw = float(self.vfo_bw_combo.itemData(index) or 0.0)
        if bw > 0:
            # 用户手动选档即采纳当前档位，清掉恢复期"配置被替换"提示
            self.vfo_bw_combo.setToolTip(self._vfo_bw_base_tip)
            self.vfo_bandwidth_changed.emit(bw)

    def _on_squelch_changed(self, dbfs: int):
        """静噪门限：相对该模式噪声基线的余量刻度（-100~-20）。"""
        self.squelch_val.setText(f"{dbfs} dB")
        self.squelch_changed.emit(float(dbfs))

    def _on_auto_squelch_toggled(self, on: bool):
        # Auto 关闭时滑块门限生效；打开时由内核自动跟踪噪声底
        self.squelch_slider.setEnabled(not bool(on))
        self.auto_squelch_toggled.emit(bool(on))

    @Slot(bool)
    def _on_agc_changed(self, checked: bool):
        self.agc_changed.emit(checked)

    @Slot(int)
    def _on_agc_speed_changed(self, _idx: int):
        self.agc_speed_changed.emit(
            str(self.agc_speed_combo.currentData()))

    @Slot(int)
    def _on_ppm_changed(self, ppm: int):
        self.ppm_changed.emit(int(ppm))

    @Slot(bool)
    def _on_offset_tuning_changed(self, checked: bool):
        self.offset_tuning_changed.emit(checked)

    @Slot(bool)
    def _on_dc_removal_changed(self, checked: bool):
        """DC 偏移去除开关 → emit dc_removal_changed(bool)。"""
        self.dc_removal_changed.emit(checked)

    @Slot(bool)
    def _on_iq_balance_changed(self, checked: bool):
        """I/Q 不平衡校正开关 → emit iq_balance_changed(bool)。"""
        self.iq_balance_changed.emit(checked)

    # ---- 收藏 ----
    @staticmethod
    def _bookmark_display_name(name: str) -> "tuple[str, bool]":
        """返回 (显示名, 是否超过上限)。超长只截显示，不改原始数据。"""
        raw = str(name or "")
        if len(raw) <= BOOKMARK_NAME_MAX_CHARS:
            return raw, False
        return raw[:BOOKMARK_NAME_MAX_CHARS] + "…", True

    def _elide_bookmark_text(self, text: str, max_px: int = 200) -> str:
        """按像素宽度省略：下拉框文本永远不超过 max_px，避免横向撑爆。

        max_px 取 200 而不是更多：右栏 320 宽时，卡内下拉框的可用文本区
        约 230px（扣掉 8px 内边距与 26px 下拉箭头），240px 的省略宽度仍会
        被控件边缘硬切一半——实拍「航空应急频率 121.500（内置）（121.50(」
        就是省略后又被裁。

        用 ElideMiddle 而不是 ElideRight：本项目书签名是「名字 + 频率」的
        顺序（内置种子 "航空应急频率 121.500"、收藏默认名 "121.500MHz AM"），
        从右边省略正好把频率切掉——而频率恰恰是选台时唯一要读的数。
        中间省略会同时保住名字开头与频率结尾。全文仍在 tooltip 里。
        """
        return self.bookmark_combo.fontMetrics().elidedText(
            text, Qt.ElideMiddle, max_px)

    def _refresh_bookmarks(self):
        self.bookmark_combo.blockSignals(True)
        self.bookmark_combo.clear()
        abnormal = 0
        if not self._bookmarks:
            self.bookmark_combo.addItem("暂无书签，右键频谱保存当前频率", None)
        else:
            for i, (hz, name, mode, builtin) in enumerate(self._bookmarks):
                shown, bad = self._bookmark_display_name(name)
                abnormal += int(bad)
                tag = "（内置）" if builtin else ""
                # 收起态只排「（内置）+ 名字」：名字本身已含频率（内置种子与
                # 「收藏当前频率」的默认名都是 "121.500MHz AM" 这种），再拼一段
                # 频率只会把 200px 的省略号顶到「（12…」那一截——既重复又看不全。
                # 完整「频率 + 模式」留在悬停全文里，不丢信息。
                full = f"{shown}{tag} ({hz / 1e6:.3f}MHz {mode})"
                label = self._elide_bookmark_text(f"{tag}{shown}")
                self.bookmark_combo.addItem(("⚠ " if bad else "") + label, i)
                if not bad:
                    # 省略后的全文仍可读：悬停下拉框给完整条目。
                    self.bookmark_combo.setItemData(
                        self.bookmark_combo.count() - 1, full, Qt.ToolTipRole)
                if bad:
                    self.bookmark_combo.setItemData(
                        self.bookmark_combo.count() - 1,
                        f"书签名 {len(str(name))} 字符，超过上限 "
                        f"{BOOKMARK_NAME_MAX_CHARS}，显示已截断；原始数据未改动。",
                        Qt.ToolTipRole)
        if abnormal:
            self.bookmark_combo.setToolTip(
                f"⚠ {abnormal} 条书签名超过 {BOOKMARK_NAME_MAX_CHARS} 字符，"
                "显示已截断；原始数据未改动，建议在书签文件中手动清理。")
        else:
            self.bookmark_combo.setToolTip("")
        self.bookmark_combo.blockSignals(False)

    def load_bookmarks_from_list(self, bookmarks: list):
        """从外部（FrequencyManager）加载书签列表。

        每项为 (freq_hz, name, mode, builtin) 元组；旧调用给的 3 元组按
        ``builtin=False`` 归一化（兼容既有测试/外部脚本）。替换当前内存
        书签并刷新下拉框。
        """
        self._bookmarks = [
            (float(b[0]), str(b[1]), str(b[2]),
             bool(b[3]) if len(b) > 3 else False)
            for b in bookmarks
        ]
        self._refresh_bookmarks()

    @Slot()
    def _on_bookmark_add(self):
        # 频率未知（连接成功但后端没回读成功）时收藏没有对象可存：
        # 旧实现直接 f"{self._freq_hz / 1e6:.3f}MHz" 抛 TypeError。
        # 按钮此刻已禁用（_sync_freq_actions_enabled），此处兜内部调用。
        if self._freq_hz is None:
            return
        default_name = f"{self._freq_hz / 1e6:.3f}MHz {self._current_mode}"
        name, ok = QInputDialog.getText(
            self, "收藏频率", "名称:", QLineEdit.Normal, default_name)
        if not ok:
            return
        name = name.strip() or default_name
        # 数据层约束：新输入超长直接拒绝并说明，不静默截断也不写盘。
        if len(name) > BOOKMARK_NAME_MAX_CHARS:
            QMessageBox.warning(
                self, "书签名过长",
                f"书签名 {len(name)} 字符，超过上限 {BOOKMARK_NAME_MAX_CHARS} 字符，"
                "未保存。请缩短后重试。")
            return
        self._bookmarks.append(
            (self._freq_hz, name, self._current_mode, False))
        self._refresh_bookmarks()
        self.bookmark_combo.setCurrentIndex(self.bookmark_combo.count() - 1)
        self.bookmark_added.emit(self._freq_hz, name, self._current_mode)

    @Slot()
    def _on_bookmark_del(self):
        idx = self.bookmark_combo.currentData()
        if idx is None or not isinstance(idx, int):
            return
        if not (0 <= idx < len(self._bookmarks)):
            return
        hz, name, _mode, builtin = self._bookmarks[idx]
        if builtin:
            QMessageBox.information(
                self, "内置书签",
                f"「{name}」是内置频率，不能删除。"
                "下一步：先调到你要保留的频点，再用「收藏当前频率」"
                "保存为你自己的自定义书签。")
            return
        answer = QMessageBox.question(
            self, "删除书签",
            f"删除书签「{name}」（{hz / 1e6:.3f} MHz）？此操作不能撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        del self._bookmarks[idx]
        self._refresh_bookmarks()
        # 删除必须落盘：只删内存列表时 FrequencyManager 里的记录还在，
        # 下次启动 load_bookmarks_from_list 会把删掉的书签原样读回来。
        self._remove_bookmark_persisted(name)

    def set_bookmark_store(self, mgr) -> None:
        """注入书签持久化仓库（mbdsdr_ai.frequency_manager.FrequencyManager）。

        未注入时删除操作懒加载默认仓库（与 main_window 同一份
        ~/.mbdsdr/bookmarks.json），保证面板单独使用也删得掉。
        """
        self._bookmark_store = mgr

    def _remove_bookmark_persisted(self, name: str) -> None:
        """把一次删除真正写到磁盘。仓库不可用/删不到时只记日志，不打断 UI。"""
        store = getattr(self, "_bookmark_store", None)
        if store is None:
            try:
                from mbdsdr_ai.frequency_manager import FrequencyManager
                store = FrequencyManager()
            except Exception as exc:
                logger.warning("书签仓库初始化失败，本次删除不落盘: %s: %s",
                               type(exc).__name__, exc)
                return
        try:
            store.remove(str(name))
        except Exception as exc:
            logger.warning("书签删除落盘失败（UI 已移除，重启会复现）: %s: %s",
                           type(exc).__name__, exc)

    @Slot(int)
    def _on_bookmark_selected(self, index: int):
        idx = self.bookmark_combo.itemData(index)
        if idx is None or not isinstance(idx, int):
            return
        if not (0 <= idx < len(self._bookmarks)):
            return
        hz, _name, mode, _builtin = self._bookmarks[idx]
        self._current_mode = mode
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText(mode)
        self.mode_combo.blockSignals(False)
        self._set_freq_hz(hz)

    @Slot(bool)
    def _on_record_toggled(self, checked: bool):
        self._recording = checked
        if checked:
            self._record_outcome = None
            self.record_button.setText("停止录音")
            self.record_status.setText("录音中... 00:00")
            self._record_seconds = 0
        else:
            self.record_button.setText("开始录音")
            # 取消/失败/正常完成由主窗口 set_record_outcome 收口；这里只在
            # 没有结果态时回到中性文案，绝不无条件宣布「录音完成」。
            if self._record_outcome is None:
                self.record_status.setText("未录音")
        self.record_toggled.emit(checked)

    def set_record_outcome(self, kind: str, text: str = "") -> None:
        """写入一次录音结果（三分支：取消 / 失败 / 正常保存）。

        kind:
          ``cancelled`` —— 用户取消或目录未确认，``text`` 可空；
          ``failed``    —— 未连接/目录不可用/写盘失败，``text`` 是原因；
          ``completed`` —— 真正落了盘，``text`` 形如「已保存 N 样本 (X KB)」。
        失败与取消路径不得出现「完成」二字（完成只属于成功路径）。
        """
        kind = str(kind or "").strip().lower()
        self._record_outcome = kind or None
        if kind == "cancelled":
            self.record_status.setText("已取消（未开始录音）")
        elif kind == "failed":
            reason = str(text or "").replace("完成", "结束").strip()
            self.record_status.setText(
                f"录音失败：{reason}" if reason else "录音失败")
        elif kind == "completed":
            self.record_status.setText(str(text or "已保存"))
        elif kind == "recording":
            self.record_status.setText("录音中... 00:00")
        else:
            self._record_outcome = None
            self.record_status.setText("未录音")

    # ========================================================================
    # 外部接口
    # ========================================================================

    def get_frequency_hz(self) -> float:
        """返回当前内部频率真值 (Hz)。供主窗口连接后下发初始中心频率。

        未设置过频率时返回文档化默认 98.5 MHz（FM 广播段），避免调用方对 None 崩溃。
        UI 显示层在未连接时仍显 "-- MHz"，不在状态区造假。
        """
        if self._freq_hz is None:
            return 98.5e6
        return float(self._freq_hz)

    def set_freq_fm(self, freq: float):
        """从外部设置 FM 频率（如频谱组件双击）。"""
        self._current_mode = "FM"
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText("FM")
        self.mode_combo.blockSignals(False)
        self._freq_hz = freq * 1e6
        self._update_freq_display()

    def set_freq_am(self, freq: int):
        """从外部设置 AM 频率。"""
        self._current_mode = "AM"
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText("AM")
        self.mode_combo.blockSignals(False)
        self._freq_hz = freq * 1e3
        self._update_freq_display()

    def get_dc_removal_enabled(self) -> bool:
        """返回 DC 偏移去除开关当前状态（默认开）。"""
        return bool(self.dc_removal_check.isChecked())

    def get_iq_balance_enabled(self) -> bool:
        """返回 I/Q 不平衡校正开关当前状态（默认开）。"""
        return bool(self.iq_balance_check.isChecked())

    def update_signal_level(self, dbfs):
        """由主窗口从 IQ 功率估计后调用，更新 S-meter。真实数据驱动，绝不造假。

        传入 None / 不可解析值 / 非有限值（NaN、±Inf）时显示 "--" 并把进度条
        归零（空）。NaN 必须走空态：max(-120, min(0, nan)) 会静默夹成 0 dBFS
        （= 满刻度），等于把"没测出电平"伪装成"信号爆表"。
        """
        if dbfs is None:
            self.smeter_label.setText("--")
            self.smeter_bar.setValue(self.smeter_bar.minimum())
            return
        try:
            v = float(dbfs)
        except (TypeError, ValueError):
            self.smeter_label.setText("--")
            self.smeter_bar.setValue(self.smeter_bar.minimum())
            return
        if not math.isfinite(v):
            self.smeter_label.setText("--")
            self.smeter_bar.setValue(self.smeter_bar.minimum())
            return
        v = max(-120.0, min(0.0, v))
        self.smeter_bar.setValue(int(v))
        self.smeter_label.setText(f"{v:.0f} dBFS")

    def update_record_time(self, seconds: int):
        """更新录音时长显示。"""
        self._record_seconds = seconds
        if self._recording:
            m, s = divmod(seconds, 60)
            self.record_status.setText(f"录音中... {m:02d}:{s:02d}")

    def set_volume(self, volume: int):
        """从外部设置音量（0–100 人类量纲）。"""
        self.volume_slider.setValue(volume)

    def set_gain_range(self, lo_db: float, hi_db: float):
        """按所连设备能力动态收放增益滑杆范围（连接后由 main_window 调用）。

        非法/退化范围（hi<=lo）如实拒绝不动，保留当前范围。
        """
        try:
            lo, hi = int(round(lo_db)), int(round(hi_db))
        except (TypeError, ValueError):
            return
        if hi <= lo:
            return
        requested = self.gain_slider.value()
        # setRange 会把超界值一并夹到新上限并触发 valueChanged；这里必须
        # 屏蔽信号，否则 _on_gain_changed 会把"设备能力夹出来的值"写回配置，
        # 用户原值（配置意图）被静默改写。
        self.gain_slider.blockSignals(True)
        self.gain_slider.setRange(lo, hi)
        applied = self.gain_slider.value()
        self.gain_slider.blockSignals(False)
        base = (f"LNA 增益 {lo}–{hi} dB（按所连设备能力动态收放；"
                "实际档位以设备为准，后端钳位）")
        # 静态说明随设备能力更新；用户后续手动调增益时还原的就是这一版
        self._gain_base_tip = base
        self.gain_slider.setToolTip(base)
        if applied != requested:
            hint = (f"当前增益 {requested} dB 超出所连设备能力 [{lo}, {hi}] dB，"
                    f"已按 {applied} dB 显示（配置文件未改）")
            logger.warning("%s", hint)
            self.gain_slider.setToolTip(f"{base}\n⚠ {hint}")
            # 标签是读数：已连接才写设备真实值；未连接保持「—」，不编造。
            if self._sdr_connected:
                self.gain_val.setText(f"{applied} dB")
                self.gain_val.setToolTip(f"⚠ {hint}")

    # ========================================================================
    # 启动恢复用 setter（持久化参数回填）
    # ------------------------------------------------------------------------
    # 这些 setter 只在程序启动、从 desktop_settings.json 读回上次参数时调用。
    # 一律 blockSignals：只更新控件显示与内部真值，不向 main_window 发射变更信号，
    # 从而不触发后端下发 / 不重复写配置。用户手动操作仍走原有 _on_* 信号路径。
    # ========================================================================

    def set_frequency_hz(self, freq_hz: float):
        """从外部恢复中心频率 (Hz)，不发射调谐信号。

        断连时只存内部真值、不刷新 freq_display（保持 "-- MHz"），
        避免持久化频率被误当成正在接收的频率；连接后由 set_sdr_connected(True) 恢复显示。

        NaN/±Inf 视为"没有频率"，保持原值不变（绝不夹成 FREQ_MAX 6 GHz，
        理由同 _set_freq_hz）。
        """
        freq_hz = float(freq_hz)
        if not math.isfinite(freq_hz):
            return
        requested = freq_hz
        freq_hz = max(FREQ_MIN_HZ, min(FREQ_MAX_HZ, freq_hz))
        self._freq_hz = freq_hz
        if freq_hz != requested:
            # 恢复值被全局边界钳位：显示按边界，但必须让用户看见"请求没照做"；
            # 配置文件原值不回写（用户意图保留）。
            side = "低于" if requested < FREQ_MIN_HZ else "高于"
            hint = (f"配置请求 {_format_frequency_hz(requested)} {side}全局调谐范围 "
                    f"[{_format_frequency_hz(FREQ_MIN_HZ)}, "
                    f"{_format_frequency_hz(FREQ_MAX_HZ)}]，已按边界 "
                    f"{_format_frequency_hz(freq_hz)} 显示（配置文件未改）")
            logger.warning("%s", hint)
            self.freq_input.setToolTip(f"{self._freq_input_base_tip}\n⚠ {hint}")
        else:
            self.freq_input.setToolTip(self._freq_input_base_tip)
        if self._sdr_connected:
            self._update_freq_display()
        # 拿到真值后立刻放开 ±步进/收藏（断开期间的禁用态在这里解除）
        self._sync_freq_actions_enabled()

    def set_mode(self, mode: str):
        """从外部恢复解调模式，不发射 mode_changed / vfo_bandwidth_changed。

        断连时只存内部真值、不刷新 freq_display；连接后由 set_sdr_connected(True) 恢复。
        """
        mode = (mode or "FM").upper()
        self._current_mode = mode
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText(mode)
        self.mode_combo.blockSignals(False)
        # 同 _on_mode_changed：预设按频段走，模式名不能当频段名（见该处注释）。
        self._populate_presets(self.band_combo.currentText())
        if self._sdr_connected:
            self._update_freq_display()
        # 同步带宽下拉到该模式典型带宽（blockSignals，不 emit）
        bw = default_bandwidth(mode)
        self.vfo_bw_combo.blockSignals(True)
        idx = self.vfo_bw_combo.findData(float(bw))
        if idx >= 0:
            self.vfo_bw_combo.setCurrentIndex(idx)
        self.vfo_bw_combo.blockSignals(False)
        # 模式切换会改写带宽档位：旧的"配置请求被替换"提示已不成立
        self.vfo_bw_combo.setToolTip(self._vfo_bw_base_tip)

    def set_gain(self, db: float):
        """从外部恢复 LNA 增益 (dB)，不发射 gain_changed。

        NaN/±Inf 视为"没有增益值"，保持原值不变：历史实现走
        int(max(0, min(49, nan)))，NaN 比较恒为 False，结果静默夹到满增益
        49 dB 并显示出来——把"读不到增益"伪装成"增益拉满"。
        """
        db = float(db)
        if not math.isfinite(db):
            return
        requested = db          # 保留请求原值（浮点），提示里如实显示
        db = int(max(0, min(49, db)))
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(db)
        self.gain_slider.blockSignals(False)
        # 滑杆=配置意图，标签=读数。恢复值还没真实施加，一律不写成读数。
        self.gain_val.setText("—")
        if self._sdr_connected:
            self.gain_val.setToolTip("已连接：等待增益下发（滑杆位置=配置意图）")
        else:
            self.gain_val.setToolTip(f"未连接：上次配置 {db} dB（未施加）")
        if abs(requested - db) > 1e-9:
            hint = (f"配置请求 {requested:g} dB 超出未连接时的显示范围 [0, 49] dB，"
                    f"已按 {db} dB 显示（配置文件未改）")
            logger.warning("%s", hint)
            self.gain_slider.setToolTip(f"{self._gain_base_tip}\n⚠ {hint}")
        else:
            self.gain_slider.setToolTip(self._gain_base_tip)

    def set_gain_applied(self, db: float, readback_source: str = "") -> bool:
        """回填设备真实增益读数；只改标签，不动滑杆。

        与 :meth:`set_gain` 的分工：``set_gain`` 写配置意图（滑杆位置），
        本方法写读数（标签）。未连接、非数值或非有限值一律拒绝渲染，
        返回 ``False``；成功显示返回 ``True``。

        后端夹值或写入失败时，调用方回读 ``get_gain()`` 后传给本方法：
        标签显示真实值并追加 ⚠ 提示，滑杆仍停在用户意图，配置文件不改。

        ``readback_source`` 是后端自报的读数来源（``gain_readback_source()``）：
        非空表示确有回读通道，空串表示该后端只回报请求值。
        """
        if not self._sdr_connected:
            return False
        try:
            value = float(db)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(value):
            return False
        self.gain_val.setText(f"{value:g} dB")
        requested = self.gain_slider.value()
        if abs(value - requested) > 1e-9:
            hint = (f"后端报告增益 {value:g} dB，配置意图 {requested} dB 未生效"
                    "（配置文件未改）")
            logger.warning("%s", hint)
            self.gain_val.setToolTip(f"⚠ {hint}")
        elif readback_source:
            # 有回读通道：如实标明读数来源，不升级成“设备实测”。
            self.gain_val.setToolTip(f"已连接：{readback_source}")
        else:
            # 相等不等于“设备回执”：该后端没有增益回读通道，读数只是
            # 请求值（可能经软件吸附/限幅）。这里只陈述来源，不替没回读
            # 过的值背书。
            self.gain_val.setToolTip(
                "已连接：增益读数为请求值，硬件未回读"
                "（该后端无增益回读通道，吸附/限幅后的真实档位未知）")
        return True

    def set_ppm_applied(self, value: int) -> bool:
        """回填设备最后确认生效的 PPM（不是用户请求值）；不发射 ppm_changed。

        后端没有 ``get_ppm`` 回读接口（见 ``mbdsdr_ai/sdr_backend.py``），
        「设备真实值」只能是最后一次被设备接受的请求值；写失败时调用方传
        这个值而不是刚被拒的请求值。未连接或不可渲染返回 ``False``。
        """
        if not self._sdr_connected:
            return False
        try:
            ppm = int(value)
        except (TypeError, ValueError):
            return False
        ppm = max(self.ppm_spin.minimum(), min(self.ppm_spin.maximum(), ppm))
        requested = self.ppm_spin.value()
        self.ppm_spin.blockSignals(True)
        self.ppm_spin.setValue(ppm)
        self.ppm_spin.blockSignals(False)
        if ppm != requested:
            hint = f"设备最后生效的 PPM 是 {ppm}（请求 {requested} 未生效）"
            logger.warning("%s", hint)
            self.ppm_spin.setToolTip(f"{self._ppm_base_tip}\n⚠ {hint}")
        else:
            self.ppm_spin.setToolTip(self._ppm_base_tip)
        return True

    def set_offset_tuning_applied(self, enabled: bool) -> bool:
        """回填设备最后确认生效的 Offset Tuning 状态；不发射 offset_tuning_changed。

        与 :meth:`set_ppm_applied` 同一策略：没有回读接口，只能显示最后一次
        被设备接受的状态。未连接返回 ``False``。
        """
        if not self._sdr_connected:
            return False
        requested = self.offset_check.isChecked()
        applied = bool(enabled)
        self.offset_check.blockSignals(True)
        self.offset_check.setChecked(applied)
        self.offset_check.blockSignals(False)
        if applied != requested:
            hint = (f"设备最后生效的 Offset Tuning 是"
                    f"{'开' if applied else '关'}（请求 "
                    f"{'开' if requested else '关'} 未生效）")
            logger.warning("%s", hint)
            self.offset_check.setToolTip(f"{self._offset_base_tip}\n⚠ {hint}")
        else:
            self.offset_check.setToolTip(self._offset_base_tip)
        return True

    @staticmethod
    def _default_sample_rate_options() -> list[float]:
        """连接前预填：仍取自后端能力（RTL 声明区间），不是前端常量表。

        区间常量取 ``mbdsdr_ai/rtlsdr_params.py``（librtlsdr 口径），档位由
        ``sample_rate_options`` 从 RTL 后端表里过滤。连接成功后
        ``refresh_sample_rate_options_from_active_backend`` 会按当前活跃后端
        重新取；后端未报告范围时只显示“未知”，不编档位。
        """
        try:
            from mbdsdr_ai.rtlsdr_params import SAMPLE_RATE_MAX_HZ, SAMPLE_RATE_MIN_HZ
            from mbdsdr_ai.device_capabilities import sample_rate_options
            return list(sample_rate_options({
                "driver": "rtlsdr",
                "sample_rate_range": (SAMPLE_RATE_MIN_HZ, SAMPLE_RATE_MAX_HZ),
                "capability_status": {"sample_rate_range": "declared"},
            }) or [])
        except Exception as exc:
            # 预填失败 → 下游显示“未知”说明项；原因写日志，不静默
            logger.warning("预填采样率档位失败，按未知处理: %s: %s",
                           type(exc).__name__, exc)
            return []

    def _populate_sample_rate_options(self, options, select_hz=None):
        """用后端可见档位重建下拉；保持“可见 ⊆ 后端可接受”，不补默认档。"""
        combo = self.sample_rate_combo
        rates = [float(r) for r in (options or [])]
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        self._sr_base_texts = [format_sample_rate(_rate) for _rate in rates]
        for _rate, _text in zip(rates, self._sr_base_texts):
            combo.addItem(_text, float(_rate))
        if not rates:
            # 未知不是“一档”，给出不可选的说明项；不拿默认采样率凑成假档位。
            combo.addItem("未知（后端未报告采样率范围）", None)
            self._sr_base_texts = ["未知（后端未报告采样率范围）"]
            combo.setEnabled(False)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
            return
        combo.setEnabled(bool(getattr(self, "_sdr_connected", False)))
        target = select_hz
        if target is None and current in rates:
            target = current
        idx = -1
        if target is not None:
            idx = combo.findData(float(target))
            if idx < 0:
                # 与 sdr_backend.nearest_sample_rate 同规则：最近档、平局取低
                idx = min(
                    range(combo.count()),
                    key=lambda i: (abs(float(combo.itemData(i)) - float(target)),
                                   float(combo.itemData(i))))
        if idx < 0:
            idx = 0
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        self._apply_sample_rate_unconfirmed_marker()

    def _apply_sample_rate_unconfirmed_marker(self):
        """未连接时把采样率档位标成「默认 · 未确认」（全 UI 同一句写法）。

        连接前下拉里的档位来自能力声明/项目基准，没有一条是设备回执；
        光显示「2.400 MS/s」会被读成"设备正在跑 2.400 MS/s"。默认档用
        ``constants.DEFAULT_SR_UNCONFIRMED``（与模块面板/设备面板同一句），
        其余声明档位标「（未确认）」——都是"可选项"而不是"当前值"。
        连接后恢复纯档位文案。
        """
        combo = getattr(self, "sample_rate_combo", None)
        base_texts = list(getattr(self, "_sr_base_texts", []) or [])
        if combo is None or not base_texts or len(base_texts) != combo.count():
            return
        connected = bool(getattr(self, "_sdr_connected", False))
        default_text = format_sample_rate(DEFAULT_SAMPLE_RATE_HZ)
        combo.blockSignals(True)
        for i, base in enumerate(base_texts):
            if connected:
                combo.setItemText(i, base)
            elif base == default_text:
                combo.setItemText(i, DEFAULT_SR_UNCONFIRMED)
            else:
                combo.setItemText(i, f"{base}（未确认）")
        combo.blockSignals(False)

    def set_sample_rate_options(self, options, select_hz=None):
        """由后端能力设置可见档位；``options=None`` 表示未知，显示说明项。"""
        self._sample_rate_options = [float(r) for r in (options or [])]
        self._populate_sample_rate_options(self._sample_rate_options, select_hz)

    def refresh_sample_rate_options_from_active_backend(self):
        """按当前活跃后端刷新可见档位（下拉展开时调用，不依赖前端常量）。"""
        win = self.window()
        backend = getattr(win, "_active_sdr_backend", None)
        if backend is None:
            return
        current = None
        try:
            current = backend.get_sample_rate()
        except Exception as exc:
            logger.warning("读取后端当前采样率失败，保持原选中档: %s: %s",
                           type(exc).__name__, exc)
            current = None
        try:
            from mbdsdr_ai.device_capabilities import sample_rate_options_for_backend
            options = sample_rate_options_for_backend(backend)
        except Exception as exc:
            logger.warning("获取后端采样率档位失败，按未知处理: %s: %s",
                           type(exc).__name__, exc)
            options = None
        self.set_sample_rate_options(options, current)

    def set_sample_rate(self, rate_hz: float):
        """从外部恢复采样率档位；无精确匹配时选最近档，不发射 sample_rate_changed。

        **能力前置校验**：请求值不在当前可见档位（= 当前设备能力）时，不许
        静默吸附。这里保留"最近档显示"，同时在采样率标签上打 ⚠、把完整
        原因写进两个 tooltip 并 logger.warning；合法值会清掉提示。
        配置文件里的原值不回写（用户意图保留，失败对用户可见）。
        """
        rate = float(rate_hz)
        combo = self.sample_rate_combo
        idx = combo.findData(rate)
        if (idx < 0 and combo.count()
                and combo.itemData(0) is not None):
            best, best_diff = 0, None
            for i in range(combo.count()):
                d = abs(float(combo.itemData(i)) - rate)
                if best_diff is None or d < best_diff:
                    best_diff, best = d, i
            idx = best
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

        if idx < 0 or combo.itemData(idx) is None:
            return None
        applied = float(combo.itemData(idx))
        if abs(applied - rate) > 1e-9:
            rates = [float(combo.itemData(i)) for i in range(combo.count())
                     if combo.itemData(i) is not None]
            lo, hi = min(rates), max(rates)
            # 超出范围与"范围内但不精确"是两回事：连续可调后端（如 network_iq）
            # 只暴露范围两端，中间值是设备支持的，不能诬称"超出能力"。
            cause = (f"超出当前设备能力 [{format_sample_rate(lo)}, "
                     f"{format_sample_rate(hi)}]"
                     if rate < lo or rate > hi else
                     f"不在当前可见档位 [{format_sample_rate(lo)}, "
                     f"{format_sample_rate(hi)}]")
            hint = (
                f"配置请求 {format_sample_rate(rate)} {cause}，"
                f"已按最近档 {format_sample_rate(applied)} 显示（配置文件未改）")
            logger.warning("%s", hint)
            self.sr_label.setText("采样率 ⚠")
            self.sr_label.setToolTip(hint)
            combo.setToolTip(hint)
        else:
            self.sr_label.setText("采样率")
            self.sr_label.setToolTip("")
            combo.setToolTip("")
        return applied

    def set_vfo_bandwidth(self, bw_hz: float):
        """从外部恢复 VFO 带宽；无精确匹配选最近档，不发射 vfo_bandwidth_changed。

        替换必须可见：配置请求不在当前可见档位时，按最近档显示并在下拉
        tooltip 追加原因 + logger.warning；精确命中则还原静态说明。
        配置文件原值不回写（用户意图保留）。
        """
        bw = float(bw_hz)
        if not math.isfinite(bw):
            return          # 无值不是"最接近的档"：保持控件原值，不伪造 500 Hz
        combo = self.vfo_bw_combo
        idx = combo.findData(bw)
        applied = bw
        if idx < 0 and combo.count():
            best, best_diff = 3, None
            for i in range(combo.count()):
                d = abs(float(combo.itemData(i)) - bw)
                if best_diff is None or d < best_diff:
                    best_diff, best = d, i
            idx = best
            applied = float(combo.itemData(idx))
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        if idx < 0:
            return
        if abs(applied - bw) > 1e-9:
            hint = (f"配置请求 {bw:g} Hz 解调带宽不在当前可见档位，"
                    f"已按最近档 {applied:g} Hz 显示（配置文件未改）")
            logger.warning("%s", hint)
            combo.setToolTip(f"{self._vfo_bw_base_tip}\n⚠ {hint}")
        else:
            combo.setToolTip(self._vfo_bw_base_tip)

    def set_agc(self, enabled: bool):
        """从外部恢复 AGC 开关，不发射 agc_changed。"""
        self.agc_check.blockSignals(True)
        self.agc_check.setChecked(bool(enabled))
        self.agc_check.blockSignals(False)

    def set_agc_speed(self, speed: str):
        """从外部恢复音频 AGC 档位，不发射 agc_speed_changed。"""
        key = str(speed or "medium").lower()
        if key not in ("off", "fast", "medium", "slow"):
            key = "medium"
        self.agc_speed_combo.blockSignals(True)
        idx = self.agc_speed_combo.findData(key)
        if idx >= 0:
            self.agc_speed_combo.setCurrentIndex(idx)
        self.agc_speed_combo.blockSignals(False)
