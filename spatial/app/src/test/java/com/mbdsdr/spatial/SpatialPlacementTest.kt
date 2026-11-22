package com.mbdsdr.spatial

import com.mbdsdr.spatial.audio.Direction
import com.mbdsdr.spatial.audio.SpatialPlacement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 声源方向数学的约定锁定：
 * - 世界坐标 +x = 东、+y = 天顶、+z = 北；
 * - 头相对坐标 +x = 头右、+y = 头上、+z = 头前，yaw 向右为正、pitch 抬头为正；
 * - 距离增益 `reference / (reference + distance)`，夹在 [0.05, 1.0]。
 */
class SpatialPlacementTest {

    private val eps = 1e-9

    private fun assertDirection(expected: Direction, actual: Direction?, delta: Double = eps) {
        assertNotNull(actual)
        assertEquals(expected.x, actual!!.x, delta)
        assertEquals(expected.y, actual.y, delta)
        assertEquals(expected.z, actual.z, delta)
    }

    @Test
    fun cardinalDirectionsLandOnWorldAxes() {
        assertDirection(Direction(0.0, 0.0, 1.0), SpatialPlacement.worldDirection(0.0, 0.0))
        assertDirection(Direction(1.0, 0.0, 0.0), SpatialPlacement.worldDirection(0.0, 90.0))
        assertDirection(Direction(0.0, 0.0, -1.0), SpatialPlacement.worldDirection(0.0, 180.0))
        assertDirection(Direction(0.0, 1.0, 0.0), SpatialPlacement.worldDirection(90.0, 0.0))
    }

    @Test
    fun arbitraryDirectionHasUnitLength() {
        val samples = listOf(
            0.0 to 359.9,
            12.5 to 45.0,
            -30.0 to 123.4,
            89.9 to -170.0,
            90.0 to 77.0,
            -90.0 to 0.0,
        )
        for ((alt, az) in samples) {
            val v = SpatialPlacement.worldDirection(alt, az)!!
            val length = kotlin.math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
            assertEquals("alt=$alt az=$az", 1.0, length, eps)
        }
    }

    @Test
    fun azimuthWrapsAcrossZero() {
        assertDirection(
            SpatialPlacement.worldDirection(10.0, 359.9)!!,
            SpatialPlacement.worldDirection(10.0, -0.1),
        )
        assertDirection(
            SpatialPlacement.worldDirection(10.0, 0.0)!!,
            SpatialPlacement.worldDirection(10.0, 360.0),
        )
    }

    @Test
    fun invalidDirectionsReturnNull() {
        assertNull(SpatialPlacement.worldDirection(null, 0.0))
        assertNull(SpatialPlacement.worldDirection(0.0, null))
        assertNull(SpatialPlacement.worldDirection(Double.NaN, 0.0))
        assertNull(SpatialPlacement.worldDirection(0.0, Double.NaN))
        assertNull(SpatialPlacement.worldDirection(Double.POSITIVE_INFINITY, 0.0))
        assertNull(SpatialPlacement.worldDirection(0.0, Double.NEGATIVE_INFINITY))
        assertNull(SpatialPlacement.worldDirection(90.1, 0.0))
        assertNull(SpatialPlacement.worldDirection(-90.1, 0.0))
    }

    @Test
    fun zeroHeadRotationEqualsWorldDirection() {
        for ((alt, az) in listOf(0.0 to 0.0, 45.0 to 120.0, -20.0 to 300.0, 90.0 to 0.0)) {
            assertDirection(
                SpatialPlacement.worldDirection(alt, az)!!,
                SpatialPlacement.headRelative(alt, az, headYawDeg = 0.0, headPitchDeg = 0.0),
            )
        }
    }

    @Test
    fun yawRightPutsNorthSourceOnTheLeft() {
        // 面向北、头向右转 90°（朝东）：北天边的源应落在头坐标 -x（左手边）。
        assertDirection(
            Direction(-1.0, 0.0, 0.0),
            SpatialPlacement.headRelative(0.0, 0.0, headYawDeg = 90.0, headPitchDeg = 0.0),
            delta = 1e-9,
        )
        // 朝东之后，东天边的源落在正前方 +z。
        assertDirection(
            Direction(0.0, 0.0, 1.0),
            SpatialPlacement.headRelative(0.0, 90.0, headYawDeg = 90.0, headPitchDeg = 0.0),
            delta = 1e-9,
        )
        // 反向转 90°，北天边的源落在 +x（右手边）。
        assertDirection(
            Direction(1.0, 0.0, 0.0),
            SpatialPlacement.headRelative(0.0, 0.0, headYawDeg = -90.0, headPitchDeg = 0.0),
        )
    }

    @Test
    fun pitchUpPutsZenithAhead() {
        // 抬头 90° 看天顶：天顶源变为头前方，北天边的源落到头下方。
        assertDirection(
            Direction(0.0, 0.0, 1.0),
            SpatialPlacement.headRelative(90.0, 123.0, headYawDeg = 0.0, headPitchDeg = 90.0),
        )
        assertDirection(
            Direction(0.0, -1.0, 0.0),
            SpatialPlacement.headRelative(0.0, 0.0, headYawDeg = 0.0, headPitchDeg = 90.0),
        )
        // 组合姿态仍保持单位长度。
        val combined = SpatialPlacement.headRelative(45.0, 60.0, headYawDeg = 30.0, headPitchDeg = 20.0)!!
        val length = kotlin.math.sqrt(
            combined.x * combined.x + combined.y * combined.y + combined.z * combined.z,
        )
        assertEquals(1.0, length, eps)
    }

    @Test
    fun nonFiniteHeadAnglesReturnNull() {
        assertNull(
            SpatialPlacement.headRelative(0.0, 0.0, headYawDeg = Double.NaN, headPitchDeg = 0.0),
        )
        assertNull(
            SpatialPlacement.headRelative(
                0.0, 0.0, headYawDeg = 0.0, headPitchDeg = Double.POSITIVE_INFINITY,
            ),
        )
        assertNull(
            SpatialPlacement.headRelative(null, 0.0, headYawDeg = 0.0, headPitchDeg = 0.0),
        )
    }

    @Test
    fun distanceGainStartsAtOneAndNeverIncreases() {
        assertEquals(1.0f, SpatialPlacement.distanceGain(0.0), 0f)

        var previous = SpatialPlacement.distanceGain(0.0)
        for (distance in listOf(0.001, 0.1, 1.0, 10.0, 1e3, 1e6, 1e12)) {
            val gain = SpatialPlacement.distanceGain(distance)
            assertTrue("distance=$distance gain=$gain", gain <= previous + 1e-7f)
            assertTrue("distance=$distance gain=$gain", gain <= 1.0f)
            assertTrue("distance=$distance gain=$gain", gain >= SpatialPlacement.MIN_GAIN)
            previous = gain
        }
    }

    @Test
    fun tinyDistanceDoesNotBoostAboveUnity() {
        val gain = SpatialPlacement.distanceGain(1e-12)
        assertTrue("gain=$gain", gain <= 1.0f)
        assertTrue("gain=$gain", gain > 0.99f)
    }

    @Test
    fun invalidDistanceOrReferenceReturnsUnity() {
        assertEquals(1.0f, SpatialPlacement.distanceGain(null), 0f)
        assertEquals(1.0f, SpatialPlacement.distanceGain(-1.0), 0f)
        assertEquals(1.0f, SpatialPlacement.distanceGain(Double.NaN), 0f)
        assertEquals(1.0f, SpatialPlacement.distanceGain(Double.POSITIVE_INFINITY), 0f)

        assertEquals(1.0f, SpatialPlacement.distanceGain(5.0, referenceM = 0.0), 0f)
        assertEquals(1.0f, SpatialPlacement.distanceGain(5.0, referenceM = -2.0), 0f)
        assertEquals(1.0f, SpatialPlacement.distanceGain(5.0, referenceM = Double.NaN), 0f)
        assertEquals(
            1.0f,
            SpatialPlacement.distanceGain(5.0, referenceM = Double.POSITIVE_INFINITY),
            0f,
        )
    }

    @Test
    fun farDistanceIsClampedAtMinimumGain() {
        assertEquals(
            SpatialPlacement.MIN_GAIN,
            SpatialPlacement.distanceGain(1e12),
            1e-6f,
        )
        assertEquals(0.5f, SpatialPlacement.distanceGain(1.0, referenceM = 1.0), 1e-7f)
    }
}
