package com.mbdsdr.spatial.sky

import com.mbdsdr.spatial.data.Star
import kotlin.math.hypot

/** 点星结果：命中的星与点击点之间的距离（像素）。 */
data class PickedStar(val star: Star, val distancePx: Float)

/**
 * 点选最近的星。
 *
 * [dots] 与 [stars] 按下标一一对应（[SkyRender.plan] 保证顺序一致且只含可见星）。
 * 距离超过 [maxDistancePx]、空表、NaN 或负坐标一律返回 null，不做兜底猜测。
 */
object Selection {
    fun pick(
        dots: List<StarDot>,
        stars: List<Star>,
        x: Float,
        y: Float,
        maxDistancePx: Float = 32f,
    ): PickedStar? {
        if (dots.isEmpty() || stars.isEmpty()) return null
        if (!x.isFinite() || !y.isFinite()) return null
        if (x < 0f || y < 0f) return null
        if (!maxDistancePx.isFinite() || maxDistancePx < 0f) return null

        var bestIndex = -1
        var bestDistance = Float.MAX_VALUE
        val count = minOf(dots.size, stars.size)
        for (i in 0 until count) {
            val dot = dots[i]
            val distance = hypot(dot.x - x, dot.y - y)
            if (!distance.isFinite()) continue
            if (distance < bestDistance) {
                bestDistance = distance
                bestIndex = i
            }
        }
        if (bestIndex < 0 || bestDistance > maxDistancePx) return null
        return PickedStar(star = stars[bestIndex], distancePx = bestDistance)
    }
}
