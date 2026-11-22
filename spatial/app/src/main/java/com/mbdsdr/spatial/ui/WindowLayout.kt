package com.mbdsdr.spatial.ui

import com.pico.spatial.ui.foundation.dsl.ContainerProxy
import com.pico.spatial.ui.foundation.dsl.Placement

/** 子窗口相对锚点窗口的默认方位，对应 `Placement.right` / `Placement.bottom`。 */
enum class LayoutSide { RIGHT, BELOW }

/** 一个空间窗口的默认布局定义（尺寸单位 dp）。 */
data class WindowSpec(
    val id: String,
    val tag: String,
    val widthDp: Int,
    val heightDp: Int,
    val side: LayoutSide,
    val anchorId: String,
)

enum class LayoutOpKind { CLOSE, OPEN }

/** 复位时的一步：先关掉旧窗口，再按默认 `placement` 重新打开。 */
data class LayoutOp(val kind: LayoutOpKind, val spec: WindowSpec)

/**
 * 四窗默认布局的唯一真源。
 *
 * `Main.kt` 的 `WindowContainer` 尺寸与 `placement` 全部从这里取，避免注册参数
 * 和复位计划各写一份。复位只关闭并重开三个伴随窗口，不碰主窗口，也不动
 * `ObservationState` 里的观测包/选中星——数据状态不由布局动作改写。
 *
 * 诚实边界：`SpatialNavigator` 没有「设置窗口位置」的 API，复位靠关闭后按注册的
 * `placement` 重新打开实现；设备上的实际落位未验证（本机无可用模拟器/真机）。
 */
object WindowLayout {
    const val SKY_ID = "Default"

    const val SKY_MIN_WIDTH_DP = 960
    const val SKY_MIN_HEIGHT_DP = 560

    val companions: List<WindowSpec> = listOf(
        WindowSpec(
            id = "spectrum",
            tag = "mbdsdr-spectrum",
            widthDp = 720,
            heightDp = 420,
            side = LayoutSide.RIGHT,
            anchorId = SKY_ID,
        ),
        WindowSpec(
            id = "archive",
            tag = "mbdsdr-archive",
            widthDp = 620,
            heightDp = 420,
            side = LayoutSide.BELOW,
            anchorId = SKY_ID,
        ),
        WindowSpec(
            id = "station",
            tag = "mbdsdr-station",
            widthDp = 520,
            heightDp = 320,
            side = LayoutSide.BELOW,
            anchorId = "archive",
        ),
    )

    fun companion(id: String): WindowSpec? = companions.firstOrNull { it.id == id }

    /** 复位顺序固定：先全部关闭，再全部打开；绝不关闭主窗口「头顶」。 */
    fun resetPlan(): List<LayoutOp> =
        companions.map { LayoutOp(LayoutOpKind.CLOSE, it) } +
            companions.map { LayoutOp(LayoutOpKind.OPEN, it) }

    /** `WindowContainer` 的 `placement` 块；找不到锚点时回退 `Placement.none()`。 */
    fun placementFor(
        spec: WindowSpec,
        containers: List<ContainerProxy>,
    ): Placement {
        val anchor = containers.firstOrNull { it.id == spec.anchorId } ?: return Placement.none()
        return when (spec.side) {
            LayoutSide.RIGHT -> Placement.right(anchor)
            LayoutSide.BELOW -> Placement.bottom(anchor)
        }
    }
}
