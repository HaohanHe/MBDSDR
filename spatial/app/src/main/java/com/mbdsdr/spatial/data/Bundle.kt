package com.mbdsdr.spatial.data

import com.google.gson.Gson
import com.google.gson.annotations.SerializedName

/** 观测站点。站址未知时三个数值都是 null——不按 0,0 渲染。 */
data class Site(
    @SerializedName("lat_deg") val latDeg: Double?,
    @SerializedName("lon_deg") val lonDeg: Double?,
    @SerializedName("alt_m") val altM: Double?,
    @SerializedName("source") val source: String?,
)

/** 观测时刻（原始记录值，不做时区猜补）。 */
data class ObsTime(
    val utc: String?,
    val source: String?,
    val precision: String?,
)

/** 星点；alt/az/visible 缺失表示该包没有地平坐标，不是「在地平线下」。 */
data class Star(
    val name: String?,
    @SerializedName("ra_deg") val raDeg: Double?,
    @SerializedName("dec_deg") val decDeg: Double?,
    val mag: Double?,
    @SerializedName("alt_deg") val altDeg: Double?,
    @SerializedName("az_deg") val azDeg: Double?,
    val visible: Boolean?,
)

data class Sky(
    val stars: List<Star>,
    val passes: List<Map<String, Any?>>,
    @SerializedName("total_stars") val totalStars: Int,
    val truncated: Boolean,
    val source: String?,
    @SerializedName("altaz_source") val altazSource: String?,
    @SerializedName("altaz_refraction") val altazRefraction: Boolean?,
)

data class SpectrumFrame(
    @SerializedName("at_s") val atS: Double,
    val db: List<Double>,
)

data class Spectrum(
    @SerializedName("center_freq_hz") val centerFreqHz: Double?,
    @SerializedName("sample_rate_hz") val sampleRateHz: Double?,
    val frames: List<SpectrumFrame>,
    @SerializedName("duration_s") val durationS: Double?,
    val source: String?,
    /** 录制总样本数；缺失表示未知，此时不判时间轴越界。 */
    @SerializedName("total_samples") val totalSamples: Long?,
)

data class TimelineEvent(
    val kind: String,
    val utc: String?,
    val samples: Long?,
    @SerializedName("at_s") val atS: Double?,
    val detail: Map<String, Any?>,
)

data class SpatialBundle(
    val schema: String,
    @SerializedName("observation_id") val observationId: String,
    val site: Site,
    val time: ObsTime,
    val sky: Sky,
    val spectrum: Spectrum,
    val audio: List<Map<String, Any?>>,
    val timeline: List<TimelineEvent>,
    val evidence: String?,
    val blockers: List<String>,
)

object BundleParser {
    const val SCHEMA = "mbdsdr.spatial/1"

    private val gson = Gson()

    /**
     * 解析 `bundle.json`（schema=`mbdsdr.spatial/1`）。
     *
     * 解析只做搬运：JSON 里没有的字段保持 null / 空表，不给默认坐标、
     * 不补假星。schema 不符或 JSON 坏掉时返回带人话的 failure。
     */
    fun parse(json: String): Result<SpatialBundle> {
        val parsed: SpatialBundle? = try {
            gson.fromJson(json, SpatialBundle::class.java)
        } catch (e: Exception) {
            return Result.failure(
                IllegalArgumentException(
                    "bundle.json 解析失败（${e.javaClass.name}）：${e.message}",
                    e,
                )
            )
        }
        if (parsed == null) {
            return Result.failure(
                IllegalArgumentException("bundle.json 里没有可解析的观测包（内容为空）")
            )
        }
        if (parsed.schema != SCHEMA) {
            return Result.failure(
                IllegalArgumentException(
                    "不支持的 schema：${parsed.schema ?: "缺失"}（本应用只认 $SCHEMA）"
                )
            )
        }
        return Result.success(parsed.normalized())
    }

    /** Gson 绕过 Kotlin 构造器，缺失字段可能让非空集合变成 null；这里只补空表。 */
    private fun SpatialBundle.normalized(): SpatialBundle {
        val siteIn = site.orDefault(Site(null, null, null, null))
        val timeIn = time.orDefault(ObsTime(null, null, null))
        val skyIn = sky.orDefault(Sky(emptyList(), emptyList(), 0, false, null, null, null))
        val spectrumIn = spectrum.orDefault(Spectrum(null, null, emptyList(), null, null, null))
        return copy(
            site = siteIn,
            time = timeIn,
            sky = skyIn.copy(
                stars = skyIn.stars.orEmptyList(),
                passes = skyIn.passes.orEmptyList(),
            ),
            spectrum = spectrumIn.copy(
                frames = spectrumIn.frames.orEmptyList().map {
                    it.copy(db = it.db.orEmptyList())
                },
            ),
            audio = audio.orEmptyList(),
            timeline = timeline.orEmptyList().map {
                it.copy(detail = it.detail.orDefault(emptyMap()))
            },
            blockers = blockers.orEmptyList(),
        )
    }

    private fun <T : Any> T?.orDefault(fallback: T): T = this ?: fallback

    private fun <T> List<T>?.orEmptyList(): List<T> = this ?: emptyList()
}

/** 只取 visible=true 的星；visible 缺失（例如没有站址/时刻）不算可见。 */
fun SpatialBundle.visibleStars(): List<Star> = sky.stars.filter { it.visible == true }

/**
 * 观测时刻 [t]（秒）下「最后一条」事件：取 `atS <= t` 中 atS 最大的，
 * 相同 atS 以时间轴里靠后的为准；`atS` 为空的事件跳过。
 */
fun SpatialBundle.eventAt(t: Double): TimelineEvent? {
    var best: TimelineEvent? = null
    for (event in timeline) {
        val at = event.atS ?: continue
        if (at > t) continue
        if (best == null || at >= best.atS!!) best = event
    }
    return best
}
