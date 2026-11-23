package com.mbdsdr.spatial

import com.google.gson.JsonParser
import com.mbdsdr.spatial.net.DeviceGatewayClient
import com.mbdsdr.spatial.net.DeviceGatewayException
import com.mbdsdr.spatial.net.GatewayResponse
import com.mbdsdr.spatial.net.GatewayTransport
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException

/**
 * 头显侧网关客户端（M4 数据层）：按桌面 `device_gateway.py` 的冻结契约收发，
 * 拒绝/不可达一律诚实抛错。全部走假传输，离线。
 */
class DeviceGatewayClientTest {

    private data class Call(
        val url: String,
        val method: String,
        val headers: Map<String, String>,
        val body: String?,
    )

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
            val call = Call(url, method, headers.toMap(), body)
            calls.add(call)
            return responder(call)
        }

        val last: Call get() = calls.last()
    }

    private fun client(transport: GatewayTransport) =
        DeviceGatewayClient("192.168.1.20", 8771, transport)

    @Test
    fun pairSendsCodeNameRoleAndReturnsToken() {
        val fake = FakeGateway {
            assertEquals("/pair", it.url.substringAfter(":8771"))
            assertEquals("POST", it.method)
            assertFalse(it.headers.containsKey("Authorization"))
            val sent = JsonParser.parseString(it.body!!).asJsonObject
            assertEquals("123456", sent.get("code").asString)
            assertEquals("目镜机", sent.get("device_name").asString)
            assertEquals("eyepiece", sent.get("role").asString)
            GatewayResponse(200, """{"token":"tok-abc","device":{"role":"eyepiece"}}""")
        }
        val out = client(fake).pair("123456", "目镜机", "eyepiece")
        assertEquals("tok-abc", out.get("token").asString)
        assertEquals(1, fake.calls.size)
    }

    @Test
    fun pairWrongCodeSurfacesServerReasonAndStatus() {
        val fake = FakeGateway {
            GatewayResponse(403, """{"error":"bad_code","reason":"配对码错误或已轮换"}""")
        }
        try {
            client(fake).pair("000000", "x", "handheld")
            fail("应当抛 DeviceGatewayException")
        } catch (e: DeviceGatewayException) {
            assertEquals(403, e.status ?: -1)
            assertTrue(e.message!!.contains("配对码"))
        }
    }

    @Test
    fun pairWithoutTokenIsNotSuccess() {
        val fake = FakeGateway { GatewayResponse(200, """{"device":{}}""") }
        try {
            client(fake).pair("123456", "x", "handheld")
            fail("应当抛 DeviceGatewayException")
        } catch (e: DeviceGatewayException) {
            assertTrue(e.message!!.contains("token"))
        }
    }

    @Test
    fun invalidRoleIsRejectedLocallyWithoutRequest() {
        val fake = FakeGateway { GatewayResponse(200, "{}") }
        try {
            client(fake).pair("123456", "x", "camera")
            fail("应当抛 DeviceGatewayException")
        } catch (e: DeviceGatewayException) {
            assertTrue(e.message!!.contains("角色"))
        }
        assertTrue(fake.calls.isEmpty())
    }

    @Test
    fun readOnlyRolesCannotPostPoseLocally() {
        val fake = FakeGateway { GatewayResponse(200, "{}") }
        for (role in listOf("handheld", "headset")) {
            try {
                client(fake).postPose(
                    token = "tok-abc", deviceId = "dev-1", role = role,
                    tUtcSeconds = 1700000000.0, azDeg = 120.0, altDeg = 30.0,
                    accuracyDeg = 1.0)
                fail("应当抛 DeviceGatewayException")
            } catch (e: DeviceGatewayException) {
                assertTrue(e.message!!.contains("只读"))
            }
        }
        assertTrue(fake.calls.isEmpty())
    }

    @Test
    fun lensPosePayloadIsSkyPoseOne() {
        val fake = FakeGateway {
            assertEquals("/pose", it.url.substringAfter(":8771"))
            assertEquals("POST", it.method)
            assertEquals("Bearer tok-abc", it.headers["Authorization"])
            GatewayResponse(200, """{"ok":true}""")
        }
        client(fake).postPose(
            token = "tok-abc", deviceId = "lens-7", role = "lens",
            tUtcSeconds = 1700000000.5, azDeg = 214.2, altDeg = 41.8,
            accuracyDeg = 0.6, fovDeg = 8.5)
        val sent = JsonParser.parseString(fake.last.body!!).asJsonObject
        assertEquals("sky-pose/1", sent.get("schema").asString)
        assertEquals("lens-7", sent.get("source").asString)
        assertEquals("lens", sent.get("role").asString)
        assertEquals(1700000000.5, sent.get("t_utc").asDouble, 1e-9)
        assertEquals(214.2, sent.get("az_deg").asDouble, 1e-9)
        assertEquals(41.8, sent.get("alt_deg").asDouble, 1e-9)
        assertEquals(0.6, sent.get("accuracy_deg").asDouble, 1e-9)
        assertEquals(0.0, sent.get("roll_deg").asDouble, 1e-9)
        assertEquals(8.5, sent.get("fov_deg").asDouble, 1e-9)
    }

    @Test
    fun readsCarryBearerAndParseContracts() {
        val fake = FakeGateway { call ->
            assertEquals("Bearer tok-abc", call.headers["Authorization"])
            when (call.url.substringAfter(":8771")) {
                "/roster" -> GatewayResponse(
                    200, """{"schema":"mbdsdr.device-roster/1","devices":[]}""")
                "/panorama" -> GatewayResponse(
                    200, """{"schema":"mbdsdr.sky-panorama/1","missing":["cloud"]}""")
                "/passes" -> GatewayResponse(
                    200, """{"schema":"mbdsdr.sat-passes/1","count":0,"passes":[]}""")
                "/fuse" -> GatewayResponse(
                    200, """{"schema":"mbdsdr.pose-fusion/1","valid":false}""")
                else -> GatewayResponse(404, """{"reason":"not found"}""")
            }
        }
        val c = client(fake)
        assertEquals("mbdsdr.device-roster/1", c.roster("tok-abc").get("schema").asString)
        assertEquals("mbdsdr.sky-panorama/1", c.panorama("tok-abc").get("schema").asString)
        assertEquals("mbdsdr.sat-passes/1", c.passes("tok-abc").get("schema").asString)
        assertEquals("mbdsdr.pose-fusion/1", c.fusedPose("tok-abc").get("schema").asString)
    }

    @Test
    fun healthNeedsNoToken() {
        val fake = FakeGateway {
            assertFalse(it.headers.containsKey("Authorization"))
            GatewayResponse(200, """{"ok":true,"pairing_required":true}""")
        }
        val out = client(fake).health()
        assertTrue(out.get("pairing_required").asBoolean)
    }

    @Test
    fun sdrLeaseOnlyForHeadsetAndCarriesBearer() {
        val fake = FakeGateway { call ->
            when (call.url.substringAfter(":8771")) {
                "/sdr/lease" -> GatewayResponse(
                    200, """{"ok":true,"renewed":false,"exclusive":true,
                            "lease":{"lease_id":"sdr-1","device_id":"dev-h",
                                     "role":"headset","name":"头显"},
                            "rtl_tcp":{"host":"127.0.0.1","port":1234}}""")
                "/sdr/release" -> GatewayResponse(
                    200, """{"ok":true,"released":true,"lease_id":"sdr-1"}""")
                else -> GatewayResponse(404, """{"reason":"not found"}""")
            }
        }
        val c = client(fake)
        val out = c.sdrLease("tok-h", "headset", "直连频谱")
        assertEquals("sdr-1", out.getAsJsonObject("lease").get("lease_id").asString)
        assertEquals("Bearer tok-h", fake.last.headers["Authorization"])
        val sent = JsonParser.parseString(fake.last.body!!).asJsonObject
        assertEquals("直连频谱", sent.get("purpose").asString)
        assertTrue(c.sdrRelease("tok-h").get("released").asBoolean)

        // 非 headset 本地拒绝，不发请求。
        val before = fake.calls.size
        try {
            c.sdrLease("tok-l", "lens")
            fail("应当抛 DeviceGatewayException")
        } catch (e: DeviceGatewayException) {
            assertTrue(e.message!!.contains("headset"))
        }
        assertEquals(before, fake.calls.size)
    }

    @Test
    fun sdrStatusConflictSurfacesReason() {
        val fake = FakeGateway {
            GatewayResponse(409, """{"error":"sdr_leased",
                                    "reason":"SDR 已被 headset（dev-2）占用到 2026-11-23T15:40:00Z"}""")
        }
        try {
            client(fake).sdrLease("tok-h", "headset")
            fail("应当抛 DeviceGatewayException")
        } catch (e: DeviceGatewayException) {
            assertEquals(409, e.status ?: -1)
            assertTrue(e.message!!.contains("已被"))
        }
    }

    @Test
    fun spectrumUsesBearerAndSurfaces503() {
        val fake = FakeGateway { call ->
            assertEquals("/spectrum", call.url.substringAfter(":8771"))
            assertEquals("GET", call.method)
            assertEquals("Bearer tok-h", call.headers["Authorization"])
            GatewayResponse(200,
                """{"schema":"mbdsdr.spectrum-snapshot/1","readonly":true,
                   "realtime":false,"center_freq_hz":100000000.0,
                   "bins":3,"powers_db":[-90.0,null,-70.0]}""")
        }
        val out = client(fake).spectrum("tok-h")
        assertEquals("mbdsdr.spectrum-snapshot/1", out.get("schema").asString)
        assertTrue(out.get("readonly").asBoolean)
        assertFalse(out.get("realtime").asBoolean)

        val missing = FakeGateway {
            GatewayResponse(503,
                """{"error":"spectrum_unavailable","reason":"桌面当前没有可读的频谱帧"}""")
        }
        try {
            client(missing).spectrum("tok-h")
            fail("应当抛 DeviceGatewayException")
        } catch (e: DeviceGatewayException) {
            assertEquals(503, e.status ?: -1)
            assertTrue(e.message!!.contains("没有可读"))
        }
    }

    @Test
    fun statusUsesBearerAndParses() {
        val fake = FakeGateway { call ->
            assertEquals("/status", call.url.substringAfter(":8771"))
            assertEquals("GET", call.method)
            assertEquals("Bearer tok-h", call.headers["Authorization"])
            GatewayResponse(200,
                """{"schema":"mbdsdr.desktop-status/1","station":null,
                   "sdr":null,"missing":["station","sdr"]}""")
        }
        val out = client(fake).status("tok-h")
        assertEquals("mbdsdr.desktop-status/1", out.get("schema").asString)
        assertEquals(2, out.getAsJsonArray("missing").size())
    }

    @Test
    fun nonJsonSuccessIsProtocolError() {
        val fake = FakeGateway { GatewayResponse(200, "OK") }
        try {
            client(fake).roster("tok-abc")
            fail("应当抛 DeviceGatewayException")
        } catch (e: DeviceGatewayException) {
            assertTrue(e.message!!.contains("不是 JSON"))
        }
    }

    @Test
    fun unreachableNamesGatewayAddress() {
        val fake = FakeGateway { throw IOException("Connection refused") }
        try {
            client(fake).panorama("tok-abc")
            fail("应当抛 DeviceGatewayException")
        } catch (e: DeviceGatewayException) {
            assertTrue(e.message!!.contains("192.168.1.20:8771"))
        }
    }
}
