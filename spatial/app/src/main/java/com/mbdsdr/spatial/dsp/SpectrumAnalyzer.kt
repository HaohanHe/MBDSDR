package com.mbdsdr.spatial.dsp

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin

/**
 * I/Q → 幅度谱（M4.2 头显直连 SDR 的显示基础）。
 *
 * 纯数值、无 Android 依赖：Hamming 窗 + 迭代 radix-2 FFT，输出**已搬移**
 * 的 `fftSize` 点幅度谱（索引 `fftSize/2` 对应中心频率，右正左负）。
 * 不做 dB、不做平滑、不做水位归一化——这些属于 UI/策略层。
 */
object SpectrumAnalyzer {

    /** 幅度谱：索引 i 的频率 = center + (i - N/2) · fs / N。 */
    fun magnitudeSpectrum(iq: FloatArray, fftSize: Int): FloatArray {
        require(fftSize >= 8 && (fftSize and (fftSize - 1)) == 0) {
            "fftSize 必须是 ≥8 的 2 的幂，收到 $fftSize"
        }
        require(iq.size >= fftSize * 2) {
            "I/Q 至少要有 ${fftSize * 2} 个浮点数（每点 I/Q 各一），收到 ${iq.size}"
        }
        val re = FloatArray(fftSize)
        val im = FloatArray(fftSize)
        for (i in 0 until fftSize) {
            // Hamming：0.54 - 0.46·cos(2πi/(N-1))
            val w = (0.54 - 0.46 * cos(2.0 * PI * i / (fftSize - 1))).toFloat()
            re[i] = iq[i * 2] * w
            im[i] = iq[i * 2 + 1] * w
        }
        fftInPlace(re, im)

        // 搬移：radix-2 输出的 0..N-1 是 0..fs；把 N/2 挪到索引 0。
        val out = FloatArray(fftSize)
        val half = fftSize / 2
        for (i in 0 until fftSize) {
            val src = (i + half) % fftSize
            out[i] = hypot(re[src], im[src]) * 2f / fftSize
        }
        return out
    }

    /** 每个频点的频率（Hz），与 [magnitudeSpectrum] 的索引一一对应。 */
    fun binFrequencies(centerHz: Long, sampleRateHz: Int, fftSize: Int): DoubleArray {
        require(fftSize >= 8 && (fftSize and (fftSize - 1)) == 0) {
            "fftSize 必须是 ≥8 的 2 的幂，收到 $fftSize"
        }
        require(sampleRateHz > 0) { "采样率必须 > 0，收到 $sampleRateHz" }
        val half = fftSize / 2.0
        val step = sampleRateHz.toDouble() / fftSize
        return DoubleArray(fftSize) { i ->
            centerHz + (i - half) * step
        }
    }

    /** 原地迭代 FFT（Cooley–Tukey，输入长度必须是 2 的幂）。 */
    private fun fftInPlace(re: FloatArray, im: FloatArray) {
        val n = re.size
        // 位反转重排
        var j = 0
        for (i in 1 until n) {
            var bit = n shr 1
            while (j and bit != 0) {
                j = j xor bit
                bit = bit shr 1
            }
            j = j or bit
            if (i < j) {
                var tmp = re[i]; re[i] = re[j]; re[j] = tmp
                tmp = im[i]; im[i] = im[j]; im[j] = tmp
            }
        }
        // 蝶形
        var len = 2
        while (len <= n) {
            val ang = -2.0 * PI / len
            val wRe = cos(ang).toFloat()
            val wIm = sin(ang).toFloat()
            var start = 0
            while (start < n) {
                var curRe = 1.0f
                var curIm = 0.0f
                for (k in 0 until len / 2) {
                    val aRe = re[start + k]
                    val aIm = im[start + k]
                    val bRe = re[start + k + len / 2]
                    val bIm = im[start + k + len / 2]
                    val tRe = bRe * curRe - bIm * curIm
                    val tIm = bRe * curIm + bIm * curRe
                    re[start + k] = aRe + tRe
                    im[start + k] = aIm + tIm
                    re[start + k + len / 2] = aRe - tRe
                    im[start + k + len / 2] = aIm - tIm
                    val nextRe = curRe * wRe - curIm * wIm
                    curIm = curRe * wIm + curIm * wRe
                    curRe = nextRe
                }
                start += len
            }
            len = len shl 1
        }
    }
}
