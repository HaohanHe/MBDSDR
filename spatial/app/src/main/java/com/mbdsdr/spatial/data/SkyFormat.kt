package com.mbdsdr.spatial.data

/**
 * 天空窗口的空态提示：只说缺了什么，不补坐标、不补时刻。
 *
 * 返回 null 有两种含义：还没加载观测包（UI 显示默认文案），
 * 或已有可见星（无需提示）。
 */
object SkyFormat {
    fun emptyHint(bundle: SpatialBundle?): String? {
        if (bundle == null) return null
        if (bundle.sky.stars.isEmpty()) return "包里没有星表（导出时可能跳过了星星）"
        if (!StationFormat.hasKnownSite(bundle)) return "不知道你在哪，无法把星放到头顶"
        if (bundle.time.utc.isNullOrBlank()) return "不知道观测时刻，无法把星放到头顶"
        if (bundle.visibleStars().isEmpty()) return "这次没有可见的星（都在地平线下）"
        return null
    }
}
