package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.BundleParser
import com.mbdsdr.spatial.data.eventAt
import com.mbdsdr.spatial.data.visibleStars
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BundleParserTest {

    private val fullJson = """
        {
          "schema": "mbdsdr.spatial/1",
          "created_utc": "2026-11-20T12:00:00+00:00",
          "observation_id": "obs-test-1",
          "site": {"lat_deg": 10.0, "lon_deg": 20.0, "alt_m": 100.0, "source": "gnss"},
          "time": {"utc": "2026-11-20T12:00:00+00:00", "source": "system", "precision": "本机时钟（未授时）"},
          "sky": {
            "stars": [
              {"name": "Vega", "ra_deg": 279.23, "dec_deg": 38.78, "mag": 0.03,
               "alt_deg": 66.5, "az_deg": 78.0, "visible": true},
              {"name": "Sirius", "ra_deg": 101.29, "dec_deg": -16.72, "mag": -1.46,
               "alt_deg": -12.0, "az_deg": 250.0, "visible": false}
            ],
            "passes": [{"name": "ISS", "peak_utc": "2026-11-20T12:10:00+00:00"}],
            "total_stars": 2,
            "truncated": false,
            "source": "hyg",
            "altaz_source": "geometric",
            "altaz_refraction": false
          },
          "spectrum": {
            "center_freq_hz": 100100000.0,
            "sample_rate_hz": 2400000.0,
            "frames": [{"at_s": 0.5, "db": [-10.0, -20.5]}],
            "duration_s": 3.5,
            "source": "recorded"
          },
          "audio": [{"path": "rec.wav", "label": "FM 录音", "utc": "2026-11-20T12:00:01+00:00"}],
          "timeline": [
            {"kind": "record_start", "utc": "2026-11-20T12:00:00+00:00", "samples": 0,
             "at_s": 0.0, "detail": {"center_freq_hz": 100100000.0}},
            {"kind": "tune", "utc": "2026-11-20T12:00:02+00:00", "samples": 4800000,
             "at_s": 2.0, "detail": {"from_hz": 100100000.0, "to_hz": 98000000.0}}
          ],
          "evidence": "real_hw",
          "blockers": []
        }
    """.trimIndent()

    @Test
    fun fullBundleKeepsAllFieldsAndVisibleStars() {
        val result = BundleParser.parse(fullJson)
        assertTrue(result.isSuccess)
        val bundle = result.getOrThrow()

        assertEquals(BundleParser.SCHEMA, bundle.schema)
        assertEquals("obs-test-1", bundle.observationId)

        assertEquals(10.0, bundle.site.latDeg!!, 1e-9)
        assertEquals(20.0, bundle.site.lonDeg!!, 1e-9)
        assertEquals(100.0, bundle.site.altM!!, 1e-9)
        assertEquals("gnss", bundle.site.source)

        assertEquals("2026-11-20T12:00:00+00:00", bundle.time.utc)
        assertEquals("system", bundle.time.source)
        assertEquals("本机时钟（未授时）", bundle.time.precision)

        assertEquals(2, bundle.sky.stars.size)
        assertEquals("Vega", bundle.sky.stars[0].name)
        assertEquals(279.23, bundle.sky.stars[0].raDeg!!, 1e-9)
        assertEquals(38.78, bundle.sky.stars[0].decDeg!!, 1e-9)
        assertEquals(66.5, bundle.sky.stars[0].altDeg!!, 1e-9)
        assertEquals(78.0, bundle.sky.stars[0].azDeg!!, 1e-9)
        assertEquals(true, bundle.sky.stars[0].visible)
        assertEquals(false, bundle.sky.stars[1].visible)
        assertEquals(2, bundle.sky.totalStars)
        assertFalse(bundle.sky.truncated)
        assertEquals("hyg", bundle.sky.source)
        assertEquals("geometric", bundle.sky.altazSource)
        assertEquals(false, bundle.sky.altazRefraction)
        assertEquals(1, bundle.sky.passes.size)

        // visibleStars() 只留 visible=true，不把「未知」当可见
        assertEquals(listOf("Vega"), bundle.visibleStars().map { it.name })

        assertEquals(100100000.0, bundle.spectrum.centerFreqHz!!, 1e-6)
        assertEquals(2400000.0, bundle.spectrum.sampleRateHz!!, 1e-6)
        assertEquals(3.5, bundle.spectrum.durationS!!, 1e-9)
        assertEquals(1, bundle.spectrum.frames.size)
        assertEquals(0.5, bundle.spectrum.frames[0].atS, 1e-9)
        assertEquals(listOf(-10.0, -20.5), bundle.spectrum.frames[0].db)

        assertEquals(1, bundle.audio.size)
        assertEquals("rec.wav", bundle.audio[0]["path"])
        assertEquals("FM 录音", bundle.audio[0]["label"])

        assertEquals(2, bundle.timeline.size)
        assertEquals("record_start", bundle.timeline[0].kind)
        assertEquals("tune", bundle.timeline[1].kind)
        assertEquals(4800000L, bundle.timeline[1].samples)
        assertEquals(
            98000000.0,
            (bundle.timeline[1].detail["to_hz"] as Number).toDouble(),
            1e-6,
        )

        assertEquals("real_hw", bundle.evidence)
        assertTrue(bundle.blockers.isEmpty())
    }

    @Test
    fun spectrumTotalSamplesParsedOrNullWhenMissing() {
        val withTotal = """
            {
              "schema": "mbdsdr.spatial/1",
              "observation_id": "obs-total",
              "spectrum": {"frames": [], "total_samples": 4800}
            }
        """.trimIndent()
        assertEquals(4800L, BundleParser.parse(withTotal).getOrThrow().spectrum.totalSamples)

        val withoutTotal = """
            {
              "schema": "mbdsdr.spatial/1",
              "observation_id": "obs-nototal",
              "spectrum": {"frames": []}
            }
        """.trimIndent()
        assertNull(BundleParser.parse(withoutTotal).getOrThrow().spectrum.totalSamples)
    }

    @Test
    fun unknownSchemaFailsWithHumanMessage() {
        val json = """{"schema": "mbdsdr.spatial/2", "observation_id": "obs-x"}"""
        val result = BundleParser.parse(json)

        assertTrue(result.isFailure)
        val message = result.exceptionOrNull()!!.message!!
        assertTrue(message, message.contains("不支持的 schema"))
        assertTrue(message, message.contains("mbdsdr.spatial/2"))
    }

    @Test
    fun brokenJsonFails() {
        val result = BundleParser.parse("""{"schema": "mbdsdr.spatial/1", "site": {""")

        assertTrue(result.isFailure)
        val message = result.exceptionOrNull()!!.message!!
        assertTrue(message, message.contains("解析失败"))
        assertTrue(message, message.contains("JsonSyntaxException"))
    }

    @Test
    fun missingSiteNumbersStayNullNotZero() {
        val json = """
            {
              "schema": "mbdsdr.spatial/1",
              "observation_id": "obs-nosite",
              "site": {"source": "none"},
              "time": {},
              "sky": {"stars": [], "passes": [], "total_stars": 0, "truncated": false},
              "spectrum": {"frames": []},
              "audio": [],
              "timeline": [],
              "blockers": ["没有可用站址（source=none）"]
            }
        """.trimIndent()

        val bundle = BundleParser.parse(json).getOrThrow()

        assertNull(bundle.site.latDeg)
        assertNull(bundle.site.lonDeg)
        assertNull(bundle.site.altM)
        assertEquals("none", bundle.site.source)
        assertNull(bundle.time.utc)
        assertEquals(1, bundle.blockers.size)
    }

    @Test
    fun missingAltAzKeepsNullAndUnavailableSource() {
        val json = """
            {
              "schema": "mbdsdr.spatial/1",
              "observation_id": "obs-noaltaz",
              "sky": {
                "stars": [{"name": "A", "ra_deg": 10.0, "dec_deg": 20.0, "mag": 1.0}],
                "passes": [],
                "total_stars": 1,
                "truncated": false,
                "source": "hyg",
                "altaz_source": "unavailable",
                "altaz_refraction": null
              },
              "spectrum": {"frames": []},
              "timeline": []
            }
        """.trimIndent()

        val bundle = BundleParser.parse(json).getOrThrow()

        assertEquals("unavailable", bundle.sky.altazSource)
        assertEquals(1, bundle.sky.stars.size)
        assertNull(bundle.sky.stars[0].altDeg)
        assertNull(bundle.sky.stars[0].azDeg)
        assertNull(bundle.sky.stars[0].visible)
        assertTrue(bundle.visibleStars().isEmpty())
        // 缺失的顶层对象/集合回落成空态，不编造坐标
        assertNull(bundle.site.latDeg)
        assertEquals(0, bundle.audio.size)
        assertEquals(0, bundle.timeline.size)
        assertEquals(0, bundle.blockers.size)
    }

    @Test
    fun eventAtPicksLastEventBeforeTimeAndSkipsNull() {
        val json = """
            {
              "schema": "mbdsdr.spatial/1",
              "observation_id": "obs-timeline",
              "timeline": [
                {"kind": "record_start", "samples": 0, "at_s": 0.0, "detail": {}},
                {"kind": "tune", "samples": 4800000, "at_s": 2.0, "detail": {}},
                {"kind": "no_time", "samples": 9600000, "detail": {}}
              ]
            }
        """.trimIndent()

        val bundle = BundleParser.parse(json).getOrThrow()

        assertNull(bundle.timeline[2].atS)
        assertEquals("tune", bundle.eventAt(2.6)?.kind)
        assertEquals("record_start", bundle.eventAt(0.0)?.kind)
        assertNull(bundle.eventAt(-1.0))
    }
}
