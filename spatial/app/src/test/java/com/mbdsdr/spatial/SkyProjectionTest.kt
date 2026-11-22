package com.mbdsdr.spatial

import com.mbdsdr.spatial.sky.SkyProjection
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SkyProjectionTest {

    private val eps = 1e-6f

    @Test
    fun zenithProjectsToCenter() {
        val p = SkyProjection.project(90.0, 123.0)!!
        assertEquals(0f, p.first, eps)
        assertEquals(0f, p.second, eps)
    }

    @Test
    fun northHorizonIsUpOnScreen() {
        val p = SkyProjection.project(0.0, 0.0)!!
        assertEquals(0f, p.first, eps)
        assertEquals(-1f, p.second, eps)
    }

    @Test
    fun eastHorizonIsRightOnScreen() {
        val p = SkyProjection.project(0.0, 90.0)!!
        assertEquals(1f, p.first, eps)
        assertEquals(0f, p.second, eps)
    }

    @Test
    fun southHorizonIsDownOnScreen() {
        val p = SkyProjection.project(0.0, 180.0)!!
        assertEquals(0f, p.first, eps)
        assertEquals(1f, p.second, eps)
    }

    @Test
    fun invalidOrBelowHorizonIsNullAndAzimuthWraps() {
        assertNull(SkyProjection.project(null, 0.0))
        assertNull(SkyProjection.project(-5.0, 0.0))
        assertNull(SkyProjection.project(45.0, null))

        val atZero = SkyProjection.project(30.0, 0.0)!!
        val atFull = SkyProjection.project(30.0, 360.0)!!
        assertEquals(atZero.first, atFull.first, eps)
        assertEquals(atZero.second, atFull.second, eps)

        // 高度角越大越靠近圆心（等距投影）
        val low = SkyProjection.project(10.0, 0.0)!!
        val high = SkyProjection.project(60.0, 0.0)!!
        assertEquals(0f, low.first, eps)
        assertEquals(-(80.0 / 90.0).toFloat(), low.second, eps)
        assertEquals(-(30.0 / 90.0).toFloat(), high.second, eps)
    }
}
