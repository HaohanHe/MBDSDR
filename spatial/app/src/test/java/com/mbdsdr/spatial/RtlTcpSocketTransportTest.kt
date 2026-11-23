package com.mbdsdr.spatial

import com.mbdsdr.spatial.net.RtlTcpClient
import com.mbdsdr.spatial.net.SocketRtlTcpTransport
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 真实 TCP（回环）联调：`SocketRtlTcpTransport` + `RtlTcpClient`。
 * 本地假 rtl_tcp 服务器只讲协议，不碰 SDR 硬件；用来验证真实 socket
 * 路径（短读、flush、超时）而不是只信假连接。
 */
class RtlTcpSocketTransportTest {

    private fun bytes(vararg v: Int): ByteArray = ByteArray(v.size) { v[it].toByte() }

    private fun readFully(input: java.io.InputStream, n: Int): ByteArray {
        val out = ByteArray(n)
        var filled = 0
        while (filled < n) {
            val got = input.read(out, filled, n - filled)
            if (got < 0) throw IllegalStateException("提前 EOF")
            filled += got
        }
        return out
    }

    @Test
    fun realSocketHandshakeTuneAndIq() {
        val header = bytes(
            0x52, 0x54, 0x4C, 0x30,
            0x05, 0x00, 0x00, 0x00,
            0x1D, 0x00, 0x00, 0x00,
        )
        val iq = bytes(1, 2, 3, 4)
        val server = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))
        val port = server.localPort
        val done = CountDownLatch(1)
        val commands = mutableListOf<ByteArray>()

        val worker = thread(name = "fake-rtl-tcp") {
            try {
                server.accept().use { socket ->
                    val output = socket.getOutputStream()
                    val input = socket.getInputStream()
                    output.write(header)
                    output.flush()
                    // 自动增益共 3 条命令：采样率、频率、增益模式。
                    repeat(3) { commands.add(readFully(input, 5)) }
                    output.write(iq)
                    output.flush()
                    done.countDown()
                    // 等客户端关闭；不主动断，避免读 I/Q 时看到 EOF。
                    try {
                        input.read()
                    } catch (ignored: Exception) {
                    }
                }
            } catch (e: Exception) {
                done.countDown()
            }
        }

        val client = RtlTcpClient(
            SocketRtlTcpTransport(connectTimeoutMs = 3000, readTimeoutMs = 3000))
        try {
            val parsed = client.connect("127.0.0.1", port)
            assertEquals(5, parsed.tunerType)
            assertEquals(29, parsed.gainCount)

            client.tune(centerHz = 145_800_000L, sampleRateHz = 2_400_000)
            assertTrue(done.await(3, TimeUnit.SECONDS))

            assertEquals(3, commands.size)
            assertArrayEquals(bytes(0x02, 0x00, 0x9F, 0x24, 0x00), commands[0])
            assertArrayEquals(bytes(0x01, 0x40, 0xBB, 0xB0, 0x08), commands[1])
            assertArrayEquals(bytes(0x03, 0x00, 0x00, 0x00, 0x00), commands[2])

            val buffer = ByteArray(4)
            assertEquals(4, client.readIq(buffer))
            assertArrayEquals(iq, buffer)
        } finally {
            client.close()
            worker.join(2000)
            server.close()
        }
    }
}
