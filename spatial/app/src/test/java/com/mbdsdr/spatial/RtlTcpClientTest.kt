package com.mbdsdr.spatial

import com.mbdsdr.spatial.net.RtlTcpClient
import com.mbdsdr.spatial.net.RtlTcpConnection
import com.mbdsdr.spatial.net.RtlTcpException
import com.mbdsdr.spatial.net.RtlTcpProtocol
import com.mbdsdr.spatial.net.RtlTcpTransport
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * rtl_tcp 客户端（M4.2）：连接/短读/坏头/调谐顺序/读 I/Q/关闭。
 * 全部走假连接，离线；不碰真实 dongle。
 */
class RtlTcpClientTest {

    private fun bytes(vararg v: Int): ByteArray = ByteArray(v.size) { v[it].toByte() }

    private val goodHeader = bytes(
        0x52, 0x54, 0x4C, 0x30, // RTL0
        0x05, 0x00, 0x00, 0x00, // tuner 5
        0x1D, 0x00, 0x00, 0x00, // 29 gains
    )

    private class FakeConnection(
        headerChunks: List<ByteArray>,
    ) : RtlTcpConnection {
        val writes = mutableListOf<ByteArray>()
        var closed = false
        var failWrite = false
        var failRead = false
        private val queued = ArrayDeque(headerChunks)

        override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
            if (failRead) throw IOException("read boom")
            val chunk = queued.removeFirstOrNull() ?: return -1
            val n = minOf(length, chunk.size)
            System.arraycopy(chunk, 0, buffer, offset, n)
            return n
        }

        override fun write(bytes: ByteArray) {
            if (failWrite) throw IOException("write boom")
            writes.add(bytes.copyOf())
        }

        override fun close() {
            closed = true
        }
    }

    private class FakeTransport(
        private val result: () -> RtlTcpConnection,
    ) : RtlTcpTransport {
        val connections = mutableListOf<RtlTcpConnection>()

        override fun connect(host: String, port: Int): RtlTcpConnection =
            result().also { connections.add(it) }
    }

    private fun client(conn: FakeConnection): RtlTcpClient =
        RtlTcpClient(FakeTransport { conn })

    @Test
    fun connectReadsHeaderAcrossShortReads() {
        val conn = FakeConnection(listOf(
            goodHeader.copyOfRange(0, 3),
            goodHeader.copyOfRange(3, 5),
            goodHeader.copyOfRange(5, 12),
        ))
        val client = client(conn)
        val header = client.connect("127.0.0.1", 1234)
        assertEquals(5, header.tunerType)
        assertEquals(29, header.gainCount)
        assertTrue(client.isConnected)
        assertTrue(conn.writes.isEmpty())
        client.close()
    }

    @Test
    fun badMagicClosesConnectionAndSaysWhy() {
        val bad = goodHeader.copyOf().also { it[3] = 0x31 } // RTL1
        val conn = FakeConnection(listOf(bad))
        val client = client(conn)
        try {
            client.connect("127.0.0.1", 1234)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("RTL0"))
        }
        assertTrue(conn.closed)
        assertFalse(client.isConnected)
        assertNull(client.header)
    }

    @Test
    fun eofBeforeFullHeaderFails() {
        val conn = FakeConnection(listOf(goodHeader.copyOfRange(0, 5)))
        val client = client(conn)
        try {
            client.connect("127.0.0.1", 1234)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("断开"))
        }
        assertTrue(conn.closed)
    }

    @Test
    fun transportFailureIsWrappedWithAddress() {
        val broken = RtlTcpClient(FakeTransport { throw IOException("refused") })
        try {
            broken.connect("10.0.0.9", 1234)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("10.0.0.9:1234"))
            assertTrue(e.message!!.contains("refused"))
        }
    }

    @Test
    fun tuneSendsSampleRateFrequencyGainAndPpmInOrder() {
        val conn = FakeConnection(listOf(goodHeader))
        val client = client(conn)
        client.connect("127.0.0.1", 1234)

        client.tune(centerHz = 100_000_000L, sampleRateHz = 2_400_000,
            gainTenthDb = 300, ppm = -3)

        assertEquals(5, conn.writes.size)
        assertArrayEquals(bytes(0x02, 0x00, 0x9F, 0x24, 0x00), conn.writes[0])
        assertArrayEquals(bytes(0x01, 0x00, 0xE1, 0xF5, 0x05), conn.writes[1])
        assertArrayEquals(bytes(0x03, 0x01, 0x00, 0x00, 0x00), conn.writes[2])
        assertArrayEquals(bytes(0x04, 0x2C, 0x01, 0x00, 0x00), conn.writes[3])
        assertArrayEquals(bytes(0x05, 0xFD, 0xFF, 0xFF, 0xFF), conn.writes[4])
    }

    @Test
    fun tuneAutoGainOmitsGainAndZeroPpm() {
        val conn = FakeConnection(listOf(goodHeader))
        val client = client(conn)
        client.connect("127.0.0.1", 1234)

        client.tune(centerHz = 145_800_000L, sampleRateHz = 1_024_000)

        assertEquals(3, conn.writes.size)
        assertArrayEquals(bytes(0x03, 0x00, 0x00, 0x00, 0x00), conn.writes[2])
    }

    @Test
    fun outOfRangeTuneWritesNothing() {
        val conn = FakeConnection(listOf(goodHeader))
        val client = client(conn)
        client.connect("127.0.0.1", 1234)

        try {
            client.tune(centerHz = 1_000_000L, sampleRateHz = 2_400_000)
            throw AssertionError("应当抛 IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("越界"))
        }
        assertTrue(conn.writes.isEmpty())
        assertTrue(client.isConnected) // 连接仍在，参数错误不该断线
    }

    @Test
    fun tuneBeforeConnectIsRejected() {
        val client = client(FakeConnection(emptyList()))
        try {
            client.tune(centerHz = 100_000_000L, sampleRateHz = 2_400_000)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("尚未连接"))
        }
    }

    @Test
    fun writeFailureIsWrapped() {
        val conn = FakeConnection(listOf(goodHeader))
        val client = client(conn)
        client.connect("127.0.0.1", 1234)
        conn.failWrite = true
        try {
            client.tune(centerHz = 100_000_000L, sampleRateHz = 2_400_000)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("调谐命令"))
            assertTrue(e.message!!.contains("write boom"))
        }
    }

    @Test
    fun readIqReturnsPartialThenFailsOnEof() {
        val iq = bytes(10, 20, 30, 40)
        val conn = FakeConnection(listOf(goodHeader, iq))
        val client = client(conn)
        client.connect("127.0.0.1", 1234)

        val buffer = ByteArray(8)
        assertEquals(4, client.readIq(buffer))
        assertArrayEquals(iq, buffer.copyOf(4))

        try {
            client.readIq(buffer)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("断开"))
        }
    }

    @Test
    fun closeIsIdempotentAndRejectsFurtherReads() {
        val conn = FakeConnection(listOf(goodHeader))
        val client = client(conn)
        client.connect("127.0.0.1", 1234)
        client.close()
        client.close()
        assertTrue(conn.closed)
        assertFalse(client.isConnected)
        assertNull(client.header)

        try {
            client.readIq(ByteArray(4))
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("尚未连接"))
        }
    }

    @Test
    fun protocolHeaderBytesAreTwelve() {
        assertEquals(12, RtlTcpProtocol.HEADER_BYTES)
        assertEquals(5, RtlTcpProtocol.COMMAND_BYTES)
    }
}
