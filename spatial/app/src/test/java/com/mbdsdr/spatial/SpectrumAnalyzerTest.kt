package com.mbdsdr.spatial

import com.mbdsdr.spatial.dsp.SpectrumAnalyzer
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 幅度谱（M4.2 显示基础）：纯数值、离线。
 * 锁定「搬移后中心索引 = N/2、正频在右」的口径，避免 UI 把左右画反。
 */
class SpectrumAnalyzerTest {

    private fun tone(n: Int, k: Int): FloatArray {
        val out = FloatArray(n * 2)
        for (i in 0 until n) {
            val phase = 2.0 * PI * k * i / n
            out[i * 2] = cos(phase).toFloat()
            out[i * 2 + 1] = sin(phase).toFloat()
        }
        return out
    }

    private fun peakIndex(values: FloatArray): Int {
        var best = 0
        for (i in values.indices) if (values[i] > values[best]) best = i
        return best
    }

    @Test
    fun dcTonePeaksAtCenterIndex() {
        val spectrum = SpectrumAnalyzer.magnitudeSpectrum(tone(64, 0), 64)
        assertEquals(32, peakIndex(spectrum))
    }

    @Test
    fun positiveToneAppearsRightOfCenter() {
        val spectrum = SpectrumAnalyzer.magnitudeSpectrum(tone(64, 8), 64)
        assertEquals(40, peakIndex(spectrum))
        assertTrue(spectrum[40] > spectrum[24])
    }

    @Test
    fun negativeToneAppearsLeftOfCenter() {
        // e^{j2π(N-8)i/N} = cos(θ) - j·sin(θ)：负频率。
        val spectrum = SpectrumAnalyzer.magnitudeSpectrum(tone(64, 56), 64)
        assertEquals(24, peakIndex(spectrum))
        assertTrue(spectrum[24] > spectrum[40])
    }

    @Test
    fun silenceGivesAllZero() {
        val spectrum = SpectrumAnalyzer.magnitudeSpectrum(FloatArray(128), 64)
        for (v in spectrum) assertEquals(0f, v, 0f)
    }

    @Test
    fun rejectsBadSizes() {
        val bad = listOf<() -> Unit>(
            { SpectrumAnalyzer.magnitudeSpectrum(FloatArray(24), 12) },
            { SpectrumAnalyzer.magnitudeSpectrum(FloatArray(8), 4) },
            { SpectrumAnalyzer.magnitudeSpectrum(FloatArray(10), 8) },
        )
        for (call in bad) {
            try {
                call()
                throw AssertionError("应当抛 IllegalArgumentException")
            } catch (e: IllegalArgumentException) {
                // 预期
            }
        }
    }

    @Test
    fun extraInputSamplesAreIgnored() {
        // 128 点里 k=16 → 前 64 点正好 8 个周期；只应看前 64 点。
        val spectrum = SpectrumAnalyzer.magnitudeSpectrum(tone(128, 16), 64)
        assertEquals(64, spectrum.size)
        assertEquals(40, peakIndex(spectrum))
    }

    @Test
    fun binFrequenciesAreCenteredAndMonotonic() {
        val freqs = SpectrumAnalyzer.binFrequencies(
            centerHz = 100_000_000L, sampleRateHz = 2_400_000, fftSize = 64)
        assertEquals(64, freqs.size)
        assertEquals(100_000_000.0, freqs[32], 1e-6)
        assertEquals(98_800_000.0, freqs[0], 1e-6)
        assertEquals(101_162_500.0, freqs[63], 1e-6)
        for (i in 1 until freqs.size) assertTrue(freqs[i] > freqs[i - 1])
    }

    @Test
    fun binFrequenciesRejectBadInputs() {
        try {
            SpectrumAnalyzer.binFrequencies(100_000_000L, 0, 64)
            throw AssertionError("应当抛 IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            // 预期
        }
        try {
            SpectrumAnalyzer.binFrequencies(100_000_000L, 2_400_000, 63)
            throw AssertionError("应当抛 IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            // 预期
        }
    }
}
