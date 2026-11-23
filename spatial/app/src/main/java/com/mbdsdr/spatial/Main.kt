package com.mbdsdr.spatial

import android.annotation.SuppressLint
import android.app.Application
import android.os.Bundle
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.mbdsdr.spatial.audio.MediaPlayerAudioPlayback
import com.mbdsdr.spatial.data.AudioFormat
import com.mbdsdr.spatial.data.BundleStore
import com.mbdsdr.spatial.data.EventRow
import com.mbdsdr.spatial.data.PassFormat
import com.mbdsdr.spatial.data.SkyFormat
import com.mbdsdr.spatial.data.SpatialBundle
import com.mbdsdr.spatial.data.SpectrumFormat
import com.mbdsdr.spatial.data.StationFormat
import com.mbdsdr.spatial.data.Star
import com.mbdsdr.spatial.data.TimelineFormat
import com.mbdsdr.spatial.data.visibleStars
import com.mbdsdr.spatial.net.GatewayController
import com.mbdsdr.spatial.net.GatewayFormat
import com.mbdsdr.spatial.net.GatewayUiState
import com.mbdsdr.spatial.sky.Selection
import com.mbdsdr.spatial.sky.SkyCanvas
import com.mbdsdr.spatial.sky.SkyRender
import com.mbdsdr.spatial.sky.SpectrumCanvas
import com.mbdsdr.spatial.ui.ArchivePanel
import com.mbdsdr.spatial.ui.GatewayPanel
import com.mbdsdr.spatial.ui.LayoutOpKind
import com.mbdsdr.spatial.ui.PassesPanel
import com.mbdsdr.spatial.ui.SdrPanel
import com.mbdsdr.spatial.ui.StationPanel
import com.mbdsdr.spatial.ui.WindowLayout
import com.pico.spatial.ui.design.Button
import com.pico.spatial.ui.design.PicoTheme
import com.pico.spatial.ui.design.Text
import com.pico.spatial.ui.foundation.dsl.DefaultWindowContainer
import com.pico.spatial.ui.foundation.dsl.Form
import com.pico.spatial.ui.foundation.dsl.SpatialAppScope
import com.pico.spatial.ui.foundation.dsl.WindowContainer
import com.pico.spatial.ui.foundation.dsl.WindowContainerSize
import com.pico.spatial.ui.foundation.dsl.launch
import com.pico.spatial.ui.platform.containers.LocalSpatialNavigator
import com.pico.spatial.ui.platform.containers.SpatialNavigator
import com.pico.spatial.ui.platform.resize.ContainerResizeType
import com.pico.spatial.ui.platform.stub.SpatialLaunchActivity
import java.util.Locale
import kotlinx.coroutines.delay

/** 子窗口的尺寸/锚点/方位只从 [WindowLayout] 取，不在注册处另写一份。 */
private val SPECTRUM_SPEC = WindowLayout.companion("spectrum")!!
private val ARCHIVE_SPEC = WindowLayout.companion("archive")!!
private val STATION_SPEC = WindowLayout.companion("station")!!

/**
 * 应用级共享状态：一个 WindowContainer 对应一个独立 Activity，
 * remember 的局部状态无法跨窗口，所以观测包放在进程级状态里。
 * 只有「头顶」窗口写入 bundle，其余三个窗口只读。
 */
private class ObservationState {
    val store = BundleStore()
    var bundle by mutableStateOf<SpatialBundle?>(null)
    var error by mutableStateOf<String?>(null)
    var playing by mutableStateOf(false)
    var audioError by mutableStateOf<String?>(null)
    /** 「回看」请求「听见」跳到的秒数；消费后置回 null，避免重复触发。 */
    var requestedAtS by mutableStateOf<Double?>(null)
    /** 「回看」的一条结果提示：跳转成功或不能跳的原因。 */
    var jumpNote by mutableStateOf<String?>(null)
    /** 头顶选中的星；回看只据此高亮 detail 里确有同名目标的事件。 */
    var selectedStar by mutableStateOf<Star?>(null)
    /**
     * 多设备网关（M4.1）：控制器与快照放进程级，跨窗口共享。
     * token 仍只在本进程内存；「此刻」只读转述全景，不覆盖 bundle 的站址/时刻。
     */
    val gateway = GatewayController()
    var gatewaySnapshot by mutableStateOf(GatewayUiState())
    val player = MediaPlayerAudioPlayback()
}

private val appState = ObservationState()

/**
 * Spatial 应用入口：默认四窗——主窗口「头顶」，子窗口「听见」「回看」「此刻」。
 *
 * 多窗口依据（BOM 6.2.8）：
 * - 样本 SpatialAudio/Main.kt:49-61 用一份 WindowContainer(id, form, defaultSize, ...) 注册额外窗口；
 * - 样本 WelcomeSpace/Main.kt:36-59 同时使用 DefaultWindowContainer 与 WindowContainer；
 * - 关联网页样本 README.md:31 说明子窗口是独立可交互窗口。
 * WindowContainer 的 content 由独立 Activity 承载，打开需要一个 tag，
 * 见样本 SpatialAudio/ui/ImmersiveScene.kt:42-52 的 openWindowContainer(id, tag)。
 */
fun mainApp(scope: SpatialAppScope) =
    with(scope) {
        val state = appState

        DefaultWindowContainer {
            PicoTheme {
                SkyWindow(
                    state = state,
                    modifier = Modifier.windowConstraints(
                        minWidth = WindowLayout.SKY_MIN_WIDTH_DP.dp,
                        minHeight = WindowLayout.SKY_MIN_HEIGHT_DP.dp,
                    ),
                )
            }
        }

        WindowContainer(
            id = SPECTRUM_SPEC.id,
            form = Form.Planar,
            defaultSize = WindowContainerSize(SPECTRUM_SPEC.widthDp.dp, SPECTRUM_SPEC.heightDp.dp),
            resizeType = ContainerResizeType.ContentSize,
            enableMaterialBackground = true,
            placement = { WindowLayout.placementFor(SPECTRUM_SPEC, containers) },
        ) {
            PicoTheme {
                SpectrumWindow(
                    state = state,
                    modifier = Modifier.windowConstraints(
                        width = SPECTRUM_SPEC.widthDp.dp,
                        height = SPECTRUM_SPEC.heightDp.dp,
                    ),
                )
            }
        }

        WindowContainer(
            id = ARCHIVE_SPEC.id,
            form = Form.Planar,
            defaultSize = WindowContainerSize(ARCHIVE_SPEC.widthDp.dp, ARCHIVE_SPEC.heightDp.dp),
            resizeType = ContainerResizeType.ContentSize,
            enableMaterialBackground = true,
            placement = { WindowLayout.placementFor(ARCHIVE_SPEC, containers) },
        ) {
            PicoTheme {
                ArchiveWindow(
                    state = state,
                    modifier = Modifier.windowConstraints(
                        width = ARCHIVE_SPEC.widthDp.dp,
                        height = ARCHIVE_SPEC.heightDp.dp,
                    ),
                )
            }
        }

        WindowContainer(
            id = STATION_SPEC.id,
            form = Form.Planar,
            defaultSize = WindowContainerSize(STATION_SPEC.widthDp.dp, STATION_SPEC.heightDp.dp),
            resizeType = ContainerResizeType.ContentSize,
            enableMaterialBackground = true,
            placement = { WindowLayout.placementFor(STATION_SPEC, containers) },
        ) {
            PicoTheme {
                StationWindow(
                    state = state,
                    modifier = Modifier.windowConstraints(
                        width = STATION_SPEC.widthDp.dp,
                        height = STATION_SPEC.heightDp.dp,
                    ),
                )
            }
        }
    }

/** 打开三个伴随窗口；样本里也是在主窗口内容中调用 openWindowContainer。 */
private fun openSiblingWindows(navigator: SpatialNavigator) {
    for (op in WindowLayout.resetPlan()) {
        if (op.kind == LayoutOpKind.OPEN) {
            navigator.openWindowContainer(op.spec.id, op.spec.tag, Bundle())
        }
    }
}

/**
 * 默认布局复位：关闭三个伴随窗口后按注册的 `placement` 重新打开。
 * 不关闭主窗口「头顶」，也不改 `ObservationState` 里的观测包与选中星。
 */
private fun resetWindowLayout(navigator: SpatialNavigator) {
    for (op in WindowLayout.resetPlan()) {
        when (op.kind) {
            LayoutOpKind.CLOSE -> navigator.closeWindowContainer(op.spec.id, op.spec.tag)
            LayoutOpKind.OPEN -> navigator.openWindowContainer(op.spec.id, op.spec.tag, Bundle())
        }
    }
}

/** 主窗口「头顶」：加载与播放入口放在这里，星图画布占据剩余空间。 */
@Composable
private fun SkyWindow(state: ObservationState, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val navigator = LocalSpatialNavigator.current
    LaunchedEffect(Unit) { openSiblingWindows(navigator) }
    DisposableEffect(Unit) {
        onDispose { state.player.stop() }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            val text = context.contentResolver.openInputStream(uri)
                ?.bufferedReader()
                ?.use { it.readText() }
            state.player.stop()
            state.playing = false
            state.audioError = null
            // 旧包的跳转请求、提示与选中星不能留到新包上。
            state.requestedAtS = null
            state.jumpNote = null
            state.selectedStar = null
            if (state.store.load(text ?: "")) {
                state.bundle = state.store.bundle
                state.error = null
            } else {
                state.bundle = null
                state.error = state.store.lastError
            }
        }
    }

    val bundle = state.bundle
    val audio = AudioFormat.first(bundle?.audio ?: emptyList())
    val skyStars = bundle?.visibleStars() ?: emptyList()

    Column(
        modifier = modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        horizontalAlignment = Alignment.Start,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(text = "头顶", style = PicoTheme.typography.bodyLarge)
            Button(onClick = { picker.launch(arrayOf("application/json")) }) {
                Text(text = "加载观测包", style = PicoTheme.typography.bodySmall)
            }
            Button(onClick = { resetWindowLayout(navigator) }) {
                Text(text = "复位布局", style = PicoTheme.typography.bodySmall)
            }
            Text(
                text = if (bundle == null) "还没有加载观测包" else state.store.description(),
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelTertiaryAlpha,
            )
        }
        state.error?.let {
            Text(
                text = it,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorScheme.error,
            )
        }
        GatewayPanel(
            controller = state.gateway,
            onChanged = { state.gatewaySnapshot = it },
        )
        SdrPanel(state.gateway)
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                text = AudioFormat.describe(audio),
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
            Button(
                onClick = {
                    if (audio == null) return@Button
                    if (state.playing) {
                        state.player.stop()
                        state.playing = false
                        state.audioError = null
                    } else if (state.player.play(audio.path)) {
                        state.playing = true
                        state.audioError = null
                    } else {
                        state.playing = false
                        state.audioError = "播放失败：文件不可读或格式不支持"
                    }
                },
                enabled = audio != null,
            ) {
                Text(
                    text = AudioFormat.buttonText(audio, state.playing),
                    style = PicoTheme.typography.bodySmall,
                )
            }
        }
        state.audioError?.let {
            Text(
                text = it,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorScheme.error,
            )
        }

        BoxWithConstraints(modifier = Modifier.fillMaxWidth().weight(1f)) {
            val widthPx = constraints.maxWidth
            val heightPx = constraints.maxHeight
            SkyCanvas(
                stars = skyStars,
                totalStars = bundle?.sky?.totalStars ?: 0,
                truncated = bundle?.sky?.truncated ?: false,
                selected = state.selectedStar,
                modifier = Modifier.fillMaxSize(),
                loaded = bundle != null,
                emptyHint = SkyFormat.emptyHint(bundle),
                onTap = { x, y ->
                    val dots = SkyRender.plan(skyStars, widthPx, heightPx)
                    state.selectedStar = Selection.pick(dots, skyStars, x, y)?.star
                },
            )
        }
        Text(
            text = selectionText(state.selectedStar),
            style = PicoTheme.typography.bodySmall,
            color = PicoTheme.colorTokens.LabelSecondaryAlpha,
        )
        PassesPanel(
            rows = PassFormat.rows(bundle?.sky?.passes ?: emptyList()),
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

/** 子窗口「听见」：只读回放共享 bundle 的频谱帧，进度与录制时刻分两处显示。 */
@Composable
private fun SpectrumWindow(state: ObservationState, modifier: Modifier = Modifier) {
    val bundle = state.bundle
    // 加载新包会得到新实例：借此把进度复位到 0 并暂停。
    val loadKey = System.identityHashCode(bundle)
    var elapsed by remember(loadKey) { mutableStateOf(0.0) }
    var playing by remember(loadKey) { mutableStateOf(false) }

    val spectrum = bundle?.spectrum
    val duration = spectrum?.let { SpectrumFormat.durationS(it) }
    val info = bundle?.let { SpectrumFormat.info(it, elapsed) }
    val playable = duration != null && duration > 0.0 && (spectrum?.frames?.size ?: 0) >= 2

    LaunchedEffect(playing, loadKey) {
        if (!playing) return@LaunchedEffect
        val total = duration ?: return@LaunchedEffect
        while (elapsed < total) {
            delay(100)
            elapsed = SpectrumFormat.advance(elapsed, 0.1, total)
        }
        playing = false
    }
    // 「回看」请求跳转：暂停播放、钳到可回放范围；消费后清空请求。
    LaunchedEffect(state.requestedAtS) {
        val requested = state.requestedAtS ?: return@LaunchedEffect
        val total = duration
        if (total == null) {
            state.requestedAtS = null
            return@LaunchedEffect
        }
        playing = false
        elapsed = SpectrumFormat.seek(requested, total)
        state.requestedAtS = null
    }
    DisposableEffect(Unit) {
        onDispose { playing = false }
    }

    Column(
        modifier = modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(text = "听见", style = PicoTheme.typography.bodyLarge)
        if (info != null) {
            Text(
                text = "${info.recordedAt} · 中心 ${info.centerFreq} · 采样 ${info.sampleRate} · ${info.source}",
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelSecondaryAlpha,
            )
            Text(
                text = info.event,
                style = PicoTheme.typography.bodySmall,
                color = PicoTheme.colorTokens.LabelTertiaryAlpha,
            )
        }
        SpectrumCanvas(
            frames = spectrum?.frames ?: emptyList(),
            elapsedS = elapsed,
            progress = info?.progress,
            tooShort = info?.tooShort ?: false,
            loaded = bundle != null,
            emptyHint = SpectrumFormat.emptyHint(bundle),
            modifier = Modifier.fillMaxWidth().weight(1f),
        )
        Button(
            onClick = {
                if (!playable) return@Button
                if (playing) {
                    playing = false
                } else {
                    if (elapsed >= (duration ?: 0.0)) elapsed = 0.0
                    playing = true
                }
            },
            enabled = playable,
        ) {
            Text(
                text = if (playing) "暂停" else "播放回放",
                style = PicoTheme.typography.bodySmall,
            )
        }
    }
}

/** 子窗口「回看」：事件列表；点一行请求「听见」跳到该时刻。 */
@Composable
private fun ArchiveWindow(state: ObservationState, modifier: Modifier = Modifier) {
    val bundle = state.bundle
    val events = bundle?.timeline ?: emptyList()
    val sampleRateHz = bundle?.spectrum?.sampleRateHz
    // 换包后旧包的行下标不能继续高亮（行对象来自旧 timeline）。
    val loadKey = System.identityHashCode(bundle)
    var selectedEvent by remember(loadKey) { mutableStateOf<EventRow?>(null) }
    ArchivePanel(
        rows = TimelineFormat.rows(
            events = events,
            totalSamples = bundle?.spectrum?.totalSamples,
            sampleRateHz = sampleRateHz,
        ),
        selectedIndex = selectedEvent?.index,
        jumpNote = state.jumpNote,
        loaded = bundle != null,
        relatedIndices = TimelineFormat.relatedIndices(events, state.selectedStar?.name),
        onSelect = { row ->
            val event = events[row.index]
            val decision = TimelineFormat.jumpDecision(
                event = event,
                totalSamples = bundle?.spectrum?.totalSamples,
                sampleRateHz = sampleRateHz,
                durationS = bundle?.spectrum?.let { SpectrumFormat.durationS(it) },
            )
            if (decision.canJump && decision.atS != null) {
                selectedEvent = row
                state.requestedAtS = decision.atS
            }
            state.jumpNote = decision.note
        },
        modifier = modifier.fillMaxSize().padding(24.dp),
    )
}

/** 子窗口「此刻」：站址 / 时间 / 证据 / 阻塞；网关全景只占一行转述。 */
@Composable
private fun StationWindow(state: ObservationState, modifier: Modifier = Modifier) {
    StationPanel(
        lines = StationFormat.lines(state.bundle),
        gatewayLine = GatewayFormat.stationLine(state.gatewaySnapshot),
        modifier = modifier.fillMaxSize().padding(24.dp),
    )
}

/** 选中星的一行说明；未选中时是一句提示，不补默认星。 */
private fun selectionText(star: Star?): String {
    if (star == null) return "点一颗星看它是什么"
    val name = star.name ?: "未命名"
    val mag = star.mag?.let { String.format(Locale.US, "%.1f 等", it) } ?: "星等 —"
    val alt = star.altDeg?.let { String.format(Locale.US, "%.1f°", it) } ?: "—"
    val az = star.azDeg?.let { String.format(Locale.US, "%.1f°", it) } ?: "—"
    return "$name · $mag · alt $alt / az $az"
}

class SpatialApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        launch(::mainApp)
    }
}

@SuppressLint("CustomSplashScreen") class LaunchActivity : SpatialLaunchActivity()
