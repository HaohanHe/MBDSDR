package com.mbdsdr.spatial.data

/** 观测包里的一条录音（只做搬运，不判断文件是否存在）。 */
data class AudioInfo(
    val path: String,
    val label: String,
    val utc: String?,
)

/**
 * 录音条目 → 展示文案。
 *
 * 只认 `path` 为非空字符串的条目；`label/utc` 非字符串一律按缺失处理，
 * 不把数字、布尔之类的值拼进文案。
 */
object AudioFormat {
    fun first(audio: List<Map<String, Any?>>): AudioInfo? {
        for (entry in audio) {
            val path = entry["path"] as? String
            if (path.isNullOrEmpty()) continue
            return AudioInfo(
                path = path,
                label = text(entry["label"]) ?: "来源未知",
                utc = text(entry["utc"]),
            )
        }
        return null
    }

    fun describe(info: AudioInfo?): String {
        if (info == null) return "这次观测没有录音"
        val utc = info.utc?.takeIf { it.isNotBlank() } ?: "时间未知"
        val label = info.label.takeIf { it.isNotBlank() } ?: "来源未知"
        return "录音：$utc · $label"
    }

    fun buttonText(info: AudioInfo?, playing: Boolean): String = when {
        info == null -> "没有录音"
        playing -> "停止"
        else -> "播放录音"
    }

    private fun text(value: Any?): String? = value as? String
}
