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

import numpy as np
import time
from typing import Optional, List, Tuple, Dict

from constants import DEFAULT_SAMPLE_RATE_HZ

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, Slot, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QLinearGradient, QImage,
    QPainterPath, QPolygonF, QCursor,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QLabel,
    QDoubleSpinBox, QSlider, QFrame, QMenu, QSplitter, QToolTip, QPushButton,
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
_MODE_HOTKEYS = {
    Qt.Key_A: "AM",
    Qt.Key_N: "NFM",
    Qt.Key_F: "FM",
    Qt.Key_W: "WFM",
    Qt.Key_C: "CW",
}

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

# 频段彩带分类色（SDR++ bandplan 仿妆: 截图同款语义——
# 航空绿 / 低轨卫星灰 / 广播蓝 / 40m 业余红, 短波广播青）
BANDPLAN_CATEGORY_COLORS = {
    "broadcast": "#3E6FA3",
    "aviation": "#3F9142",
    "amateur": "#B5495C",
    "satellite": "#8A8F98",
    "shortwave": "#2E7D8C",
}

# 书签药丸高度（像素）：谱面顶部这一条带内点击 = 激活书签（不进调谐/拖拽）。
_BM_TAG_H = 16.0

# 瀑布图色带（低饱和，从冷到暖）
WATERFALL_COLORS = [
    "#EDEAE4", "#D8DCD8", "#AFC0C4", "#8FA8B0",
    "#6BA89A", "#9FB07A", "#C4B85C", "#C4845C", "#B85C5C",
]

# SDR++ 经典瀑布调色板（深蓝→青→绿→黄→橙→红，对标 SDR++ 默认色带）
WATERFALL_COLORS_SDRPP = [
    "#000000", "#000033", "#000066", "#000099", "#0000CC", "#0033FF", "#0099FF",
    "#00FFFF", "#66FF66", "#FFFF00", "#FF9900", "#FF3300", "#FF0000", "#CC0000",
]


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
    # SDR++ main_window.cpp:34 setRawFFTSize(fftSize) 支持大尺寸 FFT，16384 是常用档。
    FFT_SIZES = (1024, 2048, 4096, 8192, 16384)
    AVG_FRAMES = (1, 4, 8, 16)

    def __init__(self, num_bins: int = 512):
        self.num_bins = num_bins
        # 单位：Hz（SDR++ main_window.cpp:84-85 默认带宽 8MHz，这里给个常见 FM 广播中心）
        self.center_freq_hz = 98.5e6
        self.sample_rate_hz = DEFAULT_SAMPLE_RATE_HZ
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
        self.max_waterfall_lines = 256
        self._wf_buf = np.full((self.max_waterfall_lines, num_bins),
                               np.nan, dtype=np.float32)
        self._wf_head = 0          # 下一个写入槽位（写后回绕）
        self._wf_count = 0         # 已写入的有效行数（<= max_waterfall_lines）
        self.wf_rows_written = 0   # 单调递增计数，供瀑布画布做增量同步
        self._has_real_data = False
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

    # ------------------------------------------------------------------ 配置
    def set_window(self, name: str):
        if name in self.WINDOWS and name != self.window_name:
            self.window_name = name
            self._reset_average()

    def set_fft_size(self, n: int):
        n = int(n)
        if n in self.FFT_SIZES and n != self.fft_size:
            self.fft_size = n
            self._reset_average()

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
        self.wf_rows_written = 0
        self._prev_wf_row = None
        self.max_hold = None
        self.min_hold = None
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
        self.wf_rows_written = 0
        self._prev_wf_row = None
        self.max_hold = None
        self.min_hold = None
        self._reset_average()

    # ------------------------------------------------------------------ 窗函数
    def _window(self, n: int) -> np.ndarray:
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

    def _resample_max(self, spec: np.ndarray) -> np.ndarray:
        if len(spec) == self.num_bins:
            return spec.astype(np.float32)
        edges = np.linspace(0, len(spec), self.num_bins + 1).astype(int)
        out = np.empty(self.num_bins, dtype=np.float32)
        for i in range(self.num_bins):
            lo, hi = edges[i], edges[i + 1]
            seg = spec[lo:hi]
            out[i] = float(seg.max()) if seg.size else -120.0
        return out

    # ------------------------------------------------------------------ 频率轴
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
        """返回该频率的功率(dBFS)；无数据返回 None。读取真实频谱数组。"""
        if not self._has_real_data:
            return None
        v = self.spectrum[self.freq_to_bin(freq_hz)]
        return float(v) if np.isfinite(v) else None

    def find_peaks(self, rel_threshold_db: float,
                   max_peaks: int = 8) -> List[Tuple[float, float]]:
        """检测高于"噪声底 + rel_threshold_db"的局部极大值。

        返回 [(freq_hz, power_db), ...]，按功率降序。为不在 GUI 线程每帧
        扫描全谱，结果按约 10 帧（~2Hz）节流复用。"""
        if not self._has_real_data:
            return []
        s = self.spectrum
        if len(s) < 3:
            return []
        tick = int(self.wf_rows_written // 10)
        pcache = getattr(self, '_peak_cache', None)
        if (pcache is not None and pcache[0] == tick
                and abs(pcache[1] - float(rel_threshold_db)) < 1e-9):
            return pcache[2]
        finite = np.isfinite(s)
        ss = s[finite]
        if ss.size < 3:
            return []
        floor = float(np.median(ss))     # 已有限，median 快于 nanmedian
        thresh = floor + float(rel_threshold_db)
        left = np.empty_like(s)
        right = np.empty_like(s)
        left[0] = s[0]
        left[1:] = s[:-1]
        right[-1] = s[-1]
        right[:-1] = s[1:]
        mask = finite & (s >= left) & (s > right) & (s >= thresh)
        idxs = np.nonzero(mask)[0]
        peaks = [(self.bin_to_freq(int(i)), float(s[i])) for i in idxs]
        peaks.sort(key=lambda q: q[1], reverse=True)
        peaks = peaks[:max_peaks]
        self._peak_cache = (tick, float(rel_threshold_db), peaks)
        return peaks


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
    span = gen.sample_rate_hz
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
        span = gen.sample_rate_hz
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


def _load_bandplan() -> List[Dict[str, object]]:
    """加载频段彩带配置; 缺文件/坏 JSON/坏条目一律跳过该条, 绝不造假。

    每条目: {"label", "start_hz", "stop_hz", "category"}; category
    必须在 BANDPLAN_CATEGORY_COLORS 内。缓存模块级, 首次绘制时载入。
    """
    global _BANDPLAN_CACHE
    if _BANDPLAN_CACHE is not None:
        return _BANDPLAN_CACHE
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "bandplan.json")
    out: List[Dict[str, object]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        for b in raw.get("bands", []):
            try:
                label = str(b.get("label", "")).strip()
                f0 = float(b["start_hz"])
                f1 = float(b["stop_hz"])
                cat = str(b.get("category", "")).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if not label or not (0 < f0 < f1) or cat not in \
                    BANDPLAN_CATEGORY_COLORS:
                continue
            out.append({"label": label, "start_hz": f0, "stop_hz": f1,
                        "category": cat})
    except (OSError, ValueError):
        out = []
    _BANDPLAN_CACHE = out
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


class _PlotState:
    """两个渲染路径共享的交互状态。"""

    def __init__(self, panel: "SpectrumPanel"):
        self.panel = panel
        self.db_min = -100.0
        self.db_max = -20.0
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
        self.show_peaks = True
        self.peak_rel_db = 6.0
        # ---- 瀑布调色板 / 时间平滑 ----
        self.wf_colors: List[str] = list(WATERFALL_COLORS)   # 当前瀑布色带
        self.wf_smooth: bool = False                 # IIR 时间平滑（仅瀑布）
        # ---- 调谐步进网格（对标 SDR++ waterfall.h:38 snapInterval）----
        # 普通滚轮/左右箭头按此步进调谐；Shift ×10，Alt ×0.1。
        self.snap_interval = 10_000.0                   # Hz，默认 10 kHz
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
        self._wf_lut: Optional[np.ndarray] = None   # db→RGB 查找表 (256,3)
        self._wf_lut_key: Tuple[float, float] = (0.0, 0.0)

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


def _render_spectrum(painter: QPainter, state: _PlotState, w: int, h: int):
    """上方 FFT 频谱曲线 widget 的完整绘制（整个 widget 即谱面）。

    仅画频谱相关元素：网格 / VFO 带宽矩形 / 谱线 / 峰值 / 中心游标 /
    悬停读数 / 固定 marker / 频率轴与 dB 轴。瀑布图由 WaterfallWidget 单独绘制。
    无真实 IQ 数据时只画网格 + 红色“未连接 SDR”提示，不画任何假谱线。
    """
    panel = state.panel
    gen = panel.generator
    has_data = gen.has_data()

    bg = QColor(panel.color_bg)
    grid = QColor(panel.color_grid)
    text = QColor(panel.color_text)
    line = QColor(panel.color_line)

    painter.fillRect(0, 0, w, h, bg)

    # 谱面绘图区（底部留出频率轴刻度空间）；x 方向从 0 到全宽，
    # 与 _freq_at_x 的“x=0 ↔ 视口左缘”映射保持一致。
    spec_rect = QRectF(0, 0, w, max(1, h - _SPEC_BOTTOM_PAD))

    view_center = _view_center_hz(state)
    span = gen.sample_rate_hz

    _draw_grid(painter, spec_rect, grid)
    # 频段彩带垫底（SDR++ bandplan 仿妆）: 网格之上、谱线之下
    _draw_bandplan(painter, state, spec_rect)

    # ---- 频谱曲线：仅在有真 IQ 数据时绘制 ----
    if has_data:
        # VFO 带宽矩形（先画在谱线下方；无数据时不画，保持"未连接"干净）
        _draw_vfo_band(painter, state, spec_rect)
        _draw_spectrum(painter, state, spec_rect, line)
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
        msg2 = "点击工具栏「连接」选择设备"
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

    # 频率轴（画在谱面底部预留区）+ dB 轴
    _draw_freq_scale(painter, spec_rect, view_center, span, text)
    _draw_db_scale(painter, spec_rect, state, text)


def _render_wf_only(painter: QPainter, state: _PlotState, w: int, h: int):
    """下方瀑布图 widget 的完整绘制（整个 widget 即瀑布画布）。

    复用持久增量画布 _draw_waterfall；无数据时画布为空白背景，不画假噪声。
    """
    panel = state.panel
    bg = QColor(panel.color_bg)
    painter.fillRect(0, 0, w, h, bg)
    if state.show_waterfall and h > 0:
        wf_rect = QRectF(0, 0, w, h)
        _draw_waterfall(painter, wf_rect, state)


def _draw_grid(painter, rect, grid):
    painter.setPen(QPen(grid, 1, Qt.DashLine))
    for i in range(1, 5):
        x = rect.x() + rect.width() * i / 5
        painter.drawLine(QPointF(x, rect.y()),
                         QPointF(x, rect.y() + rect.height()))
    for i in range(1, 4):
        y = rect.y() + rect.height() * i / 4
        painter.drawLine(QPointF(rect.x(), y),
                         QPointF(rect.x() + rect.width(), y))


def _db_to_y(rect, db, db_min, db_max):
    h = rect.height()
    ratio = (db - db_min) / (db_max - db_min)
    ratio = max(0.0, min(1.0, ratio))
    return rect.y() + rect.height() - ratio * h * 0.92


def _draw_spectrum(painter, state, rect, line_color):
    """向量化画频谱折线 + 渐变填充。numpy 一次算完全部 x/y，QPolygonF
    单次 drawPolyline/drawPolygon，避免逐 bin 函数调用与路径拼接。"""
    gen = state.panel.generator
    spec = gen.spectrum
    n = len(spec)
    if n == 0:
        return
    rx0, rw = rect.x(), rect.width()
    ry0, rh = rect.y(), rect.height()
    view_center = _view_center_hz(state)
    span = gen.sample_rate_hz
    f_left = view_center - span / 2.0
    ii = np.arange(n, dtype=np.float64)
    freq = (gen.center_freq_hz - gen.sample_rate_hz / 2.0
            + gen.sample_rate_hz * (ii + 0.5) / n)
    db = np.where(np.isfinite(spec), spec, state.db_min)
    x = rx0 + (freq - f_left) / span * rw
    ratio = np.clip((db - state.db_min) / (state.db_max - state.db_min),
                    0.0, 1.0)
    y = ry0 + rh - ratio * rh * 0.92
    poly = QPolygonF([QPointF(float(a), float(b))
                      for a, b in zip(x, y)])
    # 低饱和橙渐变填充
    fillpoly = QPolygonF()
    fillpoly.append(QPointF(rx0, ry0 + rh))
    fillpoly += poly
    fillpoly.append(QPointF(rx0 + rw, ry0 + rh))
    grad = QLinearGradient(0, ry0, 0, ry0 + rh)
    grad.setColorAt(0.0, QColor(PAL_LINE).lighter(130))
    grad.setColorAt(1.0, QColor(PAL_LINE).darker(160))
    # PySide6 QPainter 无 fillPolygon(poly, brush)：把渐变设为当前 brush，
    # drawPolygon 会按当前 brush 填充，语义与原 fillPolygon 一致。
    painter.setBrush(QBrush(grad))
    painter.drawPolygon(fillpoly)
    painter.setPen(QPen(line_color, 2))
    painter.drawPolyline(poly)


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
        span = gen.sample_rate_hz
        f_left = view_center - span / 2.0
        ii = np.arange(n, dtype=np.float64)
        freq = (gen.center_freq_hz - span / 2.0
                + span * (ii + 0.5) / n)
        db = np.where(np.isfinite(hold), hold, state.db_min)
        x = rect.x() + (freq - f_left) / span * rect.width()
        ratio = np.clip((db - state.db_min) / (state.db_max - state.db_min),
                        0.0, 1.0)
        y = rect.y() + rect.height() - ratio * rect.height() * 0.92
        poly = QPolygonF([QPointF(float(a), float(b))
                          for a, b in zip(x, y)])
        painter.setPen(QPen(color, 1))
        painter.drawPolyline(poly)

    if gen.show_max_hold and gen.max_hold is not None:
        _line(gen.max_hold, QColor(PAL_MAXHOLD))
    if gen.show_min_hold and gen.min_hold is not None:
        _line(gen.min_hold, QColor(PAL_MINHOLD))


def _draw_bandplan(painter, state, rect, overlay=False):
    """频段彩带（SDR++ bandplan 仿妆），两遍绘制防被谱线渐变填充盖住。

    overlay=False —— 网格之后、谱线之下：淡色填充垫底；
    overlay=True  —— 谱线/包络/峰值之后：底部 4px 色条 + 带内标签。
    标签只在带足够宽 (>70px) 时绘制。与书签竖旗/固定 marker 不同 y 带。
    """
    if not state.show_bandplan or not state.bandplan:
        return
    view_center = _view_center_hz(state)
    span = state.panel.generator.sample_rate_hz
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    fm = painter.fontMetrics()
    for b in state.bandplan:
        rng = _band_x_range(rect, view_center, span,
                            float(b["start_hz"]), float(b["stop_hz"]))
        if rng is None:
            continue
        x0, x1 = rng
        color = QColor(BANDPLAN_CATEGORY_COLORS[str(b["category"])])
        if not overlay:
            fill = QColor(color)
            fill.setAlpha(26)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawRect(QRectF(x0, rect.y(), x1 - x0, rect.height()))
            continue
        strip = QColor(color)
        strip.setAlpha(130)
        painter.setBrush(QBrush(strip))
        painter.drawRect(QRectF(x0, rect.y() + rect.height() - 4.0,
                                x1 - x0, 4.0))
        if x1 - x0 > 70.0:
            label = str(b["label"])
            tw = fm.horizontalAdvance(label)
            tx = max(rect.x() + 2.0,
                     min(x0 + (x1 - x0 - tw) / 2.0,
                         rect.x() + rect.width() - tw - 2.0))
            lc = QColor(color)
            lc.setAlpha(215)
            painter.setPen(QPen(lc, 1))
            painter.drawText(
                QPointF(tx, rect.y() + rect.height() - 8.0), label)


def _draw_bookmark_tags(painter, state, rect):
    """书签竖旗（gqrx 书签签名）：谱面顶部暗金药丸 + 贯顶虚线。

    药丸夹紧在谱面横向范围内（视口边缘的书签仍可见可点）；
    点击命中由 _TuningPlotMixin._bookmark_tag_at 用同一几何反算。
    """
    if not state.show_bookmarks or not state.bookmarks:
        return
    gen = state.panel.generator
    view_center = _view_center_hz(state)
    span = gen.sample_rate_hz
    f_left = view_center - span / 2.0
    color = QColor(PAL_BOOKMARK)
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    fm = painter.fontMetrics()
    painter.setBrush(QBrush(color))
    for freq_hz, name in state.bookmarks:
        x = rect.x() + (float(freq_hz) - f_left) / span * rect.width()
        if x < rect.x() or x > rect.x() + rect.width():
            continue
        painter.setPen(QPen(color, 1, Qt.DashLine))
        painter.drawLine(QPointF(x, rect.y() + _BM_TAG_H),
                         QPointF(x, rect.y() + rect.height()))
        text = str(name)
        pw = fm.horizontalAdvance(text) + 10
        x0 = min(max(rect.x(), x - pw / 2.0),
                 rect.x() + rect.width() - pw)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(x0, rect.y() + 1.0, pw, _BM_TAG_H - 2.0),
                                3.0, 3.0)
        painter.setPen(QPen(QColor(PAL_BG), 1))
        painter.drawText(QPointF(x0 + 5.0, rect.y() + 1.0 + fm.ascent()), text)


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


def _draw_peaks(painter, state, rect, color):
    gen = state.panel.generator
    peaks = gen.find_peaks(state.peak_rel_db) if state.show_peaks else []
    if not peaks:
        return
    painter.setPen(QPen(color, 1))
    painter.setBrush(QBrush(color))
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    view_center = _view_center_hz(state)
    span = gen.sample_rate_hz
    for freq_hz, db in peaks:
        x = _freq_to_x(rect, view_center, span, freq_hz)
        y = _db_to_y(rect, db, state.db_min, state.db_max)
        painter.drawEllipse(QPointF(x, y), 3, 3)
        label = f"{freq_hz/1e6:.3f}MHz {db:.1f}dB"
        painter.drawText(QPointF(x + 6, y - 6), label)


def _draw_hover_readout(painter, state, rect, text_color, line_color):
    if state.mouse_x_ratio is None or not state.mouse_in_spectrum:
        return
    gen = state.panel.generator
    view_center = _view_center_hz(state)
    span = gen.sample_rate_hz
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
    db_txt = f"{val:.1f} dBFS" if val is not None else "--"
    painter.drawText(QPointF(rect.x() + 6, rect.y() + 14),
                     f"{freq_hz/1e6:.4f} MHz   {db_txt}")


def _draw_fixed_markers(painter, state, rect, color):
    gen = state.panel.generator
    view_center = _view_center_hz(state)
    span = gen.sample_rate_hz
    f = QFont()
    f.setPointSize(8)
    for freq_hz in state.markers:
        x = _freq_to_x(rect, view_center, span, freq_hz)
        val = gen.value_at_freq(freq_hz)
        painter.setPen(QPen(color, 1))
        painter.drawLine(QPointF(x, rect.y()),
                         QPointF(x, rect.y() + rect.height()))
        painter.setFont(f)
        db_txt = f"{val:.1f}dB" if val is not None else "--"
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
    """把一行 dB 频谱（长度 num_bins）映射到画布一行 (w,4) 的 RGB。"""
    w = dst_row.shape[0]
    n = len(row_db)
    idx = np.linspace(0, n - 1, w).astype(np.int64)
    vals = row_db[idx]
    vals = np.where(np.isfinite(vals), vals, state.db_min)
    dmin, dmax = state.db_min, state.db_max
    t = np.clip((vals - dmin) / (dmax - dmin + 1e-9), 0.0, 1.0)
    li = (t * 255.0).astype(np.int64)
    dst_row[:, 0:3] = state._wf_lut[li]
    dst_row[:, 3] = 255


def _wf_ensure_canvas(state: "_PlotState", w: int, h: int) -> bool:
    """确保持久瀑布画布缓冲尺寸匹配；尺寸变化时重建为空白。"""
    if w <= 0 or h <= 0:
        state._wf_canvas = None
        return False
    if (state._wf_cw == w and state._wf_ch == h
            and state._wf_buf is not None and state._wf_canvas is not None):
        return True
    bg = QColor(PAL_BG)
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
    bg = QColor(PAL_BG)
    total = gen.wf_rows_written
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


def _draw_freq_scale(painter, rect, view_center_hz: float, span_hz: float,
                     text_color):
    painter.setPen(text_color)
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    for i in range(5):
        freq = view_center_hz - span_hz / 2.0 + span_hz * i / 4.0
        x = rect.x() + rect.width() * i / 4.0
        s = f"{freq/1e6:.2f}"
        tw = painter.fontMetrics().horizontalAdvance(s)
        painter.drawText(QPointF(x - tw / 2, rect.y() + rect.height() + 14), s)


def _draw_db_scale(painter, rect, state, text_color):
    painter.setPen(text_color)
    f = QFont()
    f.setPointSize(8)
    painter.setFont(f)
    for i in range(5):
        db = state.db_max - (state.db_max - state.db_min) * i / 4.0
        y = rect.y() + rect.height() * 0.92 * i / 4.0 + rect.height() * 0.04
        painter.drawText(QPointF(rect.x() + 4, y + 4), f"{db:.0f}")


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

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        root.addLayout(self._build_toolbar())

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
        self._wf_plot.setMinimumHeight(60)

        # 右侧 dB Max/Min 垂直滑杆（对标 SDR++ main_window.cpp:635-656）
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)
        body.addWidget(self._splitter, stretch=1)
        body.addLayout(self._build_db_sliders())
        root.addLayout(body, stretch=1)

        # 初始：未连接 → 控件置灰
        self._connected = False
        self._set_controls_enabled(False)

    def _redraw(self):
        """同时重绘 FFT 曲线与瀑布图。"""
        self._fft_plot.update()
        self._wf_plot.update()

    # ------------------------------------------------------------------ 工具栏
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 0, 4, 0)
        bar.setSpacing(6)

        bar.addWidget(QLabel("窗:"))
        self._win_combo = QComboBox()
        self._win_combo.addItems(SpectrumDataGenerator.WINDOWS)
        # SDR++ main_window.cpp:91 默认 NUTTALL
        self._win_combo.setCurrentText(self.generator.window_name)
        self._win_combo.currentTextChanged.connect(self.generator.set_window)
        bar.addWidget(self._win_combo)

        bar.addWidget(QLabel("FFT:"))
        self._fft_combo = QComboBox()
        self._fft_combo.addItems([str(s) for s in SpectrumDataGenerator.FFT_SIZES])
        self._fft_combo.setCurrentText("2048")
        self._fft_combo.currentTextChanged.connect(
            lambda t: self.generator.set_fft_size(int(t)))
        bar.addWidget(self._fft_combo)

        bar.addWidget(QLabel("平均:"))
        self._avg_combo = QComboBox()
        self._avg_combo.addItems([str(a) for a in SpectrumDataGenerator.AVG_FRAMES])
        self._avg_combo.setCurrentText("1")
        self._avg_combo.currentTextChanged.connect(
            lambda t: self.generator.set_avg_frames(int(t)))
        bar.addWidget(self._avg_combo)

        # 性能模式（高/均衡/低）：切换 FFT 点数 / 谱线数 / 刷新间隔
        bar.addWidget(QLabel("性能:"))
        self._perf_combo = QComboBox()
        self._perf_combo.addItems(["高", "均衡", "低"])
        self._perf_combo.setCurrentIndex(0)
        self._perf_combo.currentIndexChanged.connect(self.set_performance_mode)
        bar.addWidget(self._perf_combo)

        # 瀑布调色板（默认低饱和 / SDR++ 经典蓝-青-绿-黄-红）
        bar.addWidget(QLabel("调色板:"))
        self._palette_combo = QComboBox()
        self._palette_combo.addItems(["默认", "经典SDR++"])
        self._palette_combo.currentIndexChanged.connect(self._on_palette_changed)
        bar.addWidget(self._palette_combo)

        # 瀑布时间平滑（IIR，仅影响瀑布显示）
        self._wf_smooth_chk = QCheckBox("瀑布平滑")
        self._wf_smooth_chk.setChecked(False)
        self._wf_smooth_chk.toggled.connect(self._on_wf_smooth_toggled)
        bar.addWidget(self._wf_smooth_chk)

        self._peak_chk = QCheckBox("峰值")
        self._peak_chk.setChecked(True)
        self._peak_chk.toggled.connect(
            lambda on: setattr(self._state, "show_peaks", bool(on)))
        bar.addWidget(self._peak_chk)

        # Max/Min hold 包络（SDR++ maxHold/minHold 复选框签名）
        self._maxhold_chk = QCheckBox("Max保持")
        self._maxhold_chk.setToolTip(
            "Max hold：逐 bin 保留历史最高电平包络（抓瞬时 burst/峰值轮廓）")
        self._maxhold_chk.toggled.connect(self.generator.set_max_hold)
        bar.addWidget(self._maxhold_chk)
        self._minhold_chk = QCheckBox("Min保持")
        self._minhold_chk.setToolTip(
            "Min hold：逐 bin 保留历史最低电平包络（看噪声底下沿/底噪漂移）")
        self._minhold_chk.toggled.connect(self.generator.set_min_hold)
        bar.addWidget(self._minhold_chk)

        # 书签竖旗开关（gqrx 书签标签；标签本体由主窗口 set_bookmarks 灌入）
        self._bookmark_chk = QCheckBox("书签")
        self._bookmark_chk.setChecked(True)
        self._bookmark_chk.setToolTip(
            "在谱面顶部显示书签竖旗；点击药丸直接调到该书签频率")
        self._bookmark_chk.toggled.connect(
            lambda on: (setattr(self._state, "show_bookmarks", bool(on)),
                        self._redraw()))
        bar.addWidget(self._bookmark_chk)

        # 频段彩带开关（SDR++ bandplan 仿妆；条目在 desktop/bandplan.json）
        self._bandplan_chk = QCheckBox("频段")
        self._bandplan_chk.setChecked(True)
        self._bandplan_chk.setToolTip(
            "在谱面上显示频段彩带（短波/广播/航空/业余/卫星）与名称；"
            "条目可在 bandplan.json 配置")
        self._bandplan_chk.toggled.connect(
            lambda on: (setattr(self._state, "show_bandplan", bool(on)),
                        self._redraw()))
        bar.addWidget(self._bandplan_chk)

        bar.addWidget(QLabel("阈值dB:"))
        self._peak_spin = QDoubleSpinBox()
        self._peak_spin.setRange(1.0, 30.0)
        self._peak_spin.setValue(self._state.peak_rel_db)
        self._peak_spin.setSingleStep(1.0)
        self._peak_spin.valueChanged.connect(
            lambda v: setattr(self._state, "peak_rel_db", float(v)))
        bar.addWidget(self._peak_spin)

        # 调谐模式（SDR++ 调谐模式按钮同原语；gqrx 中键 / SDR# Center 亦是）
        self._tune_mode_btn = QPushButton("调谐:中心")
        self._tune_mode_btn.setCheckable(True)
        self._tune_mode_btn.setChecked(True)
        self._tune_mode_btn.setToolTip(
            "中心模式：点击谱面重调硬件中心频率（画面跟过去）\n"
            "VFO 模式：点击只移动当前 VFO，画面不动（瀑布保留多信号轨迹）")
        self._tune_mode_btn.toggled.connect(self._on_tune_mode_toggled)
        bar.addWidget(self._tune_mode_btn)

        bar.addStretch(1)

        # 只读状态显示：当前中心频率 / span / SNR（无数据时显 --）
        # 对标 SDR++ main_window.cpp 顶部频率读数；这里只读展示，由 _refresh_status_labels 刷新。
        self._freq_label = QLabel("--")
        self._span_label = QLabel("--")
        self._snr_label = QLabel("SNR --")
        _lbl_color = _c["gray_200"]
        self._freq_label.setStyleSheet(f"color:{_lbl_color};")
        self._span_label.setStyleSheet(f"color:{_lbl_color};")
        self._snr_label.setStyleSheet(f"color:{_c['spectrum_max_hold']};")
        bar.addWidget(self._freq_label)
        bar.addSpacing(12)
        bar.addWidget(self._span_label)
        bar.addSpacing(12)
        bar.addWidget(self._snr_label)
        # FFT 仪器化读数（gqrx 把瀑布当测量仪器）：RBW = span/FFT 点数，
        # 瀑布时间跨度 = 环形缓冲行数 × 绘图定时器间隔。
        self._fft_info_label = QLabel("RBW --")
        self._fft_info_label.setStyleSheet(f"color:{_lbl_color};")
        bar.addSpacing(12)
        bar.addWidget(self._fft_info_label)
        return bar

    # ------------------------------------------------------------------ dB 滑杆
    def _build_db_sliders(self) -> QVBoxLayout:
        """右侧 Max/Min 垂直滑杆，对标 SDR++ main_window.cpp:635-656。

        SDR++ 行为（main_window.cpp:638-656）：
          - VSliderFloat 范围 0.0 ~ -160.0（顶部 0 dB，底部 -160 dB）
          - 拖 Max 时 fftMax = max(fftMax, fftMin + 10)   （line 639）
          - 拖 Min 时 fftMin = min(fftMax - 10, fftMin)   （line 652）
        """
        col = QVBoxLayout()
        col.setContentsMargins(2, 0, 2, 0)
        col.setSpacing(2)

        col.addWidget(QLabel("Max", alignment=Qt.AlignHCenter))
        self._db_max_slider = QSlider(Qt.Vertical)
        # Qt 垂直滑杆：minimum 在底部、maximum 在顶部。
        # 我们用 dB 整数值：-160（底）.. 0（顶）。
        self._db_max_slider.setRange(-160, 0)
        self._db_max_slider.setValue(int(self._state.db_max))
        self._db_max_slider.setMinimumHeight(150)
        self._db_max_slider.valueChanged.connect(self._on_db_max_changed)
        col.addWidget(self._db_max_slider, stretch=1)

        col.addWidget(QLabel("Min", alignment=Qt.AlignHCenter))
        self._db_min_slider = QSlider(Qt.Vertical)
        self._db_min_slider.setRange(-160, 0)
        self._db_min_slider.setValue(int(self._state.db_min))
        self._db_min_slider.setMinimumHeight(150)
        self._db_min_slider.valueChanged.connect(self._on_db_min_changed)
        col.addWidget(self._db_min_slider, stretch=1)

        return col

    def _on_db_max_changed(self, val: int):
        # SDR++ main_window.cpp:639: fftMax = max(fftMax, fftMin + 10)
        val = int(val)
        min_val = self._db_min_slider.value()
        if val <= min_val + 10:
            val = min_val + 10
            self._db_max_slider.blockSignals(True)
            self._db_max_slider.setValue(val)
            self._db_max_slider.blockSignals(False)
        self._state.db_max = float(val)
        self._redraw()

    def _on_db_min_changed(self, val: int):
        # SDR++ main_window.cpp:652: fftMin = min(fftMax - 10, fftMin)
        val = int(val)
        max_val = self._db_max_slider.value()
        if val >= max_val - 10:
            val = max_val - 10
            self._db_min_slider.blockSignals(True)
            self._db_min_slider.setValue(val)
            self._db_min_slider.blockSignals(False)
        self._state.db_min = float(val)
        self._redraw()

    def _set_controls_enabled(self, on: bool):
        for w_ in (self._win_combo, self._fft_combo, self._avg_combo,
                   self._perf_combo, self._palette_combo, self._wf_smooth_chk,
                   self._peak_chk, self._peak_spin, self._tune_mode_btn,
                   self._maxhold_chk, self._minhold_chk, self._bookmark_chk,
                   self._bandplan_chk):
            w_.setEnabled(on)

    def _on_tune_mode_toggled(self, checked: bool):
        self._state.tune_mode = "center" if checked else "normal"
        self._tune_mode_btn.setText("调谐:中心" if checked else "调谐:VFO")

    # ------------------------------------------------------------------ 性能模式
    # level 0=高 / 1=均衡 / 2=低：FFT 点数、谱线数、刷新间隔三档联动。
    _PERF_LEVELS = (
        # (fft_size, num_bins, refresh_ms)
        (2048, 512, 50),
        (1024, 384, 66),
        (512, 256, 100),
    )

    def set_performance_mode(self, level: int):
        """切换渲染性能档位：重建 FFT/谱线与瀑布环形缓冲，调刷新定时器。

        保持 center_freq_hz / sample_rate_hz 不变。level: 0 高 / 1 均衡 / 2 低。
        """
        level = int(level)
        if not (0 <= level < len(self._PERF_LEVELS)):
            return
        fft_size, num_bins, refresh_ms = self._PERF_LEVELS[level]
        self.generator.reconfigure(fft_size, num_bins)
        # 调整绘图定时器刷新间隔（QTimer.setInterval 即时生效）
        timer = getattr(self._plot, "_timer", None)
        if timer is not None:
            timer.setInterval(refresh_ms)
        # 同步 FFT 下拉（512 不在 FFT_SIZES 列表时保持原显示，不报错）
        self._fft_combo.blockSignals(True)
        self._fft_combo.setCurrentText(str(fft_size))
        self._fft_combo.blockSignals(False)
        self._refresh_status_labels()

    # ------------------------------------------------------------------ 调色板 / 平滑
    def _on_palette_changed(self, idx: int):
        """切换瀑布调色板：重建 LUT 并清空 key，使整幅瀑布用新色带重绘。"""
        self._state.wf_colors = list(
            WATERFALL_COLORS_SDRPP if idx == 1 else WATERFALL_COLORS)
        # 强制 LUT 重建 + 让画布从空白重绘（旧行随新数据以新色带重涂）
        self._state._wf_lut = None
        self._state._wf_lut_key = (0.0, 0.0)
        self._state._wf_cw = -1
        self._state._wf_ch = -1
        self._state._wf_consumed = 0
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
        self._refresh_status_labels()

    def clear(self):
        """清空频谱（未连接语义）。"""
        self.set_connected(False)

    @Slot(object, float, float)
    def update_iq(self, iq_data: np.ndarray, center_freq_hz: float,
                  sample_rate_hz: float):
        """真接 SDR 复 IQ：做窗+fft+dBFS+平均。"""
        self.generator.push_iq(iq_data, center_freq_hz, sample_rate_hz)
        self._refresh_status_labels()

    # 向后兼容：main_window 当前调用 set_iq_data(iq, sample_rate)
    @Slot(object, float)
    def set_iq_data(self, iq, sample_rate: float = float(DEFAULT_SAMPLE_RATE_HZ)):
        cf = self.generator.center_freq_hz
        self.update_iq(iq, cf, sample_rate)

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
        """刷新顶部中心频率/span/SNR 只读读数并触发重绘。无数据时显 --。"""
        if self.generator.has_data():
            self._freq_label.setText(f"{self.generator.center_freq_hz/1e6:.3f} MHz")
            self._span_label.setText(f"{self.generator.sample_rate_hz/1e6:.3f} MHz")
            snr = self.compute_snr_db()
            self._snr_label.setText(
                f"SNR {snr:.1f} dB" if snr is not None else "SNR --")
            rbw = self.generator.sample_rate_hz / max(1, self.generator.fft_size)
            timer = getattr(self._plot, "_timer", None)
            ms = timer.interval() if timer is not None else 50
            wf_s = self.generator.max_waterfall_lines * ms / 1000.0
            self._fft_info_label.setText(
                f"RBW {rbw:g} Hz · 瀑布 {wf_s:g} s")
        else:
            self._freq_label.setText("--")
            self._span_label.setText("--")
            self._snr_label.setText("SNR --")
            self._fft_info_label.setText("RBW --")
        self._redraw()

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

    # ------------------------------------------------------------------ 工具
    def _x_ratio(self, event):
        return event.position().x() / max(1, self.width())

    def _spectrum_height(self) -> int:
        """谱图区高度像素。默认按 55% 估；SpectrumCurveWidget 已重写为整块高度。"""
        h = self.height()
        return int(h * 0.55) if self.state.show_waterfall else h

    def _freq_at_x(self, x_px: float) -> float:
        """谱面某 x 像素对应的绝对频率（含视图平移偏移）。"""
        gen = self.state.panel.generator
        view_center = _view_center_hz(self.state)
        span = gen.sample_rate_hz
        return view_center - span / 2.0 + x_px / max(1, self.width()) * span

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
        span = gen.sample_rate_hz
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
            span = gen.sample_rate_hz
            new_span = max(48_000.0, min(20_000_000.0, span * factor))
            # 光标处绝对频率（含 Ctrl 拖动的视图平移偏移）
            r = event.position().x() / max(1, self.width())
            view_center = _view_center_hz(self.state)
            anchor_freq = view_center + (r - 0.5) * span
            # 保持光标频率不变：new_view_center = anchor - (r-0.5)*new_span
            new_view_center = anchor_freq - (r - 0.5) * new_span
            gen.sample_rate_hz = new_span
            # view_offset 保持不变，反解出真调谐中心
            gen.center_freq_hz = new_view_center - self.state.view_offset_hz
            self._emit_tuned(gen.center_freq_hz)
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
    def keyPressEvent(self, event):
        # main_window.cpp:553-576：左右箭头按 snapInterval 调谐。
        # 上下箭头 = 细调（snap_interval*0.1，对标 SDR++ 步进微调）；
        # Shift ×10 / Alt ×0.1 修饰同样生效。
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            gen = self.state.panel.generator
            if key in (Qt.Key_Left, Qt.Key_Right):
                interval = self.state.snap_interval          # 左右 = 整格步进
                direction = -1.0 if key == Qt.Key_Left else 1.0
            else:
                interval = self.state.snap_interval * 0.1     # 上下 = 细调 1/10 格
                direction = 1.0 if key == Qt.Key_Up else -1.0
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
        # 单键切解调（gqrx 键盘流）；S=LSB，Shift+S=USB
        mode = None
        if key == Qt.Key_S:
            mode = "USB" if (mods & Qt.ShiftModifier) else "LSB"
        elif key in _MODE_HOTKEYS:
            mode = _MODE_HOTKEYS[key]
        if mode is not None:
            if _get_demod_mode is not None and _get_demod_mode(mode) is None:
                self._show_tune_tip(f"模式 {mode} 未注册")
                return
            self.state.panel.mode_hotkey_requested.emit(mode)
            self._show_tune_tip(f"模式 {mode}")
            self.update()
            return
        # 滤波宽窄档（, 窄 / . 宽，gqrx </./> 惯例；按模式预设表步进）
        if key in (Qt.Key_Comma, Qt.Key_Period):
            mgr = self.state.panel.vfo_manager
            vfo = mgr.current if mgr is not None else None
            direction = -1 if key == Qt.Key_Comma else 1
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
                shift = -(x - self._vfo_press_x) / max(1, self.width()) * gen.sample_rate_hz
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
        # 并按命中区域切换鼠标光标：边沿 → SizeHorCursor（提示可拖改带宽）。
        mgr = self.state.panel.vfo_manager
        if mgr is not None and not self._is_panning:
            y = event.position().y()
            if y <= self._spectrum_height():
                vfo, reg = self._hit_vfo_at(event.position().x())
                if vfo is not None:
                    mgr.set_active_context(vfo, temporary=True)
                    if reg in ('left', 'right'):
                        self.setCursor(Qt.SizeHorCursor)
                    else:
                        self.unsetCursor()
                else:
                    mgr.clear_active_context()
                    self.unsetCursor()
            else:
                mgr.clear_active_context()
                self.unsetCursor()
        else:
            self.unsetCursor()

        if self._is_panning:
            dx = event.position().x() - self._press_anchor_x
            gen = self.state.panel.generator
            shift = -dx / max(1, self.width()) * gen.sample_rate_hz
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
        freq = (gen.center_freq_hz - gen.sample_rate_hz / 2.0
                + ratio * gen.sample_rate_hz)
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

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 橡皮筋新建 VFO 结束：按 rubber-band 区间 center/bw 创建
            if self._vfo_create_mode:
                self._vfo_create_mode = False
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
            if self._vfo_drag_mode is not None:
                was_resize = self._vfo_drag_mode in ('resize_l', 'resize_r')
                self._vfo_drag_mode = None
                vfo = self._vfo_drag_vfo
                self._vfo_drag_vfo = None
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
            self._is_panning = False
            if moved > 4:
                if not self._rf_shift:
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
                    self._tune_click(self._x_ratio(event))
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
        # 双击直接调谐到鼠标处频率（SDR++ waterfall 行为），
        # center/VFO 模式语义与单击一致（_tune_click）
        self._tune_click(self._x_ratio(event))
        self.update()


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
        span = gen.sample_rate_hz
        # 捏合张开(scale>1) → span 缩小；捏合收拢(scale<1) → span 放大。
        new_span = span / max(0.2, min(5.0, scale_factor))
        new_span = max(48_000.0, min(20_000_000.0, new_span))
        r = center_pos.x() / max(1, self.width())
        view_center = _view_center_hz(self.state)
        anchor_freq = view_center + (r - 0.5) * span
        new_view_center = anchor_freq - (r - 0.5) * new_span
        gen.sample_rate_hz = new_span
        gen.center_freq_hz = new_view_center - self.state.view_offset_hz
        self._emit_tuned(gen.center_freq_hz)
        self.update()

    def _on_touch_pan(self, dx: float, dy: float) -> None:
        """单指拖动：平移中心频率（与 mouseMoveEvent 普通拖动同数学）。"""
        gen = self.state.panel.generator
        shift = -dx / max(1, self.width()) * gen.sample_rate_hz
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

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        _render_wf_only(p, self.state, self.width(), self.height())
        p.end()


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
