package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.SpectrumFrame
import com.mbdsdr.spatial.sky.SpectrumRender
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SpectrumRenderTest {

    private fun frame(atS: Double, vararg db: Double) = SpectrumFrame(atS = atS, db = db.toList())

    @Test
    fun normalizesDbToZeroHalfOne() {
        val bars = SpectrumRender.bars(frame(0.0, -100.0, -80.0, -60.0))

        assertEquals(listOf(0, 1, 2), bars.map { it.index })
        assertEquals(0.0f, bars[0].level, 1e-6f)
        assertEquals(0.5f, bars[1].level, 1e-6f)
        assertEquals(1.0f, bars[2].level, 1e-6f)
    }

    @Test
    fun equalValuesAllGetHalf() {
        val bars = SpectrumRender.bars(frame(0.0, -70.0, -70.0, -70.0))

        assertEquals(3, bars.size)
        assertTrue(bars.all { kotlin.math.abs(it.level - 0.5f) <= 1e-6f })
    }

    @Test
    fun emptyFrameHasNoBars() {
        assertTrue(SpectrumRender.bars(frame(0.0)).isEmpty())
    }

    @Test
    fun nonFiniteValuesAreTreatedAsDimmest() {
        val bars = SpectrumRender.bars(frame(0.0, Double.NaN, -60.0, -80.0))

        assertEquals(0.0f, bars[0].level, 1e-6f)
        assertEquals(1.0f, bars[1].level, 1e-6f)
        assertEquals(0.0f, bars[2].level, 1e-6f)
    }

    @Test
    fun frameAtPicksNearestAndHandlesEmpty() {
        val frames = listOf(frame(0.0, -100.0), frame(2.0, -60.0))

        assertEquals(0.0, SpectrumRender.frameAt(frames, 0.4)!!.atS, 1e-9)
        assertEquals(2.0, SpectrumRender.frameAt(frames, 1.4)!!.atS, 1e-9)
        assertEquals(2.0, SpectrumRender.frameAt(frames, 9.0)!!.atS, 1e-9)
        assertNull(SpectrumRender.frameAt(emptyList(), 0.0))
    }
}
