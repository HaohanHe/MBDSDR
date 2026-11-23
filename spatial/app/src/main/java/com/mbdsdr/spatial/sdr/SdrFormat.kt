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
}
