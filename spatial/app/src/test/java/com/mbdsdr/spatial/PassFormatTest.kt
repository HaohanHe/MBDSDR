package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.PassFormat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PassFormatTest {

    @Test
    fun normalPassesFormatNameStartDurationAltitude() {
        val rows = PassFormat.rows(
            listOf(
                mapOf(
                    "norad_id" to 25544,
                    "name" to "ISS",
                    "start_utc" to "2026-11-20T16:19:32Z",
                    "duration_s" to 321.4,
                    "max_alt_deg" to 47.25,
                    "suspect" to false,
                ),
                mapOf(
                    "norad_id" to 33591,
                    "name" to "NOAA 19",
                    "start_utc" to "2026-11-21T03:05:09Z",
                    "duration_s" to 540.0,
                    "max_alt_deg" to 12.0,
                    "suspect" to false,
                ),
            )
        )

        assertEquals(2, rows.size)
        assertEquals("ISS", rows[0].name)
        assertEquals("11-20 16:19", rows[0].startText)
        assertEquals("321 s", rows[0].durText)
        assertEquals("47.3°", rows[0].altText)
        assertFalse(rows[0].suspect)
        assertNull(rows[0].suspectText)

        assertEquals("NOAA 19", rows[1].name)
        assertEquals("11-21 03:05", rows[1].startText)
        assertEquals("540 s", rows[1].durText)
        assertEquals("12.0°", rows[1].altText)
        assertNull(rows[1].suspectText)
    }

    @Test
    fun blankNameFallsBackToNoradThenUnnamed() {
        val rows = PassFormat.rows(
            listOf(
                mapOf("name" to "  ", "norad_id" to 25544.0),
                mapOf("name" to null, "norad_id" to null),
            )
        )

        assertEquals("NORAD 25544", rows[0].name)
        assertEquals("未命名目标", rows[1].name)
    }

    @Test
    fun longDurationUsesHoursAndMissingDurationUsesDash() {
        val rows = PassFormat.rows(
            listOf(
                mapOf("duration_s" to 20235.2),
                mapOf("duration_s" to null),
                mapOf("duration_s" to "3600"),
            )
        )

        assertEquals("5.6 h", rows[0].durText)
        assertEquals("—", rows[1].durText)
        assertEquals("—", rows[2].durText)
    }

    @Test
    fun suspectReasonsAreJoinedUnderSinglePrefix() {
        val rows = PassFormat.rows(
            listOf(
                mapOf(
                    "suspect" to true,
                    "suspect_reasons" to listOf("历时异常（>5 h）", "TLE 过旧（73.1 天）"),
                )
            )
        )

        assertTrue(rows[0].suspect)
        val text = rows[0].suspectText!!
        assertTrue(text.startsWith("数据可疑："))
        assertTrue(text.contains("历时异常（>5 h）"))
        assertTrue(text.contains("TLE 过旧（73.1 天）"))
    }

    @Test
    fun suspectWithoutReasonsSaysReasonMissing() {
        val rows = PassFormat.rows(
            listOf(
                mapOf("suspect" to true),
                mapOf("suspect" to true, "suspect_reasons" to emptyList<String>()),
                mapOf("suspect" to false, "suspect_reasons" to listOf("不应显示")),
                mapOf("suspect" to "true"),
            )
        )

        assertEquals("数据可疑：原因未给出", rows[0].suspectText)
        assertEquals("数据可疑：原因未给出", rows[1].suspectText)
        assertFalse(rows[2].suspect)
        assertNull(rows[2].suspectText)
        assertFalse(rows[3].suspect)
        assertNull(rows[3].suspectText)
    }

    @Test
    fun emptyInputAndBrokenStartKeepHonestPlaceholders() {
        assertTrue(PassFormat.rows(emptyList()).isEmpty())

        val rows = PassFormat.rows(
            listOf(
                mapOf("start_utc" to "2026/11/20 16:19"),
                mapOf("start_utc" to "2026-13-40T99:99:00Z"),
                mapOf("start_utc" to null),
            )
        )

        assertEquals(listOf("—", "—", "—"), rows.map { it.startText })
    }

    @Test
    fun inputOrderIsPreserved() {
        val rows = PassFormat.rows(
            listOf(
                mapOf("name" to "A"),
                mapOf("name" to "B"),
                mapOf("name" to "C"),
            )
        )

        assertEquals(listOf("A", "B", "C"), rows.map { it.name })
    }
}
