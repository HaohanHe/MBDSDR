package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.TimelineEvent
import com.mbdsdr.spatial.data.TimelineFormat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TimelineFormatTest {

    private fun event(
        kind: String,
        atS: Double? = null,
        samples: Long? = null,
        detail: Map<String, Any?> = emptyMap(),
    ) = TimelineEvent(kind = kind, utc = null, samples = samples, atS = atS, detail = detail)

    @Test
    fun knownKindsMapToChineseLabels() {
        val expected = mapOf(
            "record_start" to "开始录制",
            "record_stop" to "停止录制",
            "tune" to "调谐",
            "gain" to "增益变更",
            "sample_rate" to "采样率变更",
            "decode_hit" to "解码命中",
            "decode" to "解码命中",
            "read_gap" to "读空 / 数据间断",
            "read_empty" to "读空",
            "empty_read" to "读空",
            "timeline_truncated" to "事件记录已截断",
            "read_failed" to "读取失败",
            "disconnect" to "设备断开",
            "record_error" to "录制错误",
        )
        for ((kind, label) in expected) {
            assertEquals(kind, label, TimelineFormat.label(kind))
        }
    }

    @Test
    fun blankKindIsUnknownAndUnknownKindStaysRaw() {
        assertEquals("未知事件", TimelineFormat.label(null))
        assertEquals("未知事件", TimelineFormat.label(""))
        assertEquals("weird_kind", TimelineFormat.label("weird_kind"))
    }

    @Test
    fun atTextAndSampleTextAreFormattedWithDashForMissing() {
        val rows = TimelineFormat.rows(
            listOf(
                event("tune", atS = 1.5, samples = 4_800_000L),
                event("read_gap"),
            ),
            totalSamples = null,
        )

        assertEquals("1.50 s", rows[0].atText)
        assertEquals("4,800,000", rows[0].sampleText)
        assertEquals("—", rows[1].atText)
        assertEquals("—", rows[1].sampleText)
    }

    @Test
    fun outOfRangeOnlyWhenSamplesExceedTotal() {
        val events = listOf(
            event("record_start", atS = 0.0, samples = 100L),
            event("tune", atS = 1.0, samples = 101L),
        )

        val rows = TimelineFormat.rows(events, totalSamples = 100L)
        assertFalse(rows[0].outOfRange)
        assertNull(rows[0].note)
        assertTrue(rows[1].outOfRange)
        assertEquals("超出录制长度", rows[1].note)

        // 没有总样本数时不判越界
        assertFalse(TimelineFormat.rows(events, totalSamples = null)[1].outOfRange)
        // samples 缺失时也不判越界
        assertFalse(TimelineFormat.rows(listOf(event("read_gap")), totalSamples = 0L)[0].outOfRange)
    }

    @Test
    fun eventBeyondRecordedTotalIsFlagged() {
        val rows = TimelineFormat.rows(
            listOf(event("tune", atS = 5.0, samples = 150L)),
            totalSamples = 100L,
        )

        assertTrue(rows[0].outOfRange)
        assertEquals("超出录制长度", rows[0].note)
    }

    @Test
    fun emptyTimelineGivesEmptyRows() {
        assertTrue(TimelineFormat.rows(emptyList(), totalSamples = 100L).isEmpty())
    }

    @Test
    fun indexAndInputOrderArePreserved() {
        val rows = TimelineFormat.rows(
            listOf(event("gain"), event("tune"), event("decode_hit")),
            totalSamples = null,
        )

        assertEquals(listOf(0, 1, 2), rows.map { it.index })
        assertEquals(listOf("gain", "tune", "decode_hit"), rows.map { it.kind })
        assertEquals(listOf("增益变更", "调谐", "解码命中"), rows.map { it.label })
    }

    @Test
    fun atTextPrefersAtSOverSamples() {
        val rows = TimelineFormat.rows(
            listOf(event("tune", atS = 1.25, samples = 6_266_880L)),
            totalSamples = null,
            sampleRateHz = 2_400_000.0,
        )

        assertEquals("1.25 s", rows[0].atText)
    }

    @Test
    fun atTextFallsBackToSamplesOverSampleRate() {
        // 真机 bundle：timeline 没有 at_s，只有 samples；6266880 / 2.4e6 = 2.6112 s。
        val rows = TimelineFormat.rows(
            listOf(
                event("record_start", samples = 0L),
                event("tune", samples = 6_266_880L),
            ),
            totalSamples = null,
            sampleRateHz = 2_400_000.0,
        )

        assertEquals("0.00 s", rows[0].atText)
        assertEquals("2.61 s", rows[1].atText)
    }

    @Test
    fun atTextStaysDashWithoutAtSOrSamples() {
        val rows = TimelineFormat.rows(
            listOf(event("read_gap"), event("tune", samples = 6_266_880L)),
            totalSamples = null,
            sampleRateHz = null,
        )

        assertEquals("—", rows[0].atText)
        assertEquals("—", rows[1].atText)
    }

    @Test
    fun outOfRangeFlagUnchangedWhenRateProvided() {
        val rows = TimelineFormat.rows(
            listOf(event("tune", samples = 8_413_184L)),
            totalSamples = 8_413_184L - 1,
            sampleRateHz = 2_400_000.0,
        )

        assertTrue(rows[0].outOfRange)
        assertEquals("超出录制长度", rows[0].note)
        // 越界行仍按真实换算显示时刻，不隐藏也不改成 0。
        assertEquals("3.51 s", rows[0].atText)
    }

    @Test
    fun jumpTextForTuneUsesHzToMHzWithThreeDecimals() {
        val tune = event(
            "tune",
            samples = 6_266_880L,
            detail = mapOf("from_hz" to 100_100_000.0, "to_hz" to 98_000_000.0),
        )

        assertEquals(
            "已跳到 2.61 s：调谐 100.100 → 98.000 MHz",
            TimelineFormat.jumpText(tune, sampleRateHz = 2_400_000.0),
        )
    }

    @Test
    fun jumpTextForGainUsesOneDecimalDb() {
        val gain = event(
            "gain",
            samples = 6_283_264L,
            detail = mapOf("from_db" to 30.0, "to_db" to 40.0),
        )

        assertEquals(
            "已跳到 2.62 s：增益变更 30.0 → 40.0 dB",
            TimelineFormat.jumpText(gain, sampleRateHz = 2_400_000.0),
        )
    }

    @Test
    fun jumpTextForRecordStartNeedsAllThreeDetailFields() {
        val complete = event(
            "record_start",
            samples = 0L,
            detail = mapOf(
                "center_freq_hz" to 100_100_000.0,
                "sample_rate_hz" to 2_400_000.0,
                "gain_db" to 30.0,
            ),
        )
        val incomplete = event(
            "record_start",
            samples = 0L,
            detail = mapOf("center_freq_hz" to 100_100_000.0, "sample_rate_hz" to 2_400_000.0),
        )

        assertEquals(
            "已跳到 0.00 s：开始录制 100.100 MHz · 2.4 MS/s · 30.0 dB",
            TimelineFormat.jumpText(complete, sampleRateHz = 2_400_000.0),
        )
        assertEquals(
            "已跳到 0.00 s：开始录制",
            TimelineFormat.jumpText(incomplete, sampleRateHz = 2_400_000.0),
        )
    }

    @Test
    fun jumpTextForOtherKindsOnlyUsesLabel() {
        val stop = event("record_stop", samples = 8_413_184L)
        val unknown = event("weird_kind", atS = 1.0)

        assertEquals(
            "已跳到 3.51 s：停止录制",
            TimelineFormat.jumpText(stop, sampleRateHz = 2_400_000.0),
        )
        assertEquals(
            "已跳到 1.00 s：weird_kind",
            TimelineFormat.jumpText(unknown, sampleRateHz = null),
        )
    }

    @Test
    fun jumpTextTruncatesToAvailableDetailWithoutInventingValues() {
        val onlyFrom = event(
            "tune",
            atS = 1.25,
            detail = mapOf("from_hz" to 100_100_000.0),
        )
        val noDetail = event("tune", atS = 1.25)

        assertEquals(
            "已跳到 1.25 s：调谐 100.100 MHz",
            TimelineFormat.jumpText(onlyFrom, sampleRateHz = null),
        )
        assertEquals(
            "已跳到 1.25 s：调谐",
            TimelineFormat.jumpText(noDetail, sampleRateHz = null),
        )
    }

    @Test
    fun relatedIndicesOnlyMatchSameNameInDetail() {
        val events = listOf(
            event(
                "decode_hit",
                detail = mapOf(
                    "decoder" to "adsb",
                    "first" to mapOf("callsign" to "Sirius"),
                ),
            ),
            event("tune", detail = mapOf("from_hz" to 100_100_000.0)),
            event(
                "decode_hit",
                detail = mapOf("target" to "Sirius", "count" to 3),
            ),
        )

        assertEquals(setOf(0, 2), TimelineFormat.relatedIndices(events, "Sirius"))
        // 同名不因大小写/首尾空格不同而漏掉；仍必须逐字同名，不做模糊匹配。
        assertEquals(setOf(0, 2), TimelineFormat.relatedIndices(events, "  sirius "))
        assertTrue(TimelineFormat.relatedIndices(events, "Siri").isEmpty())
    }

    @Test
    fun relatedIndicesAreEmptyWhenNothingMatchesOrTargetBlank() {
        val events = listOf(
            event(
                "decode_hit",
                detail = mapOf("first" to mapOf("callsign" to "Vega")),
            ),
            event("read_gap", detail = mapOf("count" to 2)),
            event(
                "tune",
                detail = mapOf("from_hz" to 100_100_000.0, "to_hz" to 98_000_000.0),
            ),
        )

        assertTrue(TimelineFormat.relatedIndices(events, "Sirius").isEmpty())
        assertTrue(TimelineFormat.relatedIndices(events, null).isEmpty())
        assertTrue(TimelineFormat.relatedIndices(events, "   ").isEmpty())
        assertTrue(TimelineFormat.relatedIndices(emptyList(), "Sirius").isEmpty())
    }

    @Test
    fun relatedIndicesSearchNestedListsAndIgnoreNonStrings() {
        val events = listOf(
            event("decode_hit", detail = mapOf("names" to listOf("Vega", "Sirius"))),
            event("gain", detail = mapOf("from_db" to 30.0, "name_flag" to true)),
        )

        assertEquals(setOf(0), TimelineFormat.relatedIndices(events, "Sirius"))
    }

    @Test
    fun jumpTextIsNullWhenMomentCannotBeResolved() {
        assertNull(
            TimelineFormat.jumpText(event("read_gap"), sampleRateHz = 2_400_000.0),
        )
        assertNull(
            TimelineFormat.jumpText(
                event("tune", samples = 6_266_880L),
                sampleRateHz = null,
            ),
        )
    }

    @Test
    fun jumpDecisionJumpsWhenMomentAndDurationAreKnown() {
        val tune = event(
            "tune",
            samples = 6_266_880L,
            detail = mapOf("from_hz" to 100_100_000.0, "to_hz" to 98_000_000.0),
        )

        val decision = TimelineFormat.jumpDecision(
            event = tune,
            totalSamples = 8_413_184L,
            sampleRateHz = 2_400_000.0,
            durationS = 3.5,
        )

        assertTrue(decision.canJump)
        assertEquals(2.6112, decision.atS!!, 1e-9)
        assertEquals("已跳到 2.61 s：调谐 100.100 → 98.000 MHz", decision.note)
    }

    @Test
    fun jumpDecisionOutOfRangeDoesNotJump() {
        val decision = TimelineFormat.jumpDecision(
            event = event("tune", atS = 5.0, samples = 150L),
            totalSamples = 100L,
            sampleRateHz = 2_400_000.0,
            durationS = 3.5,
        )

        assertFalse(decision.canJump)
        assertNull(decision.atS)
        assertEquals("超出录制长度，未跳转", decision.note)
    }

    @Test
    fun jumpDecisionNamesEveryMissingField() {
        val noMoment = TimelineFormat.jumpDecision(
            event = event("read_gap"),
            totalSamples = null,
            sampleRateHz = 2_400_000.0,
            durationS = 3.5,
        )
        val negativeAtS = TimelineFormat.jumpDecision(
            event = event("tune", atS = -1.0),
            totalSamples = null,
            sampleRateHz = 2_400_000.0,
            durationS = 3.5,
        )

        assertFalse(noMoment.canJump)
        assertEquals("无法定位时刻：缺 at_s、samples", noMoment.note)
        assertEquals("无法定位时刻：缺 at_s、samples", negativeAtS.note)
    }

    @Test
    fun jumpDecisionBlamesMissingSampleRateNotMissingSamples() {
        val decision = TimelineFormat.jumpDecision(
            event = event("tune", samples = 6_266_880L),
            totalSamples = null,
            sampleRateHz = null,
            durationS = 3.5,
        )

        assertFalse(decision.canJump)
        assertNull(decision.atS)
        // samples 在，真正缺的是 at_s 与采样率；不能再说「缺 samples」。
        assertEquals("无法定位时刻：缺 at_s、sample_rate_hz", decision.note)
        assertFalse(decision.note.contains("samples"))
    }

    @Test
    fun jumpDecisionReportsMissingPlaybackWindow() {
        val decision = TimelineFormat.jumpDecision(
            event = event("tune", atS = 1.25),
            totalSamples = null,
            sampleRateHz = null,
            durationS = null,
        )

        assertFalse(decision.canJump)
        assertEquals(1.25, decision.atS!!, 1e-9)
        assertEquals("无法跳转：这份包没有回放时长", decision.note)
    }
}
