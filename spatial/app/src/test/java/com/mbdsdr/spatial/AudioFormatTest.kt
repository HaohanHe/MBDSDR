package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.AudioFormat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioFormatTest {

    private fun entry(
        path: Any? = null,
        label: Any? = null,
        utc: Any? = null,
    ): Map<String, Any?> = mapOf("path" to path, "label" to label, "utc" to utc)

    @Test
    fun emptyAudioHasHonestEmptyState() {
        assertNull(AudioFormat.first(emptyList()))
        assertEquals("这次观测没有录音", AudioFormat.describe(null))
        assertEquals("没有录音", AudioFormat.buttonText(null, playing = false))
    }

    @Test
    fun normalEntryDescribesUtcAndLabel() {
        val info = AudioFormat.first(
            listOf(mapOf("path" to "/sdcard/obs/rec.wav", "label" to "FM 96.3", "utc" to "2026-11-20T12:00:00Z")),
        )

        assertEquals("/sdcard/obs/rec.wav", info?.path)
        val text = AudioFormat.describe(info)
        assertTrue(text, text.contains("2026-11-20T12:00:00Z"))
        assertTrue(text, text.contains("FM 96.3"))
        assertEquals("播放录音", AudioFormat.buttonText(info, playing = false))
        assertEquals("停止", AudioFormat.buttonText(info, playing = true))
    }

    @Test
    fun missingUtcSaysTimeUnknown() {
        val info = AudioFormat.first(listOf(entry(path = "/sdcard/obs/rec.wav", label = "AM 711", utc = null)))

        assertEquals(null, info?.utc)
        assertTrue(AudioFormat.describe(info).contains("时间未知"))
    }

    @Test
    fun nonStringLabelFallsBackToUnknownSourceButKeepsPath() {
        val info = AudioFormat.first(
            listOf(mapOf("path" to "/sdcard/obs/rec.wav", "label" to 42, "utc" to "2026-11-20T12:00:00Z")),
        )

        assertEquals("/sdcard/obs/rec.wav", info?.path)
        assertEquals("来源未知", info?.label)
        assertTrue(AudioFormat.describe(info).contains("来源未知"))
    }

    @Test
    fun blankPathIsSkippedForNextValidEntry() {
        val info = AudioFormat.first(
            listOf(
                entry(path = "", label = "空路径", utc = "2026-11-20T11:00:00Z"),
                entry(path = "/sdcard/obs/valid.wav", label = "有效", utc = "2026-11-20T12:00:00Z"),
            ),
        )

        assertEquals("/sdcard/obs/valid.wav", info?.path)
        assertEquals("有效", info?.label)
        assertEquals("2026-11-20T12:00:00Z", info?.utc)
    }
}
