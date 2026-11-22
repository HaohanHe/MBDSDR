package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.BundleParser
import com.mbdsdr.spatial.data.SkyFormat
import com.mbdsdr.spatial.data.StationFormat
import com.mbdsdr.spatial.data.TimelineFormat
import com.mbdsdr.spatial.data.visibleStars
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 跨语言共享夹具：`bundle_unknown_state.json` 由 Python 导出 CLI 真实生成，
 * `tests/test_spatial_consistency.py` 会复算并逐键比对。这里验证头显读同一份
 * 字节时的未知/缺失状态：不补坐标、不猜时刻、不丢事件、不改写 kind。
 */
class UnknownStateFixtureTest {

    private fun fixtureJson(): String {
        val stream = javaClass.classLoader!!
            .getResourceAsStream("bundle_unknown_state.json")
        assertNotNull("缺少测试资源 bundle_unknown_state.json", stream)
        return stream!!.bufferedReader().use { it.readText() }
    }

    @Test
    fun unknownSiteAndTimeKeepHonestEmptyState() {
        val bundle = BundleParser.parse(fixtureJson()).getOrThrow()

        assertEquals("obs-spatial-unknown", bundle.observationId)
        assertEquals("unverified", bundle.evidence)
        assertNull(bundle.site.latDeg)
        assertNull(bundle.site.lonDeg)
        assertNull(bundle.site.altM)
        assertEquals("none", bundle.site.source)
        assertNull(bundle.time.utc)
        assertNull(bundle.spectrum.totalSamples)
        assertEquals("unavailable", bundle.sky.altazSource)
        assertTrue(bundle.visibleStars().isEmpty())

        assertFalse(StationFormat.hasKnownSite(bundle))
        val lines = StationFormat.lines(bundle)
        assertTrue(lines.site, lines.site.contains("不知道你在哪"))
        assertTrue(lines.site, lines.site.contains("来源：未知"))
        assertFalse(lines.site, lines.site.contains("none"))
        assertFalse(lines.site, lines.site.contains("0.0000"))
        assertEquals("—", lines.time)
        assertEquals("unverified", lines.evidence)
        assertTrue(lines.blockers.contains("没有可用站址（source=none）"))
        assertTrue(lines.blockers.contains("没有观测时刻，无法换算 alt/az"))

        assertEquals("不知道你在哪，无法把星放到头顶", SkyFormat.emptyHint(bundle))
    }

    @Test
    fun timelineKeepsUnknownKindAndMissingSampleAsUnknown() {
        val bundle = BundleParser.parse(fixtureJson()).getOrThrow()

        val rows = TimelineFormat.rows(
            events = bundle.timeline,
            totalSamples = bundle.spectrum.totalSamples,
            sampleRateHz = bundle.spectrum.sampleRateHz,
        )

        assertEquals(2, rows.size)
        // 未知 kind 原样显示，不映射成任何已知中文名。
        assertEquals("weird_kind", rows[0].kind)
        assertEquals("weird_kind", rows[0].label)
        assertEquals("—", rows[0].atText)
        assertEquals("—", rows[0].sampleText)
        assertFalse(rows[0].outOfRange)

        assertEquals("调谐", rows[1].label)
        assertEquals("1.00 s", rows[1].atText)
        assertEquals("2,400,000", rows[1].sampleText)
        assertFalse(rows[1].outOfRange)
    }
}
