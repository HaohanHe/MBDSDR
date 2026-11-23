package com.mbdsdr.spatial.net

import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket

/** rtl_tcp 连接/调谐失败：消息带人话原因，不吞底层错误。 */
class RtlTcpException(message: String, cause: Throwable? = null) :
    Exception(message, cause)

/**
 * rtl_tcp 连接抽象：单测注入假实现，完全不碰网络。
 * `read` 语义与 `InputStream.read` 一致：返回本次读到的字节数，-1 表示 EOF。
 */
interface RtlTcpConnection {
    fun read(buffer: ByteArray, offset: Int, length: Int): Int
    fun write(bytes: ByteArray)
    fun close()
}

fun interface RtlTcpTransport {
    fun connect(host: String, port: Int): RtlTcpConnection
}

/**
 * 头显侧 rtl_tcp 客户端（M4.2）：连接 → 读 `RTL0` 头 → 下发调谐 → 读 I/Q。
 *
 * 只做协议与连接生命周期，不认授权（授权由 `/sdr/lease` 决定），也不做 FFT/UI。
 * 不在构造时联网；`connect()` 失败会关闭半开连接并抛 [RtlTcpException]。
 */
class RtlTcpClient(
    private val transport: RtlTcpTransport = SocketRtlTcpTransport(),
) : AutoCloseable {

    private var connection: RtlTcpConnection? = null

    /** 服务端问候头；未连接为 null。 */
    var header: RtlTcpProtocol.Header? = null
        private set

    val isConnected: Boolean get() = connection != null

    /**
     * 连接并读 12 字节问候头。允许短读，循环补齐；超时/EOF/坏魔数都抛错。
     */
    fun connect(host: String, port: Int): RtlTcpProtocol.Header {
        close()
        val conn = try {
            transport.connect(host, port)
        } catch (e: Exception) {
            throw RtlTcpException("连不上 rtl_tcp $host:$port：${e.message}", e)
        }
        var filled = 0
        val scratch = ByteArray(RtlTcpProtocol.HEADER_BYTES)
        try {
            while (filled < scratch.size) {
                val n = conn.read(scratch, filled, scratch.size - filled)
                if (n < 0) {
                    throw RtlTcpException(
                        "rtl_tcp 在问候头读到 ${filled}/${scratch.size} 字节就断开")
                }
                if (n == 0) {
                    throw RtlTcpException("rtl_tcp 问候头读取阻塞（0 字节）")
                }
                filled += n
            }
        } catch (e: RtlTcpException) {
            conn.close()
            throw e
        } catch (e: Exception) {
            conn.close()
            throw RtlTcpException("读取 rtl_tcp 问候头失败：${e.message}", e)
        }
        val parsed = RtlTcpProtocol.parseHeader(scratch)
            ?: run {
                conn.close()
                throw RtlTcpException("对端不是 rtl_tcp：问候头缺少 RTL0 魔数")
            }
        connection = conn
        header = parsed
        return parsed
    }

    /**
     * 下发一组调谐命令：采样率 → 中心频率 → 增益（手动或 AGC）→ ppm。
     *
     * 所有命令先构造再发送：任一参数越界会在**写第一个字节之前**抛
     * `IllegalArgumentException`，不会把 dongle 留在半配置状态。
     */
    fun tune(
        centerHz: Long,
        sampleRateHz: Int,
        gainTenthDb: Int? = null,
        ppm: Int = 0,
    ) {
        val conn = connection ?: throw RtlTcpException("尚未连接 rtl_tcp")
        val commands = buildList {
            add(RtlTcpProtocol.setSampleRate(sampleRateHz))
            add(RtlTcpProtocol.setCenterFrequency(centerHz))
            if (gainTenthDb != null) {
                add(RtlTcpProtocol.setGainMode(manual = true))
                add(RtlTcpProtocol.setGainTenthDb(gainTenthDb))
            } else {
                add(RtlTcpProtocol.setGainMode(manual = false))
            }
            if (ppm != 0) {
                add(RtlTcpProtocol.setFrequencyCorrection(ppm))
            }
        }
        for (command in commands) {
            try {
                conn.write(command)
            } catch (e: Exception) {
                throw RtlTcpException("发送调谐命令失败：${e.message}", e)
            }
        }
    }

    /** 读一段 I/Q 原始字节；允许短读，EOF 抛错（调用方不用猜长度）。 */
    fun readIq(buffer: ByteArray): Int {
        val conn = connection ?: throw RtlTcpException("尚未连接 rtl_tcp")
        val n = try {
            conn.read(buffer, 0, buffer.size)
        } catch (e: Exception) {
            throw RtlTcpException("读取 I/Q 失败：${e.message}", e)
        }
        if (n < 0) throw RtlTcpException("rtl_tcp 已断开")
        return n
    }

    override fun close() {
        val conn = connection
        connection = null
        header = null
        if (conn != null) {
            try {
                conn.close()
            } catch (e: Exception) {
                // 关闭失败不掩盖调用方原本的错误；下次连接会重建。
            }
        }
    }
}

/** 默认传输：真实 TCP，连接/读超时各 6 s。 */
class SocketRtlTcpTransport(
    private val connectTimeoutMs: Int = 6000,
    private val readTimeoutMs: Int = 6000,
) : RtlTcpTransport {
    override fun connect(host: String, port: Int): RtlTcpConnection {
        val socket = Socket()
        try {
            socket.connect(InetSocketAddress(host, port), connectTimeoutMs)
            socket.soTimeout = readTimeoutMs
            return object : RtlTcpConnection {
                private val input: InputStream = socket.getInputStream()
                private val output: OutputStream = socket.getOutputStream()

                override fun read(buffer: ByteArray, offset: Int, length: Int): Int =
                    input.read(buffer, offset, length)

                override fun write(bytes: ByteArray) {
                    output.write(bytes)
                    output.flush()
                }

                override fun close() {
                    socket.close()
                }
            }
        } catch (e: Exception) {
            try {
                socket.close()
            } catch (ignored: Exception) {
                // 原始异常更重要
            }
            throw e
        }
    }
}
