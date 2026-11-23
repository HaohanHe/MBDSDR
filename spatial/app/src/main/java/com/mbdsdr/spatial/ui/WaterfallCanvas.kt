package com.mbdsdr.spatial.ui

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.unit.IntSize
import com.mbdsdr.spatial.sdr.SdrFormat
import com.mbdsdr.spatial.sdr.WaterfallBuffer

/**
 * 瀑布图（M4.2）：把环形缓冲画成位图，新行在底部、旧行向上滚。
 *
 * 只画归一化相对幅度（不是 dBm）；[version] 变化即重建位图，
 * 因为 [WaterfallBuffer] 本身是普通对象，Compose 不感知其内部写入。
 */
@Composable
fun WaterfallCanvas(
    buffer: WaterfallBuffer,
    version: Int,
    modifier: Modifier = Modifier,
) {
    if (buffer.columns <= 0 || buffer.rows <= 0) return
    val image = remember(version) { buffer.toImageBitmap() }
    Canvas(modifier = modifier) {
        if (size.width <= 0f || size.height <= 0f) return@Canvas
        drawImage(
            image = image,
            dstSize = IntSize(size.width.toInt(), size.height.toInt()),
        )
    }
}

private fun WaterfallBuffer.toImageBitmap() = run {
    val cols = columns
    val rws = rows
    val pixels = IntArray(cols * rws) { SdrFormat.waterfallColor(0f) }
    for (age in 0 until count) {
        val row = row(age) ?: continue
        val y = rws - 1 - age          // 0 = 最新 → 画在最底部
        if (y < 0) continue
        val base = y * cols
        for (c in 0 until cols) {
            pixels[base + c] = SdrFormat.waterfallColor(row[c])
        }
    }
    val bitmap = Bitmap.createBitmap(cols, rws, Bitmap.Config.ARGB_8888)
    bitmap.setPixels(pixels, 0, cols, 0, 0, cols, rws)
    bitmap.asImageBitmap()
}
