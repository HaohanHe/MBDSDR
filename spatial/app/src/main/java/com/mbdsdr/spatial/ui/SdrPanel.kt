package com.mbdsdr.spatial.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.mbdsdr.spatial.dsp.SpectrumAnalyzer
import com.mbdsdr.spatial.net.DeviceGatewayClient
import com.mbdsdr.spatial.net.GatewayController
import com.mbdsdr.spatial.net.RtlTcpException
import com.mbdsdr.spatial.net.RtlTcpProtocol
import com.mbdsdr.spatial.sdr.DeviceGatewaySdrPort
import com.mbdsdr.spatial.sdr.SdrFormat
import com.mbdsdr.spatial.sdr.SdrSession
import com.pico.spatial.ui.design.Button
import com.pico.spatial.ui.design.PicoTheme
import com.pico.spatial.ui.design.Text
import com.pico.spatial.ui.design.TextField
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 头显直连 SDR 的最小面板（M4.2）：申请授权 → 连接 rtl_tcp → 调谐 → 画幅度谱。
 *
 * 诚实边界：
 * * 只有 `headset` 角色可用；未配对/非 headset 直接写原因，不发请求。
 * * 频率由用户输入（MHz），不在本地面板里猜「该听什么」。
 * * 画的是**按峰值归一化**的相对幅度，不是 dBm，也没有天线/滤波校准。
 * * 停止/离开窗口会关闭连接并释放授权；没有后台服务、不自动重连。
 */
@Composable
fun SdrPanel(gateway: GatewayController, modifier: Modifier = Modifier) {
    var freqText by remember { mutableStateOf("145.800") }
    var running by remember { mutableStateOf(false) }
    var status by remember {
        mutableStateOf("未连接：先在网关面板配对；点「开始接收」会申请 SDR 授权")
    }
    var spectrum by remember { mutableStateOf(FloatArray(0)) }
    var session by remember { mutableStateOf<SdrSession?>(null) }
    var job by remember { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()

    fun stop() {
        job?.cancel()
        job = null
        session?.close()
        session = null
        running = false
        spectrum = FloatArray(0)
        status = "已停止：连接已关闭，授权已释放"
    }

    DisposableEffect(Unit) {
        onDispose {
            job?.cancel()
            session?.close()
        }
    }

    fun start() {
        if (running) return
        val st = gateway.state
        if (!st.paired || st.token.isBlank()) {
            status = "未配对：先在网关面板完成配对"
            return
        }
        if (st.role != "headset") {
            status = "当前角色 ${st.role} 不能直连 SDR（仅 headset）"
            return
        }
        val port = st.portText.toIntOrNull()
        if (port == null || port !in 1..65535) {
            status = "网关端口不合法：先在上面填 1–65535"
            return
        }
        val centerHz = SdrFormat.parseCenterHz(freqText)
        if (centerHz == null) {
            status = "频率需为 MHz 数值（24–1766），例如 145.800"
            return
        }
        val sdr = SdrSession(
            DeviceGatewaySdrPort(
                DeviceGatewayClient(st.host.trim(), port)))
        running = true
        status = "申请授权并连接 rtl_tcp…"
        scope.launch {
            try {
                val info = withContext(Dispatchers.IO) {
                    sdr.start(
                        token = st.token,
                        role = st.role,
                        centerHz = centerHz,
                        sampleRateHz = SdrSession.DEFAULT_SAMPLE_RATE_HZ,
                    )
                }
                session = sdr
                status = "已连接 ${info.endpointHost}:${info.endpointPort}" +
                    " · tuner ${info.tunerType}" +
                    " · 中心 ${SdrFormat.formatCenterMhz(info.centerHz)}" +
                    " · 跨度 ${SdrFormat.formatSpanMhz(info.sampleRateHz / 2.0)}"
                job = scope.launch(Dispatchers.IO) {
                    try {
                        readSpectrumLoop(sdr) { frame -> spectrum = frame }
                    } catch (e: Exception) {
                        withContext(Dispatchers.Main) {
                            status = "接收中断：${e.message}"
                            running = false
                        }
                        sdr.close()
                    }
                }
            } catch (e: Exception) {
                sdr.close()
                status = "启动失败：${e.message}"
                running = false
            }
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
                text = "SDR 接收（头显直连）",
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
            TextField(
                value = freqText,
                onValueChange = { input ->
                    freqText = input.filter { it.isDigit() || it == '.' }.take(8)
                },
                modifier = Modifier.width(110.dp),
            )
            Text(
                text = "MHz",
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelTertiaryAlpha,
            )
            Button(
                onClick = { if (running) stop() else start() },
                enabled = if (running) session != null else true,
            ) {
                Text(
                    text = if (running) "停止" else "开始接收",
                    style = PicoTheme.typography.bodySmall,
                )
            }
        }
        Text(
            text = status,
            style = PicoTheme.typography.bodySmall,
            color = PicoTheme.colorTokens.LabelSecondaryAlpha,
        )
        if (spectrum.isNotEmpty()) {
            Text(
                text = "相对幅度谱（按峰值归一化，不是 dBm）",
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelTertiaryAlpha,
            )
        }
        LiveSpectrumCanvas(
            spectrum = spectrum,
            modifier = Modifier.fillMaxWidth().height(140.dp),
        )
    }
}

/**
 * 读满一帧 → 解 IQ8 → FFT → 回主线程更新。
 * 短读会循环补齐；取消时阻塞读由 `close()` 打断。
 */
private suspend fun readSpectrumLoop(
    session: SdrSession,
    onFrame: (FloatArray) -> Unit,
) {
    val fftSize = SdrSession.DEFAULT_FFT_SIZE
    val bytes = ByteArray(fftSize * 2)
    val iq = FloatArray(fftSize * 2)
    while (currentCoroutineContext().isActive) {
        var filled = 0
        while (filled < bytes.size) {
            val n = session.readIq(bytes, filled, bytes.size - filled)
            if (n <= 0) throw RtlTcpException("rtl_tcp 没有返回数据")
            filled += n
        }
        RtlTcpProtocol.decodeIq8(bytes, iq)
        val frame = withContext(Dispatchers.Default) {
            SpectrumAnalyzer.magnitudeSpectrum(iq, fftSize)
        }
        withContext(Dispatchers.Main) { onFrame(frame) }
    }
}
