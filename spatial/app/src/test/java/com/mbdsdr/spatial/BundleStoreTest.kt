package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.BundleStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BundleStoreTest {

    // 夹具只用中性坐标（10.0 / 20.0），不含任何真实经纬度。
    private val validJson = """
        {
          "schema": "mbdsdr.spatial/1",
          "observation_id": "obs-store-1",
          "site": {"lat_deg": 10.0, "lon_deg": 20.0, "alt_m": 100.0, "source": "gnss"},
          "time": {"utc": "2026-11-20T12:00:00+00:00", "source": "system"},
          "sky": {
            "stars": [
              {"name": "Vega", "alt_deg": 66.5, "az_deg": 78.0, "visible": true},
              {"name": "Sirius", "alt_deg": -12.0, "az_deg": 250.0, "visible": false}
            ],
            "passes": [],
            "total_stars": 2,
            "truncated": false
          },
          "spectrum": {"frames": [{"at_s": 0.0, "db": [-100.0, -80.0]}]},
          "audio": [],
          "timeline": [{"kind": "record_start", "at_s": 0.0, "detail": {}}],
          "blockers": []
        }
    """.trimIndent()

    @Test
    fun validJsonLoadsBundleAndDescribesIt() {
        val store = BundleStore()

        assertTrue(store.load(validJson))
        assertNotNull(store.bundle)
        assertNull(store.lastError)
        assertTrue(store.description(), store.description().contains("stars="))
    }

    @Test
    fun brokenJsonFailsWithErrorAndNoBundle() {
        val store = BundleStore()

        assertFalse(store.load("""{"schema": "mbdsdr.spatial/1", "site": {"""))
        assertNull(store.bundle)
        val message = store.lastError
        assertNotNull(message)
        assertTrue(message!!.isNotEmpty())
    }

    @Test
    fun successClearsPreviousError() {
        val store = BundleStore()

        assertFalse(store.load("not json"))
        assertNotNull(store.lastError)

        assertTrue(store.load(validJson))
        assertNull(store.lastError)
        assertNotNull(store.bundle)
    }

    @Test
    fun failureAfterSuccessDropsOldBundle() {
        val store = BundleStore()

        assertTrue(store.load(validJson))
        assertNotNull(store.bundle)

        assertFalse(store.load("""{"schema": "mbdsdr.spatial/1", "sky": }"""))
        assertNull(store.bundle)
        assertNotNull(store.lastError)
        assertEquals("还没有加载观测包", store.description())
    }

    @Test
    fun clearResetsEverything() {
        val store = BundleStore()
        store.load(validJson)

        store.clear()

        assertNull(store.bundle)
        assertNull(store.lastError)
        assertEquals("还没有加载观测包", store.description())
    }
}
