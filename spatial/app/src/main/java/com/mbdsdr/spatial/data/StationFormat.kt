package com.mbdsdr.spatial.data

import java.util.Locale

/** 「此刻」面板的四行文案。 */
data class StationLines(
    val site: String,
    val time: String,
    val evidence: String,
    val blockers: List<String>,
    val hasSite: Boolean,
)

/**
 * 站址/时刻/证据/阻塞 → 展示文案。
 *
 * 站址来源不是 gnss/manual，或经纬度缺失时，只说「不知道你在哪」，
 * 绝不把缺失坐标渲染成 0.0。
 */
object StationFormat {
    private val KNOWN_SOURCES = setOf("gnss", "manual")

    /**
     * 站址是否已知：经纬度非空且有限，来源是 gnss/manual。
     * `lines()` 与天空空态共用这一份判断，不出现第二套规则。
     */
    fun hasKnownSite(bundle: SpatialBundle): Boolean {
        val lat = bundle.site.latDeg
        val lon = bundle.site.lonDeg
        return lat != null && lon != null &&
            bundle.site.source in KNOWN_SOURCES &&
            lat.isFinite() && lon.isFinite()
    }

    fun lines(bundle: SpatialBundle?): StationLines {
        if (bundle == null) {
            return StationLines(
                site = "还没有加载观测包",
                time = "—",
                evidence = "—",
                blockers = emptyList(),
                hasSite = false,
            )
        }

        val lat = bundle.site.latDeg
        val lon = bundle.site.lonDeg
        val source = bundle.site.source

        val siteText: String
        val hasSite = hasKnownSite(bundle)
        if (hasSite) {
            val base = String.format(Locale.US, "%.4f, %.4f", lat, lon)
            val alt = bundle.site.altM
                ?.takeIf { it.isFinite() }
                ?.let { String.format(Locale.US, " · %.0f m", it) }
                .orEmpty()
            siteText = "$base$alt（${sourceText(source)}）"
        } else {
            siteText = "不知道你在哪（来源：${sourceText(source)}）"
        }

        return StationLines(
            site = siteText,
            time = timeText(bundle.time),
            evidence = bundle.evidence ?: "—",
            blockers = bundle.blockers,
            hasSite = hasSite,
        )
    }

    /**
     * 来源中文名：spec 要求站址窗口显示「GNSS / 手填 / 未知」。
     * `none`/缺失写「未知」，不认识的真实来源保持原样（不臆造归类）。
     */
    private fun sourceText(source: String?): String = when {
        source == "gnss" -> "GNSS"
        source == "manual" -> "手填"
        source == "none" || source.isNullOrBlank() -> "未知"
        else -> source
    }

    /** 三段分别缺则写 `—`；三段全缺时整行只写一个 `—`。 */
    private fun timeText(time: ObsTime): String {
        val parts = listOf(time.utc, time.source, time.precision)
            .map { it?.takeIf { text -> text.isNotBlank() } ?: "—" }
        return if (parts.all { it == "—" }) "—" else parts.joinToString(" · ")
    }
}
