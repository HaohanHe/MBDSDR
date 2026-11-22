package com.mbdsdr.spatial.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.mbdsdr.spatial.data.PassRow
import com.pico.spatial.ui.design.PicoTheme
import com.pico.spatial.ui.design.Text

/** 最多显示的行数；超出部分明确告知条数，不静默截断。 */
private const val MAX_ROWS = 8

/**
 * 过境面板：名称 / 开始时刻 / 历时 / 最高仰角。
 *
 * 可疑过境用低饱和红字，并在下一行附 bundle 给出的原因；
 * 空表时说明缺的是 TLE 或计算，而不是画一张空列表。
 */
@Composable
fun PassesPanel(rows: List<PassRow>, modifier: Modifier = Modifier) {
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(text = "过境（${rows.size}）", style = PicoTheme.typography.bodyLarge)
        if (rows.isEmpty()) {
            Text(
                text = "没有过境数据（缺少 TLE 或未计算）",
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelTertiaryAlpha,
            )
        } else {
            for (row in rows.take(MAX_ROWS)) {
                val color = if (row.suspect) {
                    PicoTheme.colorTokens.SubRed60
                } else {
                    PicoTheme.colorTokens.LabelSecondaryAlpha
                }
                Text(
                    text = "${row.name}  ${row.startText}  ${row.durText}  最高 ${row.altText}",
                    modifier = Modifier.fillMaxWidth(),
                    style = PicoTheme.typography.bodySmall,
                    color = color,
                )
                if (row.suspect) {
                    row.suspectText?.let {
                        Text(
                            text = it,
                            modifier = Modifier.fillMaxWidth().padding(start = 12.dp),
                            style = PicoTheme.typography.bodySmall,
                            color = color,
                        )
                    }
                }
            }
            if (rows.size > MAX_ROWS) {
                Text(
                    text = "还有 ${rows.size - MAX_ROWS} 条未显示",
                    style = PicoTheme.typography.bodySmall,
                    color = PicoTheme.colorTokens.LabelTertiaryAlpha,
                )
            }
        }
    }
}
