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
}
