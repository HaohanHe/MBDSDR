package com.mbdsdr.spatial.data

import java.util.Locale

/** 「听见」窗口一屏所需的纯文案：元信息、回放进度、当前事件。 */
data class SpectrumInfo(
    val recordedAt: String,
    val centerFreq: String,
    val sampleRate: String,
    val source: String,
    val progress: String,
    val event: String,
    val tooShort: Boolean,
    val durationS: Double?,
)

/**
 * 频谱帧 → 回放文案与推进（纯函数，不依赖 Android）。
 *
 * 只展示包内录制值：缺失写 `—` / `时间未知` / `来源未知`，不补默认频率或采样率。
 */
object SpectrumFormat {
    /** 0 帧时的空态标题；「还没有加载观测包」只在真的没加载时用。 */
    const val NO_FRAMES_TITLE = "这段观测没有频谱帧"

    /** 用来从 `blockers` 里认出与频谱有关的原因，不改写原文。 */
    private val REASON_MARKERS = listOf("频谱", "IQ", "瀑布")

    /** 元信息四要素 + 当前进度与事件；[elapsedS] 会被钳到 `0..时长`。 */
    fun info(bundle: SpatialBundle, elapsedS: Double): SpectrumInfo {
        val spectrum = bundle.spectrum
        val duration = durationS(spectrum)
        return SpectrumInfo(
            recordedAt = bundle.time.utc?.takeIf { it.isNotBlank() } ?: "时间未知",
            centerFreq = centerFreqText(spectrum.centerFreqHz),
            sampleRate = sampleRateText(spectrum.sampleRateHz),
            source = sourceText(spectrum.source, spectrum.frames.size),
            progress = progress(elapsedS, duration),
            event = eventText(bundle, elapsedS),
            tooShort = spectrum.frames.size < 2,
            durationS = duration,
        )
    }

    /**
     * 已加载但 0 帧时的原因文案；未加载或确有帧时返回 null。
     *
     * 原因只从包内 `blockers` 里挑与频谱相关的条目，原样引用、不改写；
     * 没有相关条目就只说没有频谱帧，不猜原因。站址/时刻等无关阻塞不搬进来。
     */
    fun emptyHint(bundle: SpatialBundle?): String? {
        if (bundle == null || bundle.spectrum.frames.isNotEmpty()) return null
        val reason = bundle.blockers
            .map { it.trim() }
            .firstOrNull { text ->
                text.isNotEmpty() && REASON_MARKERS.any { marker -> text.contains(marker) }
            }
        return reason?.let { "$NO_FRAMES_TITLE：$it" } ?: NO_FRAMES_TITLE
    }

    /** 回放时长：优先包内 `duration_s`，缺失时取最后一帧的 `at_s`。 */
    fun durationS(spectrum: Spectrum): Double? =
        spectrum.durationS?.takeIf { it.isFinite() && it >= 0.0 }
            ?: spectrum.frames.lastOrNull()?.atS

    /** `回放 1.2 / 3.5 s`；时长未知时两侧都写 `—`。 */
    fun progress(elapsedS: Double, durationS: Double?): String {
        val duration = durationS?.takeIf { it.isFinite() && it >= 0.0 }
            ?: return "回放 — / — s"
        val elapsed = elapsedS.takeIf { it.isFinite() } ?: 0.0
        return String.format(
            Locale.US,
            "回放 %.1f / %.1f s",
            elapsed.coerceIn(0.0, duration),
            duration,
        )
    }

    /**
     * 推进回放位置：累加 [deltaS] 后钳在 `0..durationS`。
     *
     * 时长未知或为 0 时停在 0；到末尾不循环。
     */
    fun advance(elapsedS: Double, deltaS: Double, durationS: Double?): Double {
        val duration = durationS?.takeIf { it.isFinite() && it > 0.0 } ?: return 0.0
        val elapsed = elapsedS.takeIf { it.isFinite() } ?: 0.0
        val delta = deltaS.takeIf { it.isFinite() } ?: 0.0
        return (elapsed + delta).coerceIn(0.0, duration)
    }

    /**
     * 跳到 [atS]：把目标时刻钳到 `0..durationS`。
     *
     * 时长未知或为负时停在 0，目标非有限值也按 0 处理。
     */
    fun seek(atS: Double, durationS: Double?): Double {
        val duration = durationS?.takeIf { it.isFinite() && it >= 0.0 } ?: return 0.0
        val target = atS.takeIf { it.isFinite() } ?: 0.0
        return target.coerceIn(0.0, duration)
    }

    /**
     * 事件在回放秒轴上的位置：优先用有限且 >= 0 的 `at_s`；否则用
     * `samples / sample_rate_hz` 换算（与 scripts/replay_observation.py 同一口径）；
     * 两者都不可用返回 null，不按 0 处理。
     */
    fun eventAtS(event: TimelineEvent, sampleRateHz: Double?): Double? {
        val atS = event.atS
        if (atS != null && atS.isFinite() && atS >= 0.0) return atS
        val rate = sampleRateHz?.takeIf { it.isFinite() && it > 0.0 } ?: return null
        val samples = event.samples ?: return null
        return samples.toDouble() / rate
    }

    /** 与回放秒轴同轴：取换算后 `<= elapsed` 的最后一条事件。 */
    fun eventText(bundle: SpatialBundle, elapsedS: Double): String {
        val event = eventAt(bundle, elapsedS) ?: return "此刻无事件"
        return "此刻事件：${TimelineFormat.label(event.kind)}"
    }

    /** 同 `Bundle.eventAt` 的「最后一条」语义，但秒轴包含 samples 换算。 */
    private fun eventAt(bundle: SpatialBundle, elapsedS: Double): TimelineEvent? {
        var best: TimelineEvent? = null
        var bestAt = Double.NEGATIVE_INFINITY
        for (event in bundle.timeline) {
            val at = eventAtS(event, bundle.spectrum.sampleRateHz) ?: continue
            if (at > elapsedS) continue
            if (best == null || at >= bestAt) {
                best = event
                bestAt = at
            }
        }
        return best
    }

    private fun centerFreqText(hz: Double?): String {
        val value = hz?.takeIf { it.isFinite() } ?: return "—"
        return String.format(Locale.US, "%.1f MHz", value / 1e6)
    }

    /** `2.4 MS/s` / `250.0 kS/s`；缺失或非正数写 `—`。跳转文案复用同一口径。 */
    internal fun sampleRateText(hz: Double?): String {
        val value = hz?.takeIf { it.isFinite() && it > 0.0 } ?: return "—"
        return if (value >= 1e6) {
            String.format(Locale.US, "%.1f MS/s", value / 1e6)
        } else {
            String.format(Locale.US, "%.1f kS/s", value / 1e3)
        }
    }

    /**
     * 来源文案：只有确有帧时才能说「录制回放」。
     *
     * 0 帧时无论包内 `source` 写什么（旧包可能仍是 `recorded`）都只说
     * 「无频谱帧」，避免与旁边的空态说明自相矛盾；缺失来源写「来源未知」。
     */
    private fun sourceText(source: String?, frameCount: Int): String = when {
        frameCount == 0 -> "无频谱帧"
        source.isNullOrBlank() -> "来源未知"
        source == "recorded" -> "录制回放"
        else -> source
    }
}
