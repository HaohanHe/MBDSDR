package com.mbdsdr.spatial.audio

import kotlin.math.cos
import kotlin.math.sin

/**
 * 世界坐标系中的单位方向向量（长度 1）。
 *
 * 轴向固定为：**+x = 东、+y = 天顶（上）、+z = 北**，与 `sky` 包的方位约定一致
 * （北 0°、东 90°）。世界坐标系不随头部转动，因此声源一旦算出方向就保持世界锁定。
 */
data class Direction(val x: Double, val y: Double, val z: Double)

/**
 * 声源方向与距离增益的纯数学：地平坐标（alt/az）↔ 世界向量 ↔ 头相对向量。
 *
 * 不依赖 PICO SDK、不读 bundle、不引用任何 Android 类，JVM 单测可直接运行。
 *
 * 真机绑定方式（尚未验证）：把 [worldDirection] 的结果按固定半径放到 PICO
 * SpatialAudio 的音频节点位置上，并让引擎做世界锁定；转头只改变听者姿态，
 * 头相对方向可用 [headRelative] 做离线校验。真实出声与 SDK 行为**未在真机验证**。
 */
object SpatialPlacement {

    /** 距离增益下限；远距离不静音，避免声源「突然消失」。 */
    const val MIN_GAIN = 0.05f

    /**
     * 地平坐标 → 世界单位向量。
     *
     * 公式：`y = sin(alt)`、`h = cos(alt)`、`x = h·sin(az)`、`z = h·cos(az)`。
     * 方位角为任意实数（内部按三角函数自然周期处理），不做取模截断。
     *
     * @param altDeg 高度角（度），合法范围 [-90, 90]。
     * @param azDeg 方位角（度），北 0°、东 90°。
     * @return 单位向量；任一参数为 null、NaN、Inf，或 alt 越界（> 90 / < -90）时返回
     *   null——**不猜默认方向**。
     */
    fun worldDirection(altDeg: Double?, azDeg: Double?): Direction? {
        if (altDeg == null || !altDeg.isFinite() || altDeg < -90.0 || altDeg > 90.0) return null
        if (azDeg == null || !azDeg.isFinite()) return null

        val alt = Math.toRadians(altDeg)
        val az = Math.toRadians(azDeg)
        val horizontal = cos(alt)
        return Direction(
            x = horizontal * sin(az),
            y = sin(alt),
            z = horizontal * cos(az),
        )
    }

    /**
     * 地平坐标 → 头部相对单位向量。
     *
     * 头相对坐标轴（yaw = pitch = 0 时与世界坐标重合）：**+x = 头右侧、+y = 头上方、
     * +z = 头前方**。头部姿态约定：
     * - **yaw 向右（朝东）为正**：绕 +y 轴，把 +z 转向 +x；
     * - **pitch 抬头为正**：绕头自身 +x 轴，把 +z 转向 +y。
     *
     * 头部从「头相对」转到「世界」的旋转为 `Ry(yaw)·P(pitch)`，因此这里做逆变换：
     * 先撤销 yaw（绕 +y 转 `-yaw`），再撤销 pitch（绕 +x 转 `-pitch`）。
     *
     * @return 头相对单位向量；世界方向非法（见 [worldDirection]）或 yaw/pitch 为
     *   NaN/Inf 时返回 null。
     */
    fun headRelative(
        altDeg: Double?,
        azDeg: Double?,
        headYawDeg: Double,
        headPitchDeg: Double,
    ): Direction? {
        if (!headYawDeg.isFinite() || !headPitchDeg.isFinite()) return null
        val world = worldDirection(altDeg, azDeg) ?: return null

        val yaw = Math.toRadians(headYawDeg)
        val pitch = Math.toRadians(headPitchDeg)

        // 撤销 yaw：绕 +y 轴转 -yaw。
        val cosYaw = cos(yaw)
        val sinYaw = sin(yaw)
        val xAfterYaw = world.x * cosYaw - world.z * sinYaw
        val zAfterYaw = world.x * sinYaw + world.z * cosYaw

        // 撤销 pitch：绕 +x 轴转 -pitch（抬头为正，故正 pitch 把 +z 压向 +y 的逆）。
        val cosPitch = cos(pitch)
        val sinPitch = sin(pitch)
        return Direction(
            x = xAfterYaw,
            y = world.y * cosPitch - zAfterYaw * sinPitch,
            z = world.y * sinPitch + zAfterYaw * cosPitch,
        )
    }

    /**
     * 距离衰减增益。
     *
     * 采用 `g = reference / (reference + distance)`：距离 0 时为 1，随距离单调不增，
     * 无穷远趋近 0（由 [MIN_GAIN] 夹住）。这是可解释的近似，不建模房间反射或空气吸收。
     *
     * @param distanceM 声源距离（米）；null、NaN、Inf 或负数视为未知，返回 1.0
     *   （不静音、不放大）。
     * @param referenceM 参考距离（米），必须为有限正数；非法时同样返回 1.0。
     * @return 增益，夹在 [MIN_GAIN, 1.0]。
     */
    fun distanceGain(distanceM: Double?, referenceM: Double = 1.0): Float {
        if (distanceM == null || !distanceM.isFinite() || distanceM < 0.0) return 1.0f
        if (!referenceM.isFinite() || referenceM <= 0.0) return 1.0f

        val gain = referenceM / (referenceM + distanceM)
        return gain.coerceIn(MIN_GAIN.toDouble(), 1.0).toFloat()
    }
}
