package com.mbdsdr.spatial.sdr

import com.mbdsdr.spatial.net.RtlTcpProtocol
import kotlin.math.abs

/**
 * SDR 面板的纯展示/输入逻辑（不碰 Compose/网络），便于 JVM 单测。
 */
object SdrFormat {

    /**
     * 把「MHz」输入解析成 Hz：允许 `145.8` / ` 145.800 ` / `24`。
     * 非法、非有限或超出 rtl_tcp 频率范围返回 null（不猜默认值）。
     */
    fun parseCenterHz(text: String): Long? {
        val raw = text.trim()
        if (raw.isEmpty()) return null
        val mhz = raw.toDoubleOrNull() ?: return null
        if (!mhz.isFinite() || mhz <= 0.0) return null
        val hz = Math.round(mhz * 1_000_000.0)
        if (hz < RtlTcpProtocol.MIN_FREQUENCY_HZ ||
            hz > RtlTcpProtocol.MAX_FREQUENCY_HZ
        ) {
            return null
        }
        return hz
    }

    /** Hz → 一行「xxxx.xxx MHz」。 */
    fun formatCenterMhz(hz: Long): String =
        String.format(java.util.Locale.US, "%.3f MHz", hz / 1_000_000.0)

    /**
     * 把幅度谱按峰值归一化到 [0,1] 供画柱；全零/非有限值都变 0。
     * 这不代表绝对功率，只是让画面可见。
     */
    fun normalizedBars(spectrum: FloatArray): FloatArray {
        var peak = 0f
        for (v in spectrum) {
            if (v.isFinite() && v > peak) peak = v
        }
        if (peak <= 1e-9f) return FloatArray(spectrum.size)
        return FloatArray(spectrum.size) { i ->
            val v = spectrum[i]
            if (!v.isFinite() || v <= 0f) 0f else (v / peak).coerceIn(0f, 1f)
        }
    }

    /** 跨度（±Hz）→「±1.200 MHz」；用于状态行说明频谱覆盖范围。 */
    fun formatSpanMhz(spanHz: Double): String =
        String.format(java.util.Locale.US, "±%.3f MHz", abs(spanHz) / 1_000_000.0)

    /**
     * 找局部峰值 bin：严格高于左右邻居，且不低于全局峰值的 [minRel] 倍。
     *
     * 返回按频率升序的 bin 索引（最多 [maxCount] 个，优先取幅度最高的）。
     * 空/过短/全零/全非有限 → 空列表，不造峰。
     */
    fun peakBins(
        spectrum: FloatArray,
        maxCount: Int = 3,
        minRel: Float = 0.5f,
    ): List<Int> {
        if (spectrum.size < 3 || maxCount <= 0) return emptyList()
        var peak = 0f
        for (v in spectrum) {
            if (v.isFinite() && v > peak) peak = v
        }
        if (peak <= 1e-9f) return emptyList()
        val threshold = peak * minRel.coerceIn(0f, 1f)
        val hits = mutableListOf<Int>()
        for (i in 1 until spectrum.size - 1) {
            val v = spectrum[i]
            if (!v.isFinite() || v < threshold) continue
            val left = spectrum[i - 1].takeIf { it.isFinite() } ?: 0f
            val right = spectrum[i + 1].takeIf { it.isFinite() } ?: 0f
            if (v > left && v > right) hits.add(i)
        }
        return hits.sortedByDescending { spectrum[it] }.take(maxCount).sorted()
    }

    /**
     * 最强峰的估算频率（Hz）：按「bin i 覆盖 [f0+i·sr/N, f0+(i+1)·sr/N)」取
     * bin 中心，f0 = 中心频率 − sr/2。无有效峰/参数非法返回 null。
     *
     * 这是从相对幅度谱估出来的，不是校准过的绝对功率/载波频率。
     */
    fun peakFrequencyHz(
        spectrum: FloatArray,
        centerHz: Long,
        sampleRateHz: Int,
    ): Double? {
        if (spectrum.isEmpty() || centerHz <= 0L || sampleRateHz <= 0) return null
        var bestIdx = -1
        var best = 0f
        for (i in spectrum.indices) {
            val v = spectrum[i]
            if (!v.isFinite() || v <= best) continue
            best = v
            bestIdx = i
        }
        if (bestIdx < 0 || best <= 1e-9f) return null
        val n = spectrum.size
        val frac = (bestIdx + 0.5) / n - 0.5
        return centerHz.toDouble() + frac * sampleRateHz
    }
}
