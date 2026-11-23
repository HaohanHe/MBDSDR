package com.mbdsdr.spatial

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.mbdsdr.spatial.net.DeviceGatewayException
import com.mbdsdr.spatial.net.RtlTcpConnection
import com.mbdsdr.spatial.net.RtlTcpException
import com.mbdsdr.spatial.net.RtlTcpTransport
import com.mbdsdr.spatial.sdr.SdrGatewayPort
import com.mbdsdr.spatial.sdr.SdrSession
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * SDR 会话编排（M4.2）：授权 → 连接 → 调谐 → 读 I/Q → 释放。
 * 全离线：假授权端口 + 假 rtl_tcp 连接；不碰真实 dongle。
 */
class SdrSessionTest {

    private fun bytes(vararg v: Int): ByteArray = ByteArray(v.size) { v[it].toByte() }

    private val header = bytes(
        0x52, 0x54, 0x4C, 0x30,
        0x05, 0x00, 0x00, 0x00,
        0x1D, 0x00, 0x00, 0x00,
    )

    private fun leaseJson(host: String = "127.0.0.1", port: Int = 1234): JsonObject =
        JsonParser.parseString(
            """{"ok":true,"schema":"mbdsdr.sdr-lease/1",
               "lease":{"lease_id":"sdr-1","device_id":"dev-h"},
               "rtl_tcp":{"host":"$host","port":$port}}""").asJsonObject

    private class FakePort(
        private val responder: () -> JsonObject,
    ) : SdrGatewayPort {
        var acquireCalls = 0
        var releaseCalls = 0
        var releaseToken: String? = null

        override fun acquire(token: String, role: String, purpose: String?): JsonObject {
            acquireCalls++
            return responder()
        }

        override fun release(token: String) {
            releaseCalls++
            releaseToken = token
        }
    }

    private class FakeConnection(
        private val chunks: List<ByteArray>,
        private val failHeader: Boolean = false,
    ) : RtlTcpConnection {
        val writes = mutableListOf<ByteArray>()
        var closed = false
        private val queued = ArrayDeque(chunks)

        override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
            if (failHeader) throw IOException("read boom")
            val chunk = queued.removeFirstOrNull() ?: return -1
            val n = minOf(length, chunk.size)
            System.arraycopy(chunk, 0, buffer, offset, n)
            return n
        }

        override fun write(bytes: ByteArray) {
            writes.add(bytes.copyOf())
        }

        override fun close() {
            closed = true
        }
    }

    private class FakeTransport(
        private val conn: RtlTcpConnection? = null,
    ) : RtlTcpTransport {
        var attempts = 0
        override fun connect(host: String, port: Int): RtlTcpConnection {
            attempts++
            return conn ?: throw IOException("refused")
        }
    }

    @Test
    fun startAcquiresConnectsTunesAndCloseReleases() {
        val conn = FakeConnection(listOf(header, bytes(10, 20, 30, 40)))
        val port = FakePort { leaseJson() }
        val session = SdrSession(port, FakeTransport(conn))

        val info = session.start(
            token = "tok-h", role = "headset",
            centerHz = 100_000_000L, sampleRateHz = 2_400_000,
            gainTenthDb = 300, ppm = -3, purpose = "直连频谱")

        assertEquals("127.0.0.1", info.endpointHost)
        assertEquals(1234, info.endpointPort)
        assertEquals(5, info.tunerType)
        assertEquals(29, info.gainCount)
        assertEquals(100_000_000L, info.centerHz)
        assertTrue(session.isActive)
        assertEquals(1, port.acquireCalls)

        // 调谐命令：采样率 → 频率 → 增益模式 → 增益 → ppm
        assertEquals(5, conn.writes.size)
        assertArrayEquals(bytes(0x02, 0x00, 0x9F, 0x24, 0x00), conn.writes[0])
        assertArrayEquals(bytes(0x01, 0x00, 0xE1, 0xF5, 0x05), conn.writes[1])

        val iq = ByteArray(8)
        assertEquals(4, session.readIq(iq))
        assertArrayEquals(bytes(10, 20, 30, 40), iq.copyOf(4))

        session.close()
        assertTrue(conn.closed)
        assertFalse(session.isActive)
        assertNull(session.info)
        assertEquals(1, port.releaseCalls)
        assertEquals("tok-h", port.releaseToken)

        session.close() // 幂等
        assertEquals(1, port.releaseCalls)
    }

    @Test
    fun missingRtlTcpEndpointReleasesLeaseAndDoesNotConnect() {
        val port = FakePort {
            JsonParser.parseString("""{"ok":true,"lease":{"lease_id":"sdr-1"}}""")
                .asJsonObject
        }
        val transport = FakeTransport()
        val session = SdrSession(port, transport)
        try {
            session.start("tok-h", "headset", 100_000_000L, 2_400_000)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("rtl_tcp"))
        }
        assertEquals(1, port.releaseCalls)
        assertEquals(0, transport.attempts)
        assertFalse(session.isActive)
    }

    @Test
    fun invalidPortReleasesLease() {
        val port = FakePort { leaseJson(port = 0) }
        val session = SdrSession(port, FakeTransport())
        try {
            session.start("tok-h", "headset", 100_000_000L, 2_400_000)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("port"))
        }
        assertEquals(1, port.releaseCalls)
    }

    @Test
    fun connectFailureReleasesLease() {
        val port = FakePort { leaseJson() }
        val session = SdrSession(port, FakeTransport())
        try {
            session.start("tok-h", "headset", 100_000_000L, 2_400_000)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("127.0.0.1:1234"))
        }
        assertEquals(1, port.releaseCalls)
        assertFalse(session.isActive)
    }

    @Test
    fun badGreetingClosesConnectionAndReleases() {
        val bad = header.copyOf().also { it[3] = 0x31 }
        val conn = FakeConnection(listOf(bad))
        val port = FakePort { leaseJson() }
        val session = SdrSession(port, FakeTransport(conn))
        try {
            session.start("tok-h", "headset", 100_000_000L, 2_400_000)
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("RTL0"))
        }
        assertTrue(conn.closed)
        assertEquals(1, port.releaseCalls)
    }

    @Test
    fun outOfRangeTuneClosesAndReleases() {
        val conn = FakeConnection(listOf(header))
        val port = FakePort { leaseJson() }
        val session = SdrSession(port, FakeTransport(conn))
        try {
            session.start("tok-h", "headset", 1_000_000L, 2_400_000)
            throw AssertionError("应当抛 IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("越界"))
        }
        assertTrue(conn.closed)
        assertEquals(1, port.releaseCalls)
        assertFalse(session.isActive)
    }

    @Test
    fun acquireFailureDoesNotRelease() {
        val port = FakePort { throw DeviceGatewayException("未配对：先完成配对") }
        val session = SdrSession(port, FakeTransport())
        try {
            session.start("tok-h", "headset", 100_000_000L, 2_400_000)
            throw AssertionError("应当抛 DeviceGatewayException")
        } catch (e: DeviceGatewayException) {
            assertTrue(e.message!!.contains("未配对"))
        }
        assertEquals(1, port.acquireCalls)
        assertEquals(0, port.releaseCalls)
    }

    @Test
    fun startTwiceIsRejectedWithoutSecondLease() {
        val conn = FakeConnection(listOf(header))
        val port = FakePort { leaseJson() }
        val session = SdrSession(port, FakeTransport(conn))
        session.start("tok-h", "headset", 100_000_000L, 2_400_000)
        try {
            session.start("tok-h", "headset", 100_000_000L, 2_400_000)
            throw AssertionError("应当抛 IllegalStateException")
        } catch (e: IllegalStateException) {
            assertTrue(e.message!!.contains("已启动"))
        }
        assertEquals(1, port.acquireCalls)
        session.close()
    }

    @Test
    fun readBeforeStartIsRejected() {
        val session = SdrSession(FakePort { leaseJson() }, FakeTransport())
        try {
            session.readIq(ByteArray(8))
            throw AssertionError("应当抛 RtlTcpException")
        } catch (e: RtlTcpException) {
            assertTrue(e.message!!.contains("未启动"))
        }
    }
}
