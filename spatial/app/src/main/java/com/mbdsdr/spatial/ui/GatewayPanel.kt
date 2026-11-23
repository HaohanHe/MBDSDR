package com.mbdsdr.spatial.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.mbdsdr.spatial.net.GatewayController
import com.pico.spatial.ui.design.Button
import com.pico.spatial.ui.design.PicoTheme
import com.pico.spatial.ui.design.Text
import com.pico.spatial.ui.design.TextField
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 头显侧「多设备网关」只读面板（M4.1 UI）。
 *
 * 只在用户点按钮时发请求（后台线程），不自动轮询；未配对/未接线时
 * 状态行显示具体原因，不冒充已连接。token 只在本进程内存里。
 */
@Composable
fun GatewayPanel(controller: GatewayController, modifier: Modifier = Modifier) {
    var state by remember { mutableStateOf(controller.state) }
    val scope = rememberCoroutineScope()

    fun run(block: () -> Unit) {
        if (state.busy) return
        scope.launch {
            withContext(Dispatchers.IO) { block() }
            state = controller.state
        }
    }

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "多设备网关（只读）",
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
            TextField(
                value = state.host,
                onValueChange = {
                    controller.updateHost(it)
                    state = controller.state
                },
                modifier = Modifier.width(170.dp),
            )
            TextField(
                value = state.portText,
                onValueChange = {
                    controller.updatePortText(it)
                    state = controller.state
                },
                modifier = Modifier.width(80.dp),
            )
            TextField(
                value = state.code,
                onValueChange = {
                    controller.updateCode(it)
                    state = controller.state
                },
                modifier = Modifier.width(120.dp),
            )
            Button(
                onClick = { run { controller.pair() } },
                enabled = !state.busy,
            ) {
                Text(
                    text = if (state.paired) "重新配对" else "配对",
                    style = PicoTheme.typography.bodySmall,
                )
            }
            Button(
                onClick = { run { controller.refreshPanorama() } },
                enabled = state.paired && !state.busy,
            ) {
                Text(text = "刷新全景", style = PicoTheme.typography.bodySmall)
            }
            Button(
                onClick = { run { controller.refreshPasses() } },
                enabled = state.paired && !state.busy,
            ) {
                Text(text = "刷新过境", style = PicoTheme.typography.bodySmall)
            }
        }
        Text(
            text = state.status,
            style = PicoTheme.typography.bodySmall,
            color = PicoTheme.colorTokens.LabelSecondaryAlpha,
        )
        if (state.panoramaText.isNotBlank()) {
            Text(
                text = state.panoramaText,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
        }
        if (state.passesText.isNotBlank()) {
            Text(
                text = state.passesText,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
        }
    }
}
