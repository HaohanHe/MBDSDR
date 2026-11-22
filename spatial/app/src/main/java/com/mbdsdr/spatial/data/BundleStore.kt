package com.mbdsdr.spatial.data

/**
 * 观测包的唯一持有者（纯 Kotlin，便于 JVM 单测）。
 *
 * 每次 [load] 都是一次完整替换：解析失败时清掉旧包，不拿上一次的包冒充这一次。
 */
class BundleStore {
    var bundle: SpatialBundle? = null
        private set

    var lastError: String? = null
        private set

    /** @return 解析并替换成功返回 true；失败时 [bundle] 为 null、[lastError] 为解析器原文。 */
    fun load(json: String): Boolean {
        val result = BundleParser.parse(json)
        val parsed = result.getOrNull()
        if (parsed == null) {
            bundle = null
            lastError = result.exceptionOrNull()?.message ?: "bundle.json 解析失败"
            return false
        }
        bundle = parsed
        lastError = null
        return true
    }

    fun clear() {
        bundle = null
        lastError = null
    }

    fun description(): String {
        val loaded = bundle ?: return "还没有加载观测包"
        return "obs=${loaded.observationId} stars=${loaded.sky.stars.size} " +
            "visible=${loaded.visibleStars().size} events=${loaded.timeline.size}"
    }
}
