package com.mbdsdr.spatial.sdr

/**
 * 瀑布图环形缓冲（M4.2）：只存归一化到 [0,1] 的画柱，新行覆盖最旧行。
 *
 * 纯 Kotlin、不依赖 Android/Compose，JVM 单测可验；渲染层只读 [row]。
 * 未填满时 `count < rows`，不把空槽当黑噪声画成数据。
 */
class WaterfallBuffer(val columns: Int, val rows: Int) {
    private val data: Array<FloatArray> =
        Array(if (rows > 0) rows else 0) {
            FloatArray(if (columns > 0) columns else 0)
        }
    private var next = 0

    /** 已写入的有效行数（<= rows）。 */
    var count = 0
        private set

    /**
     * 压入一行；列数不匹配或缓冲非法返回 false（不改动已有行）。
     * [levels] 应是 [0,1]；这里不替调用方归一化/裁剪。
     */
    fun push(levels: FloatArray): Boolean {
        if (columns <= 0 || rows <= 0 || levels.size != columns) return false
        levels.copyInto(data[next])
        next = (next + 1) % rows
        if (count < rows) count++
        return true
    }

    /**
     * 取第 [age] 行：0 = 最新，1 = 次新……越界或尚未填充返回 null。
     * 返回内部数组（只读约定；调用方不得修改）。
     */
    fun row(age: Int): FloatArray? {
        if (age < 0 || age >= count || rows <= 0) return null
        val idx = ((next - 1 - age) % rows + rows) % rows
        return data[idx]
    }

    /** 清空（断开/停止时调用），不保留旧画面。 */
    fun clear() {
        next = 0
        count = 0
    }
}
