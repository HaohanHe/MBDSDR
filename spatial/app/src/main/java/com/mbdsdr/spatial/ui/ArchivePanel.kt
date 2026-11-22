package com.mbdsdr.spatial.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.mbdsdr.spatial.data.EventRow
import com.pico.spatial.ui.design.PicoTheme
import com.pico.spatial.ui.design.Text

/**
 * 回看面板：时间轴事件列表。
 *
 * 空表时区分「还没加载」与「这次观测没有事件」；越界行用灰字并附原因，不隐藏。
 * [jumpNote] 是跨窗跳转的结果提示，为空时给一句操作提示。
 * [relatedIndices] 只包含 detail 里确有同名目标的事件（由
 * [com.mbdsdr.spatial.data.TimelineFormat.relatedIndices] 判定），
 * 用 `◆` 标出；空表时不多说一句，假装有关联。
 */
@Composable
fun ArchivePanel(
    rows: List<EventRow>,
    selectedIndex: Int?,
    onSelect: (EventRow) -> Unit,
    modifier: Modifier = Modifier,
    jumpNote: String? = null,
    loaded: Boolean = true,
    relatedIndices: Set<Int> = emptySet(),
) {
    Column(
        modifier = modifier.verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(text = "回看", style = PicoTheme.typography.bodyLarge)
        if (jumpNote != null || rows.isNotEmpty()) {
            Text(
                text = jumpNote ?: "点一条事件可跳到听见",
                style = PicoTheme.typography.bodySmall,
                color = if (jumpNote != null) {
                    PicoTheme.colorTokens.LabelSecondaryAlpha
                } else {
                    PicoTheme.colorTokens.LabelTertiaryAlpha
                },
            )
        }
        if (rows.isEmpty()) {
            Text(
                text = if (loaded) "这次观测没有事件" else "还没有加载观测包",
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelTertiaryAlpha,
            )
        } else {
            if (relatedIndices.isNotEmpty()) {
                Text(
                    text = "◆ 与选中目标同名的事件",
                    style = PicoTheme.typography.bodySmall,
                    color = PicoTheme.colorTokens.LabelTertiaryAlpha,
                )
            }
            for (row in rows) {
                val related = row.index in relatedIndices
                val color = when {
                    row.outOfRange -> PicoTheme.colorTokens.LabelTertiaryAlpha
                    row.index == selectedIndex -> PicoTheme.colorTokens.LabelPrimary
                    related -> PicoTheme.colorTokens.LabelPrimary
                    else -> PicoTheme.colorTokens.LabelSecondaryAlpha
                }
                Text(
                    text = (if (related) "◆ " else "") +
                        "#${row.index}  ${row.label}  ${row.atText}  ${row.sampleText}" +
                        (row.note?.let { "（$it）" }.orEmpty()),
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { onSelect(row) }
                        .padding(vertical = 2.dp),
                    style = PicoTheme.typography.bodySmall,
                    color = color,
                )
            }
        }
    }
}
