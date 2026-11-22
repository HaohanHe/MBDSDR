package com.mbdsdr.spatial.data

import java.util.Locale

/** 时间轴上的一行展示数据（纯数据，便于单测）。 */
data class EventRow(
    val index: Int,
    val kind: String,
    val label: String,
    val atText: String,
    val sampleText: String,
    val outOfRange: Boolean,
    val note: String?,
)

/**
 * 点选事件后的跳转决定：能不能跳、跳到哪、提示什么。
 *
 * 只做判断与文案，不触碰跨窗状态；文案缺什么就说什么，不把「缺采样率」
 * 误报成「缺 samples」。
 */
data class JumpDecision(
    val atS: Double?,
    val note: String,
    val canJump: Boolean,
)

/**
 * 时间轴事件 → 回看列表行。
 *
 * 中文名与桌面 `desktop/replay_timeline.py::_EVENT_LABELS` 逐字一致（跨语言
 * 钉扎见 `tests/test_spatial_consistency.py`），不另起一套叫法；不认识的事件
 * 类型原样显示 kind，不编造解释；缺 at_s / samples 就写 `—`。
 */
object TimelineFormat {
    private val LABELS = mapOf(
        "record_start" to "开始录制",
        "record_stop" to "停止录制",
        "tune" to "调谐",
        "gain" to "增益变更",
        "sample_rate" to "采样率变更",
        "decode_hit" to "解码命中",
        "decode" to "解码命中",
        "read_gap" to "读空 / 数据间断",
        "sample_loss" to "录制短录 / 样本丢失",
        "read_empty" to "读空",
        "empty_read" to "读空",
        "timeline_truncated" to "事件记录已截断",
        "read_failed" to "读取失败",
        "disconnect" to "设备断开",
        "record_error" to "录制错误",
    )

    fun label(kind: String?): String {
        if (kind.isNullOrBlank()) return "未知事件"
        return LABELS[kind] ?: kind
    }

    /**
     * @param totalSamples 录制总样本数；没有这个字段时传 null，此时不判越界。
     * @param sampleRateHz 采样率；用来把只有 `samples` 的事件换算成秒，缺失时时间列写 `—`。
     */
    fun rows(
        events: List<TimelineEvent>,
        totalSamples: Long?,
        sampleRateHz: Double? = null,
    ): List<EventRow> =
        events.mapIndexed { index, event ->
            val samples = event.samples
            val outOfRange = totalSamples != null && samples != null && samples > totalSamples
            // 换算只有一个真源：与「听见」窗口共用 SpectrumFormat.eventAtS。
            val at = SpectrumFormat.eventAtS(event, sampleRateHz)
            EventRow(
                index = index,
                kind = event.kind,
                label = label(event.kind),
                atText = at
                    ?.let { String.format(Locale.US, "%.2f s", it) }
                    ?: "—",
                sampleText = samples
                    ?.let { String.format(Locale.US, "%,d", it) }
                    ?: "—",
                outOfRange = outOfRange,
                note = if (outOfRange) "超出录制长度" else null,
            )
        }

    /**
     * 跳转提示文案；换算不出时刻返回 null（不按 0 处理）。
     *
     * 例：`已跳到 2.61 s：调谐 100.100 → 98.000 MHz`。
     * detail 缺字段时只保留已有的部分，不编造数值。
     */
    fun jumpText(event: TimelineEvent, sampleRateHz: Double?): String? {
        val at = SpectrumFormat.eventAtS(event, sampleRateHz) ?: return null
        val detail = detailText(event.kind, event.detail)
        return String.format(
            Locale.US,
            "已跳到 %.2f s：%s",
            at,
            label(event.kind) + (detail?.let { " $it" } ?: ""),
        )
    }

    /**
     * 点选事件的跳转决定（回看窗口只执行结果，不再自己拼分支）。
     *
     * 顺序：越界 → 能否定位时刻 → 包里有没有回放时长。时刻定位失败时按
     * 实际缺失项说明（`at_s` / `samples` / `sample_rate_hz`），不笼统说
     * 「缺 at_s 与 samples」。
     */
    fun jumpDecision(
        event: TimelineEvent,
        totalSamples: Long?,
        sampleRateHz: Double?,
        durationS: Double?,
    ): JumpDecision {
        if (totalSamples != null && event.samples != null && event.samples > totalSamples) {
            return JumpDecision(atS = null, note = "超出录制长度，未跳转", canJump = false)
        }
        val at = SpectrumFormat.eventAtS(event, sampleRateHz)
            ?: return JumpDecision(
                atS = null,
                note = "无法定位时刻：缺 " + missingFields(event, sampleRateHz),
                canJump = false,
            )
        val duration = durationS?.takeIf { it.isFinite() && it >= 0.0 }
            ?: return JumpDecision(at, "无法跳转：这份包没有回放时长", canJump = false)
        val note = jumpText(event, sampleRateHz)
            ?: return JumpDecision(at, "无法定位时刻：缺 at_s、samples", canJump = false)
        return JumpDecision(at, note, canJump = true)
    }

    private fun missingFields(event: TimelineEvent, sampleRateHz: Double?): String {
        val missing = mutableListOf<String>()
        val at = event.atS
        if (at == null || !at.isFinite() || at < 0.0) missing += "at_s"
        if (event.samples == null) missing += "samples"
        val rate = sampleRateHz
        if (event.samples != null && (rate == null || !rate.isFinite() || rate <= 0.0)) {
            missing += "sample_rate_hz"
        }
        return missing.joinToString("、")
    }

    /**
     * 与 [target] 同名的事件下标。
     *
     * 只在事件 `detail`（含嵌套 map / list）里出现**同名**字符串时返回；
     * 目标为空、没有同名、或 detail 只有其它字段时返回空表——不猜关联，
     * 也不用位置/时间把星点与频谱事件硬绑在一起。
     */
    fun relatedIndices(events: List<TimelineEvent>, target: String?): Set<Int> {
        val needle = target?.trim()
        if (needle.isNullOrEmpty()) return emptySet()
        return events.mapIndexedNotNull { index, event ->
            if (containsText(event.detail, needle)) index else null
        }.toSet()
    }

    private fun containsText(raw: Any?, needle: String): Boolean = when (raw) {
        is String -> raw.trim().equals(needle, ignoreCase = true)
        is Map<*, *> -> raw.values.any { containsText(it, needle) }
        is Iterable<*> -> raw.any { containsText(it, needle) }
        else -> false
    }

    private fun detailText(kind: String, detail: Map<String, Any?>): String? = when (kind) {
        "tune" -> {
            val from = freqText(detail["from_hz"])
            val to = freqText(detail["to_hz"])
            when {
                from != null && to != null -> "$from → $to MHz"
                from != null -> "$from MHz"
                to != null -> "$to MHz"
                else -> null
            }
        }
        "gain" -> {
            val from = dbText(detail["from_db"])
            val to = dbText(detail["to_db"])
            when {
                from != null && to != null -> "$from → $to dB"
                from != null -> "$from dB"
                to != null -> "$to dB"
                else -> null
            }
        }
        // 三个录制参数齐全才拼一句；缺任一就不补。
        "record_start" -> {
            val freq = freqText(detail["center_freq_hz"])
            val rate = value(detail["sample_rate_hz"])
                ?.let { SpectrumFormat.sampleRateText(it) }
                ?.takeIf { it != "—" }
            val gain = dbText(detail["gain_db"])
            if (freq != null && rate != null && gain != null) {
                "$freq MHz · $rate · $gain dB"
            } else {
                null
            }
        }
        else -> null
    }

    private fun value(raw: Any?): Double? =
        (raw as? Number)?.toDouble()?.takeIf { it.isFinite() }

    private fun freqText(raw: Any?): String? =
        value(raw)?.let { String.format(Locale.US, "%.3f", it / 1e6) }

    private fun dbText(raw: Any?): String? =
        value(raw)?.let { String.format(Locale.US, "%.1f", it) }
}
