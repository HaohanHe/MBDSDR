package com.mbdsdr.spatial

import com.mbdsdr.spatial.sdr.WaterfallBuffer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 瀑布环形缓冲（M4.2）：只读语义、覆盖顺序与清空。 */
class WaterfallBufferTest {

    @Test
    fun newestRowIsAgeZero() {
        val buf = WaterfallBuffer(columns = 2, rows = 3)
        assertTrue(buf.push(floatArrayOf(1f, 0f)))
        assertTrue(buf.push(floatArrayOf(0f, 1f)))
        assertEquals(2, buf.count)
        assertArrayEquals(floatArrayOf(0f, 1f), buf.row(0)!!, 0f)
        assertArrayEquals(floatArrayOf(1f, 0f), buf.row(1)!!, 0f)
        assertNull(buf.row(2))
        assertNull(buf.row(-1))
    }

    @Test
    fun wrapsAndDropsOldestRow() {
        val buf = WaterfallBuffer(columns = 1, rows = 3)
        for (v in 1..4) assertTrue(buf.push(floatArrayOf(v.toFloat())))
        assertEquals(3, buf.count)
        assertEquals(4f, buf.row(0)!![0], 0f)
        assertEquals(3f, buf.row(1)!![0], 0f)
        assertEquals(2f, buf.row(2)!![0], 0f)
        assertNull(buf.row(3))
    }

    @Test
    fun rejectsWrongSizeWithoutChangingRows() {
        val buf = WaterfallBuffer(columns = 3, rows = 2)
        assertTrue(buf.push(floatArrayOf(1f, 2f, 3f)))
        assertFalse(buf.push(floatArrayOf(9f, 9f)))
        assertEquals(1, buf.count)
        assertArrayEquals(floatArrayOf(1f, 2f, 3f), buf.row(0)!!, 0f)
    }

    @Test
    fun clearAndInvalidDimensionsAreSafe() {
        val buf = WaterfallBuffer(columns = 2, rows = 2)
        buf.push(floatArrayOf(1f, 1f))
        buf.clear()
        assertEquals(0, buf.count)
        assertNull(buf.row(0))

        assertFalse(WaterfallBuffer(columns = 0, rows = 2)
            .push(FloatArray(0)))
        assertFalse(WaterfallBuffer(columns = 2, rows = 0)
            .push(floatArrayOf(1f, 2f)))
    }
}
