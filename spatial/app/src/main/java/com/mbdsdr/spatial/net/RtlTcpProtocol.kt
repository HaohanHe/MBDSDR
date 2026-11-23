package com.mbdsdr.spatial.net

/**
 * rtl_tcp 线协议（M4.2 头显直连 SDR 的基础层）。
 *
 * 只做**纯字节**编解码：12 字节头、5 字节命令、8-bit I/Q 归一化。
 * 不建立 socket、不决定连接时机——授权（`/sdr/lease`）与真正连接仍由上层负责。
 * 数值边界与桌面/手机端的 SDR 范围保持一致，越界直接抛错，不发半条命令。
 */
object RtlTcpProtocol {
    /** 服务端问候头魔数；ASCII `RTL0`。 */
    val MAGIC: ByteArray = byteArrayOf(0x52, 0x54, 0x4C, 0x30)
    const val HEADER_BYTES = 12
    const val COMMAND_BYTES = 5

    const val CMD_SET_FREQUENCY = 0x01
    const val CMD_SET_SAMPLE_RATE = 0x02
    const val CMD_SET_GAIN_MODE = 0x03
    const val CMD_SET_GAIN = 0x04
    const val CMD_SET_FREQUENCY_CORRECTION = 0x05
    const val CMD_SET_AGC_MODE = 0x08

    /** 中心频率允许范围（Hz）：与产品「24–1700 MHz」口径一致，留少量上界余量。 */
    const val MIN_FREQUENCY_HZ = 24_000_000L
    const val MAX_FREQUENCY_HZ = 1_766_000_000L

    /** 采样率允许范围（Hz）：rtl_tcp 常见可用区间。 */
    const val MIN_SAMPLE_RATE_HZ = 225_001
    const val MAX_SAMPLE_RATE_HZ = 3_200_000

    /** 手动增益范围（十分之一 dB，0–49.6 dB）。 */
    const val MAX_GAIN_TENTH_DB = 496

    /** 频率校正范围（ppm）。 */
    const val MIN_PPM = -1_000
    const val MAX_PPM = 1_000

    /** 服务端问候头：tuner 类型与可调增益档数。 */
    data class Header(val tunerType: Int, val gainCount: Int)

    /**
     * 解析 12 字节头；不足 12 字节或魔数不是 `RTL0` 返回 null。
     * 偏移允许调用方把已缓冲的字节直接传进来，不复制。
     */
    fun parseHeader(bytes: ByteArray, offset: Int = 0): Header? {
        if (offset < 0 || bytes.size - offset < HEADER_BYTES) return null
        for (i in MAGIC.indices) {
            if (bytes[offset + i] != MAGIC[i]) return null
        }
        val tuner = uint32Le(bytes, offset + 4)
        val gains = uint32Le(bytes, offset + 8)
        return Header(tunerType = tuner, gainCount = gains)
    }

    /** 0x01：中心频率（Hz）。 */
    fun setCenterFrequency(hz: Long): ByteArray {
        require(hz in MIN_FREQUENCY_HZ..MAX_FREQUENCY_HZ) {
            "中心频率越界：$hz Hz（允许 $MIN_FREQUENCY_HZ–$MAX_FREQUENCY_HZ）"
        }
        return command(CMD_SET_FREQUENCY, hz)
    }

    /** 0x02：采样率（Hz）。 */
    fun setSampleRate(hz: Int): ByteArray {
        require(hz in MIN_SAMPLE_RATE_HZ..MAX_SAMPLE_RATE_HZ) {
            "采样率越界：$hz Hz（允许 $MIN_SAMPLE_RATE_HZ–$MAX_SAMPLE_RATE_HZ）"
        }
        return command(CMD_SET_SAMPLE_RATE, hz.toLong())
    }

    /** 0x03：增益模式；manual=true 表示手动（随后用 [setGainTenthDb]）。 */
    fun setGainMode(manual: Boolean): ByteArray =
        command(CMD_SET_GAIN_MODE, if (manual) 1L else 0L)

    /** 0x04：手动增益（十分之一 dB，0–49.6 dB）。 */
    fun setGainTenthDb(tenthDb: Int): ByteArray {
        require(tenthDb in 0..MAX_GAIN_TENTH_DB) {
            "增益越界：$tenthDb（0.1 dB 单位，允许 0–$MAX_GAIN_TENTH_DB）"
        }
        return command(CMD_SET_GAIN, tenthDb.toLong())
    }

    /** 0x05：频率校正（ppm）。 */
    fun setFrequencyCorrection(ppm: Int): ByteArray {
        require(ppm in MIN_PPM..MAX_PPM) {
            "频率校正越界：$ppm ppm（允许 $MIN_PPM–$MAX_PPM）"
        }
        // rtl_tcp 用 uint32 传输，负 ppm 按补码。
        return command(CMD_SET_FREQUENCY_CORRECTION, ppm.toLong() and 0xFFFF_FFFFL)
    }

    /** 0x08：RTL2832 内部 AGC 开关。 */
    fun setAgcMode(enabled: Boolean): ByteArray =
        command(CMD_SET_AGC_MODE, if (enabled) 1L else 0L)

    /**
     * 把 rtl_tcp 的 8-bit 无符号 I/Q 解成归一化浮点（[-1, 1)）：
     * `(raw - 127.5) / 128`。返回写入 [out] 的采样对数；不足一对时返回 0。
     */
    fun decodeIq8(bytes: ByteArray, out: FloatArray): Int {
        val pairs = minOf(bytes.size / 2, out.size / 2)
        for (i in 0 until pairs) {
            val iRaw = bytes[i * 2].toInt() and 0xFF
            val qRaw = bytes[i * 2 + 1].toInt() and 0xFF
            out[i * 2] = (iRaw - 127.5f) / 128f
            out[i * 2 + 1] = (qRaw - 127.5f) / 128f
        }
        return pairs
    }

    private fun command(cmd: Int, value: Long): ByteArray {
        val out = ByteArray(COMMAND_BYTES)
        out[0] = cmd.toByte()
        for (i in 0 until 4) {
            out[1 + i] = ((value shr (8 * i)) and 0xFF).toByte()
        }
        return out
    }

    private fun uint32Le(bytes: ByteArray, offset: Int): Int {
        var v = 0L
        for (i in 0 until 4) {
            v = v or ((bytes[offset + i].toLong() and 0xFFL) shl (8 * i))
        }
        return v.toInt()
    }
}
