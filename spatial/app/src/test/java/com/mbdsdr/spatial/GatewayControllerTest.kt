package com.mbdsdr.spatial

import com.google.gson.JsonParser
import com.mbdsdr.spatial.net.DeviceGatewayClient
import com.mbdsdr.spatial.net.GatewayController
import com.mbdsdr.spatial.net.GatewayFormat
import com.mbdsdr.spatial.net.GatewayResponse
import com.mbdsdr.spatial.net.GatewayTransport
import com.mbdsdr.spatial.net.GatewayUiState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * 头显网关状态机（M4.1 UI 数据层）：配对、全景/过境摘要与诚实错误。
 * 全部走假传输，离线。
 */
class GatewayControllerTest {

    private data class Call(val path: String, val headers: Map<String, String>, val body: String?)

    private class FakeGateway(
        private val responder: (Call) -> GatewayResponse,
    ) : GatewayTransport {
        val calls = mutableListOf<Call>()

        override fun send(
            url: String,
            method: String,
            headers: Map<String, String>,
            body: String?,
        ): GatewayResponse {
            val call = Call(url.substringAfter(":8771"), headers.toMap(), body)
            calls.add(call)
            return responder(call)
        }
    }

    private fun controller(fake: FakeGateway): GatewayController =
        GatewayController { host, port -> DeviceGatewayClient(host, port, fake) }

    @Test
    fun pairStoresTokenAndMarksPaired() {
        val fake = FakeGateway { call ->
            assertEquals("/pair", call.path)
            val sent = JsonParser.parseString(call.body).asJsonObject
            assertEquals("123456", sent.get("code").asString)
            assertEquals("headset", sent.get("role").asString)
            GatewayResponse(200, """{"token":"tok-h","device":{"role":"headset"}}""")
        }
        val c = controller(fake)
        c.updateCode("123456")
        c.pair()

        assertTrue(c.state.paired)
        assertEquals("tok-h", c.state.token)
        assertTrue(c.state.status.startsWith("已配对"))
        assertFalse(c.state.busy)
    }

    @Test
    fun invalidCodeFailsLocallyWithoutRequest() {
        val fake = FakeGateway { GatewayResponse(200, "{}") }
        val c = controller(fake)
        c.updateCode("12")
        c.pair()
        assertTrue(c.state.status.contains("6 位"))
        assertFalse(c.state.paired)
        assertTrue(fake.calls.isEmpty())
    }

    @Test
    fun wrongCodeSurfacesServerReason() {
        val fake = FakeGateway {
            GatewayResponse(403, """{"reason":"配对码错误或已轮换"}""")
        }
        val c = controller(fake)
        c.updateCode("000000")
        c.pair()
        assertFalse(c.state.paired)
        assertTrue(c.state.status.contains("配对码错误"))
    }

    @Test
    fun refreshBeforePairingIsRejected() {
        val fake = FakeGateway { GatewayResponse(200, "{}") }
        val c = controller(fake)
        c.refreshPanorama()
        assertTrue(c.state.status.contains("未配对"))
        assertTrue(fake.calls.isEmpty())
    }

    @Test
    fun panoramaSummaryKeepsMissingHonest() {
        val json = JsonParser.parseString(
            """{"schema":"mbdsdr.sky-panorama/1",
               "site":{"lat_deg":40.5,"lon_deg":-100.25},
               "sun":{"alt_deg":-18.4},
               "missing":["cloud_cover","kp_index"]}""").asJsonObject
        assertEquals(
            "站址 40.50, -100.25 · 太阳高度 -18.4° · 缺项 cloud_cover, kp_index",
            GatewayFormat.panoramaSummary(json),
        )
        val empty = JsonParser.parseString(
            """{"site":{},"sun":{},"missing":[]}""").asJsonObject
        assertEquals("站址 — · 太阳高度 — · 缺项 —",
            GatewayFormat.panoramaSummary(empty))
    }

    @Test
    fun passesSummaryListsFirstRowsAndCount() {
        val json = JsonParser.parseString(
            """{"schema":"mbdsdr.sat-passes/1","count":2,
               "passes":[
                 {"name":"ISS (ZARYA)","rise_utc":"2026-11-23T12:31:00+00:00","max_alt_deg":62.4},
                 {"name":"NOAA 15","rise_utc":"2026-11-23T14:05:00+00:00","max_alt_deg":31.0}
               ]}""").asJsonObject
        val text = GatewayFormat.passesSummary(json)
        assertTrue(text.startsWith("过境 2 条："))
        assertTrue(text.contains("ISS (ZARYA) 12:31 最高 62°"))
        assertTrue(text.contains("NOAA 15 14:05 最高 31°"))
    }

    @Test
    fun emptyPassesSaysSo() {
        val json = JsonParser.parseString(
            """{"schema":"mbdsdr.sat-passes/1","count":0,"passes":[]}""").asJsonObject
        assertEquals("过境 0 条（检查 TLE 与站址）", GatewayFormat.passesSummary(json))
    }

    @Test
    fun refreshPanoramaStoresSummaryAndBearer() {
        val fake = FakeGateway { call ->
            when (call.path) {
                "/pair" -> GatewayResponse(200, """{"token":"tok-h"}""")
                "/panorama" -> {
                    assertEquals("Bearer tok-h", call.headers["Authorization"])
                    GatewayResponse(200,
                        """{"site":{"lat_deg":1.0,"lon_deg":2.0},
                           "sun":{"alt_deg":-5.0},"missing":["cloud"]}""")
                }
                else -> GatewayResponse(404, """{"reason":"not found"}""")
            }
        }
        val c = controller(fake)
        c.updateCode("123456")
        c.pair()
        c.refreshPanorama()

        assertTrue(c.state.panoramaText.startsWith("站址 1.00, 2.00"))
        assertEquals("全景已更新", c.state.status)
        assertFalse(c.state.busy)
    }

    @Test
    fun refreshPassesSurfacesUnavailable() {
        val fake = FakeGateway { call ->
            when (call.path) {
                "/pair" -> GatewayResponse(200, """{"token":"tok-h"}""")
                "/passes" -> GatewayResponse(
                    503, """{"reason":"权威端尚未接入过境数据提供者"}""")
                else -> GatewayResponse(404, "{}")
            }
        }
        val c = controller(fake)
        c.updateCode("123456")
        c.pair()
        c.refreshPasses()
        assertTrue(c.state.status.contains("过境失败"))
        assertTrue(c.state.status.contains("尚未接入"))
        assertEquals("", c.state.passesText)
    }

    @Test
    fun networkFailureIsVisible() {
        val fake = FakeGateway { call ->
            if (call.path == "/pair") GatewayResponse(200, """{"token":"tok-h"}""")
            else throw IOException("Connection refused")
        }
        val c = controller(fake)
        c.updateCode("123456")
        c.pair()
        c.refreshPanorama()
        assertTrue(c.state.status.contains("全景失败"))
        assertTrue(c.state.status.contains("127.0.0.1"))
    }

    // ---- M4.3 断网回退：旧摘要保留且如实标注 ----

    private fun pairThen(
        responder: (Call) -> GatewayResponse,
    ): Pair<GatewayController, MutableList<String>> {
        val paths = mutableListOf<String>()
        val fake = FakeGateway { call ->
            paths.add(call.path)
            if (call.path == "/pair") GatewayResponse(200, """{"token":"tok-h"}""")
            else responder(call)
        }
        val c = controller(fake)
        c.updateCode("123456")
        c.pair()
        return c to paths
    }

    @Test
    fun panoramaFailureKeepsLastGoodAndMarksStale() {
        var offline = false
        val (c, _) = pairThen {
            if (offline) throw IOException("Connection refused")
            GatewayResponse(200,
                """{"site":{"lat_deg":30.0,"lon_deg":120.0},
                   "sun":{"alt_deg":-10.0},"missing":[]}""")
        }
        c.refreshPanorama()
        val before = c.state.panoramaText
        assertTrue(before.startsWith("站址 30.00, 120.00"))
        assertFalse(c.state.panoramaStale)

        offline = true
        c.refreshPanorama()
        assertEquals(before, c.state.panoramaText)
        assertTrue(c.state.panoramaStale)
        assertTrue(c.state.status.startsWith("未联网"))
        assertTrue(c.state.status.contains("上次结果"))
        assertFalse(c.state.busy)

        // 再次失败不重复「未联网」，改说「仍未联网」。
        c.refreshPanorama()
        assertTrue(c.state.status.startsWith("仍未联网"))
        assertEquals(before, c.state.panoramaText)

        // 恢复后清掉 stale 标记。
        offline = false
        c.refreshPanorama()
        assertFalse(c.state.panoramaStale)
        assertEquals("全景已更新", c.state.status)
    }

    @Test
    fun panoramaFailureWithoutHistorySaysSo() {
        val (c, _) = pairThen { throw IOException("Connection refused") }
        c.refreshPanorama()
        assertEquals("", c.state.panoramaText)
        assertFalse(c.state.panoramaStale)
        assertTrue(c.state.status.contains("全景失败"))
        assertTrue(c.state.status.contains("无上次结果"))
        assertTrue(c.state.status.contains("包内内容"))
    }

    @Test
    fun passesFailureFallsBackToLastGood() {
        var offline = false
        val (c, _) = pairThen {
            if (offline) throw IOException("Connection refused")
            GatewayResponse(200,
                """{"schema":"mbdsdr.sat-passes/1","count":1,
                   "passes":[{"name":"ISS (ZARYA)",
                              "rise_utc":"2026-11-23T12:31:00+00:00",
                              "max_alt_deg":62.4}]}""")
        }
        c.refreshPasses()
        val before = c.state.passesText
        assertTrue(before.startsWith("过境 1 条："))

        offline = true
        c.refreshPasses()
        assertEquals(before, c.state.passesText)
        assertTrue(c.state.passesStale)
        assertFalse(c.state.panoramaStale)
        assertTrue(c.state.status.startsWith("未联网"))
    }

    // ---- M4.1 收尾：「此刻」窗口的网关一行 ----

    @Test
    fun stationLineBeforeAnyRefreshPointsToPanel() {
        val line = GatewayFormat.stationLine(
            GatewayUiState(host = "192.168.1.20", portText = "8771"))
        assertEquals("未取（在「头顶」配对后刷新全景）· 192.168.1.20:8771", line)
    }

    @Test
    fun stationLineRelaysPanoramaAndStaleTag() {
        val fresh = GatewayUiState(
            host = "192.168.1.20",
            portText = "8771",
            panoramaText = "站址 40.50, -100.25 · 太阳高度 -18.4° · 缺项 cloud_cover",
        )
        assertEquals(
            "192.168.1.20:8771 · 站址 40.50, -100.25 · 太阳高度 -18.4° · 缺项 cloud_cover",
            GatewayFormat.stationLine(fresh),
        )
        val stale = fresh.copy(panoramaStale = true)
        assertTrue(GatewayFormat.stationLine(stale).endsWith("（未联网·上次结果）"))
    }

    // ---- M4.2 客户端：SDR 授权 ----

    @Test
    fun sdrSummaryIsHonestAboutHolderAndEndpoint() {
        val idle = JsonParser.parseString(
            """{"active":false,"ttl_s":300}""").asJsonObject
        assertEquals("SDR 授权：空闲（可签发，有效期 300s）",
            GatewayFormat.sdrSummary(idle, "dev-h"))

        val other = JsonParser.parseString(
            """{"active":true,"lease":{"device_id":"dev-2","role":"headset",
               "name":"头显2","expires_utc":"2026-11-23T15:40:00Z"}}""").asJsonObject
        assertEquals("SDR 授权：headset「头显2」占用到 15:40:00",
            GatewayFormat.sdrSummary(other, "dev-h"))

        val mine = JsonParser.parseString(
            """{"active":true,
                "lease":{"device_id":"dev-h","role":"headset","name":"头显",
                         "expires_utc":"2026-11-23T15:40:00Z"},
                "rtl_tcp":{"host":"127.0.0.1","port":1234}}""").asJsonObject
        assertEquals("SDR 授权：本机持有到 15:40:00（rtl_tcp 127.0.0.1:1234）",
            GatewayFormat.sdrSummary(mine, "dev-h"))

        val broken = JsonParser.parseString("""{"ok":true}""").asJsonObject
        assertEquals("SDR 授权：回执缺 active 字段",
            GatewayFormat.sdrSummary(broken, "dev-h"))
    }

    @Test
    fun sdrLeaseFlowUsesPairDeviceIdAndRelease() {
        val fake = FakeGateway { call ->
            when (call.path) {
                "/pair" -> GatewayResponse(200,
                    """{"token":"tok-h","device":{"id":"dev-h","role":"headset"}}""")
                "/sdr/lease" -> {
                    assertEquals("Bearer tok-h", call.headers["Authorization"])
                    GatewayResponse(200,
                        """{"ok":true,"renewed":false,"exclusive":true,
                           "lease":{"lease_id":"sdr-1","device_id":"dev-h",
                                    "role":"headset","name":"头显",
                                    "expires_utc":"2026-11-23T15:40:00Z"},
                           "rtl_tcp":{"host":"127.0.0.1","port":1234}}""")
                }
                "/sdr/release" -> GatewayResponse(
                    200, """{"ok":true,"released":true,"lease_id":"sdr-1"}""")
                else -> GatewayResponse(404, """{"reason":"not found"}""")
            }
        }
        val c = controller(fake)
        c.updateCode("123456")
        c.pair()
        assertEquals("dev-h", c.state.deviceId)

        c.requestSdrLease()
        assertTrue(c.state.sdrHeld)
        assertTrue(c.state.sdrText.startsWith("SDR 授权：本机持有到 15:40:00"))
        assertTrue(c.state.sdrText.contains("rtl_tcp 127.0.0.1:1234"))
        assertEquals("SDR 授权已签发", c.state.status)

        c.releaseSdrLease()
        assertFalse(c.state.sdrHeld)
        assertEquals("SDR 授权：本机已释放", c.state.sdrText)
        assertEquals("SDR 授权已释放", c.state.status)
    }

    @Test
    fun sdrLeaseConflictSurfacesReasonAndKeepsNotHeld() {
        val fake = FakeGateway { call ->
            when (call.path) {
                "/pair" -> GatewayResponse(200,
                    """{"token":"tok-h","device":{"id":"dev-h"}}""")
                "/sdr/lease" -> GatewayResponse(409,
                    """{"error":"sdr_leased","reason":"SDR 已被 headset（dev-2）占用到 15:40:00"}""")
                else -> GatewayResponse(404, "{}")
            }
        }
        val c = controller(fake)
        c.updateCode("123456")
        c.pair()
        c.requestSdrLease()
        assertFalse(c.state.sdrHeld)
        assertTrue(c.state.status.contains("SDR 授权失败"))
        assertTrue(c.state.status.contains("已被"))
    }

    @Test
    fun sdrStatusRefreshesSummary() {
        val fake = FakeGateway { call ->
            when (call.path) {
                "/pair" -> GatewayResponse(200,
                    """{"token":"tok-h","device":{"id":"dev-h"}}""")
                "/sdr/lease" -> GatewayResponse(200,
                    """{"active":false,"ttl_s":300}""")
                else -> GatewayResponse(404, "{}")
            }
        }
        val c = controller(fake)
        c.updateCode("123456")
        c.pair()
        c.refreshSdrStatus()
        assertEquals("SDR 授权：空闲（可签发，有效期 300s）", c.state.sdrText)
        assertEquals("SDR 授权已更新", c.state.status)
    }

    // ---- M3.2 消费端：融合位姿一行 ----

    @Test
    fun fuseSummaryValidAndInvalid() {
        val valid = JsonParser.parseString(
            """{"schema":"mbdsdr.pose-fusion/1","valid":true,"level":"fused",
               "observations":2,"az_deg":108.75,"alt_deg":43.2,
               "accuracy_deg":0.87,"sources":["dev-lens","dev-eye"]}""").asJsonObject
        assertEquals(
            "融合位姿：方位 108.8° 高度 43.2° · 精度 0.87° · 2 条（dev-lens, dev-eye）",
            GatewayFormat.fuseSummary(valid),
        )

        val invalid = JsonParser.parseString(
            """{"valid":false,"reason":"observations 为空","az_deg":null}""").asJsonObject
        assertEquals("融合位姿：不可用（observations 为空）",
            GatewayFormat.fuseSummary(invalid))

        val broken = JsonParser.parseString("""{"observations":0}""").asJsonObject
        assertEquals("融合位姿：回执缺 valid 字段",
            GatewayFormat.fuseSummary(broken))
    }

    @Test
    fun refreshFuseSurfacesReasonAndValue() {
        var fused = """{"valid":false,"reason":"observations 为空"}"""
        val fake = FakeGateway { call ->
            when (call.path) {
                "/pair" -> GatewayResponse(200, """{"token":"tok-h"}""")
                "/fuse" -> {
                    assertEquals("Bearer tok-h", call.headers["Authorization"])
                    GatewayResponse(200, fused)
                }
                else -> GatewayResponse(404, "{}")
            }
        }
        val c = controller(fake)
        c.updateCode("123456")
        c.pair()

        c.refreshFuse()
        assertEquals("融合位姿：不可用（observations 为空）", c.state.fuseText)
        assertEquals("融合位姿已更新", c.state.status)

        fused = """{"valid":true,"observations":2,"az_deg":110.0,"alt_deg":44.0,
                    "accuracy_deg":1.5,"sources":["dev-lens","dev-eye"]}"""
        c.refreshFuse()
        assertTrue(c.state.fuseText.startsWith("融合位姿：方位 110.0°"))
        assertTrue(c.state.fuseText.contains("2 条"))
        assertFalse(c.state.busy)
    }
}
