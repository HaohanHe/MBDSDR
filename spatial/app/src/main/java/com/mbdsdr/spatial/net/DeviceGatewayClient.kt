package com.mbdsdr.spatial.net

import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * 头显侧「多设备网关」只读客户端（M4 数据层）。
 *
 * 与手机 `DeviceGatewayClient` / 桌面 `device_gateway.py` 共用同一 HTTP 契约：
 * `GET /health`、`POST /pair`、`GET /roster`、`GET /panorama`、`GET /passes`、
 * `GET /poses`、`GET /fuse`；`POST /pose` 只允许 lens/eyepiece。没有任何写硬件/改站址端点。
 *
 * 传输可注入：单测用假传输完全离线，不碰真实网络。
 */
data class GatewayResponse(val status: Int, val body: String)

fun interface GatewayTransport {
    fun send(
        url: String,
        method: String,
        headers: Map<String, String>,
        body: String?,
    ): GatewayResponse
}

class DeviceGatewayException(
    message: String,
    val status: Int? = null,
) : Exception(message)

class DeviceGatewayClient(
    private val host: String,
    private val port: Int,
    private val transport: GatewayTransport = HttpGatewayTransport(),
) {
    companion object {
        /** 角色白名单，与桌面 `device_gateway.ROLES` 一致。 */
        val ROLES = listOf("lens", "eyepiece", "handheld", "headset")

        /** 允许上报位姿的角色。 */
        val POSE_ROLES = listOf("lens", "eyepiece")
    }

    private val gson = Gson()
    val baseUrl: String = "http://$host:$port"

    /** 免 token 探活；返回原始对象（含 pairing_required/roles）。 */
    fun health(): JsonObject = decode(send("GET", "/health", null, null))

    /** 用 6 位配对码换 token；缺 token 视为失败，不当成功。 */
    fun pair(code: String, deviceName: String, role: String): JsonObject {
        if (role !in ROLES) {
            throw DeviceGatewayException("角色必须是 ${ROLES.joinToString("/")} 之一")
        }
        val payload = JsonObject().apply {
            addProperty("code", code)
            addProperty("device_name", deviceName)
            addProperty("role", role)
        }
        val out = decode(send("POST", "/pair", null, gson.toJson(payload)))
        val token = out.get("token")
        if (token == null || !token.isJsonPrimitive || token.asString.isEmpty()) {
            throw DeviceGatewayException("配对回执缺少 token，未保存任何凭据")
        }
        return out
    }

    fun roster(token: String): JsonObject = decode(send("GET", "/roster", token, null))

    fun panorama(token: String): JsonObject =
        decode(send("GET", "/panorama", token, null))

    fun poses(token: String): JsonObject = decode(send("GET", "/poses", token, null))

    /** 权威端过境预报（`mbdsdr.sat-passes/1`）。 */
    fun passes(token: String): JsonObject = decode(send("GET", "/passes", token, null))

    /** 权威端状态（`mbdsdr.desktop-status/1`）：站址 + SDR 段 + `missing`。 */
    fun status(token: String): JsonObject = decode(send("GET", "/status", token, null))

    /** 权威端只读频谱快照（`mbdsdr.spectrum-snapshot/1`）；没有可读帧时桌面回 503。 */
    fun spectrum(token: String): JsonObject =
        decode(send("GET", "/spectrum", token, null))

    /** SDR 授权状态（`mbdsdr.sdr-lease/1`）；rtl_tcp 地址只回当前持有者。 */
    fun sdrStatus(token: String): JsonObject =
        decode(send("GET", "/sdr/lease", token, null))

    /** 申请/续租 SDR 独占授权；只有 headset 角色可用（其它角色本地即拒）。 */
    fun sdrLease(token: String, role: String, purpose: String? = null): JsonObject {
        if (role != "headset") {
            throw DeviceGatewayException("角色 $role 不能直连 SDR；只有 headset 可申请授权")
        }
        val payload = JsonObject().apply {
            if (!purpose.isNullOrBlank()) addProperty("purpose", purpose.trim())
        }
        return decode(send("POST", "/sdr/lease", token, gson.toJson(payload)))
    }

    /** 释放本机持有的 SDR 授权；非持有者会被权威端拒绝（403）。 */
    fun sdrRelease(token: String): JsonObject =
        decode(send("POST", "/sdr/release", token, null))

    /** lens+eyepiece 的融合位姿（`mbdsdr.pose-fusion/1`）。 */
    fun fusedPose(token: String): JsonObject =
        decode(send("GET", "/fuse", token, null))

    /** 上报一条 `sky-pose/1`；只读角色在本地即拒绝，一个字节都不发。 */
    fun postPose(
        token: String,
        deviceId: String,
        role: String,
        tUtcSeconds: Double,
        azDeg: Double,
        altDeg: Double,
        accuracyDeg: Double,
        rollDeg: Double = 0.0,
        fovDeg: Double? = null,
    ): JsonObject {
        if (role !in POSE_ROLES) {
            throw DeviceGatewayException("角色 $role 只读，不能上报位姿")
        }
        val payload = JsonObject().apply {
            addProperty("schema", "sky-pose/1")
            addProperty("source", deviceId)
            addProperty("role", role)
            addProperty("t_utc", tUtcSeconds)
            addProperty("az_deg", azDeg)
            addProperty("alt_deg", altDeg)
            addProperty("accuracy_deg", accuracyDeg)
            addProperty("roll_deg", rollDeg)
            if (fovDeg != null) addProperty("fov_deg", fovDeg)
        }
        return decode(send("POST", "/pose", token, gson.toJson(payload)))
    }

    private fun send(
        method: String,
        path: String,
        token: String?,
        body: String?,
    ): GatewayResponse {
        val headers = mutableMapOf("Content-Type" to "application/json")
        if (!token.isNullOrEmpty()) headers["Authorization"] = "Bearer $token"
        return try {
            transport.send("$baseUrl$path", method, headers, body)
        } catch (e: DeviceGatewayException) {
            throw e
        } catch (e: Exception) {
            throw DeviceGatewayException("连不上桌面网关 $baseUrl：${e.message}")
        }
    }

    private fun decode(response: GatewayResponse): JsonObject {
        val element = try {
            JsonParser.parseString(response.body)
        } catch (e: Exception) {
            null
        }
        val obj = if (element != null && element.isJsonObject) element.asJsonObject else null
        if (response.status in 200..299) {
            return obj ?: throw DeviceGatewayException(
                "网关返回不是 JSON 对象", response.status)
        }
        val reason = obj?.let { o ->
            listOfNotNull("reason", "error")
                .mapNotNull { key -> o.get(key)?.takeIf { it.isJsonPrimitive }?.asString }
                .firstOrNull()
        } ?: response.body
        throw DeviceGatewayException(
            reason.ifBlank { "HTTP ${response.status}" }, response.status)
    }
}

/** 默认传输：java.net.HttpURLConnection，超时 6 s，读完即断开。 */
class HttpGatewayTransport(
    private val connectTimeoutMs: Int = 6000,
    private val readTimeoutMs: Int = 6000,
) : GatewayTransport {
    override fun send(
        url: String,
        method: String,
        headers: Map<String, String>,
        body: String?,
    ): GatewayResponse {
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = connectTimeoutMs
            connection.readTimeout = readTimeoutMs
            headers.forEach { (key, value) -> connection.setRequestProperty(key, value) }
            if (body != null) {
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            return GatewayResponse(status, text)
        } catch (e: IOException) {
            throw DeviceGatewayException("连不上桌面网关 $url：${e.message}")
        } finally {
            connection.disconnect()
        }
    }
}
