package com.mbdsdr.spatial

import com.mbdsdr.spatial.data.BundleParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeFalse
import org.junit.Test
import java.io.File

/**
 * 跨语言校验：Python 侧 `export_spatial_bundle.py` 真实导出的 bundle
 * 必须被头显 Kotlin 解析器接受。
 *
 * 真实包含用户站址坐标，**绝不入仓**；测试从环境变量
 * `MBDSDR_SPATIAL_BUNDLE` 读取本机临时文件路径，未设置时跳过。
 */
class RealBundleExternalTest {

    @Test
    fun exportedBundleParsesAndKeepsProvenance() {
        val path = System.getenv("MBDSDR_SPATIAL_BUNDLE").orEmpty()
        assumeFalse("未设置 MBDSDR_SPATIAL_BUNDLE，跳过外部真实包校验", path.isBlank())

        val json = File(path).readText(Charsets.UTF_8)
        val result = BundleParser.parse(json)
        assertTrue(
            "真实 bundle 解析失败: ${result.exceptionOrNull()}",
            result.isSuccess,
        )
        val bundle = result.getOrThrow()

        assertEquals(BundleParser.SCHEMA, bundle.schema)
        assertEquals("gnss", bundle.site.source)
        assertEquals("gnss", bundle.time.source)
        assertTrue(bundle.sky.stars.isNotEmpty())
        assertTrue(bundle.sky.stars.any { it.visible == true })
        assertTrue(bundle.spectrum.frames.isNotEmpty())
        assertTrue(bundle.blockers.isEmpty())
    }
}
