package com.mbdsdr.spatial.net

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.mbdsdr.spatial.sdr.SdrFormat

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
    /** 桌面只读频谱快照一行（M2.6）；未取到时为空。 */
    val spectrumText: String = "",
    /** 快照功率轴映射成画柱高度 [0,1]；空 = 没取到/没有帧。 */
    val spectrumLevels: List<Float> = emptyList(),
    /** 桌面状态一行（站址/SDR 配置/缺项）；未取到时为空。 */
    val statusText: String = "",
    /** 两机融合位姿一行（M3.2 消费端）；未取到时为空。 */
    val fuseText: String = "",
    /** 权威端名册一行（角色·名称）；未取到时为空。 */
    val rosterText: String = "",
    /** 逐设备位姿一行（M3 消费端）；未取到时为空。 */
    val posesText: String = "",
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

    fun rosterSummary(json: JsonObject): String {
        val arr = json.getAsJsonArray("devices") ?: return "名册：回执缺 devices"
        if (arr.size() == 0) return "名册为空"
        val rows = mutableListOf<String>()
        for (el in arr) {
            if (!el.isJsonObject) continue
            val d = el.asJsonObject
            val role = d.get("role")?.takeIf { it.isJsonPrimitive }?.asString.orEmpty()
            val name = d.get("name")?.takeIf { it.isJsonPrimitive }?.asString
                ?.takeIf { it.isNotBlank() } ?: "未命名"
            rows.add("${roleLabel(role)}·$name")
        }
        return "名册 ${arr.size()} 台：" + rows.joinToString(" / ")
    }

    /** 网关角色 → 中文短标签；未知角色原样保留（不猜）。 */
    private fun roleLabel(role: String): String = when (role) {
        "lens" -> "镜头"
        "eyepiece" -> "目镜"
        "handheld" -> "手持"
        "headset" -> "头显"
        else -> if (role.isBlank()) "未知角色" else role
    }

    /**
     * 逐设备位姿一行：`镜头·方位 76.9° 高度 0.0° ±1.0°`；
     * 目镜带识别时追加 `·星名 HIP 91262 0.35°`。空列表如实说没有上报。
     */
    fun posesSummary(json: JsonObject): String {
        val arr = json.getAsJsonArray("poses") ?: return "位姿：回执缺 poses"
        if (arr.size() == 0) return "位姿：没有上报"
        val rows = mutableListOf<String>()
        for (el in arr) {
            if (!el.isJsonObject) continue
            val p = el.asJsonObject
            val role = p.get("role")?.takeIf { it.isJsonPrimitive }?.asString.orEmpty()
            fun fmt(v: Double?): String =
                v?.let { String.format(java.util.Locale.US, "%.1f°", it) } ?: "—"
            val row = StringBuilder()
            row.append(roleLabel(role)).append("·方位 ").append(fmt(num(p, "az_deg")))
                .append(" 高度 ").append(fmt(num(p, "alt_deg")))
                .append(" ±").append(fmt(num(p, "accuracy_deg")))
            val id = p.get("identification")?.takeIf { it.isJsonObject }?.asJsonObject
            val name = id?.get("name")?.takeIf { it.isJsonPrimitive }?.asString
                ?.takeIf { it.isNotBlank() }
            if (name != null && id != null) {
                val hip = id.get("hip")?.takeIf {
                    it.isJsonPrimitive && it.asJsonPrimitive.isNumber
                }?.asInt
                row.append("·").append(name)
                if (hip != null) row.append(" HIP ").append(hip)
                num(id, "separation_deg")?.let {
                    row.append(" ").append(
                        String.format(java.util.Locale.US, "%.2f°", it))
                }
            }
            rows.add(row.toString())
        }
        return "位姿 ${rows.size} 条：" + rows.joinToString(" / ")
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

    /**
     * 融合位姿（`mbdsdr.pose-fusion/1`）压成一行：
     * 有效时给方位/高度/精度/来源；无效时只转述权威端给的原因，绝不猜朝向。
     */
    fun fuseSummary(json: JsonObject): String {
        val validEl = json.get("valid") ?: return "融合位姿：回执缺 valid 字段"
        if (!validEl.isJsonPrimitive || !validEl.asJsonPrimitive.isBoolean) {
            return "融合位姿：valid 不是布尔值"
        }
        if (!validEl.asBoolean) {
            val reason = json.get("reason")?.takeIf { it.isJsonPrimitive }?.asString
            return "融合位姿：不可用（${reason?.takeIf { it.isNotBlank() } ?: "未知原因"}）"
        }
        val az = num(json, "az_deg")
        val alt = num(json, "alt_deg")
        val acc = num(json, "accuracy_deg")
        fun deg(v: Double?, digits: Int): String =
            v?.let { String.format(java.util.Locale.US, "%.${digits}f°", it) } ?: "—"
        val sources = strings(json.getAsJsonArray("sources"))
        val n = num(json, "observations")?.toInt()
        val countText = n?.toString() ?: "—"
        val sourceText = if (sources.isEmpty()) "—" else sources.joinToString(", ")
        return ("融合位姿：方位 ${deg(az, 1)} 高度 ${deg(alt, 1)}"
            + " · 精度 ${deg(acc, 2)} · $countText 条（$sourceText）")
    }

    /**
     * 桌面只读频谱快照（`mbdsdr.spectrum-snapshot/1`）压成一行。
     *
     * 明确写「只读·非实时」：这是权威端某一帧，不是头显在收流。
     * 非有限 bin 在回执里是 null，这里不参与峰值/噪声底；缺字段写「—」，
     * 不拿 0 dB 或默认频率顶替。
     */
    fun spectrumSummary(json: JsonObject): String {
        fun mhz(v: Double?): String = v?.let {
            String.format(java.util.Locale.US, "%.3f MHz", it / 1e6)
        } ?: "—"
        fun db(v: Double?): String = v?.let {
            String.format(java.util.Locale.US, "%.1f dB", it)
        } ?: "—"
        val rate = num(json, "sample_rate_hz")
        val rateText = rate?.let {
            String.format(java.util.Locale.US, "%.2f MS/s", it / 1e6)
        } ?: "—"
        val bins = num(json, "bins")?.toInt()?.toString() ?: "—"
        val age = num(json, "age_s")
        val ageText = age?.let {
            String.format(java.util.Locale.US, "%.1f s", it)
        } ?: "未知"
        val staleEl = json.get("stale")
        val stale = staleEl?.takeIf {
            it.isJsonPrimitive && it.asJsonPrimitive.isBoolean
        }?.asBoolean
        val staleText = if (stale == true) "（帧已过期）" else ""
        return ("频谱快照（只读·非实时）：${mhz(num(json, "center_freq_hz"))}"
            + " · $rateText · $bins bin"
            + " · 峰值 ${mhz(num(json, "peak_freq_hz"))} ${db(num(json, "peak_power_db"))}"
            + " · 噪声底 ${db(num(json, "noise_floor_db"))}"
            + " · 帧龄 $ageText$staleText")
    }

    /**
     * 桌面状态（`mbdsdr.desktop-status/1`）压成一行：站址 / SDR 配置 / 缺项。
     *
     * `dongle_probed=false` 时明确写「未探测 dongle」——配置了 rtl_tcp 地址
     * 不等于 dongle 在收；缺字段写「—」，不猜。
     */
    fun statusSummary(json: JsonObject): String {
        val station = json.get("station")
            ?.takeIf { it.isJsonObject }?.asJsonObject
        val lat = num(station, "lat_deg")
        val lon = num(station, "lon_deg")
        val stationText = if (lat != null && lon != null) {
            String.format(java.util.Locale.US, "%.2f, %.2f", lat, lon)
        } else {
            "—"
        }
        var sdrText = "—"
        val sdr = json.get("sdr")?.takeIf { it.isJsonObject }?.asJsonObject
        if (sdr != null) {
            val ep = sdr.get("rtl_tcp")
                ?.takeIf { it.isJsonObject }?.asJsonObject
            val host = ep?.get("host")
                ?.takeIf { it.isJsonPrimitive }?.asString
            val port = num(ep, "port")?.toInt()
            if (!host.isNullOrBlank() && port != null) {
                sdrText = "rtl_tcp $host:$port"
            }
            val probed = sdr.get("dongle_probed")
                ?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isBoolean }
                ?.asBoolean
            if (probed == false) sdrText += "（未探测 dongle）"
        }
        val missing = strings(json.getAsJsonArray("missing"))
        val missingText = if (missing.isEmpty()) "—" else missing.joinToString(", ")
        return "桌面状态：站址 $stationText · SDR $sdrText · 缺项 $missingText"
    }

    /**
     * 快照 `powers_db` → 画柱高度 [0,1]；null/非数字/非有限 bin 保持 0（不画）。
     *
     * 映射只是显示口径（-100…0 dB 线性到 0…1），不是功率标定。
     */
    fun spectrumLevels(json: JsonObject): List<Float> {
        val arr: JsonArray = json.get("powers_db")
            ?.takeIf { it.isJsonArray }?.asJsonArray ?: return emptyList()
        val db = arr.map { el: JsonElement ->
            if (el.isJsonNull) null
            else if (el.isJsonPrimitive && el.asJsonPrimitive.isNumber) el.asFloat
            else null
        }
        return SdrFormat.decibelsToLevels(db).toList()
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

    fun refreshRoster() {
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "读取名册中…")
        try {
            val data = client.roster(state.token)
            state = state.copy(
                busy = false,
                rosterText = GatewayFormat.rosterSummary(data),
                status = "名册已更新")
        } catch (e: DeviceGatewayException) {
            // 名册没有「上次结果」语义：失败就清空，避免把旧名单当现状。
            state = state.copy(
                busy = false,
                rosterText = "",
                status = "名册读取失败：${e.message ?: "未知错误"}")
        }
    }

    fun refreshPoses() {
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "读取位姿中…")
        try {
            val data = client.poses(state.token)
            state = state.copy(
                busy = false,
                posesText = GatewayFormat.posesSummary(data),
                status = "位姿已更新")
        } catch (e: DeviceGatewayException) {
            // 与名册同理：失败清空，不把旧位姿当现状。
            state = state.copy(
                busy = false,
                posesText = "",
                status = "位姿读取失败：${e.message ?: "未知错误"}")
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

    /** 拉一次两机融合位姿（M3.2 的只读消费端）；不可用时只显示权威端原因。 */
    fun refreshFuse() {
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "读取融合位姿中…")
        try {
            val data = client.fusedPose(state.token)
            state = state.copy(
                busy = false,
                fuseText = GatewayFormat.fuseSummary(data),
                status = "融合位姿已更新")
        } catch (e: DeviceGatewayException) {
            fail("融合位姿失败：${e.message}")
        }
    }

    /** 拉一次桌面只读频谱快照（M2.6）；没有可读帧时如实报 503 原因。 */
    fun refreshSpectrum() {
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "读取频谱快照中…")
        try {
            val data = client.spectrum(state.token)
            state = state.copy(
                busy = false,
                spectrumText = GatewayFormat.spectrumSummary(data),
                spectrumLevels = GatewayFormat.spectrumLevels(data),
                status = "频谱快照已更新")
        } catch (e: DeviceGatewayException) {
            fail("频谱快照失败：${e.message}")
        }
    }

    /** 拉一次桌面状态（站址/SDR 配置/缺项）；失败如实报原因。 */
    fun refreshStatus() {
        val client = readyClient() ?: return
        state = state.copy(busy = true, status = "读取桌面状态中…")
        try {
            val data = client.status(state.token)
            state = state.copy(
                busy = false,
                statusText = GatewayFormat.statusSummary(data),
                status = "桌面状态已更新")
        } catch (e: DeviceGatewayException) {
            fail("桌面状态失败：${e.message}")
        }
    }

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
