package com.mbdsdr.spatial.sky

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp
import com.mbdsdr.spatial.data.Star
import com.pico.spatial.ui.design.PicoTheme
import com.pico.spatial.ui.design.Text

private const val CANVAS_PADDING_PX = 24f

private fun countText(shown: Int, totalStars: Int, truncated: Boolean): String =
    "显示 $shown/$totalStars 颗" + if (truncated) "（已按星等截断）" else ""

/**
 * 天球画布：地平圈 + 天顶/北十字准星 + 依星等定大小的白点。
 *
 * `stars` 为空时只显示空态文案，不画任何星点或示意星——没有观测包就不伪造天空。
 * `emptyHint` 说明「已加载但没星」的具体原因；它为 null 且 `loaded=false` 时
 * 才走未加载文案。
 */
@Composable
fun SkyCanvas(
    stars: List<Star>,
    modifier: Modifier = Modifier,
    totalStars: Int = stars.size,
    truncated: Boolean = false,
    selected: Star? = null,
    onTap: ((Float, Float) -> Unit)? = null,
    loaded: Boolean = false,
    emptyHint: String? = null,
) {
    // pointerInput(Unit) 只装一次手势；用最新 lambda 避免闭包里留着旧星表。
    val currentTap = rememberUpdatedState(onTap)
    val tapModifier = Modifier.pointerInput(Unit) {
        detectTapGestures { offset -> currentTap.value?.invoke(offset.x, offset.y) }
    }
    Box(modifier = modifier.then(tapModifier), contentAlignment = Alignment.Center) {
        if (stars.isEmpty()) {
            Column(
                verticalArrangement = Arrangement.spacedBy(8.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    text = emptyHint ?: "还没有加载观测包",
                    style = PicoTheme.typography.bodyLarge,
                )
                if (!loaded && emptyHint == null) {
                    Text(
                        text = "下一步：spatial bundle（spec: spatial-observation-app S4）",
                        style = PicoTheme.typography.bodySmall,
                        color = PicoTheme.colorTokens.LabelTertiaryAlpha,
                    )
                }
            }
            if (loaded) {
                Text(
                    text = "头顶",
                    modifier = Modifier.align(Alignment.TopStart).padding(16.dp),
                    style = PicoTheme.typography.bodySmall,
                    color = PicoTheme.colorTokens.LabelSecondaryAlpha,
                )
                Text(
                    text = countText(stars.size, totalStars, truncated),
                    modifier = Modifier.align(Alignment.TopEnd).padding(16.dp),
                    style = PicoTheme.typography.bodySmall,
                    color = PicoTheme.colorTokens.LabelTertiaryAlpha,
                )
            }
        } else {
            Canvas(modifier = Modifier.fillMaxSize()) {
                val radius = size.minDimension / 2f - CANVAS_PADDING_PX
                if (radius > 0f) {
                    drawCircle(
                        color = Color.White.copy(alpha = 0.20f),
                        radius = radius,
                        center = center,
                        style = Stroke(width = 1.dp.toPx()),
                    )
                    val crosshair = Color.White.copy(alpha = 0.12f)
                    drawLine(
                        color = crosshair,
                        start = Offset(center.x, center.y - radius),
                        end = Offset(center.x, center.y + radius),
                        strokeWidth = 1.dp.toPx(),
                    )
                    drawLine(
                        color = crosshair,
                        start = Offset(center.x - radius, center.y),
                        end = Offset(center.x + radius, center.y),
                        strokeWidth = 1.dp.toPx(),
                    )
                }
                val dots = SkyRender.plan(stars, size.width.toInt(), size.height.toInt())
                for (dot in dots) {
                    drawCircle(
                        color = Color.White,
                        radius = dot.radiusPx,
                        center = Offset(dot.x, dot.y),
                    )
                }
                val picked = selected?.name
                    ?.let { name -> dots.firstOrNull { it.name == name } }
                if (picked != null) {
                    drawCircle(
                        color = Color.White.copy(alpha = 0.9f),
                        radius = picked.radiusPx + 6.dp.toPx(),
                        center = Offset(picked.x, picked.y),
                        style = Stroke(width = 1.5.dp.toPx()),
                    )
                }
            }
            Text(
                text = "头顶",
                modifier = Modifier.align(Alignment.TopStart).padding(16.dp),
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
            Text(
                text = countText(stars.size, totalStars, truncated),
                modifier = Modifier.align(Alignment.TopEnd).padding(16.dp),
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelTertiaryAlpha,
            )
        }
    }
}
