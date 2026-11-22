package com.mbdsdr.spatial.sky

import com.mbdsdr.spatial.data.Star
import kotlin.math.min

/** 一颗星在屏幕上的落点与点半径（纯数据，便于单测）。 */
data class StarDot(val x: Float, val y: Float, val radiusPx: Float, val name: String?)

/**
 * 把星表规划成屏幕像素点（纯函数，不依赖 Android）。
 *
 * 用 [SkyProjection] 的归一化圆盘坐标换算到像素；地平线以下、缺 alt/az、
 * 星等非有限值的条目一律跳过，不补假星、不抛异常。
 */
object SkyRender {
    /** @return 与输入顺序一致的点表；空输入返回空表。 */
    fun plan(
        stars: List<Star>,
        widthPx: Int,
        heightPx: Int,
        paddingPx: Float = 24f,
    ): List<StarDot> {
        if (stars.isEmpty()) return emptyList()
        if (widthPx <= 0 || heightPx <= 0) return emptyList()

        val radius = min(widthPx, heightPx) / 2f - paddingPx
        if (!radius.isFinite() || radius <= 0f) return emptyList()

        val centerX = widthPx / 2f
        val centerY = heightPx / 2f
        val dots = ArrayList<StarDot>(stars.size)
        for (star in stars) {
            val mag = star.mag
            if (mag != null && !mag.isFinite()) continue
            val projected = SkyProjection.project(star.altDeg, star.azDeg) ?: continue
            val x = centerX + projected.first * radius
            val y = centerY + projected.second * radius
            if (!x.isFinite() || !y.isFinite()) continue
            dots.add(StarDot(x = x, y = y, radiusPx = radiusFor(mag), name = star.name))
        }
        return dots
    }

    /** 星等越亮（数值越小）点越大；缺失星等按最小半径。 */
    private fun radiusFor(mag: Double?): Float {
        if (mag == null) return 1.5f
        return (1.5 + (6.0 - mag) * 0.5).coerceIn(1.5, 7.0).toFloat()
    }
}
