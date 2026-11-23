package com.mbdsdr.spatial

import com.google.gson.JsonParser
import com.mbdsdr.spatial.net.DeviceGatewayClient
import com.mbdsdr.spatial.net.GatewayController
import com.mbdsdr.spatial.net.GatewayFormat
import com.mbdsdr.spatial.net.GatewayResponse
import com.mbdsdr.spatial.net.GatewayTransport
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
}
