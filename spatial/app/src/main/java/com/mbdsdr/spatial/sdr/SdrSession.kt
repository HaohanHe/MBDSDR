package com.mbdsdr.spatial.sdr

import com.google.gson.JsonObject
import com.mbdsdr.spatial.net.DeviceGatewayClient
import com.mbdsdr.spatial.net.RtlTcpClient
import com.mbdsdr.spatial.net.RtlTcpException
import com.mbdsdr.spatial.net.RtlTcpProtocol
import com.mbdsdr.spatial.net.RtlTcpTransport
import com.mbdsdr.spatial.net.SocketRtlTcpTransport

/** 授权端口的窄接口：单测可完全离线替换。 */
interface SdrGatewayPort {
    /** 申请/续租 SDR 独占授权；失败抛 [com.mbdsdr.spatial.net.DeviceGatewayException]。 */
    fun acquire(token: String, role: String, purpose: String?): JsonObject

    /** 释放本机授权（应在关闭会话时调用）。 */
    fun release(token: String)
}

/** 用现有 `DeviceGatewayClient` 实现授权端口（只读 HTTP + SDR 授权）。 */
class DeviceGatewaySdrPort(
    private val client: DeviceGatewayClient,
) : SdrGatewayPort {
    override fun acquire(token: String, role: String, purpose: String?): JsonObject =
        client.sdrLease(token, role, purpose)

    override fun release(token: String) {
        client.sdrRelease(token)
    }
}

/** 一次已建立的 SDR 会话（授权 + 连接 + 调谐后的快照）。 */
data class SdrSessionInfo(
    val endpointHost: String,
    val endpointPort: Int,
    val tunerType: Int,
    val gainCount: Int,
    val centerHz: Long,
    val sampleRateHz: Int,
)

/**
 * 把「SDR 授权 → rtl_tcp 连接 → 调谐」串成一次会话（M4.2）。
 *
 * 不读 I/Q 循环、不做 FFT、不碰 UI：`readIq` 交由上层节奏调用。
 * 任何一步失败都会 **关闭半开连接并尝试释放授权**，不把 dongle 留在占用状态。
 */
class SdrSession(
    private val gateway: SdrGatewayPort,
    private val transport: RtlTcpTransport = SocketRtlTcpTransport(),
) : AutoCloseable {

    private var client: RtlTcpClient? = null
    private var token: String? = null

    /** 已建立的会话快照；未启动为 null。 */
    var info: SdrSessionInfo? = null
        private set

    val isActive: Boolean get() = client != null

    /**
     * 申请授权并用返回的 rtl_tcp 地址连接、调谐。
     * 已在会话中再调用会抛 [IllegalStateException]，不重复占租约。
     */
    fun start(
        token: String,
        role: String,
        centerHz: Long,
        sampleRateHz: Int,
        gainTenthDb: Int? = null,
        ppm: Int = 0,
        purpose: String? = null,
    ): SdrSessionInfo {
        if (client != null) {
            throw IllegalStateException("SDR 会话已启动：先 close() 再重开")
        }
        val lease = gateway.acquire(token, role, purpose)
        val endpoint = lease.getAsJsonObject("rtl_tcp")
            ?: run {
                gateway.release(token)
                throw RtlTcpException("授权回执缺少 rtl_tcp 地址")
            }
        val host = endpoint.get("host")?.takeIf { it.isJsonPrimitive }?.asString
            ?.takeIf { it.isNotBlank() }
            ?: run {
                gateway.release(token)
                throw RtlTcpException("授权回执的 rtl_tcp.host 为空")
            }
        val port = endpoint.get("port")?.takeIf { it.isJsonPrimitive }?.asInt ?: 0
        if (port !in 1..65535) {
            gateway.release(token)
            throw RtlTcpException("授权回执的 rtl_tcp.port 非法：$port")
        }

        val rtl = RtlTcpClient(transport)
        try {
            val header = rtl.connect(host, port)
            rtl.tune(
                centerHz = centerHz,
                sampleRateHz = sampleRateHz,
                gainTenthDb = gainTenthDb,
                ppm = ppm,
            )
            client = rtl
            this.token = token
            val built = SdrSessionInfo(
                endpointHost = host,
                endpointPort = port,
                tunerType = header.tunerType,
                gainCount = header.gainCount,
                centerHz = centerHz,
                sampleRateHz = sampleRateHz,
            )
            info = built
            return built
        } catch (e: Exception) {
            rtl.close()
            runCatching { gateway.release(token) }
            throw e
        }
    }

    /** 读一段原始 I/Q（短读允许）；未启动或已断开都会抛 [RtlTcpException]。 */
    fun readIq(buffer: ByteArray): Int {
        val rtl = client ?: throw RtlTcpException("SDR 会话未启动")
        return rtl.readIq(buffer)
    }

    /** 关闭连接并释放授权；幂等，释放失败不掩盖关闭动作。 */
    override fun close() {
        val rtl = client
        client = null
        info = null
        rtl?.let { runCatching { it.close() } }
        val t = token
        token = null
        if (t != null) runCatching { gateway.release(t) }
    }

    companion object {
        /** 默认采样率（Hz）：rtl_tcp 常用档。 */
        const val DEFAULT_SAMPLE_RATE_HZ = 2_400_000

        /** 默认 FFT 点数（2 的幂）。 */
        const val DEFAULT_FFT_SIZE = 1024
    }
}
