package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.ObsTime
import com.mbdsdr.spatial.data.Site
import com.mbdsdr.spatial.data.Sky
import com.mbdsdr.spatial.data.SpatialBundle
import com.mbdsdr.spatial.data.Spectrum
import com.mbdsdr.spatial.data.SpectrumFormat
import com.mbdsdr.spatial.data.SpectrumFrame
import com.mbdsdr.spatial.data.TimelineEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SpectrumFormatTest {

    private fun frame(atS: Double, vararg db: Double) =
        SpectrumFrame(atS = atS, db = db.toList())

    private fun spectrum(
        frames: List<SpectrumFrame> = listOf(frame(0.0, -90.0), frame(1.0, -80.0)),
        centerFreqHz: Double? = 100_100_000.0,
        sampleRateHz: Double? = 2_400_000.0,
        durationS: Double? = 3.5,
        source: String? = "recorded",
    ) = Spectrum(
        centerFreqHz = centerFreqHz,
        sampleRateHz = sampleRateHz,
        frames = frames,
        durationS = durationS,
        source = source,
        totalSamples = null,
    )

    private fun bundle(
        spectrum: Spectrum = spectrum(),
        timeline: List<TimelineEvent> = emptyList(),
        utc: String? = "2026-11-19T12:00:00Z",
        blockers: List<String> = emptyList(),
    ) = SpatialBundle(
        schema = "mbdsdr.spatial/1",
        observationId = "obs-1",
        site = Site(null, null, null, null),
        time = ObsTime(utc = utc, source = "gnss", precision = "s"),
        sky = Sky(emptyList(), emptyList(), 0, false, null, null, null),
        spectrum = spectrum,
        audio = emptyList(),
        timeline = timeline,
        evidence = null,
        blockers = blockers,
    )

    private fun event(kind: String, atS: Double?, samples: Long? = null) =
        TimelineEvent(kind = kind, utc = null, samples = samples, atS = atS, detail = emptyMap())

    @Test
    fun normalBundleShowsFourMetaFields() {
        val info = SpectrumFormat.info(bundle(), elapsedS = 0.0)

        assertEquals("2026-11-19T12:00:00Z", info.recordedAt)
        assertEquals("100.1 MHz", info.centerFreq)
        assertEquals("2.4 MS/s", info.sampleRate)
        assertEquals("录制回放", info.source)
        assertEquals(3.5, info.durationS!!, 1e-9)
        assertFalse(info.tooShort)
    }

    @Test
    fun missingFieldsFallBackToDashAndUnknown() {
        val info = SpectrumFormat.info(
            bundle(
                spectrum = spectrum(centerFreqHz = null, sampleRateHz = null, source = null),
                utc = null,
            ),
            elapsedS = 0.0,
        )

        assertEquals("时间未知", info.recordedAt)
        assertEquals("—", info.centerFreq)
        assertEquals("—", info.sampleRate)
        assertEquals("来源未知", info.source)
    }

    @Test
    fun nonRecordedSourceStaysRawAndSmallRateUsesKS() {
        val info = SpectrumFormat.info(
            bundle(spectrum = spectrum(source = "synthetic", sampleRateHz = 250_000.0)),
            elapsedS = 0.0,
        )

        assertEquals("synthetic", info.source)
        assertEquals("250.0 kS/s", info.sampleRate)
    }

    @Test
    fun fewerThanTwoFramesIsTooShort() {
        assertTrue(SpectrumFormat.info(bundle(spectrum(frames = emptyList())), 0.0).tooShort)
        assertTrue(
            SpectrumFormat.info(bundle(spectrum(frames = listOf(frame(0.0)))), 0.0).tooShort,
        )
        assertFalse(
            SpectrumFormat.info(
                bundle(spectrum(frames = listOf(frame(0.0), frame(1.0)))),
                0.0,
            ).tooShort,
        )
    }

    @Test
    fun emptyFramesNeverClaimRecordedReplay() {
        // 旧包可能仍写 source="recorded"；没有帧就不能在空态旁说「录制回放」。
        val info = SpectrumFormat.info(
            bundle(spectrum = spectrum(frames = emptyList(), durationS = null, source = "recorded")),
            elapsedS = 0.0,
        )

        assertEquals("无频谱帧", info.source)
        assertFalse(info.source, info.source.contains("录制回放"))
    }

    @Test
    fun durationPrefersFieldOverLastFrame() {
        val spectrum = spectrum(
            frames = listOf(frame(0.0, -90.0), frame(9.0, -80.0)),
            durationS = 3.5,
        )

        assertEquals(3.5, SpectrumFormat.durationS(spectrum)!!, 1e-9)
    }

    @Test
    fun durationFallsBackToLastFrameWhenFieldMissing() {
        val spectrum = spectrum(
            frames = listOf(frame(0.5, -90.0), frame(2.75, -80.0)),
            durationS = null,
        )

        assertEquals(2.75, SpectrumFormat.durationS(spectrum)!!, 1e-9)
    }

    @Test
    fun durationIsUnknownWithoutFieldAndFrames() {
        assertNull(SpectrumFormat.durationS(spectrum(frames = emptyList(), durationS = null)))
        assertEquals("回放 — / — s", SpectrumFormat.progress(0.0, null))
    }

    @Test
    fun progressFormatsOneDecimalAndClampsToDuration() {
        assertEquals("回放 1.2 / 3.5 s", SpectrumFormat.progress(1.2, 3.5))
        assertEquals("回放 3.5 / 3.5 s", SpectrumFormat.progress(9.0, 3.5))
        assertEquals("回放 0.0 / 3.5 s", SpectrumFormat.progress(-1.0, 3.5))
    }

    @Test
    fun advanceMovesForwardAndStopsAtBounds() {
        assertEquals(1.1, SpectrumFormat.advance(1.0, 0.1, 2.0), 1e-9)
        assertEquals(2.0, SpectrumFormat.advance(1.95, 0.1, 2.0), 1e-9)
        assertEquals(0.0, SpectrumFormat.advance(0.0, -0.5, 2.0), 1e-9)
        assertEquals(0.0, SpectrumFormat.advance(1.0, 0.1, null), 1e-9)
        assertEquals(0.0, SpectrumFormat.advance(1.0, 0.1, 0.0), 1e-9)
    }

    @Test
    fun seekClampsToPlaybackWindow() {
        assertEquals(3.5, SpectrumFormat.seek(9.0, 3.5), 1e-9)
        assertEquals(0.0, SpectrumFormat.seek(-1.0, 3.5), 1e-9)
        assertEquals(2.6112, SpectrumFormat.seek(2.6112, 3.5), 1e-9)
    }

    @Test
    fun seekWithoutDurationStaysAtZero() {
        assertEquals(0.0, SpectrumFormat.seek(2.0, null), 1e-9)
        assertEquals(0.0, SpectrumFormat.seek(Double.NaN, 3.5), 1e-9)
        assertEquals(0.0, SpectrumFormat.seek(2.0, -1.0), 1e-9)
        assertEquals(0.0, SpectrumFormat.seek(2.0, 0.0), 1e-9)
    }

    @Test
    fun eventTextAlignsWithTimelineAtS() {
        val bundle = bundle(
            timeline = listOf(event("tune", 0.5), event("gain", 2.0)),
        )

        assertEquals("此刻无事件", SpectrumFormat.eventText(bundle, 0.2))
        assertEquals("此刻事件：调谐", SpectrumFormat.eventText(bundle, 0.5))
        assertEquals("此刻事件：调谐", SpectrumFormat.eventText(bundle, 1.0))
        assertEquals("此刻事件：增益变更", SpectrumFormat.eventText(bundle, 2.5))
    }

    @Test
    fun unknownEventKindStaysRaw() {
        val bundle = bundle(timeline = listOf(event("weird_kind", 0.0)))

        assertEquals("此刻事件：weird_kind", SpectrumFormat.eventText(bundle, 0.0))
    }

    @Test
    fun eventWithoutAtSUsesSamplesOverSampleRate() {
        val tune = event("tune", atS = null, samples = 6_266_880L)

        assertEquals(
            2.6112,
            SpectrumFormat.eventAtS(tune, sampleRateHz = 2_400_000.0)!!,
            1e-9,
        )
    }

    @Test
    fun eventWithoutAtSAndRateIsUnknown() {
        val tune = event("tune", atS = null, samples = 6_266_880L)

        assertNull(SpectrumFormat.eventAtS(tune, sampleRateHz = null))
        assertNull(SpectrumFormat.eventAtS(tune, sampleRateHz = 0.0))
    }

    @Test
    fun eventAtSPrefersValidAtSOverSamples() {
        val event = event("tune", atS = 1.25, samples = 6_266_880L)

        assertEquals(1.25, SpectrumFormat.eventAtS(event, sampleRateHz = 2_400_000.0)!!, 1e-9)
    }

    @Test
    fun realBundleTimelineAlignedBySamples() {
        // 真机 bundle：timeline 没有 at_s，只有 samples；2 帧仍正常回放。
        val spectrum = spectrum(
            frames = listOf(frame(0.8763733333333333), frame(2.62912)),
            durationS = 3.5054933333333334,
        )
        val timeline = listOf(
            event("record_start", atS = null, samples = 0L),
            event("tune", atS = null, samples = 6_266_880L),
            event("gain", atS = null, samples = 6_283_264L),
            event("tune", atS = null, samples = 7_880_704L),
            event("record_stop", atS = null, samples = 8_413_184L),
        )
        val bundle = bundle(spectrum = spectrum, timeline = timeline)

        assertFalse(SpectrumFormat.info(bundle, 0.0).tooShort)
        assertEquals(
            2.6112,
            SpectrumFormat.eventAtS(timeline[1], spectrum.sampleRateHz)!!,
            1e-9,
        )
        assertEquals("此刻事件：调谐", SpectrumFormat.eventText(bundle, 2.6112))
        assertEquals("此刻事件：停止录制", SpectrumFormat.eventText(bundle, 3.7))
    }

    @Test
    fun textNeverClaimsRealtime() {
        val normal = SpectrumFormat.info(
            bundle(timeline = listOf(event("record_start", 0.0))),
            elapsedS = 1.0,
        )
        val missing = SpectrumFormat.info(
            bundle(
                spectrum = spectrum(centerFreqHz = null, sampleRateHz = null, source = null),
                utc = null,
            ),
            elapsedS = 0.0,
        )
        val texts = with(normal) {
            listOf(recordedAt, centerFreq, sampleRate, source, progress, event)
        } + with(missing) {
            listOf(recordedAt, centerFreq, sampleRate, source, progress, event)
        }

        for (text in texts) {
            assertFalse(text, text.contains("实时"))
            assertFalse(text, text.contains("正在接收"))
        }
    }

    @Test
    fun emptyHintOnlyForLoadedBundleWithoutFrames() {
        // 未加载、以及确有帧，都不该给「没有频谱帧」的提示。
        assertNull(SpectrumFormat.emptyHint(null))
        assertNull(SpectrumFormat.emptyHint(bundle()))
        assertEquals(
            SpectrumFormat.NO_FRAMES_TITLE,
            SpectrumFormat.emptyHint(
                bundle(spectrum = spectrum(frames = emptyList(), durationS = null))
            ),
        )
    }

    @Test
    fun emptyHintQuotesSpectrumBlockerAndIgnoresUnrelatedOnes() {
        val empty = spectrum(frames = emptyList(), durationS = null)

        // 只有站址阻塞时，不能把站址原因冒充成频谱原因。
        assertEquals(
            SpectrumFormat.NO_FRAMES_TITLE,
            SpectrumFormat.emptyHint(
                bundle(spectrum = empty, blockers = listOf("没有可用站址（source=none）"))
            ),
        )
        // 包内 blocker 原样引用，不改写。
        assertEquals(
            "这段观测没有频谱帧：没有可回放的 IQ 数据",
            SpectrumFormat.emptyHint(
                bundle(
                    spectrum = empty,
                    blockers = listOf(
                        "没有可用站址（source=none）",
                        "没有可回放的 IQ 数据",
                    ),
                )
            ),
        )
        assertEquals(
            "这段观测没有频谱帧：未生成频谱帧（--no-spectrum）",
            SpectrumFormat.emptyHint(
                bundle(spectrum = empty, blockers = listOf("未生成频谱帧（--no-spectrum）"))
            ),
        )
    }
}
