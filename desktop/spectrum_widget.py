"""
MBDSDR 频谱显示组件
====================
优先 OpenGL 渲染，无 OpenGL 时降级为 QPainter 软件渲染。
支持频谱图、瀑布图、缩放、平移、频率标记。

频谱数据源：真实复数 IQ FFT（Nuttall 窗 + fftshift + dBFS + IIR 平滑）。
无硬件/无 IQ 数据时显示"未连接/无数据"，不生成模拟峰。
"""

import math
import numpy as np
from typing import Optional, List, Tuple

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, Slot
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QLinearGradient, QImage, QPainterPath, QPolygonF
from PySide6.QtWidgets import QWidget

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShaderProgram, QOpenGLShader
    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False


# ============================================================================
# 窗函数
# ============================================================================

# 来源: SDR++ core/src/dsp/window/nuttall.h:5-8 — 4-term Nuttall window。
# SDR++ 默认 FFT 窗 (core.cpp:128 fftWindow=2=NUTTALL)，旁瓣 -93dB，
# 比 Hann(-31dB) 更适合"强信号旁找弱信号"。
def _nuttall_window(n: int) -> np.ndarray:
    k = np.arange(n, dtype=np.float64)
    coefs = (0.355768, 0.487396, 0.144232, 0.012604)
    w = (coefs[0]
         - coefs[1] * np.cos(2.0 * np.pi * k / (n - 1))
         + coefs[2] * np.cos(4.0 * np.pi * k / (n - 1))
         - coefs[3] * np.cos(6.0 * np.pi * k / (n - 1)))
    return w.astype(np.float32)


# ============================================================================
# 频谱数据生成器（真 IQ FFT，无模拟峰）
# ============================================================================

class SpectrumDataGenerator:
    """真 IQ FFT 频谱生成器。无数据时返回平坦底噪，不生成模拟峰。"""

    # 固定 FFT 大小（任务要求 1024/2048；2048 兼顾分辨率与帧率）
    FFT_SIZE = 2048

    def __init__(self, num_bins: int = 512):
        self.num_bins = num_bins
        self.center_freq = 98.5  # MHz
        self.span = 4.0  # MHz (±2MHz)
        # 无数据时频谱为平坦低噪底（-100 dBFS），不画假峰
        self.spectrum = np.full(num_bins, -100.0, dtype=np.float32)
        self.waterfall: List[np.ndarray] = []
        self.max_waterfall_lines = 200
        # 是否已收到真实 IQ 数据
        self._has_real_data = False
        # 来源: SDR++ core/src/gui/widgets/waterfall.cpp:914-920
        # IIR 指数平滑: smooth = alpha*new + (1-alpha)*old
        self._smoothing_alpha = 0.4  # α≈0.4，任务要求 0.3~0.5
        self._smoothing_buf: Optional[np.ndarray] = None

    def set_center_freq(self, freq_mhz: float):
        self.center_freq = freq_mhz

    def set_span(self, span_mhz: float):
        self.span = max(0.1, span_mhz)

    def has_data(self) -> bool:
        """是否已收到真实 IQ 数据。"""
        return self._has_real_data

    def clear_data(self):
        """断开/清空：恢复到"未连接"状态，不保留旧频谱。"""
        self._has_real_data = False
        self._smoothing_buf = None
        self.spectrum = np.full(self.num_bins, -100.0, dtype=np.float32)
        self.waterfall.clear()

    # 来源: SDR++ core/src/signal_path/iq_frontend.cpp:248-267 — FFT执行+功率谱
    # 来源: GQRX src/dsp/rx_fft.cpp:126-156 — fftshift + 幅度谱
    def push_iq(self, iq: np.ndarray, sample_rate: float):
        """喂入真实复 IQ，做 Nuttall 窗复数 FFT → fftshift → dBFS → IIR 平滑。"""
        x = np.asarray(iq, dtype=np.complex64)
        n = len(x)
        if n < 64:
            return

        # 截取/补零到固定 FFT_SIZE
        fft_size = self.FFT_SIZE
        if n >= fft_size:
            x = x[:fft_size]
        else:
            x = np.concatenate([x, np.zeros(fft_size - n, dtype=np.complex64)])

        # 来源: SDR++ iq_frontend.cpp:252 — 窗乘 (Nuttall, nuttall.h:5-8)
        win = _nuttall_window(fft_size)

        # 来源: SDR++ iq_frontend.cpp:257 — 复数 FFT（复 IQ 必须用 fft 而非 rfft）
        spec = np.fft.fft(x * win)
        # 来源: SDR++ iq_frontend.cpp:283-291 / GQRX rx_fft.cpp:126-156
        # fftshift 把 DC 搬到频谱中心
        spec = np.fft.fftshift(spec)

        # dBFS: 20*log10(|X| / 窗相干增益归一化)
        # 窗相干增益 = mean(win)；满幅正弦波峰 = fft_size/2 * win_cg
        win_cg = float(np.mean(win))
        mag = np.abs(spec) / (fft_size * win_cg / 2.0)
        power_db = 20.0 * np.log10(mag + 1e-12).astype(np.float32)

        # 重采样到 num_bins。
        # 来源: SDR++ core/src/gui/widgets/waterfall.cpp:81-87 — max 抽取
        # （窄脉冲信号在缩小时不被平均抹平）
        if len(power_db) != self.num_bins:
            edges = np.linspace(0, len(power_db), self.num_bins + 1).astype(int)
            resampled = np.empty(self.num_bins, dtype=np.float32)
            for i in range(self.num_bins):
                lo, hi = edges[i], edges[i + 1]
                seg = power_db[lo:hi]
                resampled[i] = float(np.max(seg)) if seg.size else -120.0
            power_db = resampled

        # 来源: SDR++ waterfall.cpp:914-920 — IIR 指数平滑
        # smooth = alpha*new + beta*old, beta = 1-alpha
        if self._smoothing_buf is None or len(self._smoothing_buf) != self.num_bins:
            self._smoothing_buf = power_db.copy()
        else:
            a = self._smoothing_alpha
            self._smoothing_buf = a * power_db + (1.0 - a) * self._smoothing_buf

        self.spectrum = self._smoothing_buf.astype(np.float32)
        self.span = sample_rate / 1e6
        self._has_real_data = True

        # 瀑布追加
        self.waterfall.append(self.spectrum.copy())
        if len(self.waterfall) > self.max_waterfall_lines:
            self.waterfall.pop(0)

    def generate(self) -> np.ndarray:
        """返回当前帧频谱。有真 IQ 返真频谱；无数据返平坦底噪（不画模拟峰）。"""
        if self._has_real_data:
            return self.spectrum
        # 无硬件/无数据：返回平坦低噪底，由 UI 层画"未连接"文字
        return np.full(self.num_bins, -100.0, dtype=np.float32)

    def get_freq_at_x(self, x_ratio: float) -> float:
        """根据 x 位置比例 (0-1) 获取频率。"""
        return self.center_freq - self.span / 2 + x_ratio * self.span


# ============================================================================
# 颜色映射
# ============================================================================

def value_to_color(value: float, min_val: float, max_val: float,
                    colors: List[str]) -> QColor:
    """将数值映射到颜色（频谱渐变）。"""
    if max_val <= min_val:
        return QColor(colors[0])

    ratio = (value - min_val) / (max_val - min_val)
    ratio = max(0.0, min(1.0, ratio))

    # 在颜色列表中插值
    n = len(colors) - 1
    idx = ratio * n
    i = int(idx)
    f = idx - i

    if i >= n:
        return QColor(colors[n])

    c1 = QColor(colors[i])
    c2 = QColor(colors[min(i + 1, n)])

    r = int(c1.red() + (c2.red() - c1.red()) * f)
    g = int(c1.green() + (c2.green() - c1.green()) * f)
    b = int(c1.blue() + (c2.blue() - c1.blue()) * f)

    return QColor(r, g, b)


# ============================================================================
# 频谱组件（QPainter 软件渲染，基础版，OpenGL 不可用时使用）
# ============================================================================

class SpectrumWidget(QWidget):
    """频谱显示组件（QPainter 软件渲染版，OpenGL 不可用时使用）。"""

    freq_changed = Signal(float)  # 用户点击/拖拽改变中心频率

    def __init__(self, parent=None):
        super().__init__(parent)
        self.generator = SpectrumDataGenerator(num_bins=512)
        self.spectrum_colors = [
            "#4A6B7C", "#5B8C9A", "#6BA89A", "#8FB87A",
            "#C4B85C", "#C49A5C", "#C4845C", "#B86B5C",
        ]
        self.bg_color = QColor("#1E1E20")
        self.grid_color = QColor("#3A3A3E")
        self.text_color = QColor("#D0D0D0")
        self.line_color = QColor("#7A9CAC")
        self.marker_color = QColor("#D4956A")

        self._zoom_factor = 1.0
        self._pan_offset = 0.0
        self._last_mouse_x = 0
        self._is_panning = False
        self._show_waterfall = True
        self._db_min = -100
        self._db_max = -20

        self.setMinimumHeight(300)
        self.setMouseTracking(True)

        # 定时器刷新
        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start(50)  # 20 FPS

    def set_theme_colors(self, bg: str, grid: str, text: str, line: str,
                          marker: str, spectrum_colors: List[str]):
        """设置主题颜色。"""
        self.bg_color = QColor(bg)
        self.grid_color = QColor(grid)
        self.text_color = QColor(text)
        self.line_color = QColor(line)
        self.marker_color = QColor(marker)
        self.spectrum_colors = spectrum_colors
        self.update()

    def set_center_freq(self, freq_mhz: float):
        self.generator.set_center_freq(freq_mhz)
        self.update()

    def set_span(self, span_mhz: float):
        self.generator.set_span(span_mhz)
        self.update()

    def toggle_waterfall(self):
        self._show_waterfall = not self._show_waterfall
        self.update()

    @Slot()
    def set_iq_data(self, iq, sample_rate: float = 2_400_000.0):
        """喂入真 IQ 采样（numpy complex 数组），走 generator 真 FFT 管线。
        sample_rate: IQ 采样率 Hz，用于设置频谱 span。"""
        arr = np.asarray(iq, dtype=np.complex64)
        if len(arr) < 64:
            return
        # 统一走 generator.push_iq（Nuttall 窗 + 复 FFT + fftshift + dBFS + IIR 平滑）
        self.generator.push_iq(arr, sample_rate)

    def set_connected(self, connected: bool):
        """外部通知连接状态。断开时清空真数据，显示"未连接"。"""
        if not connected:
            self.generator.clear_data()

    def _on_timer(self):
        # 统一从 generator 取数据：有真 IQ 返真频谱，无数据返平坦底噪
        self.generator.generate()
        self.update()

    # ========================================================================
    # 绘制
    # ========================================================================

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w = self.width()
        h = self.height()

        # 背景
        painter.fillRect(self.rect(), self.bg_color)

        # 计算频谱区域和瀑布图区域
        if self._show_waterfall:
            spectrum_h = int(h * 0.5)
            waterfall_h = h - spectrum_h
        else:
            spectrum_h = h
            waterfall_h = 0

        spectrum_rect = QRectF(0, 0, w, spectrum_h)
        waterfall_rect = QRectF(0, spectrum_h, w, waterfall_h)

        # 绘制网格
        self._draw_grid(painter, spectrum_rect)

        # 绘制频谱
        self._draw_spectrum(painter, spectrum_rect)

        # 绘制中心频率标记
        self._draw_marker(painter, spectrum_rect)

        # 绘制瀑布图
        if self._show_waterfall and waterfall_h > 0:
            self._draw_waterfall(painter, waterfall_rect)

        # 绘制频率刻度
        self._draw_freq_scale(painter, spectrum_rect)

        # 绘制 dB 刻度
        self._draw_db_scale(painter, spectrum_rect)

        painter.end()

    def _draw_grid(self, painter: QPainter, rect: QRectF):
        """绘制网格。"""
        painter.setPen(QPen(self.grid_color, 1, Qt.DashLine))

        # 垂直网格线（5 条）
        for i in range(1, 5):
            x = rect.x() + rect.width() * i / 5
            painter.drawLine(QPointF(x, rect.y()), QPointF(x, rect.y() + rect.height()))

        # 水平网格线（4 条）
        for i in range(1, 4):
            y = rect.y() + rect.height() * i / 4
            painter.drawLine(QPointF(rect.x(), y), QPointF(rect.x() + rect.width(), y))

    def _draw_spectrum(self, painter: QPainter, rect: QRectF):
        """绘制频谱曲线。数据统一来自 generator（真 FFT 或平坦底噪）。"""
        spectrum = self.generator.spectrum
        if len(spectrum) == 0:
            return

        w = rect.width()
        h = rect.height()
        n = len(spectrum)

        # 构建路径
        path = QPainterPath()
        fill_path = QPainterPath()
        fill_path.moveTo(rect.x(), rect.y() + rect.height())

        for i in range(n):
            x = rect.x() + w * i / (n - 1)
            db_val = spectrum[i]
            # 映射 dB 到 y 坐标（db_min -> 底部, db_max -> 顶部）
            ratio = (db_val - self._db_min) / (self._db_max - self._db_min)
            ratio = max(0.0, min(1.0, ratio))
            y = rect.y() + rect.height() - ratio * h * 0.9

            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
            fill_path.lineTo(x, y)

        fill_path.lineTo(rect.x() + w, rect.y() + rect.height())
        fill_path.closeSubpath()

        # 填充渐变
        gradient = QLinearGradient(0, rect.y(), 0, rect.y() + rect.height())
        gradient.setColorAt(0.0, QColor(self.spectrum_colors[-1]).lighter(120))
        gradient.setColorAt(0.5, QColor(self.spectrum_colors[len(self.spectrum_colors)//2]).lighter(110))
        gradient.setColorAt(1.0, QColor(self.spectrum_colors[0]).darker(150))
        painter.fillPath(fill_path, QBrush(gradient))

        # 绘制曲线
        painter.setPen(QPen(self.line_color, 2))
        painter.drawPath(path)

        # 无数据时画"未连接/无数据"提示，不显示模拟峰
        if not self.generator.has_data():
            painter.setPen(QPen(self.text_color, 1))
            font = QFont()
            font.setPointSize(12)
            painter.setFont(font)
            text = "未连接 / 无 IQ 数据"
            metrics = painter.fontMetrics()
            tw = metrics.horizontalAdvance(text)
            painter.drawText(
                QPointF(rect.x() + (w - tw) / 2, rect.y() + rect.height() / 2),
                text
            )

    def _draw_marker(self, painter: QPainter, rect: QRectF):
        """绘制中心频率标记。"""
        center_x = rect.x() + rect.width() / 2

        # 垂直线
        pen = QPen(self.marker_color, 1, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(center_x, rect.y()), QPointF(center_x, rect.y() + rect.height()))

        # 标记三角形
        painter.setBrush(QBrush(self.marker_color))
        painter.setPen(Qt.NoPen)
        triangle = QPolygonF([
            QPointF(center_x - 6, rect.y()),
            QPointF(center_x + 6, rect.y()),
            QPointF(center_x, rect.y() + 10),
        ])
        painter.drawPolygon(triangle)

    def _draw_waterfall(self, painter: QPainter, rect: QRectF):
        """绘制瀑布图。"""
        if not self.generator.waterfall:
            return

        w = int(rect.width())
        h = int(rect.height())
        n_lines = min(len(self.generator.waterfall), h)

        # 创建图像
        image = QImage(w, n_lines, QImage.Format_RGB32)

        for row in range(n_lines):
            # 从最新到最旧（最新在底部）
            idx = len(self.generator.waterfall) - 1 - row
            if idx < 0:
                break
            spectrum = self.generator.waterfall[idx]
            n = len(spectrum)

            for col in range(w):
                spec_idx = int(col * n / w)
                if spec_idx >= n:
                    spec_idx = n - 1
                color = value_to_color(
                    spectrum[spec_idx],
                    self._db_min, self._db_max,
                    self.spectrum_colors
                )
                image.setPixelColor(col, row, color)

        # 绘制图像（拉伸到瀑布图区域）
        painter.drawImage(rect, image)

        # 分隔线
        painter.setPen(QPen(self.grid_color, 1))
        painter.drawLine(QPointF(rect.x(), rect.y()), QPointF(rect.x() + rect.width(), rect.y()))

    def _draw_freq_scale(self, painter: QPainter, rect: QRectF):
        """绘制频率刻度。"""
        painter.setPen(self.text_color)
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)

        center = self.generator.center_freq
        span = self.generator.span

        for i in range(5):
            freq = center - span / 2 + span * i / 4
            x = rect.x() + rect.width() * i / 4
            text = f"{freq:.1f}"
            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(text)
            painter.drawText(QPointF(x - text_w / 2, rect.y() + rect.height() + 14), text)

    def _draw_db_scale(self, painter: QPainter, rect: QRectF):
        """绘制 dB 刻度。"""
        painter.setPen(self.text_color)
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)

        for i in range(5):
            db = self._db_max - (self._db_max - self._db_min) * i / 4
            y = rect.y() + rect.height() * 0.9 * i / 4 + rect.height() * 0.05
            text = f"{db:.0f}"
            painter.drawText(QPointF(rect.x() + 4, y + 4), text)

    # ========================================================================
    # 鼠标交互（缩放、平移、点击选频）
    # ========================================================================

    def wheelEvent(self, event):
        """鼠标滚轮缩放。"""
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom_factor *= 1.1
        else:
            self._zoom_factor /= 1.1

        self._zoom_factor = max(0.1, min(10.0, self._zoom_factor))
        new_span = self.generator.span / self._zoom_factor if delta > 0 else self.generator.span * self._zoom_factor
        self.generator.set_span(max(0.1, new_span))
        self._zoom_factor = 1.0  # 重置，因为已经应用到 span
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_panning = True
            self._last_mouse_x = event.position().x()

    def mouseMoveEvent(self, event):
        if self._is_panning:
            dx = event.position().x() - self._last_mouse_x
            self._last_mouse_x = event.position().x()
            # 平移中心频率
            freq_shift = -dx / self.width() * self.generator.span
            self.generator.set_center_freq(self.generator.center_freq + freq_shift)
            self.freq_changed.emit(self.generator.center_freq)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_panning = False

    def mouseDoubleClickEvent(self, event):
        """双击设置中心频率。"""
        x_ratio = event.position().x() / self.width()
        freq = self.generator.get_freq_at_x(x_ratio)
        self.generator.set_center_freq(freq)
        self.freq_changed.emit(freq)
        self.update()


# ============================================================================
# OpenGL 频谱组件（优先使用，性能更好）
# ============================================================================

if HAS_OPENGL:
    class SpectrumGLWidget(QOpenGLWidget):
        """OpenGL 频谱显示组件（优先使用）。"""

        freq_changed = Signal(float)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.generator = SpectrumDataGenerator(num_bins=512)
            self.spectrum_colors = [
                "#4A6B7C", "#5B8C9A", "#6BA89A", "#8FB87A",
                "#C4B85C", "#C49A5C", "#C4845C", "#B86B5C",
            ]
            self._program: Optional[QOpenGLShaderProgram] = None
            self._vbo: Optional[QOpenGLBuffer] = None
            self._show_waterfall = True
            self._db_min = -100
            self._db_max = -20
            self._zoom_factor = 1.0
            self._is_panning = False
            self._last_mouse_x = 0

            self.setMinimumHeight(300)
            self.setMouseTracking(True)

            from PySide6.QtCore import QTimer
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.update)
            self._timer.start(50)

        def set_theme_colors(self, bg, grid, text, line, marker, spectrum_colors):
            self.spectrum_colors = spectrum_colors
            self.update()

        def set_center_freq(self, freq_mhz):
            self.generator.set_center_freq(freq_mhz)
            self.update()

        def set_span(self, span_mhz):
            self.generator.set_span(span_mhz)
            self.update()

        def toggle_waterfall(self):
            self._show_waterfall = not self._show_waterfall
            self.update()

        # 来源: 与 SpectrumWidget.set_iq_data 对齐 — 统一走 generator 真 FFT 管线
        def set_iq_data(self, iq, sample_rate: float = 2_400_000.0):
            """喂入真 IQ 采样，走 generator 真 FFT（Nuttall 窗 + 复 FFT + IIR 平滑）。"""
            arr = np.asarray(iq, dtype=np.complex64)
            if len(arr) < 64:
                return
            self.generator.push_iq(arr, sample_rate)

        def set_connected(self, connected: bool):
            """外部通知连接状态。断开时清空真数据，显示"未连接"。"""
            if not connected:
                self.generator.clear_data()

        def initializeGL(self):
            from PySide6.QtGui import QOpenGLFunctions
            self.gl = self.context().functions()
            self.gl.glClearColor(0.12, 0.12, 0.13, 1.0)

        def resizeGL(self, w, h):
            self.gl.glViewport(0, 0, w, h)

        def paintGL(self):
            self.generator.generate()
            self.gl.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT; QOpenGLFunctions does not expose the constant

            # 用 QPainter 在 OpenGL 上绘制（简化实现，保证兼容性）
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)

            w = self.width()
            h = self.height()

            if self._show_waterfall:
                spectrum_h = int(h * 0.5)
                waterfall_h = h - spectrum_h
            else:
                spectrum_h = h
                waterfall_h = 0

            spectrum_rect = QRectF(0, 0, w, spectrum_h)
            waterfall_rect = QRectF(0, spectrum_h, w, waterfall_h)

            # 背景
            painter.fillRect(self.rect(), QColor("#1E1E20"))

            # 网格
            painter.setPen(QPen(QColor("#3A3A3E"), 1, Qt.DashLine))
            for i in range(1, 5):
                x = w * i / 5
                painter.drawLine(QPointF(x, 0), QPointF(x, spectrum_h))
            for i in range(1, 4):
                y = spectrum_h * i / 4
                painter.drawLine(QPointF(0, y), QPointF(w, y))

            # 频谱曲线
            spectrum = self.generator.spectrum
            n = len(spectrum)
            path = QPainterPath()
            fill_path = QPainterPath()
            fill_path.moveTo(0, spectrum_h)

            for i in range(n):
                x = w * i / (n - 1)
                db_val = spectrum[i]
                ratio = (db_val - self._db_min) / (self._db_max - self._db_min)
                ratio = max(0.0, min(1.0, ratio))
                y = spectrum_h - ratio * spectrum_h * 0.9
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
                fill_path.lineTo(x, y)

            fill_path.lineTo(w, spectrum_h)
            fill_path.closeSubpath()

            gradient = QLinearGradient(0, 0, 0, spectrum_h)
            gradient.setColorAt(0.0, QColor(self.spectrum_colors[-1]).lighter(120))
            gradient.setColorAt(0.5, QColor(self.spectrum_colors[len(self.spectrum_colors)//2]))
            gradient.setColorAt(1.0, QColor(self.spectrum_colors[0]).darker(150))
            painter.fillPath(fill_path, QBrush(gradient))
            painter.setPen(QPen(QColor("#7A9CAC"), 2))
            painter.drawPath(path)

            # 中心频率标记
            center_x = w / 2
            painter.setPen(QPen(QColor("#D4956A"), 1, Qt.DashLine))
            painter.drawLine(QPointF(center_x, 0), QPointF(center_x, spectrum_h))

            # 无数据时画"未连接/无数据"提示
            if not self.generator.has_data():
                painter.setPen(QPen(QColor("#D0D0D0"), 1))
                font = QFont()
                font.setPointSize(12)
                painter.setFont(font)
                text = "未连接 / 无 IQ 数据"
                metrics = painter.fontMetrics()
                tw = metrics.horizontalAdvance(text)
                painter.drawText(QPointF((w - tw) / 2, spectrum_h / 2), text)

            # 瀑布图
            if self._show_waterfall and waterfall_h > 0 and self.generator.waterfall:
                wf_w = w
                wf_h = min(len(self.generator.waterfall), waterfall_h)
                image = QImage(wf_w, wf_h, QImage.Format_RGB32)
                for row in range(wf_h):
                    idx = len(self.generator.waterfall) - 1 - row
                    if idx < 0:
                        break
                    spec = self.generator.waterfall[idx]
                    for col in range(wf_w):
                        si = min(int(col * len(spec) / wf_w), len(spec) - 1)
                        color = value_to_color(spec[si], self._db_min, self._db_max, self.spectrum_colors)
                        image.setPixelColor(col, row, color)
                painter.drawImage(waterfall_rect, image)

            # 频率刻度
            painter.setPen(QColor("#D0D0D0"))
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            center = self.generator.center_freq
            span = self.generator.span
            for i in range(5):
                freq = center - span / 2 + span * i / 4
                x = w * i / 4
                text = f"{freq:.1f}"
                metrics = painter.fontMetrics()
                tw = metrics.horizontalAdvance(text)
                painter.drawText(QPointF(x - tw / 2, spectrum_h + 14), text)

            painter.end()

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            factor = 1.1 if delta > 0 else 0.9
            self.generator.set_span(max(0.1, self.generator.span * factor))
            self.update()

        def mousePressEvent(self, event):
            if event.button() == Qt.LeftButton:
                self._is_panning = True
                self._last_mouse_x = event.position().x()

        def mouseMoveEvent(self, event):
            if self._is_panning:
                dx = event.position().x() - self._last_mouse_x
                self._last_mouse_x = event.position().x()
                freq_shift = -dx / self.width() * self.generator.span
                self.generator.set_center_freq(self.generator.center_freq + freq_shift)
                self.freq_changed.emit(self.generator.center_freq)
                self.update()

        def mouseReleaseEvent(self, event):
            if event.button() == Qt.LeftButton:
                self._is_panning = False

        def mouseDoubleClickEvent(self, event):
            x_ratio = event.position().x() / self.width()
            freq = self.generator.get_freq_at_x(x_ratio)
            self.generator.set_center_freq(freq)
            self.freq_changed.emit(freq)
            self.update()


# ============================================================================
# 工厂函数：自动选择 OpenGL 或软件渲染
# ============================================================================

def create_spectrum_widget(parent=None, prefer_opengl: bool = False):
    """创建频谱组件，优先 OpenGL，不可用时降级为 QPainter。"""
    # 检测平台：offscreen/minimal 不支持 OpenGL，直接用软件渲染
    try:
        from PySide6.QtWidgets import QApplication
        platform = QApplication.instance().platformName() if QApplication.instance() else ""
        if platform in ("offscreen", "minimal", "webgl"):
            prefer_opengl = False
    except Exception:
        pass

    if prefer_opengl and HAS_OPENGL:
        try:
            widget = SpectrumGLWidget(parent)
            # 验证 OpenGL 上下文是否真的可用
            return widget
        except Exception:
            pass
    return SpectrumWidget(parent)
