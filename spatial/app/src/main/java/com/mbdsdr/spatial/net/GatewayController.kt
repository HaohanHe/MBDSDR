package com.mbdsdr.spatial.net

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject

/**
 * 头显侧「多设备网关」状态机（M4.1 UI 数据层）。
 *
 * 职责：保存地址/端口/配对码与 token（进程内，不落盘），调用只读客户端并把
 * 结果整理成人类可读的一行；任何失败都写进 status，不伪造成功。
 */
data class GatewayUiState(
    val host: String = "127.0.0.1",
    val portText: String = "8771",
    val code: String = "",
    val deviceName: String = "头显",
    val role: String = "headset",
    val token: String = "",
    val paired: Boolean = false,
    val busy: Boolean = false,
    val status: String = "未配对：输入桌面地址与配对码",
    val panoramaText: String = "",
    val passesText: String = "",
    /** 上次刷新失败但留有旧摘要：面板据此标注「未联网·上次结果」。 */
    val panoramaStale: Boolean = false,
    val passesStale: Boolean = false,
)

/** 把网关 JSON 压成一行摘要；缺字段写「—」，不猜值。 */
object GatewayFormat {

    private fun num(obj: JsonObject?, key: String): Double? {
        val el: JsonElement = obj?.get(key) ?: return null
        if (!el.isJsonPrimitive || !el.asJsonPrimitive.isNumber) return null
        val v = el.asDouble
        return if (v.isFinite()) v else null
    }

    private fun strings(arr: JsonArray?): List<String> =
        arr?.mapNotNull { if (it.isJsonPrimitive) it.asString else null }
            ?.filter { it.isNotBlank() } ?: emptyList()

    fun panoramaSummary(json: JsonObject): String {
        val site = json.getAsJsonObject("site")
        val lat = num(site, "lat_deg")
        val lon = num(site, "lon_deg")
        val siteText = if (lat != null && lon != null) {
            String.format(java.util.Locale.US, "%.2f, %.2f", lat, lon)
        } else "—"
        val sunAlt = num(json.getAsJsonObject("sun"), "alt_deg")
        val sunText = sunAlt?.let { String.format(java.util.Locale.US, "%.1f°", it) } ?: "—"
        val missing = strings(json.getAsJsonArray("missing"))
        val missingText = if (missing.isEmpty()) "—" else missing.joinToString(", ")
        return "站址 $siteText · 太阳高度 $sunText · 缺项 $missingText"
    }

    fun passesSummary(json: JsonObject, limit: Int = 3): String {
        val passes = json.getAsJsonArray("passes") ?: return "无过境数据"
        val count = json.get("count")?.takeIf { it.isJsonPrimitive }?.asInt ?: passes.size()
        if (passes.size() == 0) return "过境 0 条（检查 TLE 与站址）"
        val rows = mutableListOf<String>()
        for (i in 0 until minOf(limit, passes.size())) {
            val el = passes.get(i)
            if (!el.isJsonObject) continue
            val p = el.asJsonObject
            val name = p.get("name")?.takeIf { it.isJsonPrimitive }?.asString
                ?.takeIf { it.isNotBlank() } ?: "未命名"
            val rise = p.get("rise_utc")?.takeIf { it.isJsonPrimitive }?.asString
                ?.substringAfter('T', "")?.take(5)?.takeIf { it.length == 5 } ?: "—"
            val alt = num(p, "max_alt_deg")
            val altText = alt?.let { String.format(java.util.Locale.US, "%.0f°", it) } ?: "—"
            rows.add("$name $rise 最高 $altText")
        }
        val more = if (passes.size() > limit) " …" else ""
        return "过境 $count 条：" + rows.joinToString(" / ") + more
    }
}

class GatewayController(
    private val clientFactory: (String, Int) -> DeviceGatewayClient =
        { host, port -> DeviceGatewayClient(host, port) },
) {
    var state: GatewayUiState = GatewayUiState()
        private set

    fun updateHost(value: String) {
        state = state.copy(host = value.trim())
    }

    fun updatePortText(value: String) {
        state = state.copy(portText = value.filter { it.isDigit() }.take(5))
    }

    fun updateCode(value: String) {
        state = state.copy(code = value.filter { it.isDigit() }.take(6))
    }

    private fun fail(message: String) {
        state = state.copy(busy = false, status = message)
    }

    /** 用配对码换 token；成功后本机保留 token（进程内）并标记已配对。 */
    fun pair() {
        val host = state.host.trim()
        val port = state.portText.toIntOrNull()
        if (host.isEmpty() || port == null || port !in 1..65535) {
            fail("地址或端口不合法：请填主机与 1–65535 端口")
            return
        }
        if (!Regex("^\\d{6}$").matches(state.code)) {
            fail("配对码必须是 6 位数字")
            return
        }
        state = state.copy(busy = true, status = "配对中…")
        try {
            val response = clientFactory(host, port)
                .pair(state.code, state.deviceName, state.role)
            val token = response.get("token").takeIf { it != null && it.isJsonPrimitive }
                ?.asString.orEmpty()
            if (token.isEmpty()) {
                fail("配对失败：回执缺少 token")
                return
            }
            state = state.copy(
                busy = false, token = token, paired = true,
                status = "已配对 · ${state.role} · $host:$port")
        } catch (e: DeviceGatewayException) {
            fail("配对失败：${e.message}")
        }
    }

    private fun readyClient(): DeviceGatewayClient? {
        if (!state.paired || state.token.isEmpty()) {
            fail("未配对：先完成配对")
            return null
        }
        val port = state.portText.toIntOrNull()
        if (state.host.isEmpty() || port == null || port !in 1..65535) {
            fail("地址或端口不合法")
            return null
        }
        return clientFactory(state.host.trim(), port)
    }

    fun refreshPanorama() {
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "读取全景中…")
        try {
            val data = client.panorama(state.token)
            state = state.copy(
                busy = false,
                panoramaText = GatewayFormat.panoramaSummary(data),
                panoramaStale = false,
                status = "全景已更新")
        } catch (e: DeviceGatewayException) {
            state = staleOrFail(stale = state.panoramaStale,
                hasLast = state.panoramaText.isNotBlank(),
                kind = "全景", reason = e.message ?: "未知错误")
        }
    }

    fun refreshPasses() {
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "读取过境中…")
        try {
            val data = client.passes(state.token)
            state = state.copy(
                busy = false,
                passesText = GatewayFormat.passesSummary(data),
                passesStale = false,
                status = "过境已更新")
        } catch (e: DeviceGatewayException) {
            state = staleOrFail(stale = state.passesStale,
                hasLast = state.passesText.isNotBlank(),
                kind = "过境", reason = e.message ?: "未知错误")
        }
    }

    /**
     * 失败时的两种诚实结局（M4.3）：
     * - 有上次结果：保留旧摘要并标记 stale，状态行说明「未联网·上次结果」；
     * - 没有：明确说无上次结果，让用户回看包内内容（不凭空造摘要）。
     */
    private fun staleOrFail(
        stale: Boolean,
        hasLast: Boolean,
        kind: String,
        reason: String,
    ): GatewayUiState {
        if (!hasLast) {
            return state.copy(
                busy = false,
                status = "${kind}失败：$reason（无上次结果，请回看包内内容）")
        }
        val prefix = if (stale) "仍未联网" else "未联网"
        val next = state.copy(
            busy = false,
            status = "$prefix：${kind}显示上次结果（$reason）")
        return if (kind == "全景") next.copy(panoramaStale = true)
        else next.copy(passesStale = true)
    }
}
