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
    /** 配对回执里的设备 id（判断 SDR 租约是不是本机持有）。 */
    val deviceId: String = "",
    /** SDR 授权一行（M4.2 客户端）；未取到时为空。 */
    val sdrText: String = "",
    val sdrHeld: Boolean = false,
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

    /**
     * 「此刻」窗口的网关一行（M4.1 收尾）：
     * 只转述权威端给的全景站址/太阳/缺项，不合成、不覆盖 bundle 的站址；
     * 从未取到过就返回一句提示，让人知道去哪取。
     */
    fun stationLine(state: GatewayUiState): String {
        val host = state.host.ifBlank { "—" }
        val port = state.portText.ifBlank { "—" }
        if (state.panoramaText.isBlank()) {
            return "未取（在「头顶」配对后刷新全景）· $host:$port"
        }
        val suffix = if (state.panoramaStale) "（未联网·上次结果）" else ""
        return "$host:$port · ${state.panoramaText}$suffix"
    }

    /**
     * SDR 授权（M4.2）压缩成一行：空闲 / 谁占用 / 本机持有时的 rtl_tcp 地址。
     * 缺字段一律「—」，不猜持有者；无 `active` 字段视为坏回执，明说读不到。
     */
    fun sdrSummary(json: JsonObject, myDeviceId: String): String {
        val activeEl = json.get("active") ?: return "SDR 授权：回执缺 active 字段"
        if (!activeEl.isJsonPrimitive || !activeEl.asJsonPrimitive.isBoolean) {
            return "SDR 授权：active 不是布尔值"
        }
        if (!activeEl.asBoolean) {
            val ttl = num(json, "ttl_s")
            return if (ttl != null) {
                "SDR 授权：空闲（可签发，有效期 ${ttl.toInt()}s）"
            } else "SDR 授权：空闲"
        }
        val lease = json.getAsJsonObject("lease")
            ?: return "SDR 授权：占用中（回执缺 lease 明细）"
        val holderId = lease.get("device_id")?.takeIf { it.isJsonPrimitive }?.asString
            ?: ""
        val role = lease.get("role")?.takeIf { it.isJsonPrimitive }?.asString ?: "—"
        val name = lease.get("name")?.takeIf { it.isJsonPrimitive }?.asString ?: "—"
        val until = lease.get("expires_utc")?.takeIf { it.isJsonPrimitive }?.asString
            ?.substringAfter('T', "")?.take(8)?.takeIf { it.length == 8 } ?: "—"
        if (holderId.isNotEmpty() && holderId == myDeviceId) {
            val ep = json.getAsJsonObject("rtl_tcp")
            val host = ep?.get("host")?.takeIf { it.isJsonPrimitive }?.asString ?: "—"
            val port = num(ep, "port")?.toInt()?.toString() ?: "—"
            return "SDR 授权：本机持有到 $until（rtl_tcp $host:$port）"
        }
        return "SDR 授权：$role「$name」占用到 $until"
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
            val deviceId = response.get("device")
                ?.takeIf { it.isJsonObject }?.asJsonObject
                ?.get("id")?.takeIf { it.isJsonPrimitive }?.asString.orEmpty()
            state = state.copy(
                busy = false, token = token, paired = true,
                deviceId = deviceId,
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

    // ------------------------------------------------------------ SDR 授权

    fun refreshSdrStatus() {
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "读取 SDR 授权中…")
        try {
            val data = client.sdrStatus(state.token)
            state = state.copy(
                busy = false,
                sdrText = GatewayFormat.sdrSummary(data, state.deviceId),
                status = "SDR 授权已更新")
        } catch (e: DeviceGatewayException) {
            fail("SDR 状态失败：${e.message}")
        }
    }

    /** 申请/续租 SDR 独占授权；非 headset 在本地即拒，一个字节都不发。 */
    fun requestSdrLease(purpose: String? = null) {
        if (state.role != "headset") {
            fail("当前角色 ${state.role} 不能申请 SDR 授权（仅 headset）")
            return
        }
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "申请 SDR 授权中…")
        try {
            val data = client.sdrLease(state.token, state.role, purpose)
            val renewed = data.get("renewed")?.takeIf { it.isJsonPrimitive }
                ?.asBoolean ?: false
            state = state.copy(
                busy = false,
                sdrHeld = true,
                sdrText = GatewayFormat.sdrSummary(
                    wrapLeaseAsStatus(data), state.deviceId),
                status = if (renewed) "SDR 授权已续租" else "SDR 授权已签发")
        } catch (e: DeviceGatewayException) {
            fail("SDR 授权失败：${e.message}")
        }
    }

    fun releaseSdrLease() {
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "释放 SDR 授权中…")
        try {
            client.sdrRelease(state.token)
            state = state.copy(
                busy = false, sdrHeld = false,
                sdrText = "SDR 授权：本机已释放",
                status = "SDR 授权已释放")
        } catch (e: DeviceGatewayException) {
            fail("SDR 释放失败：${e.message}")
        }
    }

    /** 把 `/sdr/lease` 的签发回执（lease/rtl_tcp 平铺）套成状态信封，复用同一摘要。 */
    private fun wrapLeaseAsStatus(leaseResponse: JsonObject): JsonObject {
        val out = JsonObject()
        out.addProperty("active", true)
        leaseResponse.get("lease")?.let { out.add("lease", it) }
        leaseResponse.get("rtl_tcp")?.let { out.add("rtl_tcp", it) }
        return out
    }
}
