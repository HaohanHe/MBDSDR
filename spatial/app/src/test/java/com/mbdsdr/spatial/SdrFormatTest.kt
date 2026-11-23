package com.mbdsdr.spatial

import com.mbdsdr.spatial.sdr.SdrFormat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** SDR 面板输入/展示（纯函数）：频率解析、归一化、跨度文案。 */
class SdrFormatTest {

    @Test
    fun parsesMegahertzToHzWithinProtocolRange() {
        assertEquals(145_800_000L, SdrFormat.parseCenterHz("145.8"))
        assertEquals(145_800_000L, SdrFormat.parseCenterHz("  145.800  "))
        assertEquals(24_000_000L, SdrFormat.parseCenterHz("24"))
        assertEquals(1_766_000_000L, SdrFormat.parseCenterHz("1766"))
        assertEquals(100_000_000L, SdrFormat.parseCenterHz("100"))
    }

    @Test
    fun rejectsInvalidOrOutOfRangeInputWithoutGuessing() {
        for (bad in listOf(
            "", "   ", "abc", "0", "-1", "23.999", "1766.001",
            "NaN", "Infinity",
        )) {
            assertNull("应当拒绝：$bad", SdrFormat.parseCenterHz(bad))
        }
    }

    @Test
    fun formatsCenterAndSpan() {
        assertEquals("145.800 MHz", SdrFormat.formatCenterMhz(145_800_000L))
        assertEquals("100.000 MHz", SdrFormat.formatCenterMhz(100_000_000L))
        assertEquals("±1.200 MHz", SdrFormat.formatSpanMhz(1_200_000.0))
    }

    @Test
    fun normalizedBarsDivideByPeakAndClamp() {
        val out = SdrFormat.normalizedBars(floatArrayOf(0f, 2f, 1f))
        assertEquals(0f, out[0], 1e-6f)
        assertEquals(1f, out[1], 1e-6f)
        assertEquals(0.5f, out[2], 1e-6f)
    }

    @Test
    fun normalizedBarsHandleZeroNegativeAndNonFinite() {
        val zero = SdrFormat.normalizedBars(FloatArray(4))
        for (v in zero) assertEquals(0f, v, 0f)

        val odd = SdrFormat.normalizedBars(
            floatArrayOf(-1f, Float.NaN, Float.POSITIVE_INFINITY, 0f))
        for (v in odd) assertEquals(0f, v, 0f)

        val mixed = SdrFormat.normalizedBars(
            floatArrayOf(1f, Float.NaN, Float.POSITIVE_INFINITY))
        assertEquals(1f, mixed[0], 1e-6f)
        assertEquals(0f, mixed[1], 0f)
        assertEquals(0f, mixed[2], 0f)
    }

    @Test
    fun decibelsMapToDisplayLevels() {
        val levels = SdrFormat.decibelsToLevels(
            listOf(-20f, -60f, null, -120f, 10f, Float.NaN))
        assertEquals(0.8f, levels[0], 1e-6f)
        assertEquals(0.4f, levels[1], 1e-6f)
        assertEquals(0f, levels[2], 0f)    // 空 bin 不画
        assertEquals(0f, levels[3], 0f)    // 低于 floor
        assertEquals(1f, levels[4], 0f)    // 高于 ceil 夹到 1
        assertEquals(0f, levels[5], 0f)    // 非有限
        // 非法范围 → 全 0，不抛。
        val zero = SdrFormat.decibelsToLevels(listOf(-20f), 0f, -10f)
        assertEquals(0f, zero[0], 0f)
    }

    @Test
    fun downsampleKeepsMaxAndHandlesEdges() {
        val out = SdrFormat.downsampleMax(
            floatArrayOf(0f, 1f, 0.5f, 0.9f), 2)
        assertEquals(1f, out[0], 0f)
        assertEquals(0.9f, out[1], 1e-6f)

        val up = SdrFormat.downsampleMax(floatArrayOf(0.2f, 0.8f), 4)
        assertEquals(4, up.size)
        assertEquals(0.2f, up[0], 1e-6f)
        assertEquals(0.8f, up[3], 1e-6f)

        assertEquals(0, SdrFormat.downsampleMax(floatArrayOf(1f), 0).size)
        val empty = SdrFormat.downsampleMax(FloatArray(0), 3)
        assertEquals(3, empty.size)
        for (v in empty) assertEquals(0f, v, 0f)
    }

    @Test
    fun waterfallColorEndpointsAndNaN() {
        assertEquals(0xFF000814.toInt(), SdrFormat.waterfallColor(0f))
        assertEquals(0xFFFFFFFF.toInt(), SdrFormat.waterfallColor(1f))
        assertEquals(0xFF00C8DC.toInt(), SdrFormat.waterfallColor(0.65f))
        assertEquals(SdrFormat.waterfallColor(0f),
            SdrFormat.waterfallColor(Float.NaN))
        assertEquals(0xFFFFFFFF.toInt(), SdrFormat.waterfallColor(2f))
    }

    @Test
    fun peakBinsFindLocalMaximaAboveThreshold() {
        val spectrum = floatArrayOf(0f, 100f, 0f, 90f, 80f, 0f, 70f, 0f)
        assertEquals(listOf(1, 3, 6), SdrFormat.peakBins(spectrum))
        assertEquals(listOf(1, 3), SdrFormat.peakBins(spectrum, maxCount = 2))
        // 低于峰值的 50% 的局部隆起不算峰。
        assertEquals(emptyList<Int>(),
            SdrFormat.peakBins(floatArrayOf(100f, 40f, 0f)))
        // 空/过短/全零/全非有限 → 不造峰。
        assertEquals(emptyList<Int>(), SdrFormat.peakBins(FloatArray(0)))
        assertEquals(emptyList<Int>(), SdrFormat.peakBins(FloatArray(2)))
        assertEquals(emptyList<Int>(), SdrFormat.peakBins(FloatArray(8)))
        // NaN 邻居按 0 处理，不吞掉真实峰。
        assertEquals(listOf(1),
            SdrFormat.peakBins(floatArrayOf(Float.NaN, 1f, Float.NaN)))
        assertEquals(listOf(1),
            SdrFormat.peakBins(floatArrayOf(Float.NaN, 10f, Float.NaN)))
    }

    @Test
    fun peakFrequencyUsesBinCenterConvention() {
        val spectrum = floatArrayOf(0f, 10f, 20f, 30f)
        // bin 3 中心：100 MHz + ((3.5/4)-0.5)*1 MHz = 100.375 MHz
        assertEquals(100_375_000.0,
            SdrFormat.peakFrequencyHz(spectrum, 100_000_000L, 1_000_000)!!,
            1.0)
        // bin 0 中心：100 MHz - 0.375 MHz
        assertEquals(99_625_000.0,
            SdrFormat.peakFrequencyHz(
                floatArrayOf(30f, 0f, 0f, 0f), 100_000_000L, 1_000_000)!!,
            1.0)
        assertNull(SdrFormat.peakFrequencyHz(FloatArray(0), 100_000_000L, 1_000_000))
        assertNull(SdrFormat.peakFrequencyHz(spectrum, 0L, 1_000_000))
        assertNull(SdrFormat.peakFrequencyHz(spectrum, 100_000_000L, 0))
        assertNull(SdrFormat.peakFrequencyHz(FloatArray(4), 100_000_000L, 1_000_000))
    }
}
