package com.mbdsdr.spatial.sky

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.mbdsdr.spatial.data.SpectrumFormat
import com.mbdsdr.spatial.data.SpectrumFrame
import com.pico.spatial.ui.design.PicoTheme
import com.pico.spatial.ui.design.Text

private val TOP_INSET = 40.dp
private val SIDE_INSET = 16.dp
private val BOTTOM_INSET = 16.dp

private val BarTop = Color(0xFF7CF6FF)
private val BarBottom = Color(0xFF0B2A6F)

/**
 * 频谱窗口：按回放位置画一帧，不表示任何实时接收状态。
 *
 * 0 帧时未加载只写一句；已加载则显示 [emptyHint]（由
 * [SpectrumFormat.emptyHint] 从包内 blockers 取原因），不再说「还没有加载」。
 * 不足 2 帧只说明录制太短，不重复单帧伪装滚动。
 */
@Composable
fun SpectrumCanvas(
    frames: List<SpectrumFrame>,
    elapsedS: Double,
    progress: String?,
    tooShort: Boolean,
    modifier: Modifier = Modifier,
    loaded: Boolean = true,
    emptyHint: String? = null,
) {
    Box(modifier = modifier, contentAlignment = Alignment.Center) {
        if (frames.isEmpty()) {
            Text(
                text = if (loaded) {
                    emptyHint ?: SpectrumFormat.NO_FRAMES_TITLE
                } else {
                    "还没有加载观测包"
                },
                style = PicoTheme.typography.bodyLarge,
            )
        } else if (tooShort) {
            Text(
                text = "这段录制太短，无法形成瀑布",
                style = PicoTheme.typography.bodyLarge,
                color = PicoTheme.colorTokens.LabelTertiaryAlpha,
            )
        } else {
            val bars = SpectrumRender.frameAt(frames, elapsedS)
                ?.let { SpectrumRender.bars(it) }
                .orEmpty()

            if (bars.isNotEmpty()) {
                Canvas(modifier = Modifier.fillMaxSize()) {
                    val top = TOP_INSET.toPx()
                    val bottom = BOTTOM_INSET.toPx()
                    val side = SIDE_INSET.toPx()
                    val plotWidth = size.width - side * 2f
                    val plotHeight = size.height - top - bottom
                    if (plotWidth > 0f && plotHeight > 0f) {
                        val slot = plotWidth / bars.size
                        val barWidth = (slot * 0.8f).coerceAtLeast(1f)
                        for (bar in bars) {
                            val barHeight = plotHeight * bar.level.coerceIn(0f, 1f)
                            if (barHeight <= 0f) continue
                            val x = side + slot * bar.index
                            val y = top + plotHeight - barHeight
                            drawRect(
                                brush = Brush.verticalGradient(
                                    colors = listOf(BarTop, BarBottom),
                                    startY = y,
                                    endY = y + barHeight,
                                ),
                                topLeft = Offset(x, y),
                                size = Size(barWidth, barHeight),
                            )
                        }
                    }
                }
            }
        }

        if (frames.isNotEmpty()) {
            progress?.let {
                Text(
                    text = it,
                    modifier = Modifier.align(Alignment.TopEnd).padding(16.dp),
                    style = PicoTheme.typography.bodySmall,
                    color = PicoTheme.colorTokens.LabelTertiaryAlpha,
                )
            }
        }
    }
}
