package com.mbdsdr.spatial.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.mbdsdr.spatial.sdr.SdrFormat
import com.pico.spatial.ui.design.PicoTheme
import com.pico.spatial.ui.design.Text

private val BarTop = Color(0xFF7CF6FF)
private val BarBottom = Color(0xFF0B2A6F)
private val CenterLine = Color(0x66FFFFFF)
private val PeakMarker = Color(0xFFFFC857)

/**
 * 实时 SDR 幅度谱（PICO 主窗内的小画布）。
 *
 * 只画「按峰值归一化后的相对高度」——不是 dBm，也没有天线/滤波校准；
 * 中心线对应调谐频率。空数组时明说还没数据，不画假噪声底。
 */
@Composable
fun LiveSpectrumCanvas(spectrum: FloatArray, modifier: Modifier = Modifier) {
    Box(modifier = modifier, contentAlignment = Alignment.Center) {
        if (spectrum.isEmpty()) {
            Text(
                text = "还没有频谱数据（点「开始接收」）",
                style = PicoTheme.typography.bodyLarge,
                color = PicoTheme.colorTokens.LabelTertiaryAlpha,
            )
        } else {
            val bars = remember(spectrum) { SdrFormat.normalizedBars(spectrum) }
            val peaks = remember(spectrum) { SdrFormat.peakBins(spectrum) }
            Canvas(modifier = Modifier.fillMaxSize().padding(8.dp)) {
                val plotWidth = size.width
                val plotHeight = size.height
                if (plotWidth <= 0f || plotHeight <= 0f) return@Canvas
                val slot = plotWidth / bars.size
                val barWidth = (slot * 0.9f).coerceAtLeast(1f)
                for (i in bars.indices) {
                    val level = bars[i].coerceIn(0f, 1f)
                    if (level <= 0f) continue
                    val h = plotHeight * level
                    val x = slot * i
                    val y = plotHeight - h
                    drawRect(
                        brush = Brush.verticalGradient(
                            colors = listOf(BarTop, BarBottom),
                            startY = y,
                            endY = y + h,
                        ),
                        topLeft = Offset(x, y),
                        size = Size(barWidth, h),
                    )
                }
                // 峰值标记：短竖线在柱顶上方；只是视觉提示，不写频率/功率数字。
                for (i in peaks) {
                    if (i !in bars.indices) continue
                    val level = bars[i].coerceIn(0f, 1f)
                    if (level <= 0f) continue
                    val y = plotHeight * (1f - level)
                    val cx = slot * i + barWidth / 2f
                    drawLine(
                        color = PeakMarker,
                        start = Offset(cx, (y - 2f).coerceAtLeast(0f)),
                        end = Offset(cx, (y - 14f).coerceAtLeast(0f)),
                        strokeWidth = 2f,
                    )
                }
                drawLine(
                    color = CenterLine,
                    start = Offset(plotWidth / 2f, 0f),
                    end = Offset(plotWidth / 2f, plotHeight),
                    strokeWidth = 1.5f,
                )
            }
        }
    }
}
