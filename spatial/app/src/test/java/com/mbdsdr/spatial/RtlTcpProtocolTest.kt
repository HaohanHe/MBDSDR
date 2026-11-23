package com.mbdsdr.spatial

import com.mbdsdr.spatial.net.RtlTcpProtocol
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * rtl_tcp 线协议（M4.2 基础层）：纯字节编解码，不碰 socket/硬件。
 * 命令字节按 rtl_tcp 公开协议锁定；越界必须本地拒绝，不能发出半条命令。
 */
class RtlTcpProtocolTest {

    private fun bytes(vararg v: Int): ByteArray =
        ByteArray(v.size) { v[it].toByte() }

    @Test
    fun commandEncodesLittleEndianUint32() {
        assertArrayEquals(
            bytes(0x01, 0x00, 0xE1, 0xF5, 0x05),
            RtlTcpProtocol.setCenterFrequency(100_000_000L),
        )
        assertArrayEquals(
            bytes(0x02, 0x00, 0x9F, 0x24, 0x00),
            RtlTcpProtocol.setSampleRate(2_400_000),
        )
        assertArrayEquals(bytes(0x03, 0x01, 0x00, 0x00, 0x00),
            RtlTcpProtocol.setGainMode(manual = true))
        assertArrayEquals(bytes(0x03, 0x00, 0x00, 0x00, 0x00),
            RtlTcpProtocol.setGainMode(manual = false))
        assertArrayEquals(bytes(0x04, 0x2C, 0x01, 0x00, 0x00),
            RtlTcpProtocol.setGainTenthDb(300))
        assertArrayEquals(bytes(0x08, 0x01, 0x00, 0x00, 0x00),
            RtlTcpProtocol.setAgcMode(true))
    }

    @Test
    fun negativePpmIsTwoComplementUint32() {
        assertArrayEquals(
            bytes(0x05, 0xFD, 0xFF, 0xFF, 0xFF),
            RtlTcpProtocol.setFrequencyCorrection(-3),
        )
        assertArrayEquals(
            bytes(0x05, 0x64, 0x00, 0x00, 0x00),
            RtlTcpProtocol.setFrequencyCorrection(100),
        )
    }

    @Test
    fun outOfRangeValuesAreRejectedLocally() {
        val bad = listOf<() -> Unit>(
            { RtlTcpProtocol.setCenterFrequency(23_999_999L) },
            { RtlTcpProtocol.setCenterFrequency(1_766_000_001L) },
            { RtlTcpProtocol.setSampleRate(225_000) },
            { RtlTcpProtocol.setSampleRate(3_200_001) },
            { RtlTcpProtocol.setGainTenthDb(-1) },
            { RtlTcpProtocol.setGainTenthDb(497) },
            { RtlTcpProtocol.setFrequencyCorrection(-1_001) },
            { RtlTcpProtocol.setFrequencyCorrection(1_001) },
        )
        for (call in bad) {
            try {
                call()
                throw AssertionError("应当抛 IllegalArgumentException")
            } catch (e: IllegalArgumentException) {
                // 预期：越界本地拒绝
            }
        }
    }

    @Test
    fun parsesServerHeaderAndRejectsBadMagic() {
        val header = bytes(
            0x52, 0x54, 0x4C, 0x30, // RTL0
            0x05, 0x00, 0x00, 0x00, // tuner type 5
            0x1D, 0x00, 0x00, 0x00, // 29 档增益
        )
        val parsed = RtlTcpProtocol.parseHeader(header)!!
        assertEquals(5, parsed.tunerType)
        assertEquals(29, parsed.gainCount)

        // 偏移：前面已有别的字节（模拟缓冲拼接），也只读 12 字节。
        val withPrefix = ByteArray(3) + header
        assertEquals(5, RtlTcpProtocol.parseHeader(withPrefix, 3)!!.tunerType)

        assertNull(RtlTcpProtocol.parseHeader(bytes(0x52, 0x54, 0x4C, 0x31)))
        assertNull(RtlTcpProtocol.parseHeader(header.copyOf(11)))
    }

    @Test
    fun decodesIq8ToNormalizedFloats() {
        val out = FloatArray(8)
        val n = RtlTcpProtocol.decodeIq8(
            bytes(0, 255, 128, 127), out)
        assertEquals(2, n)
        assertEquals(-0.99609375f, out[0], 1e-7f)
        assertEquals(0.99609375f, out[1], 1e-7f)
        assertEquals(0.00390625f, out[2], 1e-7f)
        assertEquals(-0.00390625f, out[3], 1e-7f)
    }

    @Test
    fun decodeRespectsSmallerOutputAndOddBytes() {
        val out = FloatArray(4)
        assertEquals(2, RtlTcpProtocol.decodeIq8(ByteArray(10), out))
        val odd = FloatArray(4)
        // 3 字节只能解出 1 对；不能越读。
        assertEquals(1, RtlTcpProtocol.decodeIq8(ByteArray(3), odd))
    }
}
