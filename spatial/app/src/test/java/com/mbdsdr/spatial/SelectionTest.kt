package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.Star
import com.mbdsdr.spatial.sky.PickedStar
import com.mbdsdr.spatial.sky.Selection
import com.mbdsdr.spatial.sky.StarDot
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SelectionTest {

    private fun star(name: String) = Star(
        name = name,
        raDeg = null,
        decDeg = null,
        mag = 1.0,
        altDeg = 45.0,
        azDeg = 0.0,
        visible = true,
    )

    private val stars = listOf(star("a"), star("b"))
    private val dots = listOf(
        StarDot(x = 100f, y = 100f, radiusPx = 2f, name = "a"),
        StarDot(x = 110f, y = 100f, radiusPx = 2f, name = "b"),
    )

    @Test
    fun emptyInputsReturnNull() {
        assertNull(Selection.pick(emptyList(), stars, x = 100f, y = 100f))
        assertNull(Selection.pick(dots, emptyList(), x = 100f, y = 100f))
    }

    @Test
    fun farTapReturnsNull() {
        assertNull(Selection.pick(dots, stars, x = 300f, y = 300f, maxDistancePx = 32f))
    }

    @Test
    fun nearestDotWins() {
        val picked = Selection.pick(dots, stars, x = 108f, y = 100f)
        assertEquals("b", picked?.star?.name)
        assertEquals(2f, picked!!.distancePx, 1e-4f)

        val other = Selection.pick(dots, stars, x = 103f, y = 100f)
        assertEquals("a", other?.star?.name)
        assertEquals(3f, other!!.distancePx, 1e-4f)
    }

    @Test
    fun nonFiniteOrNegativeCoordinatesReturnNull() {
        assertNull(Selection.pick(dots, stars, x = Float.NaN, y = 100f))
        assertNull(Selection.pick(dots, stars, x = 100f, y = Float.NaN))
        assertNull(Selection.pick(dots, stars, x = Float.POSITIVE_INFINITY, y = 100f))
        assertNull(Selection.pick(dots, stars, x = -1f, y = 100f))
        assertNull(Selection.pick(dots, stars, x = 100f, y = -1f))
    }

    @Test
    fun distanceEqualToMaxIsAcceptedJustOverIsRejected() {
        val single = listOf(StarDot(x = 100f, y = 100f, radiusPx = 2f, name = "a"))

        val atLimit = Selection.pick(single, stars, x = 132f, y = 100f, maxDistancePx = 32f)
        assertEquals("a", atLimit?.star?.name)
        assertEquals(32f, atLimit!!.distancePx, 1e-4f)

        assertNull(Selection.pick(single, stars, x = 133f, y = 100f, maxDistancePx = 32f))
    }

    @Test
    fun starComesFromSameIndexAsDot() {
        val swappedDots = listOf(
            StarDot(x = 10f, y = 20f, radiusPx = 2f, name = "label-mismatch"),
            StarDot(x = 80f, y = 20f, radiusPx = 2f, name = "a"),
        )

        val picked: PickedStar? = Selection.pick(swappedDots, stars, x = 10f, y = 20f)
        assertEquals("a", picked?.star?.name)
        assertEquals(0f, picked!!.distancePx, 1e-4f)
    }
}
