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
from typing import Optional, List, Tuple

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, Slot, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QLinearGradient, QImage,
    QPainterPath, QPolygonF,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QCheckBox, QLabel,
    QDoubleSpinBox, QSlider, QFrame,
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


# ============================================================================
# 默认低饱和配色（米白 / 蓝灰 / 橙）
# ============================================================================

PAL_BG = "#F5F3EF"          # 米白背景
PAL_GRID = "#5B7B8C"        # 蓝灰 网格/文字
PAL_LINE = "#C4845C"        # 橙   谱线/游标
PAL_PEAK = "#6BA89A"        # 绿   峰值标注
PAL_OFFLINE = "#B85C5C"     # 红   未连接提示

# 瀑布图色带（低饱和，从冷到暖）
WATERFALL_COLORS = [
    "#EDEAE4", "#D8DCD8", "#AFC0C4", "#8FA8B0",
    "#6BA89A", "#9FB07A", "#C4B85C", "#C4845C", "#B85C5C",
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
        self.sample_rate_hz = 2.4e6
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

        # 写入环形行缓冲：新行落在 head 槽位，head 回绕（SDR++ currentFFTLine 语义）
        self._wf_buf[self._wf_head] = self.spectrum
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

        返回 [(freq_hz, power_db), ...]，按功率降序。无数据返回 []。
        """
        if not self._has_real_data:
            return []
        s = self.spectrum
        if len(s) < 3:
            return []
        floor = float(np.nanmedian(s))
        thresh = floor + rel_threshold_db
        peaks: List[Tuple[float, float]] = []
        for i in range(1, len(s) - 1):
            v = s[i]
            if not np.isfinite(v):
                continue
            if v >= s[i - 1] and v > s[i + 1] and v >= thresh:
                peaks.append((self.bin_to_freq(i), float(v)))
        peaks.sort(key=lambda p: p[1], reverse=True)
        return peaks[:max_peaks]


# ============================================================================
# 颜色工具
# ============================================================================

def value_to_color(value: float, min_val: float, max_val: float,
                   colors: List[str]) -> QColor:
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


def _draw_vfo_band(painter: QPainter, state: "_PlotState", rect: QRectF):
    """在谱面画 VFO 带宽矩形（对标 SDR++ waterfall.cpp:221-231 / waterfall.h:77）。

    SDR++ VFO 是半透明填充矩形 + 选中边框；这里用默认主题橙 #C4845C：
    填充 alpha=30，边框 alpha=120。仅在有真数据时由调用方决定是否绘制。
    """
    eff = _effective_vfo(state)
    if eff is None:
        return
    vfo_center, bw = eff
    gen = state.panel.generator
    span = gen.sample_rate_hz
    view_center = _view_center_hz(state)
    x_center = _freq_to_x(rect, view_center, span, vfo_center)
    x_half = (bw / 2.0) / span * rect.width()
    x0 = x_center - x_half
    x1 = x_center + x_half
    if x1 <= rect.x() or x0 >= rect.x() + rect.width():
        return  # 完全在视口外
    fill = QColor(PAL_LINE)
    fill.setAlpha(30)
    painter.fillRect(QRectF(x0, rect.y(), x1 - x0, rect.height()), QBrush(fill))
    edge = QColor(PAL_LINE)
    edge.setAlpha(120)
    painter.setPen(QPen(edge, 1, Qt.SolidLine))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(QRectF(x0, rect.y(), x1 - x0, rect.height()))


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
        self.show_peaks = True
        self.peak_rel_db = 6.0
        # ---- 调谐步进网格（对标 SDR++ waterfall.h:38 snapInterval）----
        # 普通滚轮/左右箭头按此步进调谐；Shift ×10，Alt ×0.1。
        self.snap_interval = 10_000.0                   # Hz，默认 10 kHz
        # ---- VFO 带宽显示（对标 SDR++ waterfall.cpp:221-231 VFO 矩形）----
        # None = 不绘制 VFO 矩形；否则在谱面画半透明橙色带宽带。
        self.vfo_bandwidth_hz: Optional[float] = None
        self.vfo_offset_hz = 0.0                        # VFO 中心相对调谐中心偏移
        # ---- 视图平移偏移（Ctrl 拖动 RF shift：只移视图不调谐）----
        self.view_offset_hz = 0.0

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


def _render_plot(painter: QPainter, state: _PlotState, w: int, h: int):
    panel = state.panel
    gen = panel.generator
    has_data = gen.has_data()

    bg = QColor(panel.color_bg)
    grid = QColor(panel.color_grid)
    text = QColor(panel.color_text)
    line = QColor(panel.color_line)

    # 背景
    painter.fillRect(0, 0, w, h, bg)

    if state.show_waterfall:
        spectrum_h = int(h * 0.55)
    else:
        spectrum_h = h
    waterfall_h = h - spectrum_h

    spec_rect = QRectF(0, 0, w, spectrum_h)
    wf_rect = QRectF(0, spectrum_h, w, waterfall_h)

    # 显示窗口中心（含 Ctrl 拖动的视图平移偏移）
    view_center = _view_center_hz(state)
    span = gen.sample_rate_hz

    _draw_grid(painter, spec_rect, grid)

    # ---- 频谱曲线：仅在有真 IQ 数据时绘制 ----
    if has_data:
        # VFO 带宽矩形（先画在谱线下方；无数据时不画，保持"未连接"干净）
        _draw_vfo_band(painter, state, spec_rect)
        _draw_spectrum(painter, state, spec_rect, line)
        _draw_peaks(painter, state, spec_rect, QColor(PAL_PEAK))
    else:
        # 无数据：空白谱面 + 红色两行提示（不画任何谱线/瀑布/峰值/VFO）
        painter.setPen(QPen(QColor(PAL_OFFLINE), 1))
        f1 = QFont()
        f1.setPointSize(13)
        painter.setFont(f1)
        msg1 = "未连接 SDR"
        tw1 = painter.fontMetrics().horizontalAdvance(msg1)
        y1 = spectrum_h / 2.0 - 4
        painter.drawText(QPointF((w - tw1) / 2.0, y1), msg1)
        f2 = QFont()
        f2.setPointSize(9)
        painter.setFont(f2)
        msg2 = "点击工具栏「连接」选择设备"
        tw2 = painter.fontMetrics().horizontalAdvance(msg2)
        painter.drawText(QPointF((w - tw2) / 2.0, y1 + 20), msg2)

    # 中心频率游标
    _draw_center_cursor(painter, spec_rect, QColor(PAL_LINE))

    # 悬停读数 + 固定 marker（都读真实数组）
    if has_data:
        _draw_hover_readout(painter, state, spec_rect, text, line)
        _draw_fixed_markers(painter, state, spec_rect, line)

    # 瀑布图：持久增量画布（无数据时画布为空白背景，不画假噪声）
    if state.show_waterfall and waterfall_h > 0:
        _draw_waterfall(painter, wf_rect, state)
        painter.setPen(QPen(grid, 1))
        painter.drawLine(QPointF(0, wf_rect.y()), QPointF(w, wf_rect.y()))

    _draw_freq_scale(painter, spec_rect, view_center, span, text)
    _draw_db_scale(painter, spec_rect, state, text)


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
    gen = state.panel.generator
    spec = gen.spectrum
    n = len(spec)
    if n == 0:
        return
    w = rect.width()
    view_center = _view_center_hz(state)
    span = gen.sample_rate_hz
    path = QPainterPath()
    fill = QPainterPath()
    fill.moveTo(rect.x(), rect.y() + rect.height())
    for i in range(n):
        x = _freq_to_x(rect, view_center, span, gen.bin_to_freq(i))
        db = spec[i]
        if not np.isfinite(db):
            db = state.db_min
        y = _db_to_y(rect, db, state.db_min, state.db_max)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
        fill.lineTo(x, y)
    fill.lineTo(rect.x() + w, rect.y() + rect.height())
    fill.closeSubpath()
    # 低饱和橙渐变填充
    grad = QLinearGradient(0, rect.y(), 0, rect.y() + rect.height())
    grad.setColorAt(0.0, QColor(PAL_LINE).lighter(130))
    grad.setColorAt(1.0, QColor(PAL_LINE).darker(160))
    painter.fillPath(fill, QBrush(grad))
    painter.setPen(QPen(line_color, 2))
    painter.drawPath(path)


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
    """db → RGB 查找表（256×3），与 value_to_color 色带一致。"""
    n = 256
    lut = np.empty((n, 3), dtype=np.uint8)
    dmin, dmax = state.db_min, state.db_max
    for i in range(n):
        v = dmin + (dmax - dmin) * i / (n - 1)
        c = value_to_color(v, dmin, dmax, WATERFALL_COLORS)
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
            # 增量：旧内容上移一行（SDR++ memmove 语义），新行写底部。
            rows = gen.wf_latest_rows(pending)
            for row in rows:
                buf[0:h - 1, :, :] = buf[1:h, :, :]
                _wf_paint_row_into(state, row, buf[h - 1, :, :])
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

        # 选择绘图表面
        if prefer_opengl and HAS_OPENGL:
            self._plot = SpectrumGLPlot(self._state)
        else:
            self._plot = SpectrumPlot(self._state)

        # 右侧 dB Max/Min 垂直滑杆（对标 SDR++ main_window.cpp:635-656）
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)
        body.addWidget(self._plot, stretch=1)
        body.addLayout(self._build_db_sliders())
        root.addLayout(body, stretch=1)

        # 初始：未连接 → 控件置灰
        self._connected = False
        self._set_controls_enabled(False)

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

        self._peak_chk = QCheckBox("峰值")
        self._peak_chk.setChecked(True)
        self._peak_chk.toggled.connect(
            lambda on: setattr(self._state, "show_peaks", bool(on)))
        bar.addWidget(self._peak_chk)

        bar.addWidget(QLabel("阈值dB:"))
        self._peak_spin = QDoubleSpinBox()
        self._peak_spin.setRange(1.0, 30.0)
        self._peak_spin.setValue(self._state.peak_rel_db)
        self._peak_spin.setSingleStep(1.0)
        self._peak_spin.valueChanged.connect(
            lambda v: setattr(self._state, "peak_rel_db", float(v)))
        bar.addWidget(self._peak_spin)

        bar.addStretch(1)

        # 只读状态显示：当前中心频率 / span（无数据时显 --）
        # 对标 SDR++ main_window.cpp 顶部频率读数；这里只读展示，由 _refresh_status_labels 刷新。
        self._freq_label = QLabel("--")
        self._span_label = QLabel("--")
        self._freq_label.setStyleSheet(f"color:{PAL_GRID};")
        self._span_label.setStyleSheet(f"color:{PAL_GRID};")
        bar.addWidget(self._freq_label)
        bar.addSpacing(12)
        bar.addWidget(self._span_label)
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
        self._plot.update()

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
        self._plot.update()

    def _set_controls_enabled(self, on: bool):
        for w_ in (self._win_combo, self._fft_combo, self._avg_combo,
                   self._peak_chk, self._peak_spin):
            w_.setEnabled(on)

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
    def set_iq_data(self, iq, sample_rate: float = 2_400_000.0):
        cf = self.generator.center_freq_hz
        self.update_iq(iq, cf, sample_rate)

    def set_markers(self, freqs_hz):
        """外部批量设置活动频点 marker（Hz 列表）。空列表/None 清空。

        供扫频找台完成后标注实测到的活动频点；marker 只在当前瞬时带宽内
        可见，超出 span 的频点不绘制（_draw_fixed_markers 自然裁剪）。
        """
        self._state.markers = [float(f) for f in (freqs_hz or [])]
        self._plot.update()

    def clear_markers(self):
        """清空所有固定 marker。"""
        self._state.markers.clear()
        self._plot.update()

    @Slot(float)
    def set_center_freq(self, freq_mhz: float):
        """兼容接口：主窗口以 MHz 设置中心频率。"""
        self.generator.center_freq_hz = float(freq_mhz) * 1e6
        self._refresh_status_labels()

    def set_span(self, span_mhz: float):
        """兼容：缩放窗口（span 实际由采样率决定，这里仅触发重绘）。"""
        self._plot.update()

    def toggle_waterfall(self):
        self._state.show_waterfall = not self._state.show_waterfall
        self._plot.update()

    def set_vfo_bandwidth(self, bw_hz: Optional[float]):
        """主窗口设置 VFO 带宽（Hz）；转发给 _PlotState。传 None 关闭 VFO 矩形。"""
        self._state.set_vfo_bandwidth(bw_hz)
        self._plot.update()

    def set_theme_colors(self, bg, grid, text, line, marker, spectrum_colors):
        """兼容 main_window 主题；峰色/离线色固定为默认低饱和。"""
        self.color_bg = QColor(bg)
        self.color_grid = QColor(grid)
        self.color_text = QColor(text)
        self.color_line = QColor(line)
        self._plot.update()

    # ------------------------------------------------------------------ 状态读数
    def _refresh_status_labels(self):
        """刷新顶部中心频率/span 只读读数并触发重绘。无数据时显 --。"""
        if self.generator.has_data():
            self._freq_label.setText(f"{self.generator.center_freq_hz/1e6:.3f} MHz")
            self._span_label.setText(f"{self.generator.sample_rate_hz/1e6:.3f} MHz")
        else:
            self._freq_label.setText("--")
            self._span_label.setText("--")
        self._plot.update()

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
        self._vfo_dragging = False
        self._vfo_drag_vfo = None       # 正在拖拽的 VfoState
        self._vfo_press_x = 0.0
        self._vfo_press_gen_center = 0.0
        self._vfo_press_vfo_center = 0.0

    # ------------------------------------------------------------------ 工具
    def _x_ratio(self, event):
        return event.position().x() / max(1, self.width())

    def _spectrum_height(self) -> int:
        """谱图区高度像素（与 _render_plot 一致：开瀑布时 55%）。"""
        h = self.height()
        return int(h * 0.55) if self.state.show_waterfall else h

    def _freq_at_x(self, x_px: float) -> float:
        """谱面某 x 像素对应的绝对频率（含视图平移偏移）。"""
        gen = self.state.panel.generator
        view_center = _view_center_hz(self.state)
        span = gen.sample_rate_hz
        return view_center - span / 2.0 + x_px / max(1, self.width()) * span

    def _hit_vfo_band(self, x_px: float) -> bool:
        """某 x 像素是否落在 VFO 带宽矩形横向范围内（需有真数据）。"""
        if not self.state.panel.generator.has_data():
            return False
        eff = _effective_vfo(self.state)
        if eff is None:
            return False
        vfo_center, bw = eff
        gen = self.state.panel.generator
        span = gen.sample_rate_hz
        view_center = _view_center_hz(self.state)
        x_center = _freq_to_x(QRectF(0, 0, self.width(), 1),
                              view_center, span, vfo_center)
        x_half = (bw / 2.0) / span * self.width()
        return (x_center - x_half) <= x_px <= (x_center + x_half)

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
    def wheelEvent(self, event):
        # main_window.cpp:579-609：有 VFO 时滚轮=调谐；否则缩放。
        # 这里：Ctrl=以光标所在频率为锚点缩放 span（缩放后光标处频率不变），
        #       普通/Shift/Alt=按 snap 步进调谐（行为不变）。
        gen = self.state.panel.generator
        mods = event.modifiers()
        dy = event.angleDelta().y()
        if mods & Qt.ControlModifier:
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
        else:
            wheel = 1 if dy > 0 else -1     # 上滚=加频，下滚=减频
            interval = self.state.snap_interval
            if mods & Qt.ShiftModifier:
                interval *= 10.0             # Shift = ×10
            elif mods & Qt.AltModifier:
                interval *= 0.1              # Alt   = ×0.1
            nfreq = gen.center_freq_hz + interval * wheel
            # main_window.cpp:596  roundl(nfreq/interval)*interval
            nfreq = round(nfreq / interval) * interval
            gen.center_freq_hz = nfreq
            self._emit_tuned(nfreq)
        self.update()

    # ------------------------------------------------------------------ 键盘
    def keyPressEvent(self, event):
        # main_window.cpp:553-576：左右箭头按 snapInterval 调谐。
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Right):
            interval = self.state.snap_interval
            mods = event.modifiers()
            if mods & Qt.ShiftModifier:
                interval *= 10.0
            elif mods & Qt.AltModifier:
                interval *= 0.1
            direction = -1.0 if key == Qt.Key_Left else 1.0
            gen = self.state.panel.generator
            nfreq = gen.center_freq_hz + interval * direction
            nfreq = round(nfreq / interval) * interval
            gen.center_freq_hz = nfreq
            self._emit_tuned(nfreq)
            self.update()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------ 鼠标
    def _begin_vfo_drag(self, x_px: float, event):
        """命中 VFO 带宽矩形 → 进入 VFO 拖拽模式（而非普通平移调谐）。

        对标 SDR++ waterfall.cpp:336-340 选中 VFO + 466-476 拖动 VFO：
        - 已有 current VFO 且命中它 → 点击确认收编为 current（temporary=False），移动它；
        - 否则新建一个 VFO（center=光标频率），继承粘滞带宽/模式。
        """
        state = self.state
        mgr = state.panel.vfo_manager
        gen = state.panel.generator
        mouse_freq = self._freq_at_x(x_px)

        vfo = None
        if mgr is not None:
            if mgr.current is not None and mgr.current.active:
                vfo = mgr.current
                # 点击确认：收编为 current，并刷新粘滞快照
                mgr.set_active_context(vfo, temporary=False)
            else:
                bw0 = (state.vfo_bandwidth_hz if state.vfo_bandwidth_hz
                       else getattr(mgr, "last_bw_hz", 8000.0))
                vfo = mgr.add(mouse_freq, bw0, "FM", inherit_last=True)
                mgr.set_active_context(vfo, temporary=False)

        self._vfo_dragging = True
        self._vfo_drag_vfo = vfo
        self._vfo_press_x = x_px
        self._vfo_press_gen_center = gen.center_freq_hz
        self._vfo_press_vfo_center = float(vfo.center_hz) if vfo is not None else mouse_freq
        # 不进入普通平移
        self._is_panning = False
        self._rf_shift = False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            x = event.position().x()
            y = event.position().y()
            # 命中 VFO 带宽矩形（谱图区内）→ VFO 拖拽模式，而非普通平移调谐
            in_spectrum = y <= self._spectrum_height()
            if in_spectrum and self._hit_vfo_band(x):
                self._begin_vfo_drag(x, event)
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

        # VFO 拖拽：实时移动 VFO 中心 + 调谐中心频率，emit freq_changed
        if self._vfo_dragging:
            x = event.position().x()
            gen = self.state.panel.generator
            shift = -(x - self._vfo_press_x) / max(1, self.width()) * gen.sample_rate_hz
            gen.center_freq_hz = self._vfo_press_gen_center + shift
            vfo = self._vfo_drag_vfo
            if vfo is not None:
                vfo.center_hz = self._vfo_press_vfo_center + shift
            self._emit_tuned(gen.center_freq_hz)
            self.update()
            return

        # 悬停（未按键）：更新 VfoManager 三态焦点 active_context（临时悬停语义）
        mgr = self.state.panel.vfo_manager
        if mgr is not None and not self._is_panning:
            y = event.position().y()
            if y <= self._spectrum_height() and self._hit_vfo_band(x_px=event.position().x()):
                if mgr.current is not None:
                    mgr.set_active_context(mgr.current, temporary=True)
            else:
                mgr.clear_active_context()

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

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            # VFO 拖拽结束：对齐网格并 emit 最终中心频率
            if self._vfo_dragging:
                self._vfo_dragging = False
                gen = self.state.panel.generator
                snapped = self._snap_center()
                # VFO 跟随到对齐后的中心
                if self._vfo_drag_vfo is not None:
                    self._vfo_drag_vfo.center_hz = snapped
                self._vfo_drag_vfo = None
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
            elif self.state.panel.generator.has_data():
                gen = self.state.panel.generator
                freq = (gen.center_freq_hz - gen.sample_rate_hz / 2.0
                        + self._x_ratio(event) * gen.sample_rate_hz)
                self.state.markers.append(freq)
                if len(self.state.markers) > 8:
                    self.state.markers.pop(0)
            self.update()
        elif event.button() == Qt.RightButton:
            # 右键移除最近一个 marker
            if self.state.markers:
                self.state.markers.pop()
            self.update()

    def mouseDoubleClickEvent(self, event):
        # 双击直接调谐到鼠标处频率（SDR++ waterfall 行为），并对齐 snap 网格
        gen = self.state.panel.generator
        freq = (gen.center_freq_hz - gen.sample_rate_hz / 2.0
                + self._x_ratio(event) * gen.sample_rate_hz)
        gen.center_freq_hz = freq
        snapped = self._snap_center()
        self._emit_tuned(snapped)
        self.update()


# ============================================================================
# 绘图表面：QPainter 软件渲染
# ============================================================================

class SpectrumPlot(_TuningPlotMixin, QWidget):
    def __init__(self, state: _PlotState, parent=None):
        super().__init__(parent)
        self.state = state
        self._tuning_common_init()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(50)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        _render_plot(p, self.state, self.width(), self.height())
        p.end()


# ============================================================================
# 绘图表面：OpenGL（保留路径；内部仍用 QPainter 绘制以保证兼容）
# ============================================================================

if HAS_OPENGL:
    class SpectrumGLPlot(_TuningPlotMixin, QOpenGLWidget):
        def __init__(self, state: _PlotState, parent=None):
            super().__init__(parent)
            self.state = state
            self._tuning_common_init()
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.update)
            self._timer.start(50)

        def initializeGL(self):
            self.gl = self.context().functions()
            self.gl.glClearColor(1.0, 1.0, 1.0, 1.0)

        def paintGL(self):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            _render_plot(p, self.state, self.width(), self.height())
            p.end()
        # 滚轮/键盘/鼠标调谐交互全部继承自 _TuningPlotMixin，与 SpectrumPlot 一致。


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
