package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.ObsTime
import com.mbdsdr.spatial.data.Site
import com.mbdsdr.spatial.data.Sky
import com.mbdsdr.spatial.data.SpatialBundle
import com.mbdsdr.spatial.data.Spectrum
import com.mbdsdr.spatial.data.StationFormat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StationFormatTest {

    private fun bundle(
        site: Site = Site(null, null, null, null),
        time: ObsTime = ObsTime(null, null, null),
        evidence: String? = null,
        blockers: List<String> = emptyList(),
    ) = SpatialBundle(
        schema = "mbdsdr.spatial/1",
        observationId = "obs-test",
        site = site,
        time = time,
        sky = Sky(emptyList(), emptyList(), 0, false, null, null, null),
        spectrum = Spectrum(null, null, emptyList(), null, null, null),
        audio = emptyList(),
        timeline = emptyList(),
        evidence = evidence,
        blockers = blockers,
    )

    @Test
    fun nullBundleIsHonestEmptyState() {
        val lines = StationFormat.lines(null)

        assertEquals("还没有加载观测包", lines.site)
        assertEquals("—", lines.time)
        assertEquals("—", lines.evidence)
        assertTrue(lines.blockers.isEmpty())
        assertFalse(lines.hasSite)
    }

    @Test
    fun unknownSourceNeverRendersZeroCoordinates() {
        val lines = StationFormat.lines(
            bundle(site = Site(latDeg = null, lonDeg = null, altM = null, source = "none")),
        )

        assertFalse(lines.hasSite)
        assertTrue(lines.site, lines.site.contains("不知道你在哪"))
        assertTrue(lines.site, lines.site.contains("来源：未知"))
        assertFalse(lines.site, lines.site.contains("none"))
        assertFalse(lines.site, lines.site.contains("0.0000"))
    }

    @Test
    fun unrecognizedSourceKeepsItsRealName() {
        val lines = StationFormat.lines(
            bundle(site = Site(latDeg = null, lonDeg = null, altM = null, source = "gps")),
        )

        assertTrue(lines.site, lines.site.contains("来源：gps"))
        assertFalse(lines.site, lines.site.contains("0.0000"))
    }

    @Test
    fun knownSourceWithoutCoordinatesIsStillUnknown() {
        val lines = StationFormat.lines(
            bundle(site = Site(latDeg = null, lonDeg = 20.0, altM = null, source = "gnss")),
        )

        assertFalse(lines.hasSite)
        assertTrue(lines.site, lines.site.contains("不知道你在哪"))
        assertFalse(lines.site, lines.site.contains("0.0000"))
    }

    @Test
    fun knownSiteFormatsFourDecimalsWithAltitudeAndSource() {
        val lines = StationFormat.lines(
            bundle(site = Site(latDeg = 10.0, lonDeg = 20.0, altM = 100.0, source = "gnss")),
        )

        assertTrue(lines.hasSite)
        assertEquals("10.0000, 20.0000 · 100 m（GNSS）", lines.site)
    }

    @Test
    fun knownSiteWithoutAltitudeOmitsAltitude() {
        val lines = StationFormat.lines(
            bundle(site = Site(latDeg = 10.0, lonDeg = 20.0, altM = null, source = "manual")),
        )

        assertTrue(lines.hasSite)
        assertEquals("10.0000, 20.0000（手填）", lines.site)
    }

    @Test
    fun missingTimeIsDashAndPartialTimeKeepsMissingSlot() {
        assertEquals("—", StationFormat.lines(bundle()).time)

        val partial = StationFormat.lines(
            bundle(time = ObsTime(utc = "2026-11-20T12:00:00+00:00", source = "system", precision = null)),
        )
        assertTrue(partial.time, partial.time.startsWith("2026-11-20T12:00:00+00:00"))
        assertTrue(partial.time, partial.time.endsWith("—"))
    }

    @Test
    fun evidenceAndBlockersPassThrough() {
        val blockers = listOf("没有可用站址（source=none）", "时间未授时")
        val lines = StationFormat.lines(
            bundle(evidence = "real_hw", blockers = blockers),
        )

        assertEquals("real_hw", lines.evidence)
        assertEquals(blockers, lines.blockers)
    }
}
