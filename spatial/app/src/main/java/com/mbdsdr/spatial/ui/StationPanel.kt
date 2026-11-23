package com.mbdsdr.spatial.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.mbdsdr.spatial.data.StationLines
import com.pico.spatial.ui.design.PicoTheme
import com.pico.spatial.ui.design.Text

/**
 * 此刻面板：站址 / 时间 / 证据 / 阻塞。
 *
 * 阻塞为空时不显示该行；站址未知时由 [StationLines.site] 直接给出人话。
 * [gatewayLine] 是桌面网关的全景转述（M4.1 收尾）：只多一行、不改 bundle 的
 * 站址/时刻，明确标「网关」，避免与包内数据混成一份。
 */
@Composable
fun StationPanel(
    lines: StationLines,
    modifier: Modifier = Modifier,
    gatewayLine: String? = null,
) {
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(text = "此刻", style = PicoTheme.typography.bodyLarge)
        StationLine("站址", lines.site)
        StationLine("时间", lines.time)
        StationLine("证据", lines.evidence)
        if (lines.blockers.isNotEmpty()) {
            StationLine("阻塞", lines.blockers.joinToString("；"))
        }
        if (!gatewayLine.isNullOrBlank()) {
            StationLine("网关", gatewayLine)
        }
    }
}

@Composable
private fun StationLine(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = label,
            style = PicoTheme.typography.bodySmall,
            color = PicoTheme.colorTokens.LabelTertiaryAlpha,
        )
        Text(
            text = value,
            style = PicoTheme.typography.bodySmall,
            color = PicoTheme.colorTokens.LabelSecondaryAlpha,
        )
    }
}
