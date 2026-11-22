package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.Star
import com.mbdsdr.spatial.sky.SkyRender
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SkyRenderTest {

    private fun star(
        name: String? = null,
        mag: Double? = 1.0,
        altDeg: Double? = 45.0,
        azDeg: Double? = 0.0,
    ) = Star(
        name = name,
        raDeg = null,
        decDeg = null,
        mag = mag,
        altDeg = altDeg,
        azDeg = azDeg,
        visible = null,
    )

    @Test
    fun emptyInputPlansNothing() {
        assertTrue(SkyRender.plan(emptyList(), 400, 300).isEmpty())
    }

    @Test
    fun zenithLandsAtCanvasCenter() {
        val dots = SkyRender.plan(listOf(star(altDeg = 90.0, azDeg = 0.0)), 400, 300)
        assertEquals(1, dots.size)
        assertEquals(200f, dots[0].x, 0.01f)
        assertEquals(150f, dots[0].y, 0.01f)
    }

    @Test
    fun northIsUpAndEastIsRight() {
        val north = SkyRender.plan(listOf(star(altDeg = 0.0, azDeg = 0.0)), 400, 300)[0]
        assertEquals(200f, north.x, 0.01f)
        assertTrue(north.y < 150f)

        val east = SkyRender.plan(listOf(star(altDeg = 0.0, azDeg = 90.0)), 400, 300)[0]
        assertTrue(east.x > 200f)
        assertEquals(150f, east.y, 0.01f)
    }

    @Test
    fun brighterMagnitudeGetsLargerRadiusAndMissingMagnitudeUsesMinimum() {
        val bright = SkyRender.plan(listOf(star(mag = -1.0)), 400, 300)[0]
        val faint = SkyRender.plan(listOf(star(mag = 5.0)), 400, 300)[0]
        val unknown = SkyRender.plan(listOf(star(mag = null)), 400, 300)[0]

        assertTrue(bright.radiusPx > faint.radiusPx)
        assertEquals(1.5f, unknown.radiusPx, 0.0f)
    }

    @Test
    fun invalidStarsAreSkippedWithoutThrowing() {
        val dots = SkyRender.plan(
            listOf(
                star(mag = 1.0, altDeg = null, azDeg = 0.0),
                star(mag = 1.0, altDeg = 30.0, azDeg = null),
                star(mag = Double.NaN, altDeg = 30.0, azDeg = 0.0),
                star(mag = 1.0, altDeg = -10.0, azDeg = 0.0),
                star(name = "valid", mag = 2.0, altDeg = 30.0, azDeg = 180.0),
            ),
            400,
            300,
        )
        assertEquals(1, dots.size)
        assertEquals("valid", dots[0].name)

        assertTrue(
            SkyRender.plan(
                listOf(
                    star(altDeg = null),
                    star(azDeg = null),
                    star(mag = Double.POSITIVE_INFINITY),
                    star(altDeg = -1.0),
                ),
                400,
                300,
            ).isEmpty()
        )
    }

    @Test
    fun outputOrderMatchesInputOrder() {
        val dots = SkyRender.plan(
            listOf(
                star(name = "a", altDeg = 10.0, azDeg = 0.0),
                star(name = "b", altDeg = 20.0, azDeg = 90.0),
                star(name = "c", altDeg = 30.0, azDeg = 180.0),
            ),
            400,
            300,
        )
        assertEquals(listOf("a", "b", "c"), dots.map { it.name })
    }
}
