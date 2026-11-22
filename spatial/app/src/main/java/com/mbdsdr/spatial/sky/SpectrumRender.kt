package com.mbdsdr.spatial.sky

import com.mbdsdr.spatial.data.SpectrumFrame
import kotlin.math.abs

/** 一根频谱竖条：下标对应原帧 dB 表位置，[level] 已归一到 [0,1]。 */
data class SpectrumBar(val index: Int, val level: Float)

/** 频谱帧到竖条的规划（纯函数，不依赖 Android）。 */
object SpectrumRender {
    private const val EPSILON = 1e-9

    /**
     * 把一帧 dB 值归一到 0..1：`(db - minDb) / (maxDb - minDb)`。
     *
     * 空帧返回空表；dB 非有限值按 minDb 处理；整帧没有对比度（上下限差 < 1e-9）时
     * 全部 0.5，不假装有强弱差异。
     */
    fun bars(frame: SpectrumFrame): List<SpectrumBar> {
        val values = frame.db
        if (values.isEmpty()) return emptyList()

        val finite = values.filter { it.isFinite() }
        val minDb = finite.minOrNull() ?: 0.0
        val maxDb = finite.maxOrNull() ?: 0.0
        val span = maxDb - minDb

        if (span < EPSILON) {
            return values.indices.map { SpectrumBar(index = it, level = 0.5f) }
        }
        return values.mapIndexed { index, raw ->
            val value = if (raw.isFinite()) raw else minDb
            SpectrumBar(index = index, level = ((value - minDb) / span).toFloat())
        }
    }

    /** 取 `at_s` 离 [t] 最近的一帧；没有帧返回 null，等距时取更靠前的一帧。 */
    fun frameAt(frames: List<SpectrumFrame>, t: Double): SpectrumFrame? {
        var best: SpectrumFrame? = null
        var bestDistance = Double.POSITIVE_INFINITY
        for (frame in frames) {
            val distance = abs(frame.atS - t)
            if (distance < bestDistance) {
                bestDistance = distance
                best = frame
            }
        }
        return best
    }
}
