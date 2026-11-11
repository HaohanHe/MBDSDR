# SPDX-License-Identifier: MIT
"""
MBDSDR 频谱显示组件
====================
优先 OpenGL 渲染，无 OpenGL 时降级为 QPainter 软件渲染。

频谱数据源：真实复数 IQ 采样 → 窗函数 → numpy.fft.fft（复输入）
→ fftshift → dBFS 归一化 → 多帧幅度平均 → 频率轴映射。

无 SDR / 无 IQ 数据时：频谱区域只画网格 + 红色"未连接 SDR"提示，
**不绘制任何谱线、不生成高斯峰、不使用 np.random 造假谱**。
"""

import logging
import math
import numpy as np
import time
from typing import Optional, List, Tuple, Dict

_log = logging.getLogger(__name__)

from constants import DEFAULT_SAMPLE_RATE_HZ

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, Slot, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetricsF, QLinearGradient,
    QImage, QPainterPath, QPolygon, QPolygonF, QRegion, QCursor, QShortcut,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QLabel,
    QDoubleSpinBox, QSlider, QFrame, QMenu, QSplitter, QToolTip, QPushButton,
    QToolButton, QWidgetAction,
)

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False

# 多 VFO 管理器（纯数据层，无 Qt/硬件依赖）。SpectrumPanel 持有一个实例，
# VFO 拖拽时移动 current / 新建 VFO，并按悬停/点击确认维护三态焦点。
try:
    from mbdsdr_ai.vfo_manager import VfoManager
except Exception:  # pragma: no cover - 防御性：后端包未就位时不阻塞 UI 启动
    VfoManager = None  # type: ignore

# CarWith 设计令牌：QSplitter handle 厚度 / FFT-瀑布分区比例统一从 tokens 取，
# 不在此处散落 magic number。
try:
    from tokens import tokens as _tokens
except Exception:  # pragma: no cover
    _tokens = None  # type: ignore

# 滤波宽窄预设 + 模式查询（键盘 , / . 档位步进 / 单键切解调的数据源，
# gqrx kbd-shortcuts.txt / filter_preset_table 惯例）
try:
    from mode_registry import next_bandwidth_preset, get as _get_demod_mode
except Exception:  # pragma: no cover
    next_bandwidth_preset = None  # type: ignore
    _get_demod_mode = None  # type: ignore

# 单键切解调映射（gqrx kbd-shortcuts：A=AM N=NFM W=WFM S=LSB Shift+S=USB C=CW；
# F=FM 为 gqrx 所无、按我方惯例补）
# 键位本身来自 desktop/shortcuts.py（唯一事实源，scope=spectrum），
# 这里只声明「登记 id → 解调模式」的业务映射。
try:
    from shortcuts import bind as _bind_shortcut
except ImportError:  # pragma: no cover - 以 desktop 包方式导入时
    from desktop.shortcuts import bind as _bind_shortcut

#: 登记 id -> 解调模式（单键切模式；S/Shift+S 分别是 LSB/USB）
_LOCAL_MODE_SHORTCUTS = (
    ("spectrum.mode.am", "AM"),
    ("spectrum.mode.nfm", "NFM"),
    ("spectrum.mode.fm", "FM"),
    ("spectrum.mode.wfm", "WFM"),
    ("spectrum.mode.cw", "CW"),
    ("spectrum.sideband.lsb", "LSB"),
    ("spectrum.sideband.usb", "USB"),
)

#: 登记 id -> Qt 键码（keyPressEvent 用；键位串只在 shortcuts.py 维护）
_LOCAL_KEY_CODES = {
    _sid: QKeySequence(_bind_shortcut(_sid))[0].key()
    for _sid in (
        "spectrum.tune.left", "spectrum.tune.right",
        "spectrum.tune.up", "spectrum.tune.down",
        "spectrum.filter.narrow", "spectrum.filter.wide",
    )
}

#: 焦点控件（及其祖先）带此属性 = 该控件声明了局部键作用域，
#: 主窗的 WidgetWithChildrenShortcut 不得抢键（见 desktop/shortcuts.py 规则）。
LOCAL_SHORTCUT_SCOPE_ATTR = "_owns_local_shortcuts"


def owns_local_shortcuts(widget) -> bool:
    """焦点控件或其祖先是否声明「局部键优先」。

    main_window 的全天模式 A 在触发槽里用它兜底：频谱获焦时不抢 A。
    """
    while widget is not None:
        if getattr(widget, LOCAL_SHORTCUT_SCOPE_ATTR, False):
            return True
        widget = widget.parentWidget()
    return False


# 触屏手势：双指捏合缩放 + 单指拖动平移（Surface/平板）。鼠标模式下零旁路。
try:
    from touch_helpers import PinchZoomMixin
except Exception:  # pragma: no cover
    PinchZoomMixin = object  # type: ignore


# ============================================================================
# CarWith dark_car 配色（画布背景/网格/谱线/峰值/离线）
# 浅米白旧值已废弃；深色画布统一从 tokens 取色。
# ============================================================================

if _tokens is not None:
    _c = _tokens().COLORS
else:  # pragma: no cover - tokens 缺失时的兜底深色值
    _c = {"card_1": "#1f1f1f", "gray_400": "#585c63", "gray_200": "#939393",
          "accent": "#919cac", "success": "#6BA89A", "danger": "#B85C5C"}

PAL_BG = _c["card_1"]         # 画布背景（深卡片色）
PAL_GRID = _c["gray_400"]     # 网格/文字
PAL_LINE = _c["accent"]       # 谱线/游标（冷蓝灰）
PAL_PEAK = _c["success"]      # 峰值标注
PAL_OFFLINE = _c["danger"]    # 未连接提示
PAL_MAXHOLD = _c["spectrum_max_hold"]   # Max hold 包络线（SDR++ 同款语义）
PAL_MINHOLD = _c["spectrum_min_hold"]   # Min hold 包络线
PAL_BOOKMARK = _c["spectrum_bookmark"]  # 书签标签（gqrx 书签竖旗）

# 谱迹（SDR++ FFT 线签名）：1px 亮青实线 + 同色低透明度向下渐变填充。
# 不跟主题走：SDR++ 里谱迹是信号读数的"主角"，换 UI 主题也不该变灰。
PAL_TRACE = "#4FE3F0"

# 网格线：SDR++ 是极淡 1px 实线（不是虚线），只用来读刻度不抢谱迹。
PAL_GRID_ALPHA = 60

# 频段彩带分类色（SDR++ bandplan 仿妆: 截图同款语义——
# 航空绿 / 低轨卫星灰 / 广播蓝 / 40m 业余红, 短波广播青, 海事紫灰, 对讲机琥珀）
BANDPLAN_CATEGORY_COLORS = {
    "broadcast": "#3E6FA3",
    "aviation": "#3F9142",
    "amateur": "#B5495C",
    "satellite": "#8A8F98",
    "shortwave": "#2E7D8C",
    "maritime": "#7A6FA0",
    "pmr": "#A0794A",
}

# 书签药丸高度（像素）：谱面顶部这一条带内点击 = 激活书签（不进调谐/拖拽）。
_BM_TAG_H = 16.0

# 「显示」弹层里三根横滑杆（缩放/上限/下限）的最小宽（px）。
# QSlider 自身的 minimumSizeHint 只有 84 px：-160..0 的量程落到 84 px 时
# 每像素跳近 2 dB，拖不准；120 px 让上限/下限各留约 1.3 dB/px 的行程。
# 竖排时这里曾是「最小高 90/80/80 把面板最小高撑到 347、303 档裁掉末根
# 滑杆 44 px」的元凶；迁进弹层后高度不再参与面板最小尺寸。
_SLIDER_MIN_W = 120

# 瀑布图默认色带：SDR++ 启动时加载的 Turbo 色表（waterfall.cpp:11-25 的
# 经典色表在 SDR++ 里已被 Turbo 取代，见 docs/learn/sdrpp.md §3.5）。
# 深紫蓝 → 蓝 → 青 → 绿 → 黄 → 橙 → 红 → 深红：弱信号（底噪）落在冷端，
# 强信号一路烧到红端，层次比低饱和灰调清楚得多。
WATERFALL_COLORS = [
    "#30123B", "#3A32A8", "#4272E8", "#399FF8", "#1FCFD2", "#22EBA5",
    "#5CFC6C", "#A2FC3C", "#D0E834", "#F2C53A", "#FD9A2D", "#F26215",
    "#D83706", "#B01901", "#7A0402",
]

# 旧低饱和色带（保留为第二选项：打印/长时间盯屏不刺眼）
WATERFALL_COLORS_SOFT = [
    "#EDEAE4", "#D8DCD8", "#AFC0C4", "#8FA8B0",
    "#6BA89A", "#9FB07A", "#C4B85C", "#C4845C", "#B85C5C",
]


# 瀑布历史环形缓冲深度（行）。合法集合必须与 C++ tokens.h
# kWaterfallDepthChoices 逐值一致（GUI / schema / AI 共用六档）；不在档位内
# 的值（例如 384）不静默夹取，回落默认 256 并记录原因。上限 4096 按实测
# 可用性给出（512 bin 时约 8 MiB，2048 bin 时 32 MiB）。
WF_DEPTH_CHOICES = (128, 256, 512, 1024, 2048, 4096)
WF_DEPTH_DEFAULT_ROWS = 256

#: 峰值判定默认门限（dB）：高于「本帧全部有限 bin 的中位数」多少才算峰。
#: 设置 schema 的 peak_threshold_db 默认值与频谱条滑杆共用这个语义；
#: 引用请用本符号名，不要写行号（行号会随重构漂）。
#:
#: 6 dB 是 2026-11-05 裁定的检测器定义值（与 C++ tokens.h::kPeakRelDb 同值）。
#: 反证：median+8 dBFS 的持续载波在 15 dB 下数学上进不了候选。
#: 已知代价（不是 bug）：6 dB 会把比全帧中位数还低 0.3 dB 的持续噪声线
#: （88.1 MHz OTA 录音里的 87.700 MHz）经 10 帧跟踪升成常驻标签，且
#: 228/256 帧撞满标签上限。接受这个代价，别当漏掉的 bug 去"修"。
_PEAK_REL_DB_DEFAULT = 6.0

#: 绝对电平门（dBFS）：低于它的 bin 不参与候选。
#: 已知副作用（是绝对电平门的固有行为，不是 bug）：底噪中位数 -110 dBFS
#: 时，+8 dB 载波 = -102 dBFS 会被这条门挡掉（同余量在 -100 底噪下可标）。
_PEAK_ABS_FLOOR_DBFS = -100.0

#: 持续确认帧数：同一位置连续出现 3 帧才升成标签（单帧尖峰永不成熟）。
_PEAK_MIN_SEEN_FRAMES = 3
#: 跨帧匹配半径（bin）：同一信号允许 ±2 bin 漂移。
_PEAK_MATCH_BINS = 5
#: 遗忘帧数：连续 10 帧没再出现才丢弃跟踪。
_PEAK_MAX_MISS_FRAMES = 10

#: 标签数量上限：按「标签矩形互交」实测选（1360px 面板、8pt、带 SNR）。
#: 12 个丢弃 50.0%、8 个 37.5%，都超过 30% 可读性判据；6 个 16.7% 达标。
#: 证据与回归门见 tests/test_spectrum_scale_peak_range.py::test_peak_label_cap_*。
#: C++ 侧 cap=12 不冲突：它只画三角形不画文字（spectrum_display.cpp:1527-1557）。
_PEAK_MAX_LABELS = 6


# ============================================================================
# 频谱数据生成器（真 IQ FFT，无任何合成数据）
# ============================================================================

class SpectrumDataGenerator:
    """对真实复 IQ 做窗函数 + numpy.fft.fft → fftshift → dBFS → 多帧平均。

    不生成任何模拟峰；无数据时 spectrum 为 NaN（UI 层据此不画谱线）。
    """

    # SDR++ iq_frontend.h:17-21 FFTWindow 枚举仅 RECTANGULAR/BLACKMAN/NUTTALL；
    # main_window.cpp:91 默认 NUTTALL。这里额外补 Hann/Hamming/Flattop/None 便于对比。
    WINDOWS = ("Nuttall", "Hann", "Hamming", "Blackman", "Flattop", "None")
    # 唯一事实源 = desktop/config_schema.py 的 fft_size 三档（C++ settings_dialog
    # 与 AI 工具同源：1024/2048/4096）。8192/16384 内核不支持，不再暴露——
    # 旧配置/直接调用给这两个值时由 set_fft_size 回落最近档并记录原因。
    FFT_SIZES = (1024, 2048, 4096)
    AVG_FRAMES = (1, 4, 8, 16)

    def __init__(self, num_bins: int = 512):
        self.num_bins = num_bins
        # 单位：Hz（SDR++ main_window.cpp:84-85 默认带宽 8MHz，这里给个常见 FM 广播中心）
        self.center_freq_hz = 98.5e6
        self.sample_rate_hz = DEFAULT_SAMPLE_RATE_HZ
        # 显示跨度（SDR++「缩放」滑杆同语义）：None = 全带宽（= 采样率）。
        # 与 sample_rate_hz 分开：采样率是硬件事实（每帧被 push_iq 覆写），
        # 缩放大小时用户意图，**不能被下一帧数据冲掉**。
        self._zoom_span_hz: Optional[float] = None
        # FFT 参数；默认窗 Nuttall（main_window.cpp:91 IQFrontEnd::FFTWindow::NUTTALL）
        self.window_name = "Nuttall"
        self.fft_size = 2048
        self.avg_frames = 1
        # 当前帧频谱（长度 num_bins）；NaN 表示无数据 → 不画谱线
        self.spectrum: np.ndarray = np.full(num_bins, np.nan, dtype=np.float32)

        # ---- 瀑布图环形行缓冲（对标 SDR++ waterfall.h:290-293 rawFFTs 环形缓冲）----
        # SDR++ 用固定 waterfallHeight 行 × rawFFTSize 列的 float 环形数组，
        # getFFTBuffer() 里 currentFFTLine-- 后回绕写入新行；pushFFT() 里 memmove
        # 把整帧缓冲上移一行再把新行写到底部。这里用 numpy 2D 环形缓冲复刻：
        # 新行从底部写入画布，旧行向上滚动，O(1) 写入、无 list.pop(0) 开销。
        self.max_waterfall_lines = WF_DEPTH_DEFAULT_ROWS
        self._wf_buf = np.full((self.max_waterfall_lines, num_bins),
                               np.nan, dtype=np.float32)
        self._wf_head = 0          # 下一个写入槽位（写后回绕）
        self._wf_count = 0         # 已写入的有效行数（<= max_waterfall_lines）
        self._wf_base = 0          # 槽位 0 对应的绝对行号（改变深度后重算）
        self.wf_rows_written = 0   # 单调递增计数，供瀑布画布做增量同步
        # 瀑布**实测**行率（行/秒）：读数与"可回看时长"必须用它算。
        # 行是每个 IQ 帧写一行（2.4 MS/s ÷ 16384 ≈ 146 行/s），与 FFT 重绘
        # 定时器（50 ms）无关——按定时器算会把 1.75 s 说成 12.8 s（差 7 倍）。
        self._wf_rate_t0: Optional[float] = None
        self._wf_rate_n0: int = 0
        self._wf_row_rate_hz: float = 0.0
        self._has_real_data = False
        # ---- 跨帧峰跟踪（C++ rescanPeaks 同款语义）----
        # 每数据帧推进一次（push_iq 内），不随瀑布行号节流。
        self.peak_rel_db = _PEAK_REL_DB_DEFAULT   # 跟踪用相对门（面板随滑杆同步）
        self._peak_tracks: List[dict] = []
        # 多帧幅度平均累加器（复数 FFT 幅度域平均，降低噪声）
        self._mag_accum: Optional[np.ndarray] = None
        self._avg_count = 0
        # ---- 瀑布时间平滑（IIR：新行 = 0.7*new + 0.3*prev）----
        # 只影响瀑布显示，不影响频谱曲线；prev 存最近一次写入的瀑布行。
        self.wf_smooth = False
        self._prev_wf_row: Optional[np.ndarray] = None
        # ---- Max/Min hold（SDR++ maxHold/minHold 复选框签名）----
        # 开启后逐帧对 spectrum 做逐 bin 运行 max/min 包络；关闭即丢包络，
        # 再开从头攒。None = 当前无包络（未开启或刚清空）。
        self.show_max_hold = False
        self.show_min_hold = False
        self.max_hold: Optional[np.ndarray] = None
        self.min_hold: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ hold
    def set_max_hold(self, on: bool):
        """开关 Max hold；关闭即丢包络，重开从头攒。"""
        self.show_max_hold = bool(on)
        if not self.show_max_hold:
            self.max_hold = None

    def set_min_hold(self, on: bool):
        """开关 Min hold；关闭即丢包络，重开从头攒。"""
        self.show_min_hold = bool(on)
        if not self.show_min_hold:
            self.min_hold = None

    def _update_hold(self):
        """把当前 spectrum 并入运行 max/min 包络（尺寸不符则重建）。"""
        spec = self.spectrum
        if self.show_max_hold:
            if self.max_hold is None or len(self.max_hold) != self.num_bins:
                self.max_hold = spec.copy()
            else:
                np.maximum(self.max_hold, spec, out=self.max_hold)
        if self.show_min_hold:
            if self.min_hold is None or len(self.min_hold) != self.num_bins:
                self.min_hold = spec.copy()
            else:
                np.minimum(self.min_hold, spec, out=self.min_hold)

    # ------------------------------------------------------------------ 瀑布行缓冲
    def wf_row_count(self) -> int:
        return self._wf_count

    def wf_latest_rows(self, k: int) -> np.ndarray:
        """返回最新 k 行瀑布数据，按 旧→新 顺序排列（shape (m, num_bins)）。

        画布增量绘制时按此顺序逐行 scroll + 写底，最新一行落在画布底部。
        无数据返回 shape (0, num_bins)。
        """
        k = max(0, min(int(k), self._wf_count))
        if k == 0:
            return np.empty((0, self.num_bins), dtype=np.float32)
        # 最新一行是 (head - 1) mod N；往回数 k 行，旧→新顺序
        N = self.max_waterfall_lines
        start = (self._wf_head - k) % N
        if start + k <= N:
            return self._wf_buf[start:start + k]
        # 跨环边界：拼两段
        part1 = self._wf_buf[start:]
        part2 = self._wf_buf[:(start + k - N)]
        return np.concatenate([part1, part2], axis=0)

    def set_waterfall_depth(self, rows: int) -> int:
        """改变环形缓冲保留行数，返回实际生效值。

        合法值只有六档 ``WF_DEPTH_CHOICES``（与 C++/AI 同一枚举）。档位外的
        值（如 384、99999）回落到 ``WF_DEPTH_DEFAULT_ROWS`` 并写一条 warning，
        不静默夹取——夹到 384 会让 Python 与 C++ 的回看时长差 1.5 倍。

        只搬最近 ``min(已有行数, 新深度)`` 行，旧→新顺序不变；更老的历史被丢弃。
        内存 = 深度 × bin 数 × 4 字节，改变深度不影响每帧增量绘制开销。
        """
        try:
            n = int(rows)
        except (TypeError, ValueError):
            _log.warning("瀑布深度 %r 不是整数，已回落到 %d 行",
                         rows, WF_DEPTH_DEFAULT_ROWS)
            n = WF_DEPTH_DEFAULT_ROWS
        if n not in WF_DEPTH_CHOICES:
            _log.warning("瀑布深度 %r 不在六档 %s 内，已回落到 %d 行（不夹取，"
                         "以免与 C++ 回看时长不一致）",
                         rows, WF_DEPTH_CHOICES, WF_DEPTH_DEFAULT_ROWS)
            n = WF_DEPTH_DEFAULT_ROWS
        if n == self.max_waterfall_lines:
            return n
        keep = self.wf_latest_rows(min(self._wf_count, n))
        buf = np.full((n, self.num_bins), np.nan, dtype=np.float32)
        if len(keep):
            buf[:len(keep)] = keep
        self._wf_buf = buf
        self.max_waterfall_lines = n
        self._wf_head = len(keep) % n
        self._wf_count = len(keep)
        self._wf_base = self.wf_rows_written - len(keep)
        return n

    def wf_available_rows(self) -> int:
        """当前环形缓冲里可回看的有效行数（<= max_waterfall_lines）。"""
        return int(self._wf_count)

    def wf_rows_at(self, end_index: int, k: int) -> np.ndarray:
        """返回绝对行号 ``[end-k, end)`` 的瀑布数据（旧→新），用于历史回看。

        绝对行号以 ``wf_rows_written`` 计；超出当前保留范围的请求会被夹取。
        """
        k = max(0, int(k))
        end = min(int(end_index), self.wf_rows_written)
        oldest = self.wf_rows_written - self._wf_count
        start = max(end - k, self._wf_base, oldest, 0)
        if end <= start:
            return np.empty((0, self.num_bins), dtype=np.float32)
        N = self.max_waterfall_lines
        idx = (np.arange(start, end) - self._wf_base) % N
        return self._wf_buf[idx]

    # ------------------------------------------------------------------ 配置
    def set_window(self, name: str):
        if name in self.WINDOWS and name != self.window_name:
            self.window_name = name
            self._reset_average()

    def set_fft_size(self, n: int) -> int:
        """设置 FFT 点数；非法档位回落到最近合法档并记录原因（不静默）。

        返回实际生效的档位——调用方（设置面板）据此同步下拉/提示。
        旧配置里的 8192/16384（内核不支持）会落到 4096，而不是假装成功；
        回落只发生在越界值上，合法档位行为不变。
        """
        n = int(n)
        if n not in self.FFT_SIZES:
            nearest = min(self.FFT_SIZES, key=lambda s: abs(s - n))
            _log.warning(
                "fft_size=%d 不在合法档 %s（C++/AI/schema 只支持三档），"
                "回落到最近档 %d", n, self.FFT_SIZES, nearest)
            n = nearest
        if n != self.fft_size:
            self.fft_size = n
            self._reset_average()
        return self.fft_size

    def set_avg_frames(self, n: int):
        n = int(n)
        if n in self.AVG_FRAMES and n != self.avg_frames:
            self.avg_frames = n
            self._reset_average()

    def _reset_average(self):
        self._mag_accum = None
        self._avg_count = 0

    def reconfigure(self, fft_size: int, num_bins: int):
        """性能模式切换：改 FFT 点数 / 谱线数，重建 spectrum 与瀑布环形缓冲。

        保持 center_freq_hz / sample_rate_hz / 窗函数 / 平均帧数不变；
        重建后旧数据作废（spectrum 与 _wf_buf 全部填 NaN）。
        """
        self.fft_size = int(fft_size)
        self.num_bins = int(num_bins)
        self.spectrum = np.full(self.num_bins, np.nan, dtype=np.float32)
        self._wf_buf = np.full((self.max_waterfall_lines, self.num_bins),
                               np.nan, dtype=np.float32)
        self._wf_head = 0
        self._wf_count = 0
        self._wf_base = 0
        self.wf_rows_written = 0
        self._wf_rate_t0 = None
        self._wf_rate_n0 = 0
        self._wf_row_rate_hz = 0.0
        self._prev_wf_row = None
        self.max_hold = None
        self.min_hold = None
        self._peak_tracks = []
        self._reset_average()

    # ------------------------------------------------------------------ 状态
    def has_data(self) -> bool:
        return self._has_real_data

    def clear_data(self):
        """断开/清空：回到"未连接"，不保留旧谱线。"""
        self._has_real_data = False
        self.spectrum = np.full(self.num_bins, np.nan, dtype=np.float32)
        self._wf_buf.fill(np.nan)
        self._wf_head = 0
        self._wf_count = 0
        self._wf_base = 0
        self.wf_rows_written = 0
        self._wf_rate_t0 = None
        self._wf_rate_n0 = 0
        self._wf_row_rate_hz = 0.0
        self._prev_wf_row = None
        self.max_hold = None
        self.min_hold = None
        self._peak_tracks = []
        self._reset_average()

    # ------------------------------------------------------------------ 窗函数
    def _window(self, n: int) -> np.ndarray:
        # P6 热点修复：窗函数每帧重算（Nuttall 要算 6 次 cos，~0.25ms/帧，
        # 2.4MS/s 下约 36ms/s CPU）。按 (窗名, 点数) 缓存，只读复用同一数组
        # （push_iq 只做 x*win，不修改 win）。
        key = (self.window_name, int(n))
        if getattr(self, "_win_cache_key", None) == key:
            return self._win_cache
        win = self._compute_window(n)
        self._win_cache_key = key
        self._win_cache = win
        return win

    def _compute_window(self, n: int) -> np.ndarray:
        if self.window_name == "Hamming":
            return np.hamming(n).astype(np.float64)
        if self.window_name == "Blackman":
            return np.blackman(n).astype(np.float64)
        if self.window_name == "Nuttall":
            # 3-term Blackman-Nuttall（SDR++ iq_frontend.cpp 用的同款系数）：
            # a0=0.355768, a1=0.487396, a2=0.144232, a3=0.012604
            k = np.arange(n, dtype=np.float64)
            if n == 1:
                return np.ones(1, dtype=np.float64)
            x = k / (n - 1)
            return (0.355768
                    - 0.487396 * np.cos(2.0 * np.pi * x)
                    + 0.144232 * np.cos(4.0 * np.pi * x)
                    - 0.012604 * np.cos(6.0 * np.pi * x))
        if self.window_name == "Flattop":
            # 4-term 平底窗（准高斯；窄带幅度精度高）：
            # a0=0.21557895, a1=0.41663158, a2=0.277263158,
            # a3=0.083578947, a4=0.006947428
            k = np.arange(n, dtype=np.float64)
            if n == 1:
                return np.ones(1, dtype=np.float64)
            x = k / (n - 1)
            return (0.21557895
                    - 0.41663158 * np.cos(2.0 * np.pi * x)
                    + 0.277263158 * np.cos(4.0 * np.pi * x)
                    - 0.083578947 * np.cos(6.0 * np.pi * x)
                    + 0.006947428 * np.cos(8.0 * np.pi * x))
        if self.window_name == "None":
            return np.ones(n, dtype=np.float64)
        return np.hanning(n).astype(np.float64)   # Hann

    # ------------------------------------------------------------------ FFT
    def push_iq(self, iq: np.ndarray, center_freq_hz: float,
                sample_rate_hz: float):
        """喂入真实复 IQ，做 窗×fft → fftshift → dBFS → 多帧平均。"""
        x = np.asarray(iq, dtype=np.complex64)
        n = len(x)
        if n < 64:
            return

        self.center_freq_hz = float(center_freq_hz)
        self.sample_rate_hz = float(sample_rate_hz)

        N = self.fft_size
        if n >= N:
            x = x[:N]
        else:
            x = np.concatenate([x, np.zeros(N - n, dtype=np.complex64)])

        # 真实窗函数
        win = self._window(N)

        # 复 IQ 必须用 np.fft.fft（不是 rfft）
        spec = np.fft.fft(x * win)
        # DC 搬到中心
        spec = np.fft.fftshift(spec)

        # dBFS 归一化：满幅正弦波峰 = N * 窗相干增益 / 2
        win_cg = float(np.mean(win))
        mag = np.abs(spec) / (N * win_cg / 2.0)

        # 多帧幅度平均
        if self._mag_accum is None or len(self._mag_accum) != N:
            self._mag_accum = mag.copy()
            self._avg_count = 1
        else:
            self._mag_accum += mag
            self._avg_count += 1

        if self._avg_count < self.avg_frames:
            return  # 帧数不足，等下一帧

        avg_mag = self._mag_accum / self._avg_count
        self._reset_average()

        power_db = (20.0 * np.log10(avg_mag + 1e-12)).astype(np.float32)

        # 重采样到 num_bins（max 抽取，窄脉冲不被平均抹平）
        self.spectrum = self._resample_max(power_db)
        self._has_real_data = True
        self._update_hold()
        # 峰跟踪随每个数据帧推进（不挂在瀑布行号上，避免 10 帧绘制缓存）。
        self.advance_peak_tracks()

        # 瀑布行：可选 IIR 时间平滑（只影响瀑布，不影响上面的频谱曲线）
        if self.wf_smooth and self._prev_wf_row is not None \
                and len(self._prev_wf_row) == self.num_bins:
            wf_row = 0.7 * self.spectrum + 0.3 * self._prev_wf_row
        else:
            wf_row = self.spectrum
        self._prev_wf_row = wf_row.copy()

        # 写入环形行缓冲：新行落在 head 槽位，head 回绕（SDR++ currentFFTLine 语义）
        self._wf_buf[self._wf_head] = wf_row
        self._wf_head = (self._wf_head + 1) % self.max_waterfall_lines
        self._wf_count = min(self._wf_count + 1, self.max_waterfall_lines)
        self.wf_rows_written += 1
        self._wf_tick_rate()

    def _wf_tick_rate(self) -> None:
        """按实测行数/墙钟估瀑布行率（每 0.25 s 更新一次，EMA 平滑）。

        读数方（``wf_row_rate_hz()``）在拿到第一个样本前返回 0.0，调用方
        必须显示「--」而不是编一个秒数。
        """
        now = time.monotonic()
        if self._wf_rate_t0 is None:
            self._wf_rate_t0 = now
            self._wf_rate_n0 = self.wf_rows_written
            return
        dt = now - self._wf_rate_t0
        n = self.wf_rows_written - self._wf_rate_n0
        if dt < 0.25 or n <= 0:
            return
        inst = n / dt
        self._wf_row_rate_hz = (inst if self._wf_row_rate_hz <= 0.0
                                else 0.8 * self._wf_row_rate_hz + 0.2 * inst)
        self._wf_rate_t0 = now
        self._wf_rate_n0 = self.wf_rows_written

    def wf_row_rate_hz(self) -> float:
        """瀑布实测行率（行/秒）；未测到返回 0.0。"""
        return float(self._wf_row_rate_hz)

    def wf_span_seconds(self) -> Optional[float]:
        """当前深度（max_waterfall_lines 行）对应的**真实**可回看时长（秒）。

        行率未测到（刚启动/无数据）时返回 None —— 不拿定时器间隔冒充。
        """
        rate = self._wf_row_rate_hz
        if rate <= 0.0:
            return None
        return self.max_waterfall_lines / rate

    def _resample_max(self, spec: np.ndarray) -> np.ndarray:
        """谱线抽取：每 bin 取区间最大值（窄脉冲不被平均抹平）。

        P6 热点修复：原实现逐 bin Python 循环（512 次 seg.max()，约
        1.1ms/帧，占 push_iq 八成）。点数可整除时改用 reshape+max 一次
        向量化（同一 max 语义，输出逐元素相同）；不可整除才退回循环。
        """
        if len(spec) == self.num_bins:
            return spec.astype(np.float32)
        nb = self.num_bins
        if len(spec) >= nb and len(spec) % nb == 0:
            return spec.reshape(nb, -1).max(axis=1).astype(np.float32)
        edges = np.linspace(0, len(spec), nb + 1).astype(int)
        out = np.empty(nb, dtype=np.float32)
        for i in range(nb):
            lo, hi = edges[i], edges[i + 1]
            seg = spec[lo:hi]
            # 哨兵必须与可测范围不相交：空段填 NaN（未采到），不是 -120
            # dBFS（真实弱信号）。渲染层遇 NaN 不画、峰检测也跳过非有限值。
            out[i] = float(seg.max()) if seg.size else np.nan
        return out

    # ------------------------------------------------------------------ 频率轴
    @property
    def span_hz(self) -> float:
        """当前显示跨度（Hz）。

        未缩放 = 采样率；缩放后 = 用户选定值（夹在 [48 kHz, 采样率] 内）。
        所有「画在哪一列」的几何必须用它；所有「第几号 bin」的换算必须用
        sample_rate_hz——两者混用会把缩放后的谱线画到错误频率上。
        """
        sr = float(self.sample_rate_hz)
        z = self._zoom_span_hz
        if z is not None and 0.0 < float(z) < sr:
            return float(z)
        return sr

    def set_span(self, span_hz: Optional[float]) -> None:
        """设置显示跨度；None 或 >= 采样率 = 取消缩放（全带宽）。"""
        sr = float(self.sample_rate_hz)
        try:
            hz = float(span_hz) if span_hz is not None else sr
        except (TypeError, ValueError):
            hz = sr
        hz = max(48_000.0, min(sr, hz))
        self._zoom_span_hz = None if hz >= sr * 0.999 else hz

    def bin_to_freq(self, idx: int) -> float:
        """第 idx 个 bin 中心频率（Hz）。center ± sample_rate/2。"""
        idx = max(0, min(self.num_bins - 1, idx))
        return (self.center_freq_hz - self.sample_rate_hz / 2.0
                + self.sample_rate_hz * (idx + 0.5) / self.num_bins)

    def freq_to_bin(self, freq_hz: float) -> int:
        f0 = self.center_freq_hz - self.sample_rate_hz / 2.0
        idx = int(round((freq_hz - f0) / self.sample_rate_hz * self.num_bins))
        return max(0, min(self.num_bins - 1, idx))

    def value_at_freq(self, freq_hz: float) -> Optional[float]:
        """返回该频率的功率(dBFS)；无数据返回 None。读取真实频谱数组。

        2026-11-06：越出采集带宽一律返回 None。此前 `freq_to_bin` 会把越界
        频率**夹到边缘 bin**，Ctrl 平移让半屏落在带外后，悬停仍显示一个
        数字——而且它是"另一个频率的功率"，比显示「--」更具欺骗性。
        """
        if not self._has_real_data:
            return None
        half = self.sample_rate_hz / 2.0
        if not (self.center_freq_hz - half <= float(freq_hz)
                <= self.center_freq_hz + half):
            return None
        v = self.spectrum[self.freq_to_bin(freq_hz)]
        return float(v) if np.isfinite(v) else None

    def noise_floor_db(self) -> Optional[float]:
        """本帧噪声底（dBFS）= 全部有限 bin 的中位数（median）。

        口径写死在这里，供峰检测门限与峰值 SNR 标注共用：
          - 不是 min / mean / 绝对 floor，也不是只取带外样本；
          - 峰值 SNR = 峰功率 − 本帧中位数，与 _detect_peak_bins 的
            `median + rel_threshold_db` 是同一把尺子。
        无真实数据或有效 bin 不足 3 个 → None（不编造数值）。
        """
        if not self._has_real_data:
            return None
        finite = self.spectrum[np.isfinite(self.spectrum)]
        if finite.size < 3:
            return None
        return float(np.median(finite))

    def advance_peak_tracks(self, rel_threshold_db: Optional[float] = None):
        """用当前 spectrum 推进**一帧**峰跟踪（每个数据帧调用一次）。

        与 C++ rescanPeaks 同一套定义：±``_PEAK_MATCH_BINS`` 内匹配、
        命中 seen++、未命中 missed++、seen ≥ ``_PEAK_MIN_SEEN_FRAMES``
        才成熟、missed ≥ ``_PEAK_MAX_MISS_FRAMES`` 才遗忘。
        单帧尖峰永远到不了 3，因此不会变成常驻标签。
        """
        if not self._has_real_data:
            return
        s = self.spectrum
        if len(s) < 3:
            return
        thr = float(self.peak_rel_db if rel_threshold_db is None
                    else rel_threshold_db)
        fresh = [(int(i), float(s[i])) for i in _detect_peak_bins(s, thr)
                 if np.isfinite(s[i])]
        matched = [False] * len(self._peak_tracks)
        for idx, db in fresh:
            best, best_d = -1, _PEAK_MATCH_BINS
            for k, t in enumerate(self._peak_tracks):
                if matched[k]:
                    continue
                d = abs(int(t["bin"]) - idx)
                if d < best_d:
                    best_d, best = d, k
            if best >= 0:
                t = self._peak_tracks[best]
                t["bin"], t["db"] = idx, db
                t["seen"] += 1
                t["missed"] = 0
                matched[best] = True
            else:
                self._peak_tracks.append(
                    {"bin": idx, "db": db, "seen": 1, "missed": 0})
                matched.append(True)
        for k, t in enumerate(self._peak_tracks):
            if not matched[k]:
                t["missed"] += 1
        self._peak_tracks = [t for t in self._peak_tracks
                             if int(t["missed"]) < _PEAK_MAX_MISS_FRAMES]

    def find_peaks_with_snr(self, rel_threshold_db: float,
                            max_peaks: int = _PEAK_MAX_LABELS
                            ) -> List[Tuple[float, float, float]]:
        """已成熟的峰 + SNR（峰功率 − 本帧中位数）。

        只返回跨帧确认过 ≥``_PEAK_MIN_SEEN_FRAMES`` 帧的峰；每帧实时读跟踪
        状态，**没有** ``wf_rows_written // 10`` 节流缓存。
        返回 [(freq_hz, power_db, snr_db), ...]，按功率降序。
        图上的峰值标签与命中测试都读这一份，保证两处数值同源。
        """
        if not self._has_real_data:
            return []
        if len(self.spectrum) < 3:
            return []
        floor = self.noise_floor_db()
        if floor is None:
            return []
        gate = floor + float(rel_threshold_db)
        peaks = [(self.bin_to_freq(int(t["bin"])),
                  float(t["db"]),
                  float(t["db"]) - floor)
                 for t in self._peak_tracks
                 if int(t["seen"]) >= _PEAK_MIN_SEEN_FRAMES
                 and float(t["db"]) >= gate]
        peaks.sort(key=lambda q: q[1], reverse=True)
        return peaks[:max_peaks]

    def find_peaks(self, rel_threshold_db: float,
                   max_peaks: int = _PEAK_MAX_LABELS) -> List[Tuple[float, float]]:
        """双通道峰检测：窄峰（原始谱局部极大）+ 平滑后宽峰（gqrx 机制）。

        参考 gqrx plotter.cpp:1699-1757：
          - 窄峰通道用原始谱的严格局部极大；
          - 宽峰通道先做窗口平滑，再在平滑曲线上找局部极大；
          - 两路结果按窗口距离合并去重：宽峰吸收邻近窄峰后只留一个代表，
            避免宽带信号被拆成一串小峰，也避免宽峰被窄峰淹没。
        返回 [(freq_hz, power_db), ...]，按功率降序；SNR 见 find_peaks_with_snr。
        """
        return [(f, db) for f, db, _snr in
                self.find_peaks_with_snr(rel_threshold_db, max_peaks)]


def _detect_peak_bins(spectrum: np.ndarray,
                      rel_threshold_db: float,
                      abs_floor_dbfs: float = _PEAK_ABS_FLOOR_DBFS) -> List[int]:
    """双通道峰检测核心（纯函数，无 Qt/状态依赖）。

    通道 1：原始谱严格局部极大（窄峰）。
    通道 2：窗口平滑后局部极大（宽峰，gqrx plotter.cpp:1723-1757）。
    合并：宽峰窗口内的窄峰归入该宽峰，只保留最强的一个代表；落在宽峰
    之外的窄峰原样保留。这样宽带信号不会被拆成多个点，窄单音也不会被
    平滑吞掉。
    """
    n = len(spectrum)
    if n < 3:
        return []
    finite = np.isfinite(spectrum)
    ss = spectrum[finite]
    if ss.size < 3:
        return []
    # 门限基准 = 全帧中位数（median），与 SpectrumDataGenerator.noise_floor_db
    # 的 SNR 基准同源；`floor` 只是变量名，不要按 min/mean 理解。
    median_db = float(np.median(ss))
    thresh = median_db + float(rel_threshold_db)

    left = np.empty_like(spectrum)
    right = np.empty_like(spectrum)
    left[0] = spectrum[0]
    left[1:] = spectrum[:-1]
    right[-1] = spectrum[-1]
    right[:-1] = spectrum[1:]
    # 绝对电平门：低于 abs_floor_dbfs 的 bin 连候选都不算。它是绝对行为，
    # 与相对门叠加——底噪很低时（如 -110）弱载波会被它挡掉（已知副作用）。
    above_floor = finite & (spectrum > abs_floor_dbfs)
    narrow = np.nonzero(above_floor & (spectrum >= left)
                        & (spectrum > right)
                        & (spectrum >= thresh))[0]

    # 奇数平滑窗：约 3% 谱宽；宽带的细微起伏被抹平，窄单音仍保留在通道 1。
    win = max(3, (n // 64) * 2 + 1)
    pad = win // 2
    fill = np.where(finite, spectrum, median_db)
    smooth = np.convolve(np.pad(fill, pad, mode="edge"),
                         np.ones(win, dtype=np.float64) / win, mode="valid")
    sm_left = np.empty_like(smooth)
    sm_right = np.empty_like(smooth)
    sm_left[0] = smooth[0]
    sm_left[1:] = smooth[:-1]
    sm_right[-1] = smooth[-1]
    sm_right[:-1] = smooth[1:]
    wide = np.nonzero((smooth >= sm_left) & (smooth > sm_right)
                      & (smooth >= thresh)
                      & (smooth > abs_floor_dbfs)
                      & finite)[0]
    # 平滑曲线上的相邻宽峰合并为一个（同窗内的起伏仍属同一宽带信号）。
    wide_sel: List[int] = []
    for w in wide:
        if wide_sel and abs(int(w) - wide_sel[-1]) < pad:
            if smooth[w] > smooth[wide_sel[-1]]:
                wide_sel[-1] = int(w)
            continue
        wide_sel.append(int(w))

    out: List[int] = []
    used = set()
    for w in wide_sel:
        # 宽带信号的“支撑区”：平滑曲线上连续高于阈值的整段。
        # 该段内的所有窄峰起伏都归为同一宽带信号，只留最强的一个代表；
        # 只按一个平滑窗吸收会让边缘的起伏漏成多个点。
        l = int(w)
        r = int(w)
        while l > 0 and smooth[l - 1] >= thresh:
            l -= 1
        while r < n - 1 and smooth[r + 1] >= thresh:
            r += 1
        # 边界外再放宽一个平滑窗：宽带边缘的起伏略低于阈值也算同一条信号。
        near = [int(i) for i in narrow if l - pad <= int(i) <= r + pad]
        if near:
            best = max(near, key=lambda i: float(spectrum[i]))
            out.append(best)
            used.update(near)
        else:
            out.append(int(w))
    for i in narrow:
        if int(i) not in used:
            out.append(int(i))
    # 去重：相邻 1 bin 内只留一个（保留先加入者 = 宽峰代表优先）。
    out.sort()
    dedup: List[int] = []
    for i in out:
        if dedup and i - dedup[-1] < 2:
            continue
        dedup.append(i)
    return dedup


# ============================================================================
# 颜色工具
# ============================================================================

def value_to_color(value: float, min_val: float, max_val: float,
                   colors: Optional[List[str]] = None) -> QColor:
    if colors is None:
        colors = WATERFALL_COLORS
    if max_val <= min_val:
        return QColor(colors[0])
    ratio = max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))
    n = len(colors) - 1
    idx = ratio * n
    i = int(idx)
    f = idx - i
    if i >= n:
        return QColor(colors[n])
    c1, c2 = QColor(colors[i]), QColor(colors[min(i + 1, n)])
    r = int(c1.red() + (c2.red() - c1.red()) * f)
    g = int(c1.green() + (c2.green() - c1.green()) * f)
    b = int(c1.blue() + (c2.blue() - c1.blue()) * f)
    return QColor(r, g, b)


# ============================================================================
# 绘图共享逻辑（QPainter 软件渲染 / QPainter-on-OpenGL 共用）
# ============================================================================

def _view_center_hz(state: "_PlotState") -> float:
    """谱面当前显示窗口中心频率（Hz）= 实际调谐中心 + 视图平移偏移。

    普通拖动/滚轮/箭头会改 generator.center_freq_hz（真调谐）；
    Ctrl 拖动只改 state.view_offset_hz（视图平移，不调谐，对标 SDR++ center 模式）。
    """
    return state.panel.generator.center_freq_hz + state.view_offset_hz


def _px_to_freq(x_px: float, width: float, view_center_hz: float,
                span_hz: float) -> float:
    """像素 → 绝对频率的唯一定义（频谱与瀑布共用）。

    与 _freq_to_x 互逆；`view_center_hz` 已含 state.view_offset_hz，
    所以 Ctrl 平移后两区仍然落在同一频率上，不会各画各的。
    """
    return (view_center_hz - span_hz / 2.0
            + x_px / max(1.0, float(width)) * span_hz)


def _freq_to_x(rect: QRectF, view_center_hz: float, span_hz: float,
               freq_hz: float) -> float:
    """绝对频率 → 谱面 x 像素（含视图平移偏移）。"""
    return (rect.x()
            + (freq_hz - (view_center_hz - span_hz / 2.0)) / span_hz
            * rect.width())


def _effective_vfo(state: "_PlotState") -> Optional[Tuple[float, float]]:
    """返回当前应绘制/命中的 VFO 绝对中心频率与带宽 (center_hz, bw_hz)。

    优先级（对标任务要求）：
      1. VfoManager.current 存在 → 用其 center_hz / bw_hz（拖拽中移动的就是它）。
      2. 否则 _PlotState.vfo_bandwidth_hz 已由主窗口设置 → 用 view_center + offset。
      3. 都没有 → None（不画 VFO 矩形）。
    """
    mgr = getattr(state.panel, "vfo_manager", None)
    if mgr is not None and getattr(mgr, "current", None) is not None:
        cur = mgr.current
        if cur.active and cur.bw_hz > 0:
            return (float(cur.center_hz), float(cur.bw_hz))
    if state.vfo_bandwidth_hz is not None and state.vfo_bandwidth_hz > 0:
        view_center = _view_center_hz(state)
        return (view_center + state.vfo_offset_hz, float(state.vfo_bandwidth_hz))
    return None


def _vfo_px_range(state: "_PlotState", rect: QRectF,
                  center_hz: float, bw_hz: float) -> Optional[Tuple[float, float]]:
    """返回某个 VFO 矩形在谱面的 (x0, x1) 像素坐标；完全在视口外返回 None。"""
    gen = state.panel.generator
    span = gen.span_hz
    view_center = _view_center_hz(state)
    x_center = _freq_to_x(rect, view_center, span, center_hz)
    x_half = (bw_hz / 2.0) / span * rect.width()
    x0, x1 = x_center - x_half, x_center + x_half
    if x1 <= rect.x() or x0 >= rect.x() + rect.width():
        return None
    return (x0, x1)


def _draw_vfo_band(painter: QPainter, state: "_PlotState", rect: QRectF):
    """在谱面绘制全部 VFO 矩形（对标 SDR++ waterfall.cpp:221-231）。

    - 主听 VFO（= vfo_manager.current）：橙色 #C4845C，实线边框，实心标签。
    - 次听 VFO：蓝灰 #5B7B8C，虚线细边框，半透明填充。
    - 每个矩形上方标注 "VFO X  145.800 MHz"。
    - 若无 VfoManager 或其列表为空，回退到旧版单 VFO 高亮（_effective_vfo）。
    """
    mgr = state.panel.vfo_manager
    gen = state.panel.generator

    # 收集要画的 VFO：优先用 mgr.list_all()；否则回退旧版单带高亮
    vfos_to_draw = []  # List[(vfo_state_or_None, center_hz, bw_hz, is_primary, letter)]
    if mgr is not None:
        all_vfos = mgr.list_all()
        for idx, v in enumerate(all_vfos):
            if not v.active or v.bw_hz <= 0:
                continue
            letter = chr(ord('A') + (idx % 26))
            vfos_to_draw.append((v, float(v.center_hz), float(v.bw_hz),
                                 v is mgr.current, letter))

    if not vfos_to_draw:
        # 旧版回退：无多 VFO 时仍画单带宽高亮
        eff = _effective_vfo(state)
        if eff is None:
            return
        vfo_center, bw = eff
        rng = _vfo_px_range(state, rect, vfo_center, bw)
        if rng is None:
            return
        x0, x1 = rng
        fill = QColor(PAL_LINE); fill.setAlpha(30)
        painter.fillRect(QRectF(x0, rect.y(), x1 - x0, rect.height()), QBrush(fill))
        edge = QColor(PAL_LINE); edge.setAlpha(120)
        painter.setPen(QPen(edge, 1, Qt.SolidLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(x0, rect.y(), x1 - x0, rect.height()))
        return

    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)

    for v, center_hz, bw_hz, is_primary, letter in vfos_to_draw:
        rng = _vfo_px_range(state, rect, center_hz, bw_hz)
        if rng is None:
            continue
        x0, x1 = rng
        if is_primary:
            fill = QColor(PAL_LINE); fill.setAlpha(45)
            edge = QColor(PAL_LINE); edge.setAlpha(200)
            pen = QPen(edge, 2, Qt.SolidLine)
        else:
            # 次听 VFO：蓝灰 #5B7B8C，虚线细框
            fill = QColor(PAL_GRID); fill.setAlpha(25)
            edge = QColor(PAL_GRID); edge.setAlpha(160)
            pen = QPen(edge, 1, Qt.DashLine)
        painter.fillRect(QRectF(x0, rect.y(), x1 - x0, rect.height()), QBrush(fill))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(x0, rect.y(), x1 - x0, rect.height()))
        # 标签：矩形顶部内部
        label = f"VFO {letter}  {center_hz/1e6:.3f} MHz"
        painter.setPen(QPen(edge, 1))
        painter.drawText(QPointF(x0 + 3, rect.y() + 12), label)

    # Shift 拖拽新建 VFO 的橡皮筋预览
    if state.vfo_create_band is not None:
        f_lo, f_hi = state.vfo_create_band
        c0 = min(f_lo, f_hi); c1 = max(f_lo, f_hi)
        view_center = _view_center_hz(state)
        span = gen.span_hz
        xa = _freq_to_x(rect, view_center, span, c0)
        xb = _freq_to_x(rect, view_center, span, c1)
        fill = QColor(PAL_GRID); fill.setAlpha(35)
        painter.fillRect(QRectF(xa, rect.y(), xb - xa, rect.height()), QBrush(fill))
        painter.setPen(QPen(QColor(PAL_GRID), 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(xa, rect.y(), xb - xa, rect.height()))


# ---------------------------------------------------------------------------
# 频段彩带（SDR++ bandplan 仿妆）: 皮进配置 desktop/bandplan.json
# ---------------------------------------------------------------------------
_BANDPLAN_CACHE: Optional[List[Dict[str, object]]] = None


def _parse_band_entry(b: Dict[str, object]) -> Optional[Dict[str, object]]:
    """单条频段条目校验/归一; 坏条目返回 None（跳过，绝不造假）。

    必需: label 非空 / 0<start_hz<stop_hz / category 在分类色表内。
    可选: desc / modulation（悬停提示文案）, priority（high|low, 缺省 low）,
    color（Qt 颜色串, 覆盖 category 色）。
    """
    try:
        label = str(b.get("label", "")).strip()
        f0 = float(b["start_hz"])
        f1 = float(b["stop_hz"])
        cat = str(b.get("category", "")).strip()
    except (KeyError, TypeError, ValueError):
        return None
    if not label or not (0 < f0 < f1) or cat not in BANDPLAN_CATEGORY_COLORS:
        return None
    out: Dict[str, object] = {"label": label, "start_hz": f0, "stop_hz": f1,
                              "category": cat}
    desc = str(b.get("desc", "")).strip()
    if desc:
        out["desc"] = desc
    mod = str(b.get("modulation", "")).strip()
    if mod:
        out["modulation"] = mod
    prio = str(b.get("priority", "")).strip().lower()
    out["priority"] = prio if prio in ("high", "low") else "low"
    color = str(b.get("color", "")).strip()
    if color and QColor(color).isValid():
        out["color"] = color
    return out


def _load_bandplan(path: Optional[str] = None) -> List[Dict[str, object]]:
    """加载频段彩带配置; 缺文件/坏 JSON/坏条目一律跳过该条, 绝不造假。

    path=None 用模块旁 bandplan.json 并走模块级缓存（首次绘制时载入）;
    显式 path 不缓存（测试/外部数据用）。
    """
    global _BANDPLAN_CACHE
    if path is None and _BANDPLAN_CACHE is not None:
        return _BANDPLAN_CACHE
    import json
    import os
    p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "bandplan.json")
    out: List[Dict[str, object]] = []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        bands = raw.get("bands", []) if isinstance(raw, dict) else []
        for b in bands:
            if not isinstance(b, dict):
                continue
            e = _parse_band_entry(b)
            if e is not None:
                out.append(e)
    except (OSError, ValueError):
        out = []
    if path is None:
        _BANDPLAN_CACHE = out
    return out


def _band_color(b: Dict[str, object]) -> QColor:
    """单条 color 覆盖优先, 否则按 category 取色。"""
    c = str(b.get("color", ""))
    return QColor(c) if c else QColor(BANDPLAN_CATEGORY_COLORS[str(b["category"])])


def _bandplan_label_placements(
        items: List[Tuple[float, float, str, str, bool]],
        measure, rect_x: float, rect_w: float) -> Dict[int, float]:
    """频段标签放置（重要性分级 + 碰撞剔除），纯函数便于无头测试。

    items: [(x0, x1, label, priority, forced)]; 返回 {item_index: text_x}。
      1. forced（悬停/选中）最优先，其次 priority=high，再次 low；同级按 x0 升序；
      2. 非 forced 条目带内宽度 < 文本宽 + 8px 不画（带太窄塞不下）；
      3. 标签在带内居中并夹紧在视口内；
      4. 与已放置标签（两侧各留 3px）冲突则跳过——缩放变窄时低优先级先消失，
         标签不糊成一团。
    """
    order = sorted(range(len(items)),
                   key=lambda i: (not items[i][4],
                                  0 if items[i][3] == "high" else 1,
                                  items[i][0]))
    placed: List[Tuple[float, float]] = []
    out: Dict[int, float] = {}
    for i in order:
        x0, x1, label, _prio, forced = items[i]
        tw = float(measure(label))
        if not forced and x1 - x0 < tw + 8.0:
            continue
        tx = x0 + (x1 - x0 - tw) / 2.0
        tx = max(rect_x + 2.0, min(tx, rect_x + rect_w - tw - 2.0))
        lx0, lx1 = tx - 3.0, tx + tw + 3.0
        if any(lx0 < p1 and lx1 > p0 for p0, p1 in placed):
            continue
        placed.append((lx0, lx1))
        out[i] = tx
    return out


def _band_x_range(rect: QRectF, view_center_hz: float, span_hz: float,
                  f0: float, f1: float) -> Optional[Tuple[float, float]]:
    """频段 [f0,f1] 在谱面上的夹紧 x 区间; 完全在视口外返回 None。"""
    f_left = view_center_hz - span_hz / 2.0
    x0 = rect.x() + (f0 - f_left) / span_hz * rect.width()
    x1 = rect.x() + (f1 - f_left) / span_hz * rect.width()
    if x1 < rect.x() or x0 > rect.x() + rect.width():
        return None
    return (max(rect.x(), x0), min(rect.x() + rect.width(), x1))


#: 未连接时谱面第二行提示的默认文案（IDE 形态：工具栏就在上方）。
OFFLINE_HINT_DEFAULT = "点击工具栏「连接」选择设备"


class _PlotState:
    """两个渲染路径共享的交互状态。"""

    def __init__(self, panel: "SpectrumPanel"):
        self.panel = panel
        # dB 显示量程（自动量程接管前的手动初值；SDR++ 同款 -100…-20 起步）
        self.db_min = -100.0
        self.db_max = -20.0
        # dB 轴拖动中：绘制时叠加当前量程读数（跟手反馈）。
        self.db_drag_active = False
        # Ctrl+竖拖 = 锚点缩放：光标旁弹数值气泡（与轴区读数同格式）。
        self.db_drag_zoom = False
        self.db_drag_cursor_y = 0.0
        # 无数据时谱面第二行提示：宿主可替换（星象仪全屏窗没有工具栏，
        # 同一句「点击工具栏」在那里不可执行），见 SpectrumPanel.set_offline_hint。
        self.offline_hint: str = OFFLINE_HINT_DEFAULT
        self.show_waterfall = True
        self.mouse_x_ratio: Optional[float] = None     # 鼠标悬停 x 比例
        self.mouse_in_spectrum = False
        self.markers: List[float] = []                 # 固定 marker 频率 Hz
        # ---- 书签标签（gqrx 书签竖旗；[(freq_hz, name), ...]）----
        # 与固定 marker 是两套系统：marker 是扫频结果的瞬时标注，
        # 书签是用户收藏的持久频点，点击药丸直接调谐。
        self.bookmarks: List[Tuple[float, str]] = []
        self.show_bookmarks = True
        # ---- 频段彩带（SDR++ bandplan 仿妆；皮进 bandplan.json）----
        self.bandplan: List[Dict[str, object]] = _load_bandplan()
        self.show_bandplan = True
        self.bandplan_hover: Optional[int] = None      # 悬停频段索引（提亮+气泡）
        self.bandplan_selected: Optional[int] = None   # 点击选中频段（常驻描边）
        self.show_peaks = True
        # 门限 = 本帧全谱中位数 + 6 dB（本侧默认比 C++ 的 15 dB 敏感）。
        # 不照抄 C++ tokens.h:352 的 15 dB / 12 峰：那边另有 -100 dBFS 绝对
        # 门与 kPeakMinSeenFrames=3 的持续门，宽带噪声不会碎成假峰；本侧
        # 单帧检测没有持续门，抬到 15 dB 会把真实弱载波整条藏掉。
        # 两边基准同源（均为 median），数值不同是机制差异，不是写错。
        self.peak_rel_db = _PEAK_REL_DB_DEFAULT
        # ---- 瀑布调色板 / 时间平滑 ----
        self.wf_colors: List[str] = list(WATERFALL_COLORS)   # 当前瀑布色带
        self.wf_smooth: bool = False                 # IIR 时间平滑（仅瀑布）
        # 自动量程（SDR++ autoRange 语义）：把底噪锚在色带冷端 ~22% 处，
        # 固定量程做不到——-100…-20 时常见 -60 dBFS 底噪正好落在色带中段，
        # 整幅瀑布糊成一片亮绿（2026-11-10 实拍）。用户动过任一 dB 滑杆/轴上
        # 拖动即永久转手动（apply_db_range 负责关掉），不再抢量程。
        self.auto_db_range: bool = True
        # ---- 调谐步进网格（对标 SDR++ waterfall.h:38 snapInterval）----
        # 普通滚轮/左右箭头按此步进调谐；Shift ×10，Alt ×0.1。
        self.snap_interval = 10_000.0                   # Hz，默认 10 kHz
        # 瀑布历史回看：None=跟随最新；否则为「显示窗口的上边界绝对行号」。
        self.wf_history_end: Optional[int] = None
        self._wf_hist_key = None                        # 回看画面缓存键（避免 10 Hz 重算）
        # ---- VFO 带宽显示（对标 SDR++ waterfall.cpp:221-231 VFO 矩形）----
        # None = 不绘制 VFO 矩形；否则在谱面画半透明橙色带宽带。
        self.vfo_bandwidth_hz: Optional[float] = None
        self.vfo_offset_hz = 0.0                        # VFO 中心相对调谐中心偏移
        # ---- 视图平移偏移（Ctrl 拖动 RF shift：只移视图不调谐）----
        self.view_offset_hz = 0.0
        # ---- 调谐模式（对标 SDR++ 调谐模式按钮）----
        # center=点击/双击重调硬件中心频率（默认）；normal=点击只移当前 VFO，
        # 画面不动，瀑布保留多信号轨迹。
        self.tune_mode = "center"
        # ---- Shift 拖拽新建 VFO 的橡皮筋临时矩形（频率 Hz 二元组）----
        # None = 不显示；否则 (freq_start, freq_cur) 在谱面画半透明蓝灰带。
        self.vfo_create_band: Optional[Tuple[float, float]] = None

        # ---- 持久瀑布画布（对标 SDR++ waterfallFb 帧缓冲）----
        # SDR++ pushFFT() 用 memmove 把整帧缓冲上移一行、再把新行写到底部；
        # 这里用 numpy (h,w,4) 缓冲 + 同名 QImage 包装实现同款增量滚动，
        # 避免每帧逐像素 setPixelColor。无数据时整块填充背景色（空白，不画假噪声）。
        self._wf_canvas: Optional[QImage] = None
        self._wf_buf: Optional[np.ndarray] = None
        self._wf_cw: int = -1          # 画布宽度（像素）
        self._wf_ch: int = -1          # 画布高度（像素）
        self._wf_consumed: int = 0     # 已并入画布的瀑布行数（对齐 gen.wf_rows_written）
        self._wf_map_key: Optional[Tuple[float, float, int, int, int]] = None
        self._wf_lut: Optional[np.ndarray] = None   # db→RGB 查找表 (256,3)
        self._wf_lut_key: Tuple[float, float] = (0.0, 0.0)
        # ---- 谱迹填充：1×h 渐变贴图 + 整数裁剪区域（都按画布高度缓存）----
        # 原实现把渐变笔刷挂在 drawPolygon 上，噪声谱每扫描线数百个跨度，
        # 光栅器逐跨度算渐变实测 6–8 ms/帧；改成「贴图 + 裁剪」后只剩一次
        # blit。剪裁必须用整数 QRegion：QPainterPath 的路径→区域转换单次
        # 6–11 ms（实测），比它省下的还贵。
        self._spec_fill_img: Optional[QImage] = None
        self._spec_fill_h: int = -1

    # ------------------------------------------------------------------ setter
    def set_snap_interval(self, hz: float):
        """主窗口设置调谐步进网格（Hz）。"""
        if hz and hz > 0:
            self.snap_interval = float(hz)

    def set_vfo_bandwidth(self, bw_hz: Optional[float]):
        """主窗口设置 VFO 带宽（Hz）；传 None 关闭 VFO 矩形。"""
        self.vfo_bandwidth_hz = None if bw_hz is None else float(bw_hz)

    def set_vfo_offset(self, offset_hz: float):
        """主窗口设置 VFO 中心相对调谐中心的偏移（Hz）。"""
        self.vfo_offset_hz = float(offset_hz)


# FFT 谱面底部预留高度（像素）：用于画频率轴刻度，避免标签被裁剪。
_SPEC_BOTTOM_PAD = 18

# 频率轴右端留白（像素）：贴着右缘画「MHz」时，右侧竖滑杆列的「最小」标签
# 只隔 2px，实拍读成「MHz最小」一串。轴内退这么多，让单位与滑杆标签分开。
_AXIS_RIGHT_PAD = 6.0

# 左侧 dB 轴拖拽热区宽度（像素）：gqrx plotter.cpp:350-368 YAXIS 捕获区。
# 在频谱左缘这条带内按住拖动 = 整体平移 dB 量程（不调谐、不改 span）；
# Ctrl+竖拖 = 以按下处 dB 为锚缩放跨度；该带上的滚轮同样只缩跨度。
_DB_AXIS_DRAG_W = 40.0

# dB 量程边界与跨度限制（唯一口径；滑杆、拖动、滚轮都走 apply_db_range）。
_DB_AXIS_MIN = -160.0
_DB_AXIS_MAX = 0.0
_DB_MIN_SPAN = 10.0       # 与右栏滑杆最小间隔一致（SDR++ main_window.cpp:639）

#: 右侧「缩放」滑杆刻度数（整数 0..N，对数映射到 [48 kHz, 采样率]）
_SPAN_SLIDER_STEPS = 1000
#: 显示跨度下限（Hz）：再窄就只剩 FFT 泄漏和窗函数旁瓣，没有可读信息
_SPAN_MIN_HZ = 48_000.0
_DB_MAX_SPAN = _DB_AXIS_MAX - _DB_AXIS_MIN
# Ctrl+竖拖满量程一屏 = 跨度 ×4（或 ÷4）；半屏 = ×2，手感线性于像素。
_DB_ZOOM_PER_HEIGHT = 4.0


def _clamp_db_span(lo: float, hi: float, span: float) -> Tuple[float, float]:
    """把 [lo, hi] 夹进 [_DB_AXIS_MIN, _DB_AXIS_MAX]，尽量保持 span。"""
    if lo < _DB_AXIS_MIN:
        hi += _DB_AXIS_MIN - lo
        lo = _DB_AXIS_MIN
    if hi > _DB_AXIS_MAX:
        lo += _DB_AXIS_MAX - hi
        hi = _DB_AXIS_MAX
    lo = max(_DB_AXIS_MIN, lo)
    hi = min(_DB_AXIS_MAX, hi)
    if hi < lo:                     # span 超过全量程时给合法空区间
        lo, hi = _DB_AXIS_MIN, _DB_AXIS_MIN + span
        hi = min(hi, _DB_AXIS_MAX)
    return lo, hi


# ---------------------------------------------------------------------------
# 坐标轴刻度：1/2/5×10ⁿ 阶梯 + 按量程自动单位（SDR++ waterfall.cpp:40-62）
# ---------------------------------------------------------------------------
def _nice_step(span: float, max_ticks: int) -> float:
    """从 1/2/5×10ⁿ 阶梯选最小步长，使刻度数不超过 max_ticks。"""
    if span <= 0 or max_ticks < 2:
        return 0.0
    target = float(span) / float(max_ticks)
    exp = math.floor(math.log10(target))
    for e in range(exp - 2, exp + 3):
        for m in (1.0, 2.0, 5.0):
            step = m * 10.0 ** e
            if step >= target:
                return step
    return 5.0 * 10.0 ** (exp + 2)


def _next_nice_step(step: float) -> float:
    """阶梯内下一个更大的 1/2/5×10ⁿ 步长。"""
    if step <= 0:
        return 1.0
    exp = math.floor(math.log10(step))
    m = step / 10.0 ** exp
    if m < 1.5:
        return 2.0 * 10.0 ** exp
    if m < 3.5:
        return 5.0 * 10.0 ** exp
    return 10.0 ** (exp + 1)


def _nice_ticks(lo: float, hi: float, max_ticks: int = 5) -> Tuple[float, List[float]]:
    """返回 (步长, 落在 [lo, hi] 内的阶梯刻度值)。"""
    span = hi - lo
    step = _nice_step(span, max_ticks)
    if step <= 0:
        return 0.0, []
    vals: List[float] = []
    for _ in range(8):
        k0 = int(math.ceil((lo - 1e-9 * step) / step))
        k1 = int(math.floor((hi + 1e-9 * step) / step))
        vals = [k * step for k in range(k0, k1 + 1)]
        if len(vals) <= max_ticks:
            break
        step = _next_nice_step(step)
    return step, vals


def _step_decimals(step_in_unit: float) -> int:
    """按步长决定小数位（0.5 → 1 位，0.05 → 2 位）。"""
    if step_in_unit <= 0:
        return 0
    return max(0, min(3, int(math.ceil(-math.log10(step_in_unit)))))


def _freq_unit(span_hz: float) -> Tuple[float, str]:
    """按量程自动选轴单位（kHz / MHz / GHz）。"""
    if span_hz < 1e6:
        return 1e3, "kHz"
    if span_hz < 1e9:
        return 1e6, "MHz"
    return 1e9, "GHz"


def _labels_fit(vals, labels, lo: float, hi: float, width_px: float,
                advance, reserve_px: float = 0.0) -> bool:
    """按真实落位 + 端点夹边模拟，判断一组刻度标签是否互不压字。"""
    span = hi - lo
    right_bound = max(0.0, width_px - reserve_px)
    boxes = []
    for v, s in zip(vals, labels):
        x = (v - lo) / span * width_px
        w = advance(s)
        tx = min(max(0.0, x - w / 2.0), max(0.0, right_bound - w))
        boxes.append((tx, tx + w))
    boxes.sort()
    return all(b[0] >= a[1] + 3.0 for a, b in zip(boxes, boxes[1:]))


def _freq_scale_ticks(view_center_hz: float, span_hz: float, width_px: float,
                      advance, max_ticks: int = 5, reserve_px: float = 0.0):
    """生成频轴刻度：先按 1/2/5 阶梯定步长，再按标注宽度递减数量。

    单位不写在每个标签里（窄面会因单位后缀互相压字），由调用方在轴右端
    画一次自动单位。返回 (step_hz, tick_hz 列表, 标签列表, 单位名)。
    """
    lo = view_center_hz - span_hz / 2.0
    hi = view_center_hz + span_hz / 2.0
    unit, uname = _freq_unit(span_hz)
    for mt in (max_ticks, max_ticks - 1, 3, 2):
        if mt < 2:
            continue
        step, vals = _nice_ticks(lo, hi, mt)
        if len(vals) < 2:
            continue
        d = _step_decimals(step / unit)
        labels = [f"{v / unit:.{d}f}" for v in vals]
        if _labels_fit(vals, labels, lo, hi, width_px, advance, reserve_px):
            return step, vals, labels, uname
    # 极端窄面：退化为两端点，逐级减少小数直到放得下（绝不压字/裁字）。
    for d in range(3, -1, -1):
        vals = [lo, hi]
        labels = [f"{lo / unit:.{d}f}", f"{hi / unit:.{d}f}"]
        if _labels_fit(vals, labels, lo, hi, width_px, advance, reserve_px):
            return span_hz, vals, labels, uname
    return span_hz, [lo, hi], [f"{lo / unit:.0f}", f"{hi / unit:.0f}"], uname


def _render_spectrum(painter: QPainter, state: _PlotState, w: int, h: int):
    """上方 FFT 频谱曲线 widget 的完整绘制（整个 widget 即谱面）。

    仅画频谱相关元素：网格 / VFO 带宽矩形 / 谱线 / 峰值 / 中心游标 /
    悬停读数 / 固定 marker / 频率轴与 dB 轴。瀑布图由 WaterfallWidget 单独绘制。
    无真实 IQ 数据时只画网格 + 红色“未连接 SDR”提示，不画任何假谱线。
    """
    panel = state.panel
    gen = panel.generator
    has_data = gen.has_data()

    # 自动量程先于一切几何：谱线/瀑布/刻度都吃同一份 db_min/db_max。
    if has_data:
        _auto_db_range(state)

    bg = QColor(panel.color_bg)
    grid = QColor(panel.color_grid)
    text = QColor(panel.color_text)
    line = QColor(panel.color_line)

    painter.fillRect(0, 0, w, h, bg)

    # 谱面绘图区（底部留出频率轴刻度空间）；x 方向从 0 到全宽，
    # 与 _freq_at_x 的“x=0 ↔ 视口左缘”映射保持一致。
    spec_rect = QRectF(0, 0, w, max(1, h - _SPEC_BOTTOM_PAD))

    # 缩放后视口只显示带宽中间一段：所有以频率落位的元素（谱线、VFO、
    # 书签、峰值）都可能落到谱面矩形之外，必须整体裁剪，不能画到杆上。
    painter.save()
    painter.setClipRect(spec_rect)

    view_center = _view_center_hz(state)
    span = gen.span_hz

    _draw_grid(painter, spec_rect, grid, state)
    # 频段彩带垫底（SDR++ bandplan 仿妆）: 网格之上、谱线之下
    _draw_bandplan(painter, state, spec_rect)

    # ---- 频谱曲线：仅在有真 IQ 数据时绘制 ----
    if has_data:
        # VFO 带宽矩形（先画在谱线下方；无数据时不画，保持"未连接"干净）
        _draw_vfo_band(painter, state, spec_rect)
        # 谱线/填充只关几何 AA：900×400 实测 45.8→25.0 ms/帧（-45%），
        # 差异仅在填充边缘 1px 由混叠变硬（4× 放大才看得出，见
        # docs/perf-evidence/aa_*_crop4x.png）；峰点/刻度/文字仍走原 AA。
        _aa = bool(painter.renderHints() & QPainter.Antialiasing)
        painter.setRenderHint(QPainter.Antialiasing, False)
        _draw_spectrum(painter, state, spec_rect)
        painter.setRenderHint(QPainter.Antialiasing, _aa)
        _draw_hold_lines(painter, state, spec_rect)
        _draw_peaks(painter, state, spec_rect, QColor(PAL_PEAK))
    else:
        # 无数据：空白谱面 + 红色两行提示（不画任何谱线/瀑布/峰值/VFO）
        painter.setPen(QPen(QColor(PAL_OFFLINE), 1))
        f1 = QFont()
        f1.setPointSize(13)
        painter.setFont(f1)
        msg1 = "未连接 SDR"
        tw1 = painter.fontMetrics().horizontalAdvance(msg1)
        y1 = spec_rect.y() + spec_rect.height() / 2.0 - 4
        painter.drawText(QPointF((w - tw1) / 2.0, y1), msg1)
        f2 = QFont()
        f2.setPointSize(9)
        painter.setFont(f2)
        msg2 = getattr(state, "offline_hint", "") or OFFLINE_HINT_DEFAULT
        tw2 = painter.fontMetrics().horizontalAdvance(msg2)
        painter.drawText(QPointF((w - tw2) / 2.0, y1 + 20), msg2)

    # 中心频率游标
    _draw_bandplan(painter, state, spec_rect, overlay=True)
    _draw_center_cursor(painter, spec_rect, QColor(PAL_LINE))

    # 悬停读数 + 固定 marker + 书签竖旗（都读真实数组/真实收藏）
    if has_data:
        _draw_hover_readout(painter, state, spec_rect, text, line)
        _draw_fixed_markers(painter, state, spec_rect, line)
    _draw_bookmark_tags(painter, state, spec_rect)
    painter.restore()

    # 频率轴（画在谱面底部预留区）+ dB 轴
    _draw_freq_scale(painter, spec_rect, view_center, span, text)
    _draw_db_scale(painter, spec_rect, state, text)


def _render_wf_only(painter: QPainter, state: _PlotState, w: int, h: int):
    """下方瀑布图 widget 的完整绘制（整个 widget 即瀑布画布）。

    复用持久增量画布 _draw_waterfall；无数据时画布为空白背景，不画假噪声。
    顶缘画频段彩带条（与谱面同几何同色，schema"谱面+瀑布色带"的瀑布半边）。
    """
    panel = state.panel
    bg = QColor(panel.color_bg)
    painter.fillRect(0, 0, w, h, bg)
    # 瀑布与谱面是两块独立 widget，绘制顺序不由我们决定：自动量程必须
    # 在这里也算一次，否则瀑布先画时仍用旧量程上色，谱面再改量程 →
    # 同一帧里上下两半用两个色标（实拍：瀑布亮绿、刻度已到 0 dBFS）。
    if panel.generator.has_data():
        _auto_db_range(state)
    if h > 0:
        # 瀑布不再单独画一条频率轴：谱面底部那条轴就在两区之间，是二者
        # 共用的唯一刻度；瀑布画布按同一 _px_to_freq 落列（见
        # _wf_paint_row_into），Ctrl 平移后仍与上方刻度对齐。
        wf_rect = QRectF(0, 0, w, h)
        if not panel.generator.has_data():
            _draw_wf_placeholder(painter, state, wf_rect)
        elif state.show_waterfall:
            _draw_waterfall(painter, wf_rect, state)
        # 顶缘频段彩带是视口装饰不是数据，瀑布关着也画（频率锚不丢）
        _draw_bandplan_wf_strip(painter, state, wf_rect)


def _draw_wf_placeholder(painter, state, rect):
    """无 IQ 数据时瀑布的诚实空态。

    一整块 200px 高的纯黑在界面里读起来像"渲染坏了"，而 SDR++ 那块永远
    有噪声。没有硬件就不画噪声（红线），但可以让它读起来是"在等数据"：
    与谱面同几何的极淡频率网格 + 一行说明。网格用同一套 _freq_scale_ticks，
    所以接上硬件后噪声行落下时不会跳位。
    """
    gen = state.panel.generator
    grid = QColor(state.panel.color_grid)
    grid.setAlpha(26)
    painter.setPen(QPen(grid, 1))
    old_font = painter.font()
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    fm = painter.fontMetrics()
    reserve = fm.horizontalAdvance("MHz") + 8.0 + _AXIS_RIGHT_PAD
    view_center = _view_center_hz(state)
    _step, ticks, _labels, _unit = _freq_scale_ticks(
        view_center, gen.span_hz, rect.width(),
        fm.horizontalAdvance, reserve_px=reserve)
    for tick_hz in ticks:
        x = rect.center().x() + (tick_hz - view_center) / gen.span_hz * rect.width()
        if rect.x() < x < rect.x() + rect.width():
            painter.drawLine(QPointF(x, rect.y()),
                             QPointF(x, rect.y() + rect.height()))

    text = QColor(state.panel.color_text)
    text.setAlpha(120)
    painter.setPen(QPen(text, 1))
    f1 = QFont()
    f1.setPointSize(9)
    painter.setFont(f1)
    msg = "瀑布图 · 等待 IQ 数据"
    cy = rect.center().y()
    painter.drawText(QPointF(rect.center().x()
                             - painter.fontMetrics().horizontalAdvance(msg) / 2.0,
                             cy), msg)
    f2 = QFont()
    f2.setPointSize(8)
    painter.setFont(f2)
    text.setAlpha(90)
    painter.setPen(QPen(text, 1))
    # 紧凑槽（星象仪底部条约 50px 高）只放得下一行：第二行会被裁进频率轴，
    # 宁可不写也不叠字；第一行「等待 IQ 数据」已说明状态。
    if rect.height() >= 64:
        sub = "没有真实采样就不画噪声条"
        painter.drawText(QPointF(rect.center().x()
                                 - painter.fontMetrics().horizontalAdvance(sub) / 2.0,
                                 cy + 16), sub)
    painter.setFont(old_font)


def _draw_grid(painter, rect, grid, state):
    """刻度网格：竖线对频率刻度、横线对 dB 刻度，极淡 1px 实线。

    网格不是装饰、也不是等分的四六格：每一根都必须落在 _draw_freq_scale /
    _draw_db_scale 实际画出的刻度值上，否则"线在这里、数字在那里"就是
    2026-11-10 用户说的"垃圾玩意"。两条轴共用同一套刻度生成器与映射。
    """
    g = QColor(grid)
    g.setAlpha(PAL_GRID_ALPHA)
    painter.setPen(QPen(g, 1))
    gen = state.panel.generator
    # 与 _draw_freq_scale 完全同参：8pt 字度量 + 右端单位预留，否则刻度
    # 会被挤掉一格、网格线又对不上数字。
    old_font = painter.font()
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    fm = painter.fontMetrics()
    reserve = fm.horizontalAdvance("MHz") + 8.0 + _AXIS_RIGHT_PAD
    _step, ticks, _labels, _unit = _freq_scale_ticks(
        _view_center_hz(state), gen.span_hz, rect.width(),
        fm.horizontalAdvance, reserve_px=reserve)
    for tick_hz in ticks:
        x = rect.center().x() + (tick_hz - _view_center_hz(state)) \
            / gen.span_hz * rect.width()
        if rect.x() < x < rect.x() + rect.width():
            painter.drawLine(QPointF(x, rect.y()),
                             QPointF(x, rect.y() + rect.height()))
    _step, dbs = _nice_ticks(state.db_min, state.db_max, 5)
    for db in dbs:
        y = _db_to_y(rect, db, state.db_min, state.db_max)
        if rect.y() < y < rect.y() + rect.height():
            painter.drawLine(QPointF(rect.x(), y),
                             QPointF(rect.x() + rect.width(), y))
    painter.setFont(old_font)


# 自动量程把底噪锚在量程的这个位置（0=底、1=顶）。SDR++ autoRange 的观感：
# 底噪贴着底边（深紫/深蓝），只有抬头的信号才走到青绿黄红；锚太高（实测
# 0.22 在 Turbo 色表上已是整片亮青）整幅瀑布又变成一块发光板。
_AUTO_DB_NOISE_POS = 0.10
# 自动量程的最小/最大跨度。跨度必须能收窄：0 dBFS 是硬天花板，底噪 -56 时
# 想把噪声压到 10% 处，跨度最多只剩 ~62 dB；硬撑 80 dB 会被天花板顶回去，
# 噪声又回到色带 30% 的整片亮青（2026-11-10 实拍两次）。
_AUTO_DB_MIN_SPAN = 40.0
_AUTO_DB_MAX_SPAN = 80.0


def _auto_db_range(state) -> None:
    """自动量程（SDR++ autoRange 的显示语义）：把底噪锚在色带下 10% 处。

    只在用户没动过 dB 滑杆/轴拖动时生效（``auto_db_range``，apply_db_range
    一旦被手动调用就永久关掉）。跨度在手动态量程的 40–80 dB 之间收放，
    只整体平移窗口：底噪落在冷端 → 强信号一路烧到红端，与 SDR++ 观感一致。
    滞回 3 dB，避免量程随噪声每次都在 10 Hz 刷新里来回跳。
    """
    if not state.auto_db_range:
        return
    if getattr(state.panel, "_db_min_slider", None) is None:
        return
    spec = state.panel.generator.spectrum
    finite = spec[np.isfinite(spec)]
    if finite.size < 64:
        return
    span = min(max(state.db_max - state.db_min, _AUTO_DB_MIN_SPAN),
               _AUTO_DB_MAX_SPAN)
    noise = float(np.percentile(finite, 20))
    # 先按"底噪落 10%、顶到 0 dBFS"反推跨度，再夹进可用区间。
    span = min(span, (_DB_AXIS_MAX - noise) / (1.0 - _AUTO_DB_NOISE_POS))
    span = max(span, _AUTO_DB_MIN_SPAN)
    lo = noise - _AUTO_DB_NOISE_POS * span
    if lo + span > _DB_AXIS_MAX:
        lo = _DB_AXIS_MAX - span
    lo = max(_DB_AXIS_MIN, lo)
    hi = lo + span
    if abs(lo - state.db_min) < 3.0 and abs(hi - state.db_max) < 3.0:
        return
    lo, hi = float(round(lo)), float(round(hi))
    state.db_min, state.db_max = lo, hi
    # 滑杆是唯一状态源的共享显示：状态变了必须同帧跟上（阻断回调环，
    # 也不走 apply_db_range —— 那是"手动"入口，会关掉自动量程）。
    panel = state.panel
    for slider, val in ((panel._db_max_slider, hi),
                        (panel._db_min_slider, lo)):
        slider.blockSignals(True)
        slider.setValue(int(val))
        slider.blockSignals(False)


def _db_to_y(rect, db, db_min, db_max):
    h = rect.height()
    ratio = (db - db_min) / (db_max - db_min)
    ratio = max(0.0, min(1.0, ratio))
    # 上下各留 4%：谱线、hold、峰值、VFO 带与 dB 轴刻度共用这一条映射，
    # 刻度文字才真正落在它标的那条电平上（旧式 1-0.92r 与轴上 0.04+0.92f
    # 差 0.04h，1400×360 的谱面上就是 14px 的错位）。
    return rect.y() + rect.height() * (0.04 + 0.92 * (1.0 - ratio))


def _spectrum_xy(state, rect):
    """谱迹几何：(x, y, floor_y, finite)，按显示跨度把 bin 映射到画布像素。

    绘制与「填充边界 ±1 px」回归测试共用这一份映射，测试才能拿来建边界带，
    而不是自己抄一份公式。
    """
    gen = state.panel.generator
    spec = gen.spectrum
    n = len(spec)
    rx0, rw = rect.x(), rect.width()
    ry0, rh = rect.y(), rect.height()
    # x 几何用显示跨度（缩放后变窄），bin→频率仍用采样率（bins 覆盖全带宽）。
    span = gen.span_hz
    f_left = _view_center_hz(state) - span / 2.0
    ii = np.arange(n, dtype=np.float64)
    freq = (gen.center_freq_hz - gen.sample_rate_hz / 2.0
            + gen.sample_rate_hz * (ii + 0.5) / n)
    finite = np.isfinite(spec)
    db = np.where(finite, spec, state.db_min)
    x = rx0 + (freq - f_left) / span * rw
    ratio = np.clip((db - state.db_min) / (state.db_max - state.db_min),
                    0.0, 1.0)
    y = ry0 + rh * (0.96 - 0.92 * ratio)
    return x, y, ry0 + rh * 0.96, finite


#: 谱迹填充渐变的上下端点透明度（SDR++ 同款：顶部 96、底部 10）。
_TRACE_FILL_TOP_ALPHA = 96
_TRACE_FILL_BOT_ALPHA = 10


def _trace_fill_image(state, h: int) -> QImage:
    """谱迹填充用的 1×h 竖直渐变贴图（缓存在 ``_PlotState`` 上）。

    渐变只取决于高度与谱迹色/透明度，与 dB 量程无关（量程只改 y 几何），
    所以整幅重绘期间只在高度变化时重建一次；绘制时横向拉伸到画布宽。
    """
    h = max(1, int(round(h)))
    if state._spec_fill_img is not None and state._spec_fill_h == h:
        return state._spec_fill_img
    img = QImage(1, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    grad = QLinearGradient(0, 0, 0, h)
    top = QColor(PAL_TRACE)
    top.setAlpha(_TRACE_FILL_TOP_ALPHA)
    bot = QColor(PAL_TRACE)
    bot.setAlpha(_TRACE_FILL_BOT_ALPHA)
    grad.setColorAt(0.0, top)
    grad.setColorAt(1.0, bot)
    p.fillRect(0, 0, 1, h, QBrush(grad))
    p.end()
    state._spec_fill_img = img
    state._spec_fill_h = h
    return img


def _fill_spectrum_under(painter, state, fillpoly, rect):
    """按 ``fillpoly`` 裁剪后一次贴上渐变贴图（替代渐变笔刷 drawPolygon）。

    只负责填充：轮廓与谱线由调用方用纯色描边，两者分离后光栅器不再
    逐跨度计算渐变。裁剪走整数 ``QRegion``——多边形顶点在这里被取整，
    填充边界因此有 1 px 量级的位移（非抗锯齿，见 docs 的等价口径）。
    """
    reg = QRegion(QPolygon([q.toPoint() for q in fillpoly]))
    painter.save()
    painter.setClipRegion(reg, Qt.ClipOperation.IntersectClip)
    painter.drawImage(QRectF(rect.x(), rect.y(), rect.width(), rect.height()),
                      _trace_fill_image(state, rect.height()))
    painter.restore()


def _draw_spectrum(painter, state, rect):
    """向量化画频谱折线 + 渐变填充。numpy 一次算完全部 x/y，QPolygonF
    单次 drawPolyline，填充走「渐变贴图 + 整数区域裁剪」。"""
    gen = state.panel.generator
    if len(gen.spectrum) == 0:
        return
    x, y, floor_y, finite = _spectrum_xy(state, rect)
    rx0, rw = rect.x(), rect.width()
    n = len(x)
    # SDR++ 签名：1px 亮青实线 + 同色低透明度向下渐变（越靠底越淡），
    # 不再用整块不透明灰蓝把谱面糊成一面墙。
    trace = QColor(PAL_TRACE)
    # 填充与描边分离：填充贴渐变图，描边用纯色。
    painter.setPen(QPen(trace, 1))
    painter.setBrush(Qt.NoBrush)
    if finite.all():
        poly = QPolygonF([QPointF(float(a), float(b))
                          for a, b in zip(x, y)])
        fillpoly = QPolygonF()
        fillpoly.append(QPointF(rx0, floor_y))
        fillpoly += poly
        fillpoly.append(QPointF(rx0 + rw, floor_y))
        _fill_spectrum_under(painter, state, fillpoly, rect)
        painter.drawPolygon(fillpoly)
        painter.drawPolyline(poly)
        return
    # NaN = 未采到（不是弱信号）：在断点处分段，不连线、不填充。
    start = None
    for i in range(n + 1):
        ok = bool(finite[i]) if i < n else False
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            a, b = start, i
            start = None
            seg = QPolygonF([QPointF(float(x[j]), float(y[j]))
                             for j in range(a, b)])
            if b - a >= 2:
                fillpoly = QPolygonF()
                fillpoly.append(QPointF(float(x[a]), floor_y))
                fillpoly += seg
                fillpoly.append(QPointF(float(x[b - 1]), floor_y))
                _fill_spectrum_under(painter, state, fillpoly, rect)
                painter.drawPolygon(fillpoly)
                painter.drawPolyline(seg)
            else:
                painter.drawPoint(QPointF(float(x[a]), float(y[a])))


def _draw_hold_lines(painter, state, rect):
    """Max/Min hold 包络线（SDR++ maxHold/minHold 签名）。

    各画一根 1px 实线，只画线不填充：max 包络暖橙悬在谱线上方，
    min 包络冷蓝压在噪声底下沿。包络由 generator 逐帧维护。
    """
    gen = state.panel.generator

    def _line(hold: np.ndarray, color: QColor):
        n = len(hold)
        if n == 0:
            return
        view_center = _view_center_hz(state)
        span = gen.span_hz
        sr = gen.sample_rate_hz
        f_left = view_center - span / 2.0
        ii = np.arange(n, dtype=np.float64)
        freq = (gen.center_freq_hz - sr / 2.0
                + sr * (ii + 0.5) / n)
        finite = np.isfinite(hold)
        db = np.where(finite, hold, state.db_min)
        x = rect.x() + (freq - f_left) / span * rect.width()
        ratio = np.clip((db - state.db_min) / (state.db_max - state.db_min),
                        0.0, 1.0)
        y = rect.y() + rect.height() * (0.96 - 0.92 * ratio)
        painter.setPen(QPen(color, 1))
        if finite.all():
            poly = QPolygonF([QPointF(float(a), float(b))
                              for a, b in zip(x, y)])
            painter.drawPolyline(poly)
            return
        # NaN = 未采到：断点处分段，不连线。
        start = None
        for i in range(n + 1):
            ok = bool(finite[i]) if i < n else False
            if ok and start is None:
                start = i
            elif not ok and start is not None:
                a, b = start, i
                start = None
                seg = QPolygonF([QPointF(float(x[j]), float(y[j]))
                                 for j in range(a, b)])
                if b - a >= 2:
                    painter.drawPolyline(seg)
                else:
                    painter.drawPoint(QPointF(float(x[a]), float(y[a])))

    if gen.show_max_hold and gen.max_hold is not None:
        _line(gen.max_hold, QColor(PAL_MAXHOLD))
    if gen.show_min_hold and gen.min_hold is not None:
        _line(gen.min_hold, QColor(PAL_MINHOLD))


def _draw_bandplan(painter, state, rect, overlay=False):
    """频段彩带（SDR++ bandplan 仿妆），两遍绘制防被谱线渐变填充盖住。

    overlay=False —— 网格之后、谱线之下：淡色填充垫底；
    overlay=True  —— 谱线/包络/峰值之后：底部 4px 色条 + 重要性分级标签。
    悬停带提亮+描边，点击选中带常驻描边；标签由 _bandplan_label_placements
    按 priority 分级放置，缩放变窄时低优先级先消失，不糊成一团。
    随频率缩放/平移联动（view_center/span 与谱线同源）。与书签竖旗/固定
    marker 不同 y 带。
    """
    if not state.show_bandplan or not state.bandplan:
        return
    view_center = _view_center_hz(state)
    span = state.panel.generator.span_hz
    hover = getattr(state, "bandplan_hover", None)
    selected = getattr(state, "bandplan_selected", None)
    if not overlay:
        painter.setPen(Qt.NoPen)
        for i, b in enumerate(state.bandplan):
            rng = _band_x_range(rect, view_center, span,
                                float(b["start_hz"]), float(b["stop_hz"]))
            if rng is None:
                continue
            x0, x1 = rng
            fill = QColor(_band_color(b))
            fill.setAlpha(46 if i == hover else (38 if i == selected else 26))
            painter.setBrush(QBrush(fill))
            painter.drawRect(QRectF(x0, rect.y(), x1 - x0, rect.height()))
        return
    # ---- overlay 遍: 色条 + 分级标签 ----
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    fm = painter.fontMetrics()
    visible: List[Tuple[int, float, float, Dict[str, object]]] = []
    for i, b in enumerate(state.bandplan):
        rng = _band_x_range(rect, view_center, span,
                            float(b["start_hz"]), float(b["stop_hz"]))
        if rng is None:
            continue
        visible.append((i, rng[0], rng[1], b))
    items = [(x0, x1, str(b["label"]), str(b.get("priority", "low")),
              i == hover or i == selected)
             for i, x0, x1, b in visible]
    tx_map = _bandplan_label_placements(items, fm.horizontalAdvance,
                                        rect.x(), rect.width())
    for k, (i, x0, x1, b) in enumerate(visible):
        color = _band_color(b)
        strip = QColor(color)
        strip.setAlpha(210 if i == hover else (180 if i == selected else 130))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(strip))
        painter.drawRect(QRectF(x0, rect.y() + rect.height() - 4.0,
                                x1 - x0, 4.0))
        if i == hover or i == selected:
            edge = QColor(color)
            edge.setAlpha(230)
            painter.setPen(QPen(edge, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(x0 + 0.5, rect.y() + 0.5,
                                    x1 - x0 - 1.0, rect.height() - 1.0))
        if k in tx_map:
            lc = QColor(color)
            lc.setAlpha(215)
            painter.setPen(QPen(lc, 1))
            painter.drawText(QPointF(tx_map[k], rect.y() + rect.height() - 8.0),
                             str(b["label"]))


def _draw_bandplan_wf_strip(painter, state, rect):
    """频段彩带瀑布顶缘条（bandplan.json schema 承诺的"瀑布色带"半边）。

    与谱面 overlay 色条同几何（_band_x_range 同视口）同色（_band_color）
    同强调语义（悬停/选中提亮，状态由共享 _PlotState 自动同步）；
    瀑布向下滚动，顶缘条是它唯一的频段锚。
    """
    if not state.show_bandplan or not state.bandplan:
        return
    view_center = _view_center_hz(state)
    span = state.panel.generator.span_hz
    hover = getattr(state, "bandplan_hover", None)
    selected = getattr(state, "bandplan_selected", None)
    painter.setPen(Qt.NoPen)
    for i, b in enumerate(state.bandplan):
        rng = _band_x_range(rect, view_center, span,
                            float(b["start_hz"]), float(b["stop_hz"]))
        if rng is None:
            continue
        x0, x1 = rng
        strip = QColor(_band_color(b))
        strip.setAlpha(210 if i == hover else (180 if i == selected else 130))
        painter.setBrush(QBrush(strip))
        painter.drawRect(QRectF(x0, rect.y(), x1 - x0, 4.0))


def _draw_bookmark_tags(painter, state, rect):
    """书签竖旗（gqrx 书签签名）：谱面顶部暗金药丸 + 贯顶虚线。

    药丸夹紧在谱面横向范围内（视口边缘的书签仍可见可点）；
    点击命中由 _TuningPlotMixin._bookmark_tag_at 用同一几何反算。
    """
    if not state.show_bookmarks or not state.bookmarks:
        return
    gen = state.panel.generator
    view_center = _view_center_hz(state)
    span = gen.span_hz
    f_left = view_center - span / 2.0
    color = QColor(PAL_BOOKMARK)
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    fm = painter.fontMetrics()
    painter.setBrush(QBrush(color))
    entries = []
    for freq_hz, name in state.bookmarks:
        x = rect.x() + (float(freq_hz) - f_left) / span * rect.width()
        if x < rect.x() or x > rect.x() + rect.width():
            continue
        text = str(name)
        pw = fm.horizontalAdvance(text) + 10
        x0 = min(max(rect.x(), x - pw / 2.0),
                 rect.x() + rect.width() - pw)
        entries.append((x, x0, pw, text))
    # 近距离书签药丸会叠字（截图事故）：按 x 排序后逐行下移，
    # 3 行仍放不下就只留虚线旗、不画药丸文字。虚线旗始终绘制。
    placed_boxes: list = []
    for x, x0, pw, text in sorted(entries, key=lambda e: e[0]):
        painter.setPen(QPen(color, 1, Qt.DashLine))
        painter.drawLine(QPointF(x, rect.y() + _BM_TAG_H),
                         QPointF(x, rect.y() + rect.height()))
        pill = None
        for row in range(3):
            box = QRectF(x0, rect.y() + 1.0 + row * _BM_TAG_H,
                         pw, _BM_TAG_H - 2.0)
            if all(not box.intersects(b) for b in placed_boxes):
                pill = box
                break
        if pill is None:
            continue
        placed_boxes.append(pill)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(pill, 3.0, 3.0)
        painter.setPen(QPen(QColor(PAL_BG), 1))
        painter.drawText(QPointF(x0 + 5.0, pill.y() + fm.ascent()), text)


def _draw_center_cursor(painter, rect, color):
    cx = rect.x() + rect.width() / 2
    painter.setPen(QPen(color, 1, Qt.DashLine))
    painter.drawLine(QPointF(cx, rect.y()),
                     QPointF(cx, rect.y() + rect.height()))
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.NoPen)
    painter.drawPolygon(QPolygonF([
        QPointF(cx - 6, rect.y()),
        QPointF(cx + 6, rect.y()),
        QPointF(cx, rect.y() + 10),
    ]))


def _peak_label_placements(peaks_xy, measure, x_min: float, x_max: float,
                           row_h: float, max_rows: int = 4, gap: float = 4.0,
                           text_ascent=None, text_descent: float = 0.0):
    """峰值标注碰撞剔除（与频段彩带 placement 同款贪心，纯函数可测）。

    peaks_xy: [(x, y, label)] 按 x 升序；measure(label) -> 文本宽。
    返回 [(x, y, label, row)]：row 0 = 点上方最近行。

    标注锚定自己的峰（tx = x + 6），绝不水平漂移——漂移超过峰距就会把
    标签挂到相邻峰上（误归属）。与本行已占区间冲突时沿纵向往下一行错开
    （最多 max_rows 行），全部放不下或锚点出界则诚实丢弃：不互相压字、
    不越界、不漂走。
    """
    if text_ascent is None:
        text_ascent = max(1.0, row_h - 2.0)   # 与 fm.height() ≈ 行高一致
    row_right: list = []             # row -> 已占右缘 x（同一行必须横向分开）
    placed: list = []                # 已放标签的二维文本框 (x0,x1,y0,y1)
    out = []
    for x, y, label in peaks_xy:
        w = float(measure(label))
        tx = x + 6.0
        if tx < x_min or tx + w > x_max:
            continue                  # 锚点出界：每行位置相同，诚实丢弃
        done = False
        for row in range(max_rows):
            while len(row_right) <= row:
                row_right.append(x_min - 1.0)
            if tx < row_right[row] + gap:
                continue              # 本行横向已占，换下一行
            ty = y - 6.0 - row * row_h
            # 纵向盒用真实字形高度：相邻行的锚点 y 并不相同（峰高不同），
            # 只比「同行占位 x」会出现上下两行各自错开几 px 仍压字的情况。
            box = (tx, tx + w, ty - text_ascent, ty + text_descent)
            hit = False
            for p in placed:
                if (box[0] < p[1] + gap and box[1] > p[0] - gap
                        and box[2] < p[3] and box[3] > p[2]):
                    hit = True
                    break
            if not hit:
                row_right[row] = tx + w
                placed.append(box)
                out.append((tx, ty, label, row))
                done = True
                break
        if not done:
            continue                  # 行满：丢弃，不叠字
    return out


#: 纵轴刻度文字画在谱内左上（距左边 4px）；峰标签必须让开它。
_DB_AXIS_INSET = 4.0
#: 峰标签与刻度文字之间至少留的横向空隙（像素）。
_DB_AXIS_GAP = 6.0


def _peak_label_left_bound(rect, state, fm) -> float:
    """峰标签左界：让开谱内左上那列 dBFS 刻度文字。

    实拍缺陷（2026-11-11，星象仪 180px 紧凑频谱条）：靠近左缘的峰标签
    「97.730 MHz」直接压在「-40 dBFS」上，两串读数互相污染。刻度画在
    ``rect.x() + 4``，而峰标签只让了 2px。这里按当前量程实测最宽刻度，
    把标签起点推到刻度右侧；推不过去的峰按既有策略诚实丢弃（不漂移、
    不叠字），与 ``_peak_label_placements`` 的锚定规则一致。
    """
    _step, dbs = _nice_ticks(state.db_min, state.db_max, 5)
    if not dbs:
        dbs = [state.db_min, state.db_max]
    axis_w = max(float(fm.horizontalAdvance(_fmt_axis_tick(db)))
                 for db in dbs)
    return rect.x() + _DB_AXIS_INSET + axis_w + _DB_AXIS_GAP


def _peak_annotations(state):
    """峰值标注唯一数据源：[(freq_hz, db, snr_db, label), ...]。

    绘制与命中（_peak_at / 悬停 / 点击提示）都读这一份，标签里的 SNR
    与命中返回的 SNR 因此不可能分叉。SNR 基准见
    SpectrumDataGenerator.noise_floor_db（本帧全谱中位数）。

    常驻标签只写频率（SDR++ 峰值标签是站名/频点短签，不是读数表）：
    dBFS/SNR 这类每秒跳几十次的量只在悬停提示里出现，绝不挂在谱面上。
    """
    gen = state.panel.generator
    if not state.show_peaks or not gen.has_data():
        return []
    return [(freq_hz, db, snr, f"{freq_hz/1e6:.3f} MHz")
            for freq_hz, db, snr in gen.find_peaks_with_snr(state.peak_rel_db)]


def _draw_peaks(painter, state, rect, color):
    gen = state.panel.generator
    ann = _peak_annotations(state)
    if not ann:
        return
    painter.setPen(QPen(color, 1))
    painter.setBrush(QBrush(color))
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    fm = painter.fontMetrics()
    view_center = _view_center_hz(state)
    span = gen.span_hz
    pts = []
    for freq_hz, db, _snr, label in ann:
        x = _freq_to_x(rect, view_center, span, freq_hz)
        y = _db_to_y(rect, db, state.db_min, state.db_max)
        painter.drawEllipse(QPointF(x, y), 3, 3)
        pts.append((x, y, label))
    # 碰撞剔除（截图叠字事故）：邻近峰标签分行错开，放不下诚实丢弃
    pts.sort(key=lambda p: p[0])
    row_h = fm.height() + 2.0
    for tx, ty, label, _row in _peak_label_placements(
            pts, fm.horizontalAdvance, _peak_label_left_bound(rect, state, fm),
            rect.x() + rect.width() - 2.0, row_h,
            text_ascent=fm.ascent(), text_descent=fm.descent()):
        painter.drawText(QPointF(tx, ty), label)


#: 绝对电平的统一单位：悬停、峰值提示、纵轴共用同一个口径（U-05）。
_DBFS_UNIT = "dBFS"
#: 相对量的统一基准写法：门限/SNR/余量都必须注明「相对什么」。
_REL_BASE = "相对本帧中位"


def _fmt_abs_db(db: float) -> str:
    """绝对电平（dBFS）唯一格式化出口。"""
    return f"{db:.1f} {_DBFS_UNIT}"


def _fmt_axis_tick(db: float) -> str:
    """纵轴刻度带单位，避免读者把 dBFS 当成任意 dB。"""
    return f"{db:.0f} {_DBFS_UNIT}"


def _fmt_peak_text(freq_hz: float, power_db: float, snr_db: float,
                   verb: str = "峰值") -> str:
    """峰值提示唯一出口：绝对量标 dBFS，SNR 标「相对本帧中位」。"""
    return (f"{verb} {freq_hz / 1e6:.4f} MHz"
            f"（{power_db:.1f} {_DBFS_UNIT} · "
            f"SNR（{_REL_BASE}）{snr_db:.1f} dB）")


def _draw_hover_readout(painter, state, rect, text_color, line_color):
    if state.mouse_x_ratio is None or not state.mouse_in_spectrum:
        return
    gen = state.panel.generator
    view_center = _view_center_hz(state)
    span = gen.span_hz
    freq_hz = view_center - span / 2.0 + state.mouse_x_ratio * span
    x = rect.x() + state.mouse_x_ratio * rect.width()
    val = gen.value_at_freq(freq_hz)
    painter.setPen(QPen(line_color, 1, Qt.DashLine))
    painter.drawLine(QPointF(x, rect.y()),
                     QPointF(x, rect.y() + rect.height()))
    painter.setPen(QPen(text_color, 1))
    f = QFont()
    f.setPointSize(9)
    painter.setFont(f)
    db_txt = _fmt_abs_db(val) if val is not None else "--"
    painter.drawText(QPointF(rect.x() + 6, rect.y() + 14),
                     f"{freq_hz/1e6:.4f} MHz   {db_txt}")


def _draw_fixed_markers(painter, state, rect, color):
    gen = state.panel.generator
    view_center = _view_center_hz(state)
    span = gen.span_hz
    f = QFont()
    f.setPointSize(8)
    for freq_hz in state.markers:
        x = _freq_to_x(rect, view_center, span, freq_hz)
        val = gen.value_at_freq(freq_hz)
        painter.setPen(QPen(color, 1))
        painter.drawLine(QPointF(x, rect.y()),
                         QPointF(x, rect.y() + rect.height()))
        painter.setFont(f)
        db_txt = f"{val:.1f}{_DBFS_UNIT}" if val is not None else "--"
        painter.drawText(QPointF(x + 4, rect.y() + 26),
                         f"{freq_hz/1e6:.3f}MHz {db_txt}")


def _wf_build_lut(state: "_PlotState"):
    """db → RGB 查找表（256×3），使用 state.wf_colors 指定的色带。"""
    n = 256
    lut = np.empty((n, 3), dtype=np.uint8)
    dmin, dmax = state.db_min, state.db_max
    colors = state.wf_colors
    for i in range(n):
        v = dmin + (dmax - dmin) * i / (n - 1)
        c = value_to_color(v, dmin, dmax, colors)
        lut[i] = (c.red(), c.green(), c.blue())
    state._wf_lut = lut
    state._wf_lut_key = (dmin, dmax)


def _wf_paint_row_into(state: "_PlotState", row_db: np.ndarray, dst_row: np.ndarray):
    """把一行 dB 频谱（长度 num_bins）映射到画布一行 (w,4) 的 RGB。

    x→频率走 _px_to_freq（含 view_offset_hz），与上方谱面同一把尺子；
    平移后频率落在采集带宽外的像素填背景色，不把边界数据拉伸。
    """
    w = dst_row.shape[0]
    n = len(row_db)
    gen = state.panel.generator
    view_center = _view_center_hz(state)
    span = gen.span_hz
    sr = gen.sample_rate_hz
    # 像素中心 → 频率 → bin；与 _freq_at_x 的像素含义严格一致。
    x = np.arange(w, dtype=np.float64) + 0.5
    freq = _px_to_freq(x, w, view_center, span)
    f0 = gen.center_freq_hz - sr / 2.0
    idx = np.floor((freq - f0) / sr * n).astype(np.int64)
    valid = (idx >= 0) & (idx < n)
    # NaN = 未采到：该像素画背景色（不是色图最低档，避免与真实弱信号混淆）。
    raw = np.where(valid, row_db[np.clip(idx, 0, max(0, n - 1))], np.nan)
    ok = np.isfinite(raw)
    vals = np.where(ok, raw, state.db_min)
    dmin, dmax = state.db_min, state.db_max
    t = np.clip((vals - dmin) / (dmax - dmin + 1e-9), 0.0, 1.0)
    li = (t * 255.0).astype(np.int64)
    bg = QColor(state.panel.color_bg)
    dst_row[:, 0:3] = (bg.red(), bg.green(), bg.blue())
    dst_row[ok, 0:3] = state._wf_lut[li[ok]]
    dst_row[:, 3] = 255


def _wf_ensure_canvas(state: "_PlotState", w: int, h: int) -> bool:
    """确保持久瀑布画布缓冲尺寸匹配；尺寸变化时重建为空白。"""
    if w <= 0 or h <= 0:
        state._wf_canvas = None
        return False
    if (state._wf_cw == w and state._wf_ch == h
            and state._wf_buf is not None and state._wf_canvas is not None):
        return True
    bg = QColor(state.panel.color_bg)
    buf = np.empty((h, w, 4), dtype=np.uint8)
    buf[:, :, 0] = bg.red()
    buf[:, :, 1] = bg.green()
    buf[:, :, 2] = bg.blue()
    buf[:, :, 3] = 255
    state._wf_buf = buf
    state._wf_cw = w
    state._wf_ch = h
    state._wf_consumed = 0   # 尺寸变化后从环形缓冲全量重建
    # QImage 直接包装 numpy 缓冲（不拷贝）；buf 由 state 持有，保证内存存活。
    # 需 C 连续内存；Format_RGBA8888 字节序为 R,G,B,A，与 buf 通道一致。
    state._wf_canvas = QImage(
        memoryview(buf), w, h, w * 4, QImage.Format.Format_RGBA8888)
    return True


def _draw_waterfall(painter, rect, state):
    """持久瀑布画布增量绘制（对标 SDR++ waterfall.cpp:898 pushFFT 行滚动）。

    每帧只做：把新行 scroll 上移一行 + 写新行到底部，再整图 drawImage。
    无数据时画布为空白背景（不画假噪声）。
    """
    gen = state.panel.generator
    w = int(rect.width())
    h = int(rect.height())
    if not _wf_ensure_canvas(state, w, h):
        return
    if state._wf_lut is None or state._wf_lut_key != (state.db_min, state.db_max):
        _wf_build_lut(state)

    buf = state._wf_buf
    bg = QColor(state.panel.color_bg)
    total = gen.wf_rows_written

    # 历史回看：滚轮在瀑布上滚到旧行时，按绝对窗口重绘，不再增量跟随最新。
    # 回看窗口固定（新数据继续入环形缓冲，不抢走用户正在看的旧行）。
    hist_end = getattr(state, "wf_history_end", None)
    if hist_end is not None:
        key = (int(hist_end), h, w, float(state.db_min), float(state.db_max))
        if getattr(state, "_wf_hist_key", None) == key:
            painter.drawImage(rect, state._wf_canvas)
            return
        rows = gen.wf_rows_at(int(hist_end), h)
        buf[:, :, 0] = bg.red()
        buf[:, :, 1] = bg.green()
        buf[:, :, 2] = bg.blue()
        buf[:, :, 3] = 255
        m = len(rows)
        for i in range(m):
            _wf_paint_row_into(state, rows[i], buf[h - m + i])
        state._wf_hist_key = key
        painter.drawImage(rect, state._wf_canvas)
        return

    # x→频率映射变化（Ctrl 平移 / 改调谐中心 / 换采样率）时，旧画布是按老
    # 映射画成的，必须按环形缓冲全量重绘，否则瀑布与上方光谱会横向错开。
    #
    # 2026-11-06：键里补上 dB 量程。此前只含几何与频率映射，拖动 Min/Max
    # 后 LUT 虽已重建，但已画进 buf 的历史行仍是旧配色 —— 瀑布上出现一条
    # 永久的水平色带断层，直到平移/改深度才消失。回看键（_wf_hist_key）与
    # 改配色处（_on_palette_changed）本来就是含配色的，这里属于漏项。
    map_key = (round(_view_center_hz(state), 3),
               float(gen.span_hz), float(gen.sample_rate_hz),
               int(gen.num_bins), w, h,
               float(state.db_min), float(state.db_max))
    if state._wf_map_key != map_key:
        state._wf_map_key = map_key
        buf[:, :, 0] = bg.red()
        buf[:, :, 1] = bg.green()
        buf[:, :, 2] = bg.blue()
        buf[:, :, 3] = 255
        m = min(total, h)
        if m > 0:
            rows = gen.wf_latest_rows(m)
            for i in range(len(rows)):
                _wf_paint_row_into(state, rows[i], buf[h - len(rows) + i])
        state._wf_consumed = total

    consumed = state._wf_consumed

    # 检测复位（clear_data() 后 total 归零）：清空画布回到空白。
    if total < consumed:
        buf[:, :, 0] = bg.red()
        buf[:, :, 1] = bg.green()
        buf[:, :, 2] = bg.blue()
        buf[:, :, 3] = 255
        consumed = 0
        state._wf_consumed = 0

    pending = total - consumed
    if pending > 0:
        if pending >= h:
            # 积压一屏以上：直接全量重建（取最新 h 行，旧→新，贴底）。
            rows = gen.wf_latest_rows(h)
            m = len(rows)
            buf[:, :, 0] = bg.red()
            buf[:, :, 1] = bg.green()
            buf[:, :, 2] = bg.blue()
            buf[:, :, 3] = 255
            for i in range(m):
                _wf_paint_row_into(state, rows[i], buf[h - m + i])
            state._wf_consumed = total
        else:
            # 增量：旧内容一次上移 k 行（一次 memmove），再把 k 个新行
            # （旧→新）依次写底部，避免逐行整图复制。
            k = int(pending)
            rows = gen.wf_latest_rows(k)
            buf[0:h - k, :, :] = buf[k:h, :, :]
            for j, row in enumerate(rows):
                _wf_paint_row_into(state, row, buf[h - k + j, :, :])
            state._wf_consumed = total

    painter.drawImage(rect, state._wf_canvas)


def _axis_pen(text_color) -> QPen:
    """刻度字用半透明文字色：SDR++ 的轴字是"能读但不抢"的灰，不是纯白。"""
    c = QColor(text_color)
    c.setAlpha(175)
    return QPen(c, 1)


def _draw_freq_scale(painter, rect, view_center_hz: float, span_hz: float,
                     text_color):
    painter.setPen(_axis_pen(text_color))
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    fm = painter.fontMetrics()
    # 1/2/5×10ⁿ 阶梯 + 按量程自动单位（SDR++ waterfall.cpp:40-62）。
    # 单位只在轴右端画一次；刻度的可用宽度扣掉单位区，避免标签贴上去。
    unit_w = fm.horizontalAdvance("MHz")   # 取最长单位宽度做统一预留
    reserve = unit_w + 8.0 + _AXIS_RIGHT_PAD
    _step, ticks, labels, unit = _freq_scale_ticks(
        view_center_hz, span_hz, rect.width(), fm.horizontalAdvance,
        reserve_px=reserve)
    right_bound = rect.x() + rect.width() - reserve
    for tick_hz, s in zip(ticks, labels):
        # 刻度按真实频率落位（不是等距），保证与谱线/网格严格对齐。
        x = rect.x() + (tick_hz - view_center_hz) / span_hz * rect.width() \
            + rect.width() / 2.0
        tw = fm.horizontalAdvance(s)
        tx = max(rect.x(), min(x - tw / 2.0, right_bound - tw))
        painter.drawText(QPointF(tx, rect.y() + rect.height() + 14), s)
    # 自动单位（kHz / MHz / GHz）只标一次，右端对齐（留 _AXIS_RIGHT_PAD，
    # 避免与右侧滑杆列的「最小」标签贴成一串）。
    uw = fm.horizontalAdvance(unit)
    painter.drawText(
        QPointF(rect.x() + rect.width() - uw - _AXIS_RIGHT_PAD,
                rect.y() + rect.height() + 14),
        unit)


def _draw_db_scale(painter, rect, state, text_color):
    painter.setPen(_axis_pen(text_color))
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    fm = painter.fontMetrics()
    # dB 轴同理：1/2/5×10ⁿ 阶梯（默认 -80…-20 会选出 20 dB 步长，
    # 与原 5 等分位置一致；量程变了也不会出现非整数刻度）。
    _step, dbs = _nice_ticks(state.db_min, state.db_max, 5)
    if not dbs:
        dbs = [state.db_min, state.db_max]
    for db in dbs:
        y = _db_to_y(rect, db, state.db_min, state.db_max)
        # 顶端/底端刻度都贴边：baseline 固定在 y+4 时，「0 dBFS」上半截
        # 被谱面上缘切掉、「-60 dBFS」下半截掉进频率轴（2026-11-11 星象仪
        # 紧凑条实拍）。按 ascent/descent 夹住 baseline，整行字落在谱内。
        by = min(max(y + 4.0, rect.y() + fm.ascent() + 1.0),
                 rect.y() + rect.height() - fm.descent() - 1.0)
        painter.drawText(QPointF(rect.x() + 4, by), _fmt_axis_tick(db))
    # 拖动时读数跟手：把当前量程直接画在轴区右上，位置不随值移动，
    # 内容随每一帧重算（几何断言 + 离屏截图可验，不靠肉眼）。
    if getattr(state, "db_drag_active", False):
        span_now = state.db_max - state.db_min
        txt = (f"{state.db_min:.0f} … {state.db_max:.0f} {_DBFS_UNIT}"
               f" · 跨度 {span_now:.0f} dB（相对）")
        tw = fm.horizontalAdvance(txt)
        tx = max(rect.x() + 4, rect.x() + rect.width() - tw - 4)
        painter.drawText(QPointF(tx, rect.y() + 16), txt)
        # Ctrl 缩放（以按下处 dB 为锚）时，同一个读数再贴光标弹一次：
        # 文本与轴区读数完全同源同格式，不另造第二套。
        if getattr(state, "db_drag_zoom", False):
            y = float(getattr(state, "db_drag_cursor_y", 0.0))
            y = max(rect.y() + 10.0,
                    min(y, rect.y() + rect.height() - 4.0))
            bx = rect.x() + 44.0
            bw = tw + 12.0
            bh = fm.height() + 4.0
            painter.setPen(QPen(QColor(PAL_GRID), 1))
            painter.setBrush(QBrush(QColor(state.panel.color_bg)))
            painter.drawRoundedRect(
                QRectF(bx, y - bh / 2.0, bw, bh), 4.0, 4.0)
            painter.setPen(QPen(text_color, 1))
            painter.drawText(QPointF(bx + 6.0, y + fm.ascent() - 2.0), txt)


# ============================================================================
# 频谱面板（容器：工具栏 + 绘图表面）—— main_window 直接操作的对象
# ============================================================================

class SpectrumPanel(QWidget):
    """对外统一接口。包含参数工具栏与实际绘图表面（QPainter / OpenGL）。"""

    freq_changed = Signal(float)   # MHz
    # 多 VFO 交互信号：频谱组件只动 vfo_manager 数据层并发出信号，
    # 主窗口接收后绑定 DSP / 更新控制面板 / 切换主听。
    vfo_created = Signal(str, float, float)   # vfo_id, center_hz, bw_hz
    vfo_moved = Signal(str, float)            # vfo_id, center_hz
    vfo_bw_changed = Signal(str, float)       # vfo_id, bw_hz
    vfo_selected = Signal(str)                # vfo_id（用户点击设为主听）
    vfo_removed = Signal(str)                 # vfo_id（用户右键删除）
    mode_hotkey_requested = Signal(str)       # 键盘单键切解调（gqrx A/N/W/S/C）
    bookmark_activated = Signal(float)        # 点击书签竖旗 (freq_hz) → 主窗口调谐

    def __init__(self, parent=None, prefer_opengl: bool = False):
        super().__init__(parent)
        self.generator = SpectrumDataGenerator(num_bins=512)

        # 多 VFO 管理器（纯数据层）：拖拽 VFO 框时移动 current / 新建 VFO。
        # 三态焦点 active_context/current/visual 按悬停/点击确认语义维护。
        self.vfo_manager = VfoManager() if VfoManager is not None else None

        # 配色（默认低饱和；set_theme_colors 可覆盖 bg/grid/text/line）
        self.color_bg = PAL_BG
        self.color_grid = PAL_GRID
        self.color_text = PAL_GRID
        self.color_line = PAL_LINE

        self._state = _PlotState(self)
        # 紧凑形态（星象仪底部条）标志：见 set_compact。
        self._compact = False
        # 上一帧是否已有真实数据：用于「无数据 → 有数据」时补画首帧。
        # 重绘唯一驱动是面板定时器（_plot._timer），但首帧/重连首帧可能还没
        # 排到定时器上，靠这个标记保证首屏一定画出来（见 _refresh_status_labels）。
        self._label_has_data = False
        # 当前性能档索引（0 高 / 1 均衡 / 2 低）：送绘根数的上限由它决定
        # （见 _apply_render_bins；性能下拉创建时默认也是第 0 项）。
        self._perf_level = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # 工具栏装进独立 QWidget：布局对象本身没有 minimumSizeHint/geometry，
        # 而「窄档 331px 下不许裁切」只能用控件几何反证（见
        # tests/test_spectrum_slider_overflow.py 反证一）。
        self._toolbar = QWidget()
        self._toolbar.setObjectName("spectrumToolbar")
        self._toolbar.setLayout(self._build_toolbar())
        root.addWidget(self._toolbar)

        # SDR++ 风格：垂直 QSplitter 上下分屏 ——
        #   上方 = FFT 频谱曲线（承载全部调谐 / VFO 拖拽交互）
        #   下方 = 瀑布图（持久增量画布）
        # 分隔条可拖拽调整两区高度；stretch 3:2，默认 300:200。
        self._fft_plot = SpectrumCurveWidget(self._state)
        self._wf_plot = WaterfallWidget(self._state)
        # 向后兼容别名：main_window / 测试直接用 panel._plot.update()。
        self._plot = self._fft_plot

        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setContentsMargins(0, 0, 0, 0)
        # handle 厚度统一取自 tokens（4px）；QSS 里也写了，这里显式兜底。
        try:
            self._splitter.setHandleWidth(int(_tokens().SIZE["splitter_handle"]))
        except Exception:
            pass
        self._splitter.addWidget(self._fft_plot)
        self._splitter.addWidget(self._wf_plot)
        # 弹性分区：stretch 因子 3:2（对应 tokens RATIO spectrum_fft/spectrum_wf），
        # 不写死 setSizes([固定像素])；最小高度兜底防止两区被拖塌。
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._fft_plot.setMinimumHeight(120)
        # 瀑布地板 60→40（裁定 B，2026-11-11）：150% 档面板高度余量 6→26px，
        # 代价只在贴地板时兑现（可见历史 60→40 行 = 6s→4s，1 行 = 1px）。
        # 数据与两方案取舍见 docs/research/2026-11-11-gap-layout.md §3.9。
        self._wf_plot.setMinimumHeight(40)

        # 右侧滑杆列已迁到工具栏「显示」弹出层（2026-11-11）：常驻会白占 30px
        # 宽、把面板最小高撑到 247px，窄档 331px 下绘图区被挤。滑杆对象与信号
        # 保持不变，只是换了父容器（见 _build_db_sliders）。
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)
        body.addWidget(self._splitter, stretch=1)
        root.addLayout(body, stretch=1)

        # 初始：未连接 → 控件置灰
        self._connected = False
        self._set_controls_enabled(False)

    def _redraw(self):
        """同时重绘 FFT 曲线与瀑布图。"""
        self._fft_plot.update()
        self._wf_plot.update()

    # ------------------------------------------------------------------ 宿主适配
    def set_offline_hint(self, text: Optional[str]) -> None:
        """设置「未连接」时谱面第二行提示；None/空串恢复默认文案。

        星象仪全屏窗里没有工具栏，默认的「点击工具栏…」在那里不可执行，
        由宿主换成「按 Esc 回主界面…」。提示只在无数据时出现，有数据时
        改它无需重绘；无数据才重绘，避免每帧白刷。
        """
        hint = (text or "").strip() or OFFLINE_HINT_DEFAULT
        if hint == self._state.offline_hint:
            return
        self._state.offline_hint = hint
        if not self.generator.has_data():
            self._fft_plot.update()

    def set_compact(self, on: bool) -> None:
        """紧凑形态（星象仪底部 180px 条）：压低谱面/瀑布分区下限。

        三根量程滑杆已迁出面板（工具栏「显示」弹出层，见 _build_db_sliders），
        面板最小高不再包含滑杆列；但非紧凑的 120+60 两区加上工具栏仍装不进
        180px 槽，所以紧凑形态把分区下限降到 80/40 并切成 [110, 50]：
        否则 QSplitter 按各自最小高溢出，谱面与瀑布互相覆盖、频率轴被压进
        瀑布（截图可判）。缩放改用 Ctrl+滚轮/双指捏合。

        只改布局，不动数据与控制器。
        """
        on = bool(on)
        if on == self._compact:
            return
        self._compact = on
        if on:
            self._fft_plot.setMinimumHeight(80)
            self._wf_plot.setMinimumHeight(40)
            self._splitter.setSizes([110, 50])
        else:
            self._fft_plot.setMinimumHeight(120)
            self._wf_plot.setMinimumHeight(40)
            # 不恢复像素值：分区拉伸比是 3:2（见 __init__），回到 IDE 后
            # 面板变高，QSplitter 按拉伸比重新分配；硬塞回紧凑时的 [110,50]
            # 反而会把两区钉在贴底的位置。

    # ------------------------------------------------------------------ 工具栏
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 0, 4, 0)
        bar.setSpacing(6)

        # 窗函数 / FFT 点数也不再常驻工具栏：它们各自要 100+px，把频谱面板的
        # 最小宽顶到 364px，而 1000 宽窗口下左栏只有约 340px——频谱被直接
        # 裁掉右边（用户 2026-11-10「默认布局直接冲上，显示不全」）。
        # 控件本体与信号不变，只是换成在「更多」菜单里出现。
        more_rows: list[QWidget] = []

        def _row(*widgets: QWidget) -> QWidget:
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(8, 3, 8, 3)
            hl.setSpacing(6)
            for w_ in widgets:
                hl.addWidget(w_)
            hl.addStretch(1)
            return holder

        # 窗函数（SDR++ main_window.cpp:91 默认 NUTTALL）
        self._win_combo = QComboBox()
        self._win_combo.addItems(SpectrumDataGenerator.WINDOWS)
        self._win_combo.setCurrentText(self.generator.window_name)
        self._win_combo.currentTextChanged.connect(self.generator.set_window)
        more_rows.append(_row(QLabel("窗:"), self._win_combo))

        # FFT 点数
        self._fft_combo = QComboBox()
        self._fft_combo.addItems([str(s) for s in SpectrumDataGenerator.FFT_SIZES])
        self._fft_combo.setCurrentText("2048")
        self._fft_combo.setToolTip(
            "FFT 点数 = 计算分辨率（RBW/峰值测量精度）。\n"
            "它不是屏幕上的列数：谱线会按画布像素宽与性能档 max 抽取后再画。")
        self._fft_combo.currentTextChanged.connect(self._on_fft_size_changed)
        # 真实口径标注：「计算 N / 显示 M」。M 是实际送绘根数（generator.num_bins），
        # 用户能直接看出 4096 点并不等于 4096 列（2026-11-11 用户裁决：不许让
        # 用户误以为看到的是 4096 的显示分辨率）。
        self._fft_bin_label = QLabel()
        self._fft_bin_label.setObjectName("fftBinTruthLabel")
        self._fft_bin_label.setToolTip(
            "计算 = FFT 点数（决定 RBW 与峰值测量精度）；\n"
            "显示 = 实际送绘的谱线根数（由画布像素宽与性能档决定，max 抽取）。\n"
            "两者不同不代表设置没生效：仍按 N 点计算，只是画布放不下那么多列。")
        self._update_fft_bin_label()
        more_rows.append(_row(QLabel("FFT:"), self._fft_combo,
                              self._fft_bin_label))

        avg_label = QLabel("平均:")
        self._avg_combo = QComboBox()
        self._avg_combo.addItems([str(a) for a in SpectrumDataGenerator.AVG_FRAMES])
        self._avg_combo.setCurrentText("1")
        self._avg_combo.currentTextChanged.connect(
            lambda t: self.generator.set_avg_frames(int(t)))
        more_rows.append(_row(avg_label, self._avg_combo))

        # 性能模式（高/均衡/低）：切换刷新间隔与送绘根数上限，不动 FFT 点数
        perf_label = QLabel("性能:")
        self._perf_combo = QComboBox()
        self._perf_combo.addItems(["高", "均衡", "低"])
        self._perf_combo.setCurrentIndex(0)
        self._perf_combo.currentIndexChanged.connect(self.set_performance_mode)
        more_rows.append(_row(perf_label, self._perf_combo))

        # 瀑布调色板（默认 SDR++ Turbo 色表 / 旧低饱和色带）
        palette_label = QLabel("调色板:")
        self._palette_combo = QComboBox()
        self._palette_combo.addItems(["SDR++ 经典", "低饱和"])
        self._palette_combo.currentIndexChanged.connect(self._on_palette_changed)
        more_rows.append(_row(palette_label, self._palette_combo))

        # 瀑布时间平滑（IIR，仅影响瀑布显示）
        self._wf_smooth_chk = QCheckBox("瀑布平滑")
        self._wf_smooth_chk.setChecked(False)
        self._wf_smooth_chk.toggled.connect(self._on_wf_smooth_toggled)

        self._peak_chk = QCheckBox("峰值")
        self._peak_chk.setChecked(True)
        self._peak_chk.toggled.connect(
            lambda on: setattr(self._state, "show_peaks", bool(on)))

        # Max/Min hold 包络（SDR++ maxHold/minHold 复选框签名）
        self._maxhold_chk = QCheckBox("Max保持")
        self._maxhold_chk.setToolTip(
            "Max hold：逐 bin 保留历史最高电平包络（抓瞬时 burst/峰值轮廓）")
        self._maxhold_chk.toggled.connect(self.generator.set_max_hold)
        self._minhold_chk = QCheckBox("Min保持")
        self._minhold_chk.setToolTip(
            "Min hold：逐 bin 保留历史最低电平包络（看噪声底下沿/底噪漂移）")
        self._minhold_chk.toggled.connect(self.generator.set_min_hold)

        # 书签竖旗开关（gqrx 书签标签；标签本体由主窗口 set_bookmarks 灌入）
        self._bookmark_chk = QCheckBox("书签")
        self._bookmark_chk.setChecked(True)
        self._bookmark_chk.setToolTip(
            "在谱面顶部显示书签竖旗；点击药丸直接调到该书签频率")
        self._bookmark_chk.toggled.connect(
            lambda on: (setattr(self._state, "show_bookmarks", bool(on)),
                        self._redraw()))

        # 频段彩带开关（SDR++ bandplan 仿妆；条目在 desktop/bandplan.json）
        self._bandplan_chk = QCheckBox("频段")
        self._bandplan_chk.setChecked(True)
        self._bandplan_chk.setToolTip(
            "在谱面上显示频段彩带（短波/广播/航空/业余/卫星/海事）与名称；"
            "悬停提亮看详情、点击常驻描边；条目可在 bandplan.json 配置")
        self._bandplan_chk.toggled.connect(
            lambda on: (setattr(self._state, "show_bandplan", bool(on)),
                        self._redraw()))

        # 自动量程（SDR++ autoRange 语义）：底噪锚在色带冷端，强信号烧到红端。
        # 动过任一 dB 滑杆/轴上拖动后自动关掉（手动优先，不跟用户抢量程）。
        self._auto_db_chk = QCheckBox("自动量程")
        self._auto_db_chk.setChecked(True)
        self._auto_db_chk.setToolTip(
            "跟着底噪自动平移 dB 显示量程（跨度不变）：底噪落色带冷端、"
            "强信号到红端。手动拖过 dB 滑杆/轴后即转手动，勾回来可恢复")
        self._auto_db_chk.toggled.connect(self._on_auto_db_toggled)

        # 每行 3 个开关，菜单总高控制在 ~3 行（窄屏纵向也省）
        more_rows.append(_row(self._wf_smooth_chk, self._peak_chk,
                              self._maxhold_chk))
        more_rows.append(_row(self._minhold_chk, self._bookmark_chk,
                              self._bandplan_chk))
        more_rows.append(_row(self._auto_db_chk))

        gate_label = QLabel("峰值门(相对本帧中位):")
        gate_label.setObjectName("peakGateLabel")
        gate_tip = (
            "门限 = 本帧全部有限 bin 的中位数 + 该值（不是 dBFS 绝对电平）。\n"
            "示例：中位数 −72 dBFS → 门限 −66 dBFS（+6.0 dB）。")
        gate_label.setToolTip(gate_tip)
        self._peak_spin = QDoubleSpinBox()
        self._peak_spin.setObjectName("peakGateSpin")
        self._peak_spin.setRange(1.0, 30.0)
        self._peak_spin.setValue(self._state.peak_rel_db)
        self._peak_spin.setSingleStep(1.0)
        self._peak_spin.setDecimals(1)   # U-04 文案口径：+6.0 dB（一位小数）
        self._peak_spin.setPrefix("+")
        self._peak_spin.setSuffix(" dB")
        self._peak_spin.setToolTip(gate_tip)
        self._peak_spin.valueChanged.connect(
            lambda v: setattr(self._state, "peak_rel_db", float(v)))
        more_rows.append(_row(gate_label, self._peak_spin))

        # 调谐模式（SDR++ 调谐模式按钮同原语；gqrx 中键 / SDR# Center 亦是）
        self._tune_mode_btn = QPushButton("调谐:中心")
        self._tune_mode_btn.setCheckable(True)
        self._tune_mode_btn.setChecked(True)
        self._tune_mode_btn.setToolTip(
            "中心模式：点击谱面重调硬件中心频率（画面跟过去）\n"
            "VFO 模式：点击只移动当前 VFO，画面不动（瀑布保留多信号轨迹）")
        self._tune_mode_btn.toggled.connect(self._on_tune_mode_toggled)
        more_rows.append(_row(self._tune_mode_btn))

        # 「更多 ⋯」入口：工具栏只剩这一个按钮（约 70px），全部显示参数
        # 都收在这里，频谱面板才能缩到左栏实际宽度而不被裁。
        self._more_btn = QToolButton()
        self._more_btn.setText("更多")
        self._more_btn.setObjectName("spectrumMoreButton")
        self._more_btn.setPopupMode(QToolButton.InstantPopup)
        self._more_btn.setToolTip(
            "频谱显示选项：窗 / FFT 点数 / 平均 / 性能 / 调色板 / 瀑布平滑 / "
            "峰值 / Max·Min 保持 / 书签 / 频段 / 峰值门 / 调谐模式")
        more_menu = QMenu(self._more_btn)
        more_menu.setToolTipsVisible(True)
        for row in more_rows:
            act = QWidgetAction(more_menu)
            act.setDefaultWidget(row)
            more_menu.addAction(act)
        self._more_btn.setMenu(more_menu)
        self._more_menu = more_menu
        bar.addWidget(self._more_btn)

        # 「显示」入口：三根量程滑杆（缩放 / 上限 / 下限）收进弹出层。
        # 常驻竖排时它们白占 30px 宽、把面板最小高撑到 247px（窄档 331px 下
        # 绘图区被挤，303 档还会裁掉末根滑杆）；收进弹层后工具栏最小宽只从
        # 约 59px 涨到约 120px，绘图区宽度与高度都不再被它影响。
        # 点击一次弹出 + 拖动一次即生效（与常驻滑杆相比只多「点开」这一步）。
        self._display_btn = QToolButton()
        self._display_btn.setText("显示")
        self._display_btn.setObjectName("spectrumDisplayButton")
        self._display_btn.setPopupMode(QToolButton.InstantPopup)
        self._display_btn.setToolTip(
            "显示量程与缩放：缩放跨度 / 显示上限 / 显示下限。\n"
            "三者都只改怎么看，不改硬件采样率与接收增益。")
        self._db_slider_col = self._build_db_sliders()
        display_menu = QMenu(self._display_btn)
        display_menu.setToolTipsVisible(True)
        display_action = QWidgetAction(display_menu)
        display_action.setDefaultWidget(self._db_slider_col)
        display_menu.addAction(display_action)
        self._display_btn.setMenu(display_menu)
        self._display_menu = display_menu
        bar.addWidget(self._display_btn)

        bar.addStretch(1)

        # 顶栏不放任何实时读数（2026-11-10 用户裁决）：
        # SNR / 跨度 / RBW / 瀑布秒数每秒跳几十次，没人读得过来，SDR++ 顶上
        # 也没有这些数字。频率看谱面刻度与主窗顶栏；电平看左边 dB 轴；
        # 细节只在鼠标悬停时临时出现（_draw_hover_readout）。
        return bar

    # ------------------------------------------------------------------ dB 滑杆
    def _build_db_sliders(self) -> QWidget:
        """三根横滑杆：缩放 / 上限 / 下限（装在工具栏「显示」弹出层里）。

        语义沿用 SDR++ 主窗右侧竖排（main_window.cpp:635-656）：
          - 缩放：收窄显示跨度（起始/截止频率靠拢），不碰硬件采样率
          - 上限 / 下限：纵轴 dBFS 量程；拖动时的 10 dB 最小间隔由
            _on_db_max_changed / _on_db_min_changed 保证（同 main_window.cpp:639/652）
        方向：左端 = 最小值、右端 = 最大值。

        标签只写「缩放/上限/下限」，**不写数值**：这三根每秒都可能变，
        挂在界面上就是噪声；当前位置由滑杆拇指本身表达。
        """
        col = QWidget()
        col.setObjectName("dbSliderCol")
        lay = QVBoxLayout(col)
        lay.setContentsMargins(8, 2, 8, 4)
        lay.setSpacing(4)

        def _add(caption: str, tip: str, slider: QSlider) -> None:
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(caption)
            lbl.setObjectName("dbCapLabel")
            lbl.setToolTip(tip)
            lbl.setMinimumWidth(28)
            slider.setMinimumWidth(_SLIDER_MIN_W)
            slider.setToolTip(tip)
            row.addWidget(lbl)
            row.addWidget(slider, stretch=1)
            lay.addLayout(row)

        # ① 缩放：显示跨度（起始/截止频率区间）
        self._span_slider = QSlider(Qt.Horizontal)
        self._span_slider.setRange(0, _SPAN_SLIDER_STEPS)
        self._span_slider.setValue(0)
        self._span_slider.valueChanged.connect(self._on_span_slider_changed)
        _add("缩放",
             "显示跨度：右推收窄（放大细节），左端回到全带宽。\n"
             "只改变显示范围，不改变硬件采样率。",
             self._span_slider)

        # ② 上限：纵轴上限（dBFS）
        self._db_max_slider = QSlider(Qt.Horizontal)
        self._db_max_slider.setRange(-160, 0)
        self._db_max_slider.setValue(int(self._state.db_max))
        self._db_max_slider.valueChanged.connect(self._on_db_max_changed)
        _add("上限",
             "显示上限（dBFS，纵轴顶部）：右推=整个谱面下移，左推=放大弱信号。\n"
             "它只改显示刻度，不改增益。",
             self._db_max_slider)

        # ③ 下限：纵轴下限（dBFS）
        self._db_min_slider = QSlider(Qt.Horizontal)
        self._db_min_slider.setRange(-160, 0)
        self._db_min_slider.setValue(int(self._state.db_min))
        self._db_min_slider.valueChanged.connect(self._on_db_min_changed)
        _add("下限",
             "显示下限（dBFS，纵轴底部）：右推=隐藏弱信号，左推=弱信号也显示。\n"
             "它只改显示刻度，不改增益。",
             self._db_min_slider)

        return col

    # ------------------------------------------------------------------ 缩放滑杆
    def _span_from_slider(self, value: int) -> float:
        """滑杆整数 → 显示跨度（对数刻度，0=全带宽，1000=48 kHz）。"""
        sr = max(_SPAN_MIN_HZ, float(self.generator.sample_rate_hz))
        f = max(0.0, min(1.0, float(value) / _SPAN_SLIDER_STEPS))
        return sr * (_SPAN_MIN_HZ / sr) ** f

    def _slider_from_span(self, span_hz: float) -> int:
        """显示跨度 → 滑杆整数（与 ``_span_from_slider`` 互逆）。"""
        sr = max(_SPAN_MIN_HZ, float(self.generator.sample_rate_hz))
        span = max(_SPAN_MIN_HZ, min(sr, float(span_hz)))
        if sr <= _SPAN_MIN_HZ * 1.001:
            return 0
        ratio = math.log(span / sr) / math.log(_SPAN_MIN_HZ / sr)
        return int(round(max(0.0, min(1.0, ratio)) * _SPAN_SLIDER_STEPS))

    @Slot(int)
    def _on_span_slider_changed(self, value: int) -> None:
        if getattr(self, "_syncing_span_slider", False):
            return
        span = self._span_from_slider(value)
        if abs(span - self.generator.span_hz) < 1.0:
            return
        self.generator.set_span(span)
        # 保持视图中心不动（缩放是"拉近看"，不是"换台"）。
        self._redraw()

    def sync_span_slider(self) -> None:
        """外部改跨度（Ctrl+滚轮/捏合/换采样率）后，把滑杆拉到对应位置。"""
        slider = getattr(self, "_span_slider", None)
        if slider is None:
            return
        self._syncing_span_slider = True
        try:
            slider.setValue(self._slider_from_span(self.generator.span_hz))
        finally:
            self._syncing_span_slider = False

    def apply_db_range(self, db_min: float, db_max: float):
        """dB 量程的唯一状态源：滑杆、轴上拖动、外部调用都走这里。

        先把值规范成整数（滑杆本身是整数刻度，状态若留小数就会出现
        "状态一套、滑杆一套"的假量程），再同步状态与两根滑杆。
        """
        # 手动改量程 = 自动量程让位（勾选框同步取消，避免"勾着却在手调"）。
        self._state.auto_db_range = False
        chk = getattr(self, "_auto_db_chk", None)
        if chk is not None and chk.isChecked():
            chk.blockSignals(True)
            chk.setChecked(False)
            chk.blockSignals(False)
        lo = max(_DB_AXIS_MIN, min(float(db_min), _DB_AXIS_MAX))
        hi = max(_DB_AXIS_MIN, min(float(db_max), _DB_AXIS_MAX))
        if hi - lo < _DB_MIN_SPAN:        # SDR++ main_window.cpp:639 最小间隔 10
            hi = lo + _DB_MIN_SPAN
            if hi > _DB_AXIS_MAX:
                hi = _DB_AXIS_MAX
                lo = _DB_AXIS_MAX - _DB_MIN_SPAN
        lo, hi = float(round(lo)), float(round(hi))
        self._state.db_min = lo
        self._state.db_max = hi
        # 共享源：状态变了滑杆必须同帧跟上（信号阻断防回调环）。
        for slider, val in ((self._db_max_slider, hi),
                            (self._db_min_slider, lo)):
            slider.blockSignals(True)
            slider.setValue(int(val))
            slider.blockSignals(False)
        # 标签只写「缩放/上限/下限」，位置由滑杆拇指表达（不挂实时数值）。
        self._redraw()

    def db_range(self) -> Tuple[float, float]:
        """当前 dB 量程 (min, max)（状态即唯一事实源）。"""
        return (float(self._state.db_min), float(self._state.db_max))

    def _on_db_max_changed(self, val: int):
        # SDR++ main_window.cpp:639: fftMax = max(fftMax, fftMin + 10)
        self.apply_db_range(self._state.db_min, int(val))

    def _on_db_min_changed(self, val: int):
        # SDR++ main_window.cpp:652: fftMin = min(fftMax - 10, fftMin)
        self.apply_db_range(int(val), self._state.db_max)

    def _on_auto_db_toggled(self, on: bool):
        """自动量程开关：勾回来下一帧就按底噪重新锚定（不立刻改量程）。"""
        self._state.auto_db_range = bool(on)
        self._redraw()

    def _set_controls_enabled(self, on: bool):
        for w_ in (self._win_combo, self._fft_combo, self._avg_combo,
                   self._perf_combo, self._palette_combo, self._wf_smooth_chk,
                   self._peak_chk, self._peak_spin, self._tune_mode_btn,
                   self._maxhold_chk, self._minhold_chk, self._bookmark_chk,
                   self._bandplan_chk, self._auto_db_chk):
            w_.setEnabled(on)

    def _on_tune_mode_toggled(self, checked: bool):
        self._state.tune_mode = "center" if checked else "normal"
        self._tune_mode_btn.setText("调谐:中心" if checked else "调谐:VFO")

    def _on_fft_size_changed(self, text: str):
        """FFT 点数变更：改计算点数，并按当前画布重算送绘根数。"""
        self.generator.set_fft_size(int(text))
        self._apply_render_bins()
        self._update_fft_bin_label()

    def _update_fft_bin_label(self):
        """把真实口径「计算 N / 显示 M」写进菜单行（M = 实际送绘根数）。"""
        lbl = getattr(self, "_fft_bin_label", None)
        if lbl is None:
            return
        lbl.setText(
            f"计算 {self.generator.fft_size} / 显示 {self.generator.num_bins}")

    # ------------------------------------------------------------------ 性能模式
    # level 0=高 / 1=均衡 / 2=低：送绘根数上限、刷新间隔档位。
    # 第一列的 FFT 点数只是档位设计参考值，**不再参与下发**：FFT 的单一
    # 真值源是设置项 fft_size（见 set_performance_mode 注释）。
    # 第二列是**上限**而不是固定值：实际根数 = min(FFT 点数, 画布像素宽, 上限)，
    # 见 _bins_for_width / _apply_render_bins（2026-11-11 用户裁决：画布放不下
    # 的谱线不许假装画出来，也不许把 4096 点全送出去）。
    _PERF_LEVELS = (
        # (fft_size, bins_cap, refresh_ms)
        (2048, 2048, 50),
        (1024, 512, 66),
        # 低档（默认 eco）：根数上限保持 256，不因画布变宽而加倍，守住省电预算。
        (1024, 256, 100),
    )
    #: 送绘根数的合法档：必须整除 FFT 点数，才能走 _resample_max 的
    #: reshape+max 快速路径（非整除会退回逐 bin 循环，明显更慢）。
    _BINS_STEPS = (64, 128, 256, 512, 1024, 2048, 4096)

    @classmethod
    def _bins_for_width(cls, fft_size: int, width_px: int, cap: int) -> int:
        """送绘根数 = min(FFT 点数, 画布像素宽, 档位上限)，取最接近的整除档。

        画布上每多一根谱线最多对应 1 px——比像素还密的列只会互相覆盖，
        没有信息量，却要付折线与重采样的成本。因此以画布像素宽为目标，
        在能整除 FFT 点数的 2 的幂里取**比值最接近**的一档（256/512/1024…），
        既不多送，也保住 _resample_max 的 reshape+max 快速路径。
        """
        fft_size = int(fft_size)
        limit = max(cls._BINS_STEPS[0], min(fft_size, int(cap)))
        target = float(max(1, int(width_px)))
        cands = [d for d in cls._BINS_STEPS
                 if d <= limit and fft_size % d == 0]
        if not cands:
            return min(limit, fft_size)
        return min(cands, key=lambda d: abs(math.log(d / target)))

    def _apply_render_bins(self) -> int:
        """按当前画布宽度重算送绘根数；变化时重建 spectrum/瀑布缓冲。

        量化到 _BINS_STEPS，所以拖动窗口时只在跨档（512↔1024）时重建一次，
        不会每个像素都清空瀑布历史。
        """
        if not hasattr(self, "_fft_plot"):
            return self.generator.num_bins
        cap = self._PERF_LEVELS[self._perf_level][1]
        want = self._bins_for_width(
            self.generator.fft_size, self._fft_plot.width(), cap)
        if want != self.generator.num_bins:
            self.generator.reconfigure(self.generator.fft_size, want)
            self._update_fft_bin_label()
        return want

    def resizeEvent(self, event):  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._apply_render_bins()

    def set_performance_mode(self, level: int):
        """切换渲染性能档位：只调绘图刷新间隔与送绘根数上限（不改 FFT 点数）。

        FFT 点数的单一真值源是设置项的 fft_size（经 SettingsPanel.
        _apply_fft_size 下发）。性能档若顺手 reconfigure，启动时回推的
        fft_size=4096 就会被档位默认覆盖成 1024（实测缺口 S-01）。
        """
        level = int(level)
        if not (0 <= level < len(self._PERF_LEVELS)):
            return
        self._perf_level = level
        _, _bins_cap, refresh_ms = self._PERF_LEVELS[level]
        # 调整绘图定时器刷新间隔（QTimer.setInterval 即时生效）
        timer = getattr(self._plot, "_timer", None)
        if timer is not None:
            timer.setInterval(refresh_ms)
        # 档位上限可能变窄：按当前画布重算送绘根数（FFT 点数保持不变）
        self._apply_render_bins()
        self._update_fft_bin_label()
        self._refresh_status_labels()

    # ------------------------------------------------------------------ 调色板 / 平滑
    def _on_palette_changed(self, idx: int):
        """切换瀑布调色板：重建 LUT 并清空 key，使整幅瀑布用新色带重绘。"""
        self._state.wf_colors = list(
            WATERFALL_COLORS_SOFT if idx == 1 else WATERFALL_COLORS)
        # 强制 LUT 重建 + 让画布从空白重绘（旧行随新数据以新色带重涂）
        self._state._wf_lut = None
        self._state._wf_lut_key = (0.0, 0.0)
        self._state._wf_cw = -1
        self._state._wf_ch = -1
        self._state._wf_consumed = 0
        self._state._wf_map_key = None
        self._redraw()

    def _on_wf_smooth_toggled(self, on: bool):
        self._state.wf_smooth = bool(on)
        self.generator.wf_smooth = bool(on)
        # 切换时清掉上一帧 IIR 记忆，避免新旧系数串扰
        self.generator._prev_wf_row = None

    # ------------------------------------------------------------------ 对外接口
    @Slot(bool)
    def set_connected(self, connected: bool):
        """外部通知 SDR 连接状态。断开时清空频谱并禁用控件。"""
        self._connected = bool(connected)
        self._set_controls_enabled(self._connected)
        if not self._connected:
            self.generator.clear_data()
            self._state.markers.clear()
        # 断开必须立刻重画成「未连接」空态：_refresh_status_labels 只管
        # 数据通路，无数据时它不会再排重绘（重绘驱动只有面板定时器）。
        self._label_has_data = False
        self._redraw()
        self._refresh_status_labels()

    def clear(self):
        """清空频谱（未连接语义）。"""
        self.set_connected(False)

    @Slot(object, float, float)
    def update_iq(self, iq_data: np.ndarray, center_freq_hz: float,
                  sample_rate_hz: float):
        """真接 SDR 复 IQ：做窗+fft+dBFS+平均。"""
        # 峰跟踪门限跟 UI 滑杆同步（跟踪每帧都在 push_iq 内推进）。
        self.generator.peak_rel_db = float(self._state.peak_rel_db)
        self.generator.push_iq(iq_data, center_freq_hz, sample_rate_hz)
        self._refresh_status_labels()

    # 向后兼容：main_window 当前调用 set_iq_data(iq, sample_rate)
    @Slot(object, float)
    def set_iq_data(self, iq, sample_rate: float = float(DEFAULT_SAMPLE_RATE_HZ)):
        cf = self.generator.center_freq_hz
        self.update_iq(iq, cf, sample_rate)
        # 把同一块 IQ 广播给其它消费者（中栏 ADS-B 地图等）。主窗口每帧
        # 无条件调本方法，这里是「一行接线」的唯一稳定点：新增消费者不必
        # 再改主窗口。总线只存引用、只留最新帧，不 copy、不排队。
        try:
            from desktop.iq_bus import get_iq_bus
            get_iq_bus().publish(iq, sample_rate, cf)
        except Exception:
            pass

    def set_markers(self, freqs_hz):
        """外部批量设置活动频点 marker（Hz 列表）。空列表/None 清空。

        供扫频找台完成后标注实测到的活动频点；marker 只在当前瞬时带宽内
        可见，超出 span 的频点不绘制（_draw_fixed_markers 自然裁剪）。
        """
        self._state.markers = [float(f) for f in (freqs_hz or [])]
        self._redraw()

    def set_bookmarks(self, bookmarks):
        """外部批量设置书签标签 [(freq_hz, name), ...]；空列表/None 清空。

        与 marker 两套系统：书签是用户收藏的持久频点，点击药丸发
        bookmark_activated 调谐；marker 是扫频瞬时标注。
        """
        self._state.bookmarks = [
            (float(hz), str(name)) for hz, name in (bookmarks or [])]
        self._redraw()

    def clear_markers(self):
        """清空所有固定 marker。"""
        self._state.markers.clear()
        self._redraw()

    @Slot(float)
    def set_center_freq(self, freq_mhz: float):
        """兼容接口：主窗口以 MHz 设置中心频率。"""
        self.generator.center_freq_hz = float(freq_mhz) * 1e6
        self._refresh_status_labels()

    def set_span(self, span_mhz: float):
        """兼容：缩放窗口（span 实际由采样率决定，这里仅触发重绘）。"""
        self._redraw()

    def toggle_waterfall(self):
        self._state.show_waterfall = not self._state.show_waterfall
        # 瀑布关闭时隐藏下方分屏，FFT 曲线自动占满整个 splitter 高度。
        self._wf_plot.setVisible(self._state.show_waterfall)
        self._redraw()

    def reset_splitter(self):
        """双击 splitter handle：按 tokens RATIO 的 FFT/瀑布比例复位（弹性，非死像素）。"""
        try:
            total = self._splitter.height()
            if total <= 0:
                return
            f = float(_tokens().RATIO["spectrum_fft"])
            w = float(_tokens().RATIO["spectrum_wf"])
            h = f + w
            fft_h = int(round(total * f / h))
            wf_h = max(60, total - fft_h)
            self._splitter.setSizes([fft_h, wf_h])
        except Exception:
            pass

    def set_snap_interval(self, hz: float):
        """主窗口设置滚轮 / 键盘调谐步进网格（Hz），转发给 _PlotState。"""
        self._state.set_snap_interval(hz)

    def set_waterfall_depth_rows(self, rows: int) -> int:
        """设置瀑布历史保留行数（六档：128/256/512/1024/2048/4096），
        立即生效并返回实际生效值；非档位值回落 256 并记录原因。

        深度只决定能回看多久与内存占用；每帧增量绘制仍只处理新行，
        不随深度线性变慢。回看用滚轮在下方瀑布窗口操作。
        """
        n = self.generator.set_waterfall_depth(rows)
        self._state.wf_history_end = None
        self._state._wf_hist_key = None  # 回看缓存按旧缓冲算的，必须作废
        self._state._wf_map_key = None   # 深度变了 → 画布按新环形缓冲全量重建
        self._state._wf_consumed = 0
        self._redraw()
        return n

    def set_vfo_bandwidth(self, bw_hz: Optional[float]):
        """主窗口设置 VFO 带宽（Hz）；转发给 _PlotState。传 None 关闭 VFO 矩形。"""
        self._state.set_vfo_bandwidth(bw_hz)
        self._redraw()

    def set_theme_colors(self, bg, grid, text, line, marker, spectrum_colors):
        """兼容 main_window 主题；峰色/离线色固定为默认低饱和。"""
        self.color_bg = QColor(bg)
        self.color_grid = QColor(grid)
        self.color_text = QColor(text)
        self.color_line = QColor(line)
        self._redraw()

    # ------------------------------------------------------------------ 状态读数
    def compute_snr_db(self) -> Optional[float]:
        """当前 SNR (dB)：VFO 带内峰值 − 带外中位噪声底。

        SDR++ 语义：有主听 VFO 时只在收听带宽内找信号峰、带宽外估底噪；
        无 VFO 时退回全谱峰 − 中位数。无数据/有效 bin 太少返回 None
        （顶栏显 "--"，绝不造假读数）。
        """
        gen = self.generator
        if not gen.has_data():
            return None
        spec = gen.spectrum
        n = len(spec)
        if n < 16 or not np.isfinite(spec).any():
            return None
        vfo = _effective_vfo(self._state)
        if vfo is not None:
            center_hz, bw_hz = vfo
            f0 = gen.center_freq_hz - gen.sample_rate_hz / 2.0
            span = gen.sample_rate_hz
            i0 = int((center_hz - bw_hz / 2.0 - f0) / span * n)
            i1 = int((center_hz + bw_hz / 2.0 - f0) / span * n)
            i0, i1 = max(0, i0), min(n, i1)
            if i1 - i0 >= 2:
                mask = np.ones(n, dtype=bool)
                mask[i0:i1] = False
                in_band = spec[i0:i1]
                out_band = spec[mask]
                in_band = in_band[np.isfinite(in_band)]
                out_band = out_band[np.isfinite(out_band)]
                if in_band.size and out_band.size:
                    return float(in_band.max() - np.median(out_band))
                return None
        vals = spec[np.isfinite(spec)]
        if vals.size < 8:
            return None
        return float(vals.max() - np.median(vals))

    def _refresh_status_labels(self):
        """本帧数据已更新：对齐缩放滑杆 + 「无数据 → 有数据」时补画首帧。

        重绘的唯一驱动是面板自己的 ``_plot._timer``（性能档决定 10/20 fps）。
        旧实现在这里无条件 ``_redraw()``：等于每个 IQ 帧（2.4 MS/s 下约
        146 帧/s）都排一次重绘，把性能档的刷新让路完全冲掉（2026-11-11 回归）。
        首帧必须画（面板定时器可能还没排到），靠 ``_label_has_data`` 标记补一帧；
        调用方另按 10 Hz 节流（见 main_window._on_pipeline_frame_ready）。
        """
        self.sync_span_slider()
        has_data = bool(self.generator.has_data())
        if has_data and not self._label_has_data:
            self._redraw()
        self._label_has_data = has_data

    # ------------------------------------------------------------------ 交互转发
    def on_plot_freq_changed(self, freq_mhz: float):
        self.freq_changed.emit(freq_mhz)


# ============================================================================
# 调谐交互共享 mixin（QPainter / OpenGL 两条渲染路径行为一致）
# 对标 SDR++ core/src/gui/main_window.cpp：
#   - 553-576  左右箭头调谐：nfreq = roundl(nfreq/interval)*interval
#   - 579-609  滚轮调谐：Shift ×10 / Alt ×0.1 / 普通 = snapInterval；Ctrl 缩放 span
#   - 269-281  VFO 拖动调谐；309-322 中心频率拖动（centerFreqMoved → tune）
# ============================================================================

class _TuningPlotMixin:
    """滚轮/键盘/拖动调谐共用逻辑。self.state 必须是 _PlotState。"""

    def _tuning_common_init(self):
        # 接收键盘事件需要 StrongFocus（否则 keyPressEvent 不触发）
        self.setFocusPolicy(Qt.StrongFocus)
        # 局部快捷键（A/N/F/W/C、S/Shift+S）：装成 QShortcut + WidgetShortcut，
        # 只有本控件自身获焦时才匹配——这是 Qt 里最具体的上下文，优先于主窗
        # 的全天模式 A（WidgetWithChildrenShortcut）。键位来自 shortcuts.py。
        # 同时打上作用域标记，供主窗槽内兜底判断（见 owns_local_shortcuts）。
        setattr(self, LOCAL_SHORTCUT_SCOPE_ATTR, True)
        self._local_mode_shortcuts: List[QShortcut] = []
        self._rebuild_local_shortcuts()
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self._press_x: Optional[float] = None
        self._press_anchor_x = 0.0
        self._is_panning = False
        self._rf_shift = False          # Ctrl 拖动 = 只平移视图，不调谐中心频率
        self._last_pan_emit = 0.0
        # ---- VFO 框拖拽状态（对标 SDR++ waterfall.cpp:466-476 拖动 VFO）----
        # _vfo_drag_mode: None | 'move' | 'resize_l' | 'resize_r'
        self._vfo_drag_mode: Optional[str] = None
        self._vfo_drag_vfo = None       # 正在拖拽的 VfoState
        self._vfo_press_x = 0.0
        self._vfo_press_gen_center = 0.0
        # move 模式：记录按下时的 VFO 中心
        self._vfo_press_vfo_center = 0.0
        # resize 模式：记录按下时的左右边沿绝对频率
        self._vfo_press_left = 0.0
        self._vfo_press_right = 0.0
        # Shift+空白拖拽 = 橡皮筋新建 VFO
        self._vfo_create_mode = False
        self._vfo_create_start_x = 0.0
        # ---- dB 轴拖动（gqrx plotter.cpp:350-368 YAXIS 捕获）----
        self._db_drag_active = False
        self._db_drag_press_y = 0.0
        self._db_drag_press_min = 0.0
        self._db_drag_press_max = 0.0
        self._db_drag_zoom = False       # Ctrl+竖拖 = 锚点缩放跨度
        self._peak_hover_key = None      # 当前悬停峰（避免每帧重弹气泡）

    # ------------------------------------------------------------------ 工具
    def _rebuild_local_shortcuts(self) -> None:
        """按 shortcuts.py 的**当前生效**键位重建解调模式局部键。

        改键界面保存后由主窗转调（见 apply_shortcut_overrides）：
        局部键是 QShortcut 对象，键位在构造时就固定了，不重建就还是旧键。
        """
        for sc in getattr(self, "_local_mode_shortcuts", []):
            try:
                sc.setParent(None)
                sc.deleteLater()
            except RuntimeError:
                pass        # 已随控件销毁：忽略，不影响后续重建
        self._local_mode_shortcuts = []
        for _sid, _mode in _LOCAL_MODE_SHORTCUTS:
            _sc = QShortcut(QKeySequence(_bind_shortcut(_sid)), self)
            _sc.setContext(Qt.WidgetShortcut)
            _sc.activated.connect(lambda m=_mode: self._apply_mode_hotkey(m))
            self._local_mode_shortcuts.append(_sc)

    def apply_shortcut_overrides(self) -> int:
        """热更新本控件的局部键，返回改动条数（主窗改键后调用）。

        两类键各自刷新：
          * ``_LOCAL_KEY_CODES``：keyPressEvent 里比对的键码（调谐/滤波）；
          * ``_local_mode_shortcuts``：解调模式的 QShortcut 对象。
        """
        changed = 0
        for sid in list(_LOCAL_KEY_CODES):
            new = QKeySequence(_bind_shortcut(sid))[0].key()
            if new != _LOCAL_KEY_CODES[sid]:
                _LOCAL_KEY_CODES[sid] = new
                changed += 1
        before = [sc.key().toString()
                  for sc in getattr(self, "_local_mode_shortcuts", [])]
        self._rebuild_local_shortcuts()
        after = [sc.key().toString() for sc in self._local_mode_shortcuts]
        changed += sum(1 for a, b in zip(before, after) if a != b)
        return changed

    def _x_ratio(self, event):
        return event.position().x() / max(1, self.width())

    def _spectrum_height(self) -> int:
        """谱图区高度像素。默认按 55% 估；SpectrumCurveWidget 已重写为整块高度。"""
        h = self.height()
        return int(h * 0.55) if self.state.show_waterfall else h

    def _freq_at_x(self, x_px: float) -> float:
        """谱面某 x 像素对应的绝对频率（含视图平移偏移）。"""
        gen = self.state.panel.generator
        return _px_to_freq(x_px, self.width(),
                           _view_center_hz(self.state), gen.span_hz)

    # ------------------------------------------------------------------ dB 轴拖动
    def _begin_db_drag(self, y_px: float, zoom: bool = False) -> None:
        """在左侧 dB 轴热区按下：锁定按下时的量程与 y 基准。

        zoom=True（Ctrl）：以按下处 dB 为锚缩放跨度；False：整体平移。
        """
        self._db_drag_active = True
        self.state.db_drag_active = True
        self._db_drag_zoom = bool(zoom)
        self.state.db_drag_zoom = bool(zoom)
        self.state.db_drag_cursor_y = float(y_px)
        self._db_drag_press_y = float(y_px)
        self._db_drag_press_min = float(self.state.db_min)
        self._db_drag_press_max = float(self.state.db_max)
        self.setCursor(Qt.SizeVerCursor)
        self.update()

    def _apply_db_drag(self, y_px: float) -> None:
        """拖动中：平移量程，或 Ctrl 时以按下处 dB 为锚缩放跨度。"""
        if not self._db_drag_active:
            return
        self.state.db_drag_cursor_y = float(y_px)
        h = max(1.0, float(self._spectrum_height() - _SPEC_BOTTOM_PAD))
        span = self._db_drag_press_max - self._db_drag_press_min
        if self._db_drag_zoom:
            y = max(0.0, min(h, float(y_px)))
            anchor = (self._db_drag_press_max
                      - (self._db_drag_press_y / h) * span)
            frac = (anchor - self._db_drag_press_min) / max(1e-9, span)
            factor = _DB_ZOOM_PER_HEIGHT ** (
                (self._db_drag_press_y - y) / h)
            new_span = max(_DB_MIN_SPAN,
                           min(_DB_MAX_SPAN, span * factor))
            lo = anchor - frac * new_span
            hi = lo + new_span
        else:
            delta_db = (self._db_drag_press_y - float(y_px)) / h * span
            lo = self._db_drag_press_min + delta_db
            hi = self._db_drag_press_max + delta_db
        # 夹回 [-160, 0] 时整体平移/整体回弹，保持选定量程跨度。
        lo, hi = _clamp_db_span(lo, hi, span)
        # 唯一状态源：状态 + 右栏两根滑杆由 panel.apply_db_range 同步。
        self.state.panel.apply_db_range(lo, hi)
        self.update()

    def _zoom_db_span_at(self, y_px: float, wheel: int) -> None:
        """dB 轴滚轮：以光标处 dB 为锚缩/放跨度（一格 ×1.1 或 ÷1.1）。"""
        h = max(1.0, float(self._spectrum_height() - _SPEC_BOTTOM_PAD))
        y = max(0.0, min(h, float(y_px)))
        lo = float(self.state.db_min)
        hi = float(self.state.db_max)
        span = hi - lo
        if span <= 0:
            return
        anchor = hi - (y / h) * span
        frac = (anchor - lo) / span
        new_span = max(_DB_MIN_SPAN,
                       min(_DB_MAX_SPAN, span * (1.1 ** int(wheel))))
        new_lo = anchor - frac * new_span
        new_hi = new_lo + new_span
        new_lo, new_hi = _clamp_db_span(new_lo, new_hi, new_span)
        self.state.panel.apply_db_range(new_lo, new_hi)
        lo2, hi2 = self.state.panel.db_range()
        self._show_tune_tip(
            f"量程 {lo2:.0f} … {hi2:.0f} dB"
            f"（跨度 {hi2 - lo2:.0f} dB）")

    def _end_db_drag(self) -> None:
        if not self._db_drag_active:
            return
        self._db_drag_active = False
        self.state.db_drag_active = False
        self.state.db_drag_zoom = False
        self.unsetCursor()
        self.update()

    def _hit_vfo_at(self, x_px: float):
        """命中检测：返回 (vfo, region)，region ∈ {'body','left','right'}。

        从后往前遍历（后画的 VFO 在上层，重叠时优先命中）。边缘命中容差 5px。
        未命中返回 (None, None)。
        """
        mgr = self.state.panel.vfo_manager
        if mgr is None or not self.state.panel.generator.has_data():
            return None, None
        rect = QRectF(0, 0, self.width(), self._spectrum_height())
        edge = 5.0
        for vfo in reversed(mgr.list_all()):
            if not vfo.active or vfo.bw_hz <= 0:
                continue
            rng = _vfo_px_range(self.state, rect, vfo.center_hz, vfo.bw_hz)
            if rng is None:
                continue
            x0, x1 = rng
            # 窄带保护：宽度 ≤ 2×边沿容差时左右手柄互相覆盖，body 永远进不去
            # （2.4 MHz span 落在 1000 px 上时 1 px≈2.4 kHz，2.7 kHz USB 通带
            # 只有约 1.1 px）。此时整条按 body 处理，不提供边沿手柄；命中
            # footprint 与旧手柄一致（±edge），不额外放大拦截范围。
            if x1 - x0 <= 2 * edge:
                if x0 - edge <= x_px <= x1 + edge:
                    return vfo, 'body'
                continue
            if abs(x_px - x0) <= edge:
                return vfo, 'left'
            if abs(x_px - x1) <= edge:
                return vfo, 'right'
            if x0 <= x_px <= x1:
                return vfo, 'body'
        return None, None

    def _bookmark_tag_at(self, x_px: float):
        """命中谱面顶部书签药丸 → 返回其 freq_hz；未命中 None。

        与 _draw_bookmark_tags 同一几何：±30px 容差，视口外不算。
        """
        state = self.state
        if not getattr(state, "show_bookmarks", False) or not state.bookmarks:
            return None
        gen = state.panel.generator
        view_center = _view_center_hz(state)
        span = gen.span_hz
        w = max(1, self.width())
        f_left = view_center - span / 2.0
        best = None
        best_d = 30.0
        for freq_hz, _name in state.bookmarks:
            x = (float(freq_hz) - f_left) / span * w
            d = abs(x - x_px)
            if d <= best_d:
                best_d = d
                best = float(freq_hz)
        return best

    def _band_at(self, x_px: float) -> Optional[int]:
        """命中频段彩带：返回最窄包含光标频率的频段索引（子带比母带更具体，
        重叠时窄者优先，如 业余卫星上行 盖 2m 业余）；未命中返回 None。"""
        state = self.state
        if not state.show_bandplan or not state.bandplan:
            return None
        f = self._freq_at_x(x_px)
        best: Optional[int] = None
        best_w = 0.0
        for i, b in enumerate(state.bandplan):
            f0, f1 = float(b["start_hz"]), float(b["stop_hz"])
            if f0 <= f <= f1 and (best is None or f1 - f0 < best_w):
                best, best_w = i, f1 - f0
        return best

    @staticmethod
    def _band_tip_text(b: Dict[str, object]) -> str:
        """频段气泡/提示文案：名称 · 频率范围 · 调制 · 描述（缺省字段不编造）。"""
        parts = [str(b["label"]),
                 f"{float(b['start_hz']) / 1e6:.4f}–"
                 f"{float(b['stop_hz']) / 1e6:.4f} MHz"]
        if b.get("modulation"):
            parts.append(str(b["modulation"]))
        if b.get("desc"):
            parts.append(str(b["desc"]))
        return " · ".join(parts)

    def _snap_center(self) -> float:
        """把当前中心频率对齐到 snap_interval 网格（SDR++ roundl 语义）。"""
        gen = self.state.panel.generator
        interval = self.state.snap_interval
        if interval and interval > 0:
            gen.center_freq_hz = round(gen.center_freq_hz / interval) * interval
        return gen.center_freq_hz

    def _emit_tuned(self, freq_hz: float):
        self.state.panel.on_plot_freq_changed(freq_hz / 1e6)
        self.state.panel._refresh_status_labels()

    # ------------------------------------------------------------------ 滚轮
    def _show_tune_tip(self, text: str) -> None:
        """瞬时 tooltip（phase83-A6）：滚轮/键盘调谐时给可见反馈。

        短驻留（800ms），不抢焦点、不入布局；只回答"我一步动了多少/动到哪"。
        """
        try:
            QToolTip.showText(QCursor.pos(), text, self, self.rect(), 800)
        except Exception:  # noqa: BLE001 —— 提示是辅助，失败绝不影响调谐
            pass

    def wheelEvent(self, event):
        # main_window.cpp:579-609：有 VFO 时滚轮=调谐；否则缩放。
        # 手势分层（Ctrl 锚点缩放照 SDR++ waterfall.cpp processInputs；
        # VFO 悬停三层照 gqrx）：
        #   Ctrl+滚轮              = 以光标为锚缩放 span（谱面空白处）
        #   Ctrl+滚轮 悬停 VFO     = 对称展宽/收窄带宽（gqrx Ctrl+wheel）
        #   滚轮 悬停 VFO 边沿      = 非对称调带宽（单侧边跟随光标）
        #   滚轮 悬停 VFO 本体      = 整体搬移通带（gqrx Shift+wheel 语义）
        #   普通/Shift/Alt+滚轮    = 按 snap 步进调谐（Shift×10 / Alt×0.1）
        gen = self.state.panel.generator
        mods = event.modifiers()
        dy = event.angleDelta().y()
        wheel = 1 if dy > 0 else -1     # 上滚=加/扩，下滚=减/收
        step = self.state.snap_interval
        if mods & Qt.ShiftModifier:
            step *= 10.0
        elif mods & Qt.AltModifier:
            step *= 0.1
        # dB 轴（左缘）滚轮 = 缩放 dB 跨度，不落到调频：用户想缩量程时
        # 滚轮却动了频率，是“看着正常、实际做错事”的偏。优先级同按下。
        if (event.position().x() <= _DB_AXIS_DRAG_W
                and event.position().y()
                < self._spectrum_height() - _SPEC_BOTTOM_PAD):
            self._zoom_db_span_at(event.position().y(), wheel)
            self.update()
            return
        vfo, edge_region = self._hit_vfo_at(event.position().x())
        # 频率轴手势分层（P2-9，gqrx 密度）：悬停底部频率刻度轴上滚轮 =
        # 以光标为锚缩放 span（与 Ctrl+滚轮同数学）；VFO 正下方不抢手势。
        on_freq_axis = (event.position().y()
                        >= self.height() - _SPEC_BOTTOM_PAD)
        if (mods & Qt.ControlModifier) or (
                on_freq_axis and vfo is None
                and not (mods & (Qt.ShiftModifier | Qt.AltModifier))):
            if vfo is not None:
                # 对称带宽（gqrx Ctrl+滚轮）：中心不动，双边同扩同收
                new_bw = max(100.0, float(vfo.bw_hz) + step * wheel)
                vfo.bw_hz = new_bw
                self.state.panel.vfo_bw_changed.emit(
                    vfo.vfo_id, float(vfo.bw_hz))
                self.update()
                self._show_tune_tip(
                    f"VFO 带宽 {new_bw / 1e3:.1f} kHz（对称）")
                return
            # Ctrl+滚轮 = 以光标为中心缩放 span（对标 SDR++ waterfall.cpp
            # processInputs 的鼠标锚点缩放：缩放前后光标处绝对频率不变）。
            factor = 1.1 if dy > 0 else 1 / 1.1
            span = gen.span_hz
            # 缩放是**显示跨度**，不是硬件采样率：采样率每帧被 push_iq
            # 覆写，写在这里等于下一帧就被抹掉（真机缩放失灵的根因）。
            new_span = max(48_000.0, min(float(gen.sample_rate_hz), span * factor))
            # 光标处绝对频率（含 Ctrl 拖动的视图平移偏移）
            r = event.position().x() / max(1, self.width())
            view_center = _view_center_hz(self.state)
            anchor_freq = view_center + (r - 0.5) * span
            # 保持光标频率不变：new_view_center = anchor - (r-0.5)*new_span
            new_view_center = anchor_freq - (r - 0.5) * new_span
            gen.set_span(new_span)
            # view_offset 保持不变，反解出真调谐中心
            gen.center_freq_hz = new_view_center - self.state.view_offset_hz
            self._emit_tuned(gen.center_freq_hz)
            self.state.panel.sync_span_slider()
            self._show_tune_tip(
                f"缩放 {new_span / 1e6:.3f} MHz")
        else:
            if vfo is not None:
                if edge_region in ('left', 'right'):
                    # 非对称调带宽：单侧边跟随光标
                    new_bw = max(100.0, float(vfo.bw_hz) + step * wheel)
                    vfo.bw_hz = new_bw
                    self.state.panel.vfo_bw_changed.emit(
                        vfo.vfo_id, float(vfo.bw_hz))
                    self.update()
                    self._show_tune_tip(
                        f"VFO 带宽 {new_bw / 1e3:.1f} kHz"
                        f"（步进 {step / 1e3:g} kHz）")
                    return
                if edge_region == 'body':
                    # 整体搬移通带：VFO 中心按步进挪动，带宽不动
                    new_center = float(vfo.center_hz) + step * wheel
                    vfo.center_hz = new_center
                    self.state.panel.vfo_moved.emit(
                        vfo.vfo_id, float(new_center))
                    self.update()
                    self._show_tune_tip(
                        f"VFO {new_center / 1e6:.4f} MHz"
                        f"（搬移 {step / 1e3:g} kHz）")
                    return

            # 未命中 VFO：按 snap 步进调谐（SDR++ 语义）
            nfreq = gen.center_freq_hz + step * wheel
            # main_window.cpp:596  roundl(nfreq/interval)*interval
            nfreq = round(nfreq / step) * step
            gen.center_freq_hz = nfreq
            self._emit_tuned(nfreq)
            self._show_tune_tip(
                f"{nfreq / 1e6:.4f} MHz（步进 {step / 1e3:g} kHz）")
        self.update()

    # ------------------------------------------------------------------ 键盘
    def _apply_mode_hotkey(self, mode: str) -> None:
        """应用单键解调模式（局部 QShortcut 的唯一落点）。

        模式键不再走 keyPressEvent：键盘事件永远先给焦点控件，局部
        QShortcut 由 Qt 在焦点控件获焦时派发，输入框里按这些字母不会被抢。
        """
        if _get_demod_mode is not None and _get_demod_mode(mode) is None:
            self._show_tune_tip(f"模式 {mode} 未注册")
            return
        self.state.panel.mode_hotkey_requested.emit(mode)
        self._show_tune_tip(f"模式 {mode}")
        self.update()

    def keyPressEvent(self, event):
        # main_window.cpp:553-576：左右箭头按 snapInterval 调谐。
        # 上下箭头 = 细调（snap_interval*0.1，对标 SDR++ 步进微调）；
        # Shift ×10 / Alt ×0.1 修饰同样生效。
        # 键位来自 shortcuts.py（scope=spectrum），别处不再写裸键码。
        key = event.key()
        mods = event.modifiers()
        k_left = _LOCAL_KEY_CODES["spectrum.tune.left"]
        k_right = _LOCAL_KEY_CODES["spectrum.tune.right"]
        k_up = _LOCAL_KEY_CODES["spectrum.tune.up"]
        k_down = _LOCAL_KEY_CODES["spectrum.tune.down"]
        if key in (k_left, k_right, k_up, k_down):
            gen = self.state.panel.generator
            if key in (k_left, k_right):
                interval = self.state.snap_interval          # 左右 = 整格步进
                direction = -1.0 if key == k_left else 1.0
            else:
                interval = self.state.snap_interval * 0.1     # 上下 = 细调 1/10 格
                direction = 1.0 if key == k_up else -1.0
            if mods & Qt.ShiftModifier:
                interval *= 10.0
            elif mods & Qt.AltModifier:
                interval *= 0.1
            nfreq = gen.center_freq_hz + interval * direction
            # main_window.cpp:596  roundl(nfreq/interval)*interval
            nfreq = round(nfreq / interval) * interval
            gen.center_freq_hz = nfreq
            self._emit_tuned(nfreq)
            self._show_tune_tip(
                f"{nfreq / 1e6:.4f} MHz（步进 {interval / 1e3:g} kHz）")
            self.update()
            return
        # 滤波宽窄档（, 窄 / . 宽，gqrx </./> 惯例；按模式预设表步进）
        k_narrow = _LOCAL_KEY_CODES["spectrum.filter.narrow"]
        k_wide = _LOCAL_KEY_CODES["spectrum.filter.wide"]
        if key in (k_narrow, k_wide):
            mgr = self.state.panel.vfo_manager
            vfo = mgr.current if mgr is not None else None
            direction = -1 if key == k_narrow else 1
            if vfo is None or next_bandwidth_preset is None:
                self._show_tune_tip("无主听 VFO：滤波档未生效")
                return
            new_bw = next_bandwidth_preset(
                getattr(vfo, "mode", "") or "", float(vfo.bw_hz), direction)
            vfo.bw_hz = new_bw
            self.state.panel.vfo_bw_changed.emit(vfo.vfo_id, float(new_bw))
            self._show_tune_tip(f"滤波 {new_bw / 1e3:g} kHz")
            self.update()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ 鼠标
    def _begin_vfo_drag(self, x_px: float, vfo, region: str, event):
        """命中已有 VFO → 进入移动 / 缩放拖拽模式。

        对标 SDR++ waterfall.cpp:336-340 选中 VFO + 466-476 拖动 VFO：
        - 点击 VFO body → 设为主听（current）并开始移动；
        - 拖拽左右边缘 → 调整带宽；
        - 拖拽同时移动硬件中心频率，使 VFO 始终可见（不把信号拖出视口）。
        """
        state = self.state
        mgr = state.panel.vfo_manager
        gen = state.panel.generator

        # 点击确认：收编为主听（current），刷新粘滞快照，并通知主窗口
        if mgr is not None:
            mgr.set_active_context(vfo, temporary=False)
            state.panel.vfo_selected.emit(vfo.vfo_id)

        self._vfo_drag_mode = 'move' if region == 'body' else ('resize_l' if region == 'left' else 'resize_r')
        self._vfo_drag_vfo = vfo
        self._vfo_press_x = x_px
        self._vfo_press_gen_center = gen.center_freq_hz
        self._vfo_press_vfo_center = float(vfo.center_hz)
        half = float(vfo.bw_hz) / 2.0
        self._vfo_press_left = float(vfo.center_hz) - half
        self._vfo_press_right = float(vfo.center_hz) + half
        # 不进入普通平移
        self._is_panning = False
        self._rf_shift = False

    def _delete_vfo(self, vfo_id: str):
        """删除指定 VFO（至少保留一个），切主听到剩余 VFO，发 vfo_removed。"""
        mgr = self.state.panel.vfo_manager
        if mgr is None:
            return
        if len(mgr.list_all()) <= 1:
            return  # 至少保留一个 VFO
        v = mgr.get(vfo_id)
        if v is None:
            return
        was_current = (mgr.current is v)
        mgr.remove(vfo_id)
        # 删的是主听 → 切到列表首个剩余 VFO
        if was_current and mgr.list_all():
            mgr.set_active_context(mgr.list_all()[0], temporary=False)
        self.state.panel.vfo_removed.emit(vfo_id)
        self.update()

    def contextMenuEvent(self, event):
        """右键命中 VFO → 弹“删除此 VFO”菜单；否则交给默认（marker 移除）。"""
        vfo, _region = self._hit_vfo_at(event.pos().x())
        mgr = self.state.panel.vfo_manager
        if vfo is not None and mgr is not None and len(mgr.list_all()) > 1:
            menu = QMenu(self)
            act = menu.addAction(f"删除此 VFO ({vfo.vfo_id})")
            chosen = menu.exec(event.globalPos())
            if chosen == act:
                self._delete_vfo(vfo.vfo_id)
            event.accept()
            return
        super().contextMenuEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            x = event.position().x()
            y = event.position().y()
            in_spectrum = y <= self._spectrum_height()
            if in_spectrum:
                # 顶部书签药丸带优先：命中 → 调谐该书签（gqrx 书签点击语义），
                # 不进 VFO 拖拽 / 橡皮筋 / 平移。
                if y <= _BM_TAG_H and not (
                        event.modifiers() & Qt.ShiftModifier):
                    bm_freq = self._bookmark_tag_at(x)
                    if bm_freq is not None:
                        self.state.panel.bookmark_activated.emit(bm_freq)
                        self._show_tune_tip(f"书签 {bm_freq / 1e6:.4f} MHz")
                        return
                # dB 轴热区（左缘竖条）：按住拖动 = 平移量程，不进调谐/VFO；
                # Ctrl+竖拖 = 以按下处 dB 为锚缩放跨度。
                if x <= _DB_AXIS_DRAG_W:
                    self._begin_db_drag(
                        y, zoom=bool(event.modifiers() & Qt.ControlModifier))
                    return
                vfo, region = self._hit_vfo_at(x)
                if vfo is not None:
                    self._begin_vfo_drag(x, vfo, region, event)
                    self.update()
                    return
                # Shift + 空白处 → 橡皮筋新建 VFO
                if event.modifiers() & Qt.ShiftModifier:
                    self._vfo_create_mode = True
                    self._vfo_create_start_x = x
                    self.state.vfo_create_band = (self._freq_at_x(x), self._freq_at_x(x))
                    self._is_panning = False
                    self._rf_shift = False
                    self.update()
                    return
            self._press_x = x
            self._press_anchor_x = x
            self._is_panning = True
            self._last_pan_emit = 0.0
            # Ctrl 按住：RF shift 模式，只平移视图不改中心频率
            self._rf_shift = bool(event.modifiers() & Qt.ControlModifier)

    def mouseMoveEvent(self, event):
        r = self._x_ratio(event)
        self.state.mouse_x_ratio = r
        self.state.mouse_in_spectrum = True

        if self._db_drag_active:
            self._apply_db_drag(event.position().y())
            return

        # Shift 橡皮筋新建：实时更新预览矩形
        if self._vfo_create_mode:
            x = event.position().x()
            f0 = self._freq_at_x(self._vfo_create_start_x)
            f1 = self._freq_at_x(x)
            self.state.vfo_create_band = (f0, f1)
            self.update()
            return

        # VFO 移动 / 缩放拖拽
        if self._vfo_drag_mode is not None:
            x = event.position().x()
            gen = self.state.panel.generator
            vfo = self._vfo_drag_vfo
            if vfo is None:
                return
            if self._vfo_drag_mode == 'move':
                # 移动 VFO：硬件中心随光标平移（保持 VFO 在视口内可见）
                shift = -(x - self._vfo_press_x) / max(1, self.width()) * gen.span_hz
                gen.center_freq_hz = self._vfo_press_gen_center + shift
                vfo.center_hz = self._vfo_press_vfo_center + shift
                self.state.panel.vfo_moved.emit(vfo.vfo_id, float(vfo.center_hz))
            else:
                # 缩放：视图保持不动，边沿直接落在光标所在绝对频率上。
                # （不能像 move 那样平移 gen.center，否则 _freq_at_x 的视图平移
                # 会把光标频率抵消回按下时的边沿，导致带宽纹丝不动。）
                cur_edge = self._freq_at_x(x)
                if self._vfo_drag_mode == 'resize_l':
                    left, right = cur_edge, self._vfo_press_right
                else:
                    left, right = self._vfo_press_left, cur_edge
                if right - left > 100.0:   # 最小带宽 100 Hz
                    vfo.center_hz = (left + right) / 2.0
                    vfo.bw_hz = right - left
                    self.state.panel.vfo_bw_changed.emit(vfo.vfo_id, float(vfo.bw_hz))

            self._emit_tuned(gen.center_freq_hz)
            self.update()
            return

        # 悬停（未按键）：更新 VfoManager 三态焦点 active_context（临时悬停语义）
        # 并按命中区域切换鼠标光标：VFO 边沿 → SizeHorCursor；峰 → PointingHand。
        mgr = self.state.panel.vfo_manager
        y_hover = event.position().y()
        axis_top = max(0.0, self._spectrum_height() - _SPEC_BOTTOM_PAD)
        hovering = (not self._is_panning and self._vfo_drag_mode is None
                    and not self._vfo_create_mode)
        peak = None
        if mgr is not None and not self._is_panning:
            if y_hover <= self._spectrum_height():
                vfo, reg = self._hit_vfo_at(event.position().x())
                if vfo is not None:
                    mgr.set_active_context(vfo, temporary=True)
                    if reg in ('left', 'right'):
                        self.setCursor(Qt.SizeHorCursor)
                    else:
                        self.unsetCursor()
                else:
                    mgr.clear_active_context()
                    if hovering and y_hover < axis_top:
                        peak = self._peak_at(event.position().x(), y_hover)
                    if peak is not None:
                        self.setCursor(Qt.PointingHandCursor)
                    else:
                        self.unsetCursor()
            else:
                mgr.clear_active_context()
                self.unsetCursor()
        elif not self._is_panning:
            if hovering and y_hover < axis_top:
                peak = self._peak_at(event.position().x(), y_hover)
            if peak is not None:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.unsetCursor()
        else:
            self.unsetCursor()

        # 峰值气泡：只在“进入某个峰”时弹一次，离开/拖拽时收；
        # 不弹右键菜单峰值项，避免长出第二个假入口。
        key = None if peak is None else round(peak[0])
        if key != self._peak_hover_key:
            self._peak_hover_key = key
            try:
                if peak is not None and hovering:
                    QToolTip.showText(
                        QCursor.pos(),
                        _fmt_peak_text(peak[0], peak[1], peak[2],
                                       verb="跳到"),
                        self)
                else:
                    QToolTip.hideText()
            except Exception as exc:  # noqa: BLE001 —— 气泡是辅助，失败留痕
                _log.debug("峰值悬停提示失败: %s", exc)

        # 频段彩带悬停（SDR++ bandplan 仿妆）：命中变化时提亮 + 气泡提示；
        # 拖拽/平移中不刷气泡，避免噪声。峰命中优先，两个气泡不抢同一光标。
        if hovering:
            y = event.position().y()
            idx = self._band_at(event.position().x()) \
                if (peak is None and y <= self._spectrum_height()) else None
            if idx != self.state.bandplan_hover:
                self.state.bandplan_hover = idx
                try:
                    if idx is not None:
                        QToolTip.showText(
                            QCursor.pos(),
                            self._band_tip_text(self.state.bandplan[idx]),
                            self)
                    else:
                        QToolTip.hideText()
                except Exception:  # noqa: BLE001 —— 气泡是辅助，失败静默
                    pass

        if self._is_panning:
            dx = event.position().x() - self._press_anchor_x
            gen = self.state.panel.generator
            shift = -dx / max(1, self.width()) * gen.span_hz
            if self._rf_shift:
                # 仅平移视图偏移，不调谐、不 emit（对标 center tuning 平移）
                self.state.view_offset_hz += shift
            else:
                # 普通拖动 = 调谐中心频率（main_window.cpp:309-322 centerFreqMoved）
                gen.center_freq_hz += shift
                now = time.monotonic()
                if now - self._last_pan_emit >= 0.1:
                    self._last_pan_emit = now
                    snapped = self._snap_center()
                    self._emit_tuned(snapped)
            self._press_anchor_x = event.position().x()
        self.update()

    def _tune_click(self, ratio: float):
        """单击/双击调谐（SDR++ 调谐模式语义）。

        center（默认）：重调硬件中心频率，画面跟过去；
        normal：只把当前主听 VFO 移到点击频率（按 snap 对齐），画面不动，
        瀑布保留多信号轨迹。无主听 VFO 时退回 center 行为。
        """
        gen = self.state.panel.generator
        # 必须走 _freq_at_x：它含 view_offset_hz（Ctrl 平移后的视图偏移）。
        # 只按 gen.center 算会让点击系统性偏 -Δ —— 用户看着正常，实际收错频率。
        freq = self._freq_at_x(ratio * max(1, self.width()))
        if self.state.tune_mode == "normal":
            mgr = self.state.panel.vfo_manager
            vfo = mgr.current if mgr is not None else None
            if vfo is not None:
                interval = self.state.snap_interval
                freq = round(freq / interval) * interval
                vfo.center_hz = freq
                self.state.panel.vfo_moved.emit(vfo.vfo_id, float(freq))
                self.update()
                return
            # 无 VFO 可移：退回中心模式
        gen.center_freq_hz = freq
        snapped = self._snap_center()
        self._emit_tuned(snapped)

    def _tune_peak(self, freq_hz: float) -> None:
        """点中峰：落点取 find_peaks 的 freq_hz，1 Hz 直下发，不做 snap。

        空白单击仍走 _tune_click（带吸附），这是两回事：前者是"点到一个
        测出来的峰"，后者是"顺手点一下谱面"。
        """
        gen = self.state.panel.generator
        freq = float(freq_hz)
        if self.state.tune_mode == "normal":
            mgr = self.state.panel.vfo_manager
            vfo = mgr.current if mgr is not None else None
            if vfo is not None:
                vfo.center_hz = freq
                self.state.panel.vfo_moved.emit(vfo.vfo_id, freq)
                self.update()
                return
        gen.center_freq_hz = freq
        self._emit_tuned(freq)

    def _peak_at(self, x_px: float, y_px: float):
        """峰值命中测试：返回 (freq_hz, db, snr_db) 或 None。

        命中区 = 峰圆点 ±6px + 它自己的标签文字框（与 _draw_peaks 共用
        同一套 _peak_label_placements 与 _peak_annotations）。命中区可以
        宽（容手抖），落点只认测量到的 freq_hz，绝不按像素换算。
        """
        state = self.state
        gen = state.panel.generator
        ann = _peak_annotations(state)
        if not ann:
            return None
        rect = QRectF(0, 0, self.width(),
                      max(1, self._spectrum_height() - _SPEC_BOTTOM_PAD))
        if y_px > rect.height():
            return None
        view_center = _view_center_hz(state)
        span = gen.span_hz
        f = QFont()
        f.setPointSize(8)
        fm = QFontMetricsF(f)
        row_h = fm.height() + 2.0
        pts = []
        for freq_hz, db, snr, label in ann:
            px = _freq_to_x(rect, view_center, span, freq_hz)
            py = _db_to_y(rect, db, state.db_min, state.db_max)
            pts.append((px, py, label, float(freq_hz), float(db), float(snr)))
        pts.sort(key=lambda p: p[0])
        placed = _peak_label_placements(
            [(p[0], p[1], p[2]) for p in pts], fm.horizontalAdvance,
            _peak_label_left_bound(rect, state, fm),
            rect.x() + rect.width() - 2.0, row_h,
            text_ascent=fm.ascent(), text_descent=fm.descent())
        hits = []
        for p in pts:
            if abs(x_px - p[0]) <= 6.0 and abs(y_px - p[1]) <= 6.0:
                hits.append((abs(x_px - p[0]) + abs(y_px - p[1]), p))
        for tx, ty, _label, _row in placed:
            src = min(pts, key=lambda p: abs(p[0] - (tx - 6.0)))
            if abs(src[0] - (tx - 6.0)) > 0.01:
                continue
            w = fm.horizontalAdvance(src[2])
            if (tx - 2.0 <= x_px <= tx + w + 2.0
                    and ty - fm.ascent() - 2.0 <= y_px
                    <= ty + fm.descent() + 2.0):
                hits.append((100.0 + abs(x_px - src[0]), src))
        if not hits:
            return None
        hits.sort(key=lambda h: h[0])
        best = hits[0][1]
        return best[3], best[4], best[5]

    def _end_all_gestures(self):
        """松手统一清拖拽态（release 入口无条件调用，分支只负责副作用）。

        返回 (vfo, vfo_mode, rf_shift, was_create)：这些上下文决定分支副作用，
        必须在清空前读出——尤其 `_vfo_drag_vfo`，先清后读会丢拖拽目标，
        发不出 vfo_moved / vfo_bw_changed。
        """
        ctx = (self._vfo_drag_vfo, self._vfo_drag_mode,
               self._rf_shift, self._vfo_create_mode)
        self._vfo_drag_vfo = None
        self._vfo_drag_mode = None
        self._vfo_create_mode = False
        self._rf_shift = False
        self._is_panning = False
        return ctx

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 先无条件清全部拖拽态：任一分支提前 return 都不会留下
            # `_is_panning=True`（否则松手后鼠标一动整屏跟着平移）。
            vfo, vfo_mode, rf_shift, was_create = self._end_all_gestures()
            # dB 轴拖动结束：量程已随每帧写入唯一状态源，这里只清拖拽态。
            if self._db_drag_active:
                self._end_db_drag()
                return
            # 橡皮筋新建 VFO 结束：按 rubber-band 区间 center/bw 创建
            if was_create:
                band = self.state.vfo_create_band
                self.state.vfo_create_band = None
                if band is not None:
                    f0, f1 = band
                    lo, hi = min(f0, f1), max(f0, f1)
                    bw = hi - lo
                    if bw >= 100.0:
                        center = (lo + hi) / 2.0
                        mgr = self.state.panel.vfo_manager
                        if mgr is not None:
                            mode = getattr(mgr, "last_mode", "FM")
                            v = mgr.add(center, bw, mode, inherit_last=False)
                            mgr.set_active_context(v, temporary=False)
                            self.state.panel.vfo_created.emit(v.vfo_id, center, bw)
                            self.state.panel.vfo_selected.emit(v.vfo_id)
                self.update()
                return

            # VFO 移动/缩放结束：对齐网格并 emit 最终值
            if vfo_mode is not None:
                was_resize = vfo_mode in ('resize_l', 'resize_r')
                gen = self.state.panel.generator
                snapped = self._snap_center()
                if vfo is not None:
                    if was_resize:
                        # 缩放结束：边沿/中心已在拖拽中按 left/right 重算，
                        # 保持用户摆放的位置，只补发最终带宽信号。
                        self.state.panel.vfo_bw_changed.emit(vfo.vfo_id, float(vfo.bw_hz))
                    else:
                        # 移动后 VFO 中心跟随对齐后的硬件中心
                        vfo.center_hz = snapped
                        self.state.panel.vfo_moved.emit(vfo.vfo_id, float(vfo.center_hz))
                        self.state.panel.vfo_bw_changed.emit(vfo.vfo_id, float(vfo.bw_hz))
                self._emit_tuned(snapped)
                self.update()
                return

            moved = abs(event.position().x() - (self._press_x or 0))
            if moved > 4:
                if not rf_shift:
                    # 拖拽结束：对齐网格并 emit 最终中心频率
                    snapped = self._snap_center()
                    self._emit_tuned(snapped)
                # rf_shift 视图平移结束：不调谐，仅停留视图位置
            else:
                y = event.position().y()
                # 单击谱面空白处（未命中 VFO / 非 Shift 新建模式，已在 press 阶段分流）
                # → 调谐到点击位置（center/VFO 模式语义见 _tune_click）。
                # 瀑布区（y > 谱面高度）不响应。
                if y <= self._spectrum_height():
                    # 频段彩带点击反馈：先按点击时视图命中（center 模式调谐后
                    # 画面跟过去，同 x 已对应别的频率），再调谐；选中 → 常驻
                    # 描边 + 摘要提示，点在空白频率 → 清除。不改变调谐语义。
                    sel = self._band_at(event.position().x())
                    axis_top = max(
                        0.0, self._spectrum_height() - _SPEC_BOTTOM_PAD)
                    peak = (self._peak_at(event.position().x(), y)
                            if y < axis_top else None)
                    if peak is not None:
                        # 点中峰：落点取测量值，1 Hz 直下发（不吸附）
                        self._tune_peak(peak[0])
                        self._show_tune_tip(
                            _fmt_peak_text(peak[0], peak[1], peak[2]))
                    else:
                        # 空白/刻度轴仍走顺手调谐（带 snap）
                        self._tune_click(self._x_ratio(event))
                    if sel != self.state.bandplan_selected:
                        self.state.bandplan_selected = sel
                        if sel is not None and peak is None:
                            self._show_tune_tip(
                                "频段 "
                                + self._band_tip_text(self.state.bandplan[sel]))
            self.update()
        elif event.button() == Qt.RightButton:
            # 右键命中 VFO → 删除菜单（contextMenuEvent）；
            # 右键谱面空白处 → 添加 marker；瀑布区不响应。
            x = event.position().x()
            y = event.position().y()
            vfo, _reg = self._hit_vfo_at(x)
            if vfo is None and y <= self._spectrum_height():
                self.state.markers.append(self._freq_at_x(x))
                if len(self.state.markers) > 8:
                    self.state.markers.pop(0)
            self.update()

    def mouseDoubleClickEvent(self, event):
        # 双击底部频率刻度轴 = 复位 Ctrl 平移留下的 view_offset（回调谐中心）。
        # 这是唯一的复位入口：view_offset 只增不减，没有它用户拖出去就回不来。
        y = event.position().y()
        axis_top = max(0.0, self._spectrum_height() - _SPEC_BOTTOM_PAD)
        if event.button() == Qt.LeftButton \
                and axis_top <= y <= self._spectrum_height():
            if self.state.view_offset_hz != 0.0:
                self.state.view_offset_hz = 0.0
                self._show_tune_tip("视图已复位到调谐中心")
            else:
                self._show_tune_tip("视图已在调谐中心")
            self.update()
            return
        # 双击峰同样按测量值落点；空白处仍按像素调谐（SDR++ waterfall 行为）。
        peak = self._peak_at(event.position().x(), y)
        if peak is not None:
            self._tune_peak(peak[0])
            self._show_tune_tip(
                _fmt_peak_text(peak[0], peak[1], peak[2]))
        else:
            self._tune_click(self._x_ratio(event))
        self.update()

    def leaveEvent(self, event):
        # 鼠标离开谱面：清频段悬停态与峰值悬停，提亮/气泡不残留
        hover_stale = False
        if getattr(self.state, "bandplan_hover", None) is not None:
            self.state.bandplan_hover = None
            hover_stale = True
        if self._peak_hover_key is not None:
            self._peak_hover_key = None
            hover_stale = True
        if hover_stale:
            try:
                QToolTip.hideText()
            except Exception:  # noqa: BLE001 —— 气泡是辅助，失败静默
                pass
            self.update()
        super().leaveEvent(event)


# ============================================================================
# 绘图表面：上方 FFT 频谱曲线 widget（承载全部调谐 / VFO 拖拽交互）
# ============================================================================

# 触屏不可用时 PinchZoomMixin 兜底为 object；此时绝不能把 object 显式列在
# QWidget 之前作为基类，否则 Python C3 线性化报 TypeError（object 必须排在
# Qt 继承链末尾）。用条件基类元组在两种情况下都得到一致、合法的 MRO。
_PinchBases = (PinchZoomMixin,) if PinchZoomMixin is not object else ()


class SpectrumCurveWidget(*_PinchBases, _TuningPlotMixin, QWidget):
    """上方 FFT 频谱曲线。整个 widget 即谱面，鼠标 / 滚轮 / 键盘交互全在此。

    触屏（Surface/平板）：PinchZoomMixin 提供双指捏合缩放（复用 Ctrl+滚轮的
    锚点缩放数学）+ 单指拖动平移（复用 mouseMoveEvent 的中心频率平移）。
    鼠标模式下 wheelEvent / mousePressEvent 等原有逻辑完全不变。
    """

    def __init__(self, state: _PlotState, parent=None):
        super().__init__(parent)
        self.state = state
        self._tuning_common_init()
        # 触屏手势：开启 WA_AcceptTouchEvents + 抓取 Pinch/Pan 手势。
        # PinchZoomMixin 兜底为 object 时跳过连接，鼠标模式不受影响。
        if PinchZoomMixin is not object and hasattr(self, "init_pinch_zoom"):
            self.init_pinch_zoom()
            try:
                self.pinch_scale_changed.connect(self._on_touch_pinch_zoom)
                self.pan_changed.connect(self._on_touch_pan)
            except Exception:
                pass
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(50)

    # ---- 触屏手势回调：复用既有 wheel 缩放 / mouseMove 平移数学 ----
    def _on_touch_pinch_zoom(self, scale_factor: float, center_pos: QPointF) -> None:
        """双指捏合：以捏合中心为锚点缩放频率 span（与 Ctrl+滚轮同数学）。

        QPinchGesture.scaleFactor>1 = 双指张开 = 内容放大 = 收窄 span（看更细）。
        """
        gen = self.state.panel.generator
        span = gen.span_hz
        # 捏合张开(scale>1) → span 缩小；捏合收拢(scale<1) → span 放大。
        new_span = span / max(0.2, min(5.0, scale_factor))
        new_span = max(48_000.0, min(float(gen.sample_rate_hz), new_span))
        r = center_pos.x() / max(1, self.width())
        view_center = _view_center_hz(self.state)
        anchor_freq = view_center + (r - 0.5) * span
        new_view_center = anchor_freq - (r - 0.5) * new_span
        gen.set_span(new_span)
        gen.center_freq_hz = new_view_center - self.state.view_offset_hz
        self._emit_tuned(gen.center_freq_hz)
        self.state.panel.sync_span_slider()
        self.update()

    def _on_touch_pan(self, dx: float, dy: float) -> None:
        """单指拖动：平移中心频率（与 mouseMoveEvent 普通拖动同数学）。"""
        gen = self.state.panel.generator
        shift = -dx / max(1, self.width()) * gen.span_hz
        gen.center_freq_hz += shift
        snapped = self._snap_center()
        self._emit_tuned(snapped)
        self.update()

    def _spectrum_height(self) -> int:
        # 瀑布已拆到下方独立 widget，本 widget 整块都是谱面。
        return self.height()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        _render_spectrum(p, self.state, self.width(), self.height())
        p.end()


# ============================================================================
# 绘图表面：下方瀑布图 widget（仅持久增量画布，不承载调谐交互）
# ============================================================================

class WaterfallWidget(QWidget):
    """下方瀑布图。只读显示；调谐 / VFO 交互由上方 SpectrumCurveWidget 负责。"""

    def __init__(self, state: _PlotState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setMinimumHeight(80)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(100)

    def freq_at_x(self, x_px: float) -> float:
        """瀑布某 x 像素对应的绝对频率：与上方谱面 _freq_at_x 同一公式。"""
        gen = self.state.panel.generator
        return _px_to_freq(x_px, self.width(),
                           _view_center_hz(self.state), gen.span_hz)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        _render_wf_only(p, self.state, self.width(), self.height())
        p.end()

    def wheelEvent(self, event):
        """滚轮回看历史：上滚看更旧，下滚回到最新；回看时新数据继续入缓冲。

        回看窗口按绝对行号固定，不会被新行推走；深度由设置项
        ``waterfall_depth_rows`` 决定（六档 128/256/512/1024/2048/4096 行）。
        """
        gen = self.state.panel.generator
        total = gen.wf_rows_written
        avail = gen.wf_available_rows()
        if total <= 0 or avail <= 0:
            event.ignore()
            return
        dy = event.angleDelta().y()
        if dy == 0:
            event.ignore()
            return
        h = max(1, self.height())
        oldest = total - avail
        min_end = min(total, oldest + h)
        end = self.state.wf_history_end
        if end is None:
            end = total
        step = max(1, h // 20)                 # 一格滚轮约 5% 屏高
        end += -step if dy > 0 else step
        end = max(min_end, min(total, end))
        if end >= total:
            self.state.wf_history_end = None
            self.state._wf_hist_key = None     # 退出回看，缓存的旧窗口画面作废
            self.state._wf_map_key = None      # 回到实时 → 按最新 h 行全量重建
            tip = "瀑布：回到最新"
        else:
            self.state.wf_history_end = int(end)
            self.state._wf_hist_key = None     # 窗口边界变了，缓存键必然失配
            tip = f"瀑布回看：-{total - int(end)} 行（下滚回到最新）"
        self.update()
        QToolTip.showText(event.globalPosition().toPoint(), tip,
                          self, self.rect(), 800)
        event.accept()


# 向后兼容别名：旧代码若直接引用 SpectrumPlot / SpectrumGLPlot 仍可用。
SpectrumPlot = SpectrumCurveWidget
SpectrumGLPlot = SpectrumCurveWidget


# ============================================================================
# 工厂函数
# ============================================================================

def create_spectrum_widget(parent=None, prefer_opengl: bool = False):
    """创建频谱组件，优先 OpenGL，不可用时降级为 QPainter 软件渲染。"""
    try:
        from PySide6.QtWidgets import QApplication
        platform = (QApplication.instance().platformName()
                    if QApplication.instance() else "")
        if platform in ("offscreen", "minimal", "webgl"):
            prefer_opengl = False
    except Exception:
        pass
    return SpectrumPanel(parent, prefer_opengl=prefer_opengl)
