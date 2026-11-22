package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.ObsTime
import com.mbdsdr.spatial.data.Site
import com.mbdsdr.spatial.data.Sky
import com.mbdsdr.spatial.data.SkyFormat
import com.mbdsdr.spatial.data.SpatialBundle
import com.mbdsdr.spatial.data.Spectrum
import com.mbdsdr.spatial.data.Star
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SkyFormatTest {

    private fun star(visible: Boolean? = true) = Star(
        name = "Sirius",
        raDeg = 101.287,
        decDeg = -16.716,
        mag = -1.46,
        altDeg = 30.0,
        azDeg = 120.0,
        visible = visible,
    )

    private fun bundle(
        stars: List<Star> = listOf(star()),
        site: Site = Site(latDeg = 10.0, lonDeg = 20.0, altM = null, source = "gnss"),
        utc: String? = "2026-11-20T12:00:00Z",
    ) = SpatialBundle(
        schema = "mbdsdr.spatial/1",
        observationId = "obs-test",
        site = site,
        time = ObsTime(utc = utc, source = "gnss", precision = "s"),
        sky = Sky(
            stars = stars,
            passes = emptyList(),
            totalStars = stars.size,
            truncated = false,
            source = null,
            altazSource = null,
            altazRefraction = null,
        ),
        spectrum = Spectrum(null, null, emptyList(), null, null, null),
        audio = emptyList(),
        timeline = emptyList(),
        evidence = null,
        blockers = emptyList(),
    )

    @Test
    fun nullBundleNeedsNoHint() {
        assertNull(SkyFormat.emptyHint(null))
    }

    @Test
    fun emptyStarTableSaysStarTableMissing() {
        assertEquals(
            "包里没有星表（导出时可能跳过了星星）",
            SkyFormat.emptyHint(bundle(stars = emptyList())),
        )
    }

    @Test
    fun missingSiteSaysCannotPlaceStars() {
        assertEquals(
            "不知道你在哪，无法把星放到头顶",
            SkyFormat.emptyHint(
                bundle(site = Site(latDeg = null, lonDeg = null, altM = null, source = "gnss")),
            ),
        )
    }

    @Test
    fun missingTimeSaysCannotPlaceStars() {
        assertEquals(
            "不知道观测时刻，无法把星放到头顶",
            SkyFormat.emptyHint(bundle(utc = null)),
        )
    }

    @Test
    fun noVisibleStarsSaysAllBelowHorizon() {
        assertEquals(
            "这次没有可见的星（都在地平线下）",
            SkyFormat.emptyHint(bundle(stars = listOf(star(visible = false)))),
        )
    }

    @Test
    fun visibleStarNeedsNoHint() {
        assertNull(SkyFormat.emptyHint(bundle()))
    }

    @Test
    fun unknownSourceWithCoordinatesStillSaysUnknownSite() {
        assertEquals(
            "不知道你在哪，无法把星放到头顶",
            SkyFormat.emptyHint(
                bundle(site = Site(latDeg = 10.0, lonDeg = 20.0, altM = null, source = "none")),
            ),
        )
    }
}
