package com.mbdsdr.spatial.sky

import kotlin.math.cos
import kotlin.math.sin

/**
 * 天球地平坐标 → 2D 圆盘的等距投影（纯函数，不依赖 Android，供 Compose Canvas 使用）。
 *
 * 圆心 = 天顶，圆盘边缘 = 地平线；返回归一化坐标 [-1, 1]。
 * 方位约定：北 = 0°、东 = 90°，北在屏幕上方（y 轴向下为正）。
 */
object SkyProjection {
    /**
     * @param altDeg 高度角（度）；null、非有限值或地平线以下（< 0）返回 null。
     * @param azDeg 方位角（度），任意实数，按 [0, 360) 归一。
     * @param maxAltDeg 可见高度上限（度），超过它的点投到圆盘外，返回 null。
     * @return 归一化坐标；alt=90 时为 (0, 0)，alt=0 时落在单位圆上。
     */
    fun project(
        altDeg: Double?,
        azDeg: Double?,
        maxAltDeg: Double = 90.0,
    ): Pair<Float, Float>? {
        if (altDeg == null || !altDeg.isFinite() || altDeg < 0.0) return null
        if (azDeg == null || !azDeg.isFinite()) return null
        if (!maxAltDeg.isFinite() || maxAltDeg <= 0.0 || altDeg > maxAltDeg) return null

        val r = (90.0 - altDeg) / 90.0
        val az = ((azDeg % 360.0) + 360.0) % 360.0
        val azRad = Math.toRadians(az)
        val x = r * sin(azRad)
        val y = -r * cos(azRad)
        return x.toFloat() to y.toFloat()
    }
}
