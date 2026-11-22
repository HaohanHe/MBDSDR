package com.mbdsdr.spatial.data

import java.util.Locale

/** 过境列表的一行展示数据（纯数据，便于单测）。 */
data class PassRow(
    val name: String,
    val startText: String,
    val durText: String,
    val altText: String,
    val suspect: Boolean,
    val suspectText: String?,
)

/**
 * bundle 的 `sky.passes` → 过境列表行。
 *
 * 字段缺失或类型不对时写 `—` / 占位名，不猜补数值；suspect 只有显式
 * `true` 才算，原因按 bundle 给的原样拼接。
 */
object PassFormat {
    private const val DASH = "—"

    fun rows(passes: List<Map<String, Any?>>): List<PassRow> = passes.map { pass ->
        val suspect = pass["suspect"] == true
        PassRow(
            name = nameText(pass),
            startText = startText(pass["start_utc"]),
            durText = durationText(pass["duration_s"]),
            altText = altText(pass["max_alt_deg"]),
            suspect = suspect,
            suspectText = if (suspect) suspectText(pass["suspect_reasons"]) else null,
        )
    }

    private fun nameText(pass: Map<String, Any?>): String {
        val name = (pass["name"] as? String)?.takeIf { it.isNotBlank() }
        if (name != null) return name
        val id = noradIdText(pass["norad_id"]) ?: return "未命名目标"
        return "NORAD $id"
    }

    private fun noradIdText(value: Any?): String? = when (value) {
        is String -> value.takeIf { it.isNotBlank() }
        is Number -> {
            val number = value.toDouble()
            when {
                !number.isFinite() -> null
                number == Math.floor(number) -> String.format(Locale.US, "%.0f", number)
                else -> number.toString()
            }
        }
        else -> null
    }

    /** `2026-11-20T16:19:32Z` → `11-20 16:19`；缺失或格式不对 → `—`。 */
    private fun startText(value: Any?): String {
        val text = value as? String ?: return DASH
        if (text.length < 16) return DASH
        if (text[4] != '-' || text[7] != '-' || text[10] != 'T' || text[13] != ':') return DASH
        val year = text.substring(0, 4).toIntOrNull() ?: return DASH
        val month = text.substring(5, 7).toIntOrNull() ?: return DASH
        val day = text.substring(8, 10).toIntOrNull() ?: return DASH
        val hour = text.substring(11, 13).toIntOrNull() ?: return DASH
        val minute = text.substring(14, 16).toIntOrNull() ?: return DASH
        if (year <= 0 || month !in 1..12 || day !in 1..31 ||
            hour !in 0..23 || minute !in 0..59
        ) {
            return DASH
        }
        return String.format(Locale.US, "%02d-%02d %02d:%02d", month, day, hour, minute)
    }

    private fun durationText(value: Any?): String {
        val seconds = (value as? Number)?.toDouble()?.takeIf { it.isFinite() } ?: return DASH
        return if (seconds >= 3600.0) {
            String.format(Locale.US, "%.1f h", seconds / 3600.0)
        } else {
            String.format(Locale.US, "%.0f s", seconds)
        }
    }

    private fun altText(value: Any?): String {
        val degrees = (value as? Number)?.toDouble()?.takeIf { it.isFinite() } ?: return DASH
        return String.format(Locale.US, "%.1f°", degrees)
    }

    private fun suspectText(value: Any?): String {
        val reasons = (value as? List<*>)
            ?.mapNotNull { (it as? String)?.takeIf { reason -> reason.isNotBlank() } }
            .orEmpty()
        return if (reasons.isEmpty()) "数据可疑：原因未给出"
        else "数据可疑：" + reasons.joinToString("；")
    }
}
