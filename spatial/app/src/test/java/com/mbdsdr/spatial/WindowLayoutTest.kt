package com.mbdsdr.spatial

import com.mbdsdr.spatial.ui.LayoutOpKind
import com.mbdsdr.spatial.ui.LayoutSide
import com.mbdsdr.spatial.ui.WindowLayout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class WindowLayoutTest {

    private val companions = WindowLayout.companions

    @Test
    fun definesThreeCompanionWindowsWithUniqueIdsAndTags() {
        assertEquals(3, companions.size)
        val ids = listOf(WindowLayout.SKY_ID) + companions.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
        assertEquals(companions.size, companions.map { it.tag }.toSet().size)
        assertTrue(companions.all { it.tag.isNotBlank() })
        assertTrue(companions.none { it.id == WindowLayout.SKY_ID })
    }

    @Test
    fun defaultSizesMatchDocumentedLayout() {
        val sizes = companions.associate { it.id to (it.widthDp to it.heightDp) }

        assertEquals(720 to 420, sizes["spectrum"])
        assertEquals(620 to 420, sizes["archive"])
        assertEquals(520 to 320, sizes["station"])
    }

    @Test
    fun everyCompanionAnchorsToAnExistingWindow() {
        val known = setOf(WindowLayout.SKY_ID) + companions.map { it.id }

        for (spec in companions) {
            assertTrue(spec.id, spec.anchorId in known)
            assertNotEquals(spec.id, spec.anchorId)
        }
    }

    @Test
    fun companionLayoutKeepsRelativePlacement() {
        val spectrum = WindowLayout.companion("spectrum")!!
        val archive = WindowLayout.companion("archive")!!
        val station = WindowLayout.companion("station")!!

        assertEquals(WindowLayout.SKY_ID, spectrum.anchorId)
        assertEquals(LayoutSide.RIGHT, spectrum.side)
        assertEquals(WindowLayout.SKY_ID, archive.anchorId)
        assertEquals(LayoutSide.BELOW, archive.side)
        // 站址窗挂在回看窗下方，而不是直接从头顶垂下。
        assertEquals(archive.id, station.anchorId)
        assertEquals(LayoutSide.BELOW, station.side)
    }

    @Test
    fun resetPlanClosesAllCompanionsBeforeOpeningAny() {
        val plan = WindowLayout.resetPlan()

        assertEquals(companions.size * 2, plan.size)
        val closes = plan.take(companions.size)
        val opens = plan.drop(companions.size)
        assertTrue(closes.all { it.kind == LayoutOpKind.CLOSE })
        assertTrue(opens.all { it.kind == LayoutOpKind.OPEN })
        assertEquals(companions.map { it.id }, closes.map { it.spec.id })
        assertEquals(companions.map { it.id }, opens.map { it.spec.id })
    }

    @Test
    fun resetPlanIsPureAndNeverTouchesSkyWindow() {
        val first = WindowLayout.resetPlan()
        val second = WindowLayout.resetPlan()

        assertEquals(first, second)
        assertTrue(first.none { it.spec.id == WindowLayout.SKY_ID })
    }

    @Test
    fun unknownCompanionIdReturnsNull() {
        assertEquals(null, WindowLayout.companion("missing"))
    }
}
