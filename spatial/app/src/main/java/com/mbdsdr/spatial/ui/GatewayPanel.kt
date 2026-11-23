package com.mbdsdr.spatial.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import com.mbdsdr.spatial.net.GatewayUiState
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
 * [onChanged] 把最新状态交给进程级共享，供「此刻」窗口只读转述（M4.1 收尾）。
 */
@Composable
fun GatewayPanel(
    controller: GatewayController,
    modifier: Modifier = Modifier,
    onChanged: (GatewayUiState) -> Unit = {},
) {
    var state by remember { mutableStateOf(controller.state) }
    val scope = rememberCoroutineScope()

    fun publish() {
        state = controller.state
        onChanged(controller.state)
    }

    fun run(block: () -> Unit) {
        if (state.busy) return
        scope.launch {
            withContext(Dispatchers.IO) { block() }
            publish()
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
                    publish()
                },
                modifier = Modifier.width(170.dp),
            )
            TextField(
                value = state.portText,
                onValueChange = {
                    controller.updatePortText(it)
                    publish()
                },
                modifier = Modifier.width(80.dp),
            )
            TextField(
                value = state.code,
                onValueChange = {
                    controller.updateCode(it)
                    publish()
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
        }
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
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
            Button(
                onClick = { run { controller.refreshFuse() } },
                enabled = state.paired && !state.busy,
            ) {
                Text(text = "刷新融合", style = PicoTheme.typography.bodySmall)
            }
            Button(
                onClick = { run { controller.refreshSdrStatus() } },
                enabled = state.paired && !state.busy,
            ) {
                Text(text = "SDR 状态", style = PicoTheme.typography.bodySmall)
            }
            Button(
                onClick = { run { controller.refreshSpectrum() } },
                enabled = state.paired && !state.busy,
            ) {
                Text(text = "频谱快照", style = PicoTheme.typography.bodySmall)
            }
            Button(
                onClick = { run { controller.refreshStatus() } },
                enabled = state.paired && !state.busy,
            ) {
                Text(text = "桌面状态", style = PicoTheme.typography.bodySmall)
            }
            Button(
                onClick = { run { controller.requestSdrLease() } },
                enabled = state.paired && !state.busy && state.role == "headset",
            ) {
                Text(text = "申请 SDR", style = PicoTheme.typography.bodySmall)
            }
            Button(
                onClick = { run { controller.releaseSdrLease() } },
                enabled = state.paired && !state.busy && state.sdrHeld,
            ) {
                Text(text = "释放 SDR", style = PicoTheme.typography.bodySmall)
            }
        }
        Text(
            text = state.status,
            style = PicoTheme.typography.bodySmall,
            color = PicoTheme.colorTokens.LabelSecondaryAlpha,
        )
        if (state.panoramaText.isNotBlank()) {
            Text(
                text = staleTag(state.panoramaStale) + state.panoramaText,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
        }
        if (state.passesText.isNotBlank()) {
            Text(
                text = staleTag(state.passesStale) + state.passesText,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
        }
        if (state.fuseText.isNotBlank()) {
            Text(
                text = state.fuseText,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
        }
        if (state.statusText.isNotBlank()) {
            Text(
                text = state.statusText,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
        }
        if (state.spectrumText.isNotBlank()) {
            Text(
                text = state.spectrumText,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
        }
        if (state.spectrumLevels.isNotEmpty()) {
            // 复用实时谱画布：只画桌面快照一帧，不随时间滚动/刷新。
            LiveSpectrumCanvas(
                spectrum = state.spectrumLevels.toFloatArray(),
                modifier = Modifier.fillMaxWidth().height(96.dp),
            )
        }
        if (state.sdrText.isNotBlank()) {
            Text(
                text = state.sdrText,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
        }
    }
}

/** 旧摘要前缀：断网时如实标注，不让人误以为是刚取的。 */
private fun staleTag(stale: Boolean): String =
    if (stale) "（未联网·上次结果）" else ""
