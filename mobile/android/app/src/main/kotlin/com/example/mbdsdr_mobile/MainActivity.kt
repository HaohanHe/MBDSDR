package com.example.mbdsdr_mobile

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbManager
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Build
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import kotlin.concurrent.thread

// ============================================================================
// [云未编译·真机待验] 本文件 Kotlin 由 B3 契约（docs/learn/phase4/audits/B3-mobile-native.md）
// 生成。云 VM 无 Android SDK / 无设备，未编译、未链接、未真机运行。待真机验证：
//   ① 实时收听：start/write → AudioTrack 出声；
//   ② 文件回放：startFile → 从 .wav PCM 块入队、onComplete/onPosition 回调、进度准确；
//   ③ USB GNSS：枚举 → 申请权限 → open → onData 吐出 NMEA 字节。
// 未在真机验证前，UI 不得宣称"实时收听/文件回放/USB-GNSS 已可用"。
// ============================================================================

class MainActivity : FlutterActivity() {

    private val AUDIO_CHANNEL = "mbdsdr/audio"
    private val USB_SERIAL_CHANNEL = "mbdsdr/usb_serial"
    companion object {
        const val ACTION_USB_PERMISSION = "com.example.mbdsdr_mobile.USB_PERMISSION"
    }

    private lateinit var audioChannel: MethodChannel
    private lateinit var usbChannel: MethodChannel
    private lateinit var audioEngine: AudioEngine
    private var usb: UsbSerialHost? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        audioEngine = AudioEngine()

        // ---- mbdsdr/audio：实时流式出声 + 文件回放 ----
        audioChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, AUDIO_CHANNEL)
        audioChannel.setMethodCallHandler { call, result ->
            when (call.method) {
                // 实时流契约（见 platform_pcm_sink.dart）：48k/mono/16bit 小端。
                "start" -> {
                    val sr = call.argument<Int>("sampleRate") ?: 48000
                    val ch = call.argument<Int>("channels") ?: 1
                    audioEngine.startStream(sr, ch)
                    result.success(null)
                }
                "write" -> {
                    val pcm = call.argument<ByteArray>("pcm")
                    if (pcm != null) audioEngine.writeStream(pcm)
                    result.success(null)
                }
                "setVolume" -> {
                    val v = (call.argument<Double>("volume") ?: 1.0).toFloat()
                    audioEngine.setVolume(v)
                    result.success(null)
                }
                "setMuted" -> {
                    val m = call.argument<Boolean>("muted") ?: false
                    audioEngine.setMuted(m)
                    result.success(null)
                }
                "dispose" -> {
                    audioEngine.stopStream()
                    result.success(null)
                }
                // ---- 文件回放契约（见 audio/file_player.dart）----
                "startFile" -> {
                    val path = call.argument<String>("path")
                    val sr = call.argument<Int>("sampleRate") ?: 48000
                    val ch = call.argument<Int>("channels") ?: 1
                    if (path != null) audioEngine.startFile(path, sr, ch, audioChannel)
                    result.success(null)
                }
                "pauseFile" -> { audioEngine.pauseFile(); result.success(null) }
                "resumeFile" -> { audioEngine.resumeFile(); result.success(null) }
                "stopFile" -> { audioEngine.stopFile(); result.success(null) }
                "filePosition" -> result.success(audioEngine.filePosition())
                else -> result.notImplemented()
            }
        }

        // ---- mbdsdr/usb_serial：USB host 读 NMEA ----
        usb = UsbSerialHost(this)
        usbChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, USB_SERIAL_CHANNEL)
        usbChannel.setMethodCallHandler { call, result ->
            when (call.method) {
                "listDevices" -> result.success(usb!!.listDevices())
                "open" -> {
                    val id = call.argument<Int>("deviceId") ?: -1
                    val baud = call.argument<Int>("baud") ?: 9600
                    result.success(usb!!.open(id, baud, usbChannel))
                }
                "close" -> { usb!!.close(); result.success(null) }
                else -> result.notImplemented()
            }
        }
    }
}

// ============================================================================
// AudioEngine：实时流 AudioTrack + 文件回放（第二个 AudioTrack MODE_STREAM 读 WAV）。
// [云未编译·真机待验]
// ============================================================================
private class AudioEngine {
    private var streamTrack: AudioTrack? = null
    private var fileTrack: AudioTrack? = null
    private var fileThread: Thread? = null
    @Volatile private var fileRunning = false
    @Volatile private var filePaused = false
    private var fileBytesWritten = 0L
    private var fileTotalBytes = 0L
    private var fileSampleRate = 48000

    private fun makeTrack(sr: Int, ch: Int): AudioTrack {
        val channelMask = if (ch >= 2)
            AudioFormat.CHANNEL_OUT_STEREO else AudioFormat.CHANNEL_OUT_MONO
        val minBuf = AudioTrack.getMinBufferSize(sr, channelMask, AudioFormat.ENCODING_PCM_16BIT)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            AudioTrack(
                AudioManager.STREAM_MUSIC, sr, channelMask,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf * 2, 8192), AudioTrack.MODE_STREAM
            )
        } else {
            @Suppress("DEPRECATION")
            AudioTrack(
                AudioManager.STREAM_MUSIC, sr, channelMask,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf * 2, 8192), AudioTrack.MODE_STREAM
            )
        }
    }

    fun startStream(sr: Int, ch: Int) {
        stopStream()
        streamTrack = makeTrack(sr, ch).also { it.play() }
    }

    fun writeStream(pcm: ByteArray) {
        val t = streamTrack ?: return
        t.write(pcm, 0, pcm.size) // WRITE_BLOCKING 等价
    }

    fun setVolume(v: Float) { streamTrack?.setVolume(v) }
    fun setMuted(m: Boolean) { streamTrack?.setVolume(if (m) 0f else 1f) }

    fun stopStream() {
        streamTrack?.let { try { it.stop(); it.release() } catch (_: Throwable) {} }
        streamTrack = null
    }

    // ---- 文件回放：读 WAV 头定位 PCM 数据区，开线程按块 write 进第二个 AudioTrack ----
    fun startFile(path: String, sr: Int, ch: Int, channel: MethodChannel) {
        stopFile()
        val f = java.io.RandomAccessFile(path, "r")
        // 解析 RIFF/WAVE 头：找到 "data" 块偏移与长度（契约见 wav_writer.dart）。
        var dataOffset = 44L
        var dataLength = 0L
        var magic = ByteArray(4); f.read(magic)
        if (String(magic) == "RIFF") {
            f.seek(12)
            var hdr = ByteArray(4)
            while (f.filePointer < f.length()) {
                f.read(hdr)
                val size = f.readIntLe()
                if (String(hdr) == "data") { dataLength = size.toLong(); break }
                f.seek(f.filePointer + size + (size and 1)) // 块对齐 16bit
            }
            dataOffset = f.filePointer
        }
        fileSampleRate = sr
        fileTotalBytes = dataLength
        fileBytesWritten = 0
        fileTrack = makeTrack(sr, ch).also { it.play() }
        fileRunning = true
        filePaused = false
        fileThread = thread(start = true) {
            val buf = ByteArray(8192)
            f.seek(dataOffset)
            var remaining = dataLength
            while (fileRunning && remaining > 0) {
                if (filePaused) { Thread.sleep(20); continue }
                val n = minOf(buf.size, remaining.toInt())
                val r = f.read(buf, 0, n)
                if (r <= 0) break
                fileTrack?.write(buf, 0, r)
                fileBytesWritten += r
                remaining -= r
                channel.invokeMethod("onPosition", mapOf(
                    "positionMs" to ((fileBytesWritten / 2.0 / fileSampleRate) * 1000).toInt(),
                    "durationMs" to ((fileTotalBytes / 2.0 / fileSampleRate) * 1000).toInt(),
                    "playing" to true
                ))
            }
            if (fileRunning) channel.invokeMethod("onComplete", null)
            f.close()
        }
    }

    fun pauseFile() { filePaused = true }
    fun resumeFile() { filePaused = false }

    fun stopFile() {
        fileRunning = false
        fileThread?.interrupt(); fileThread = null
        fileTrack?.let { try { it.stop(); it.release() } catch (_: Throwable) {} }
        fileTrack = null
        fileBytesWritten = 0
        fileTotalBytes = 0
    }

    fun filePosition(): Map<String, Any> = mapOf(
        "positionMs" to ((fileBytesWritten / 2.0 / fileSampleRate) * 1000).toInt(),
        "durationMs" to ((fileTotalBytes / 2.0 / fileSampleRate) * 1000).toInt(),
        "playing" to (fileTrack != null && fileRunning && !filePaused)
    )
}

private fun java.io.RandomAccessFile.readIntLe(): Int {
    val b = ByteArray(4); read(b)
    return (b[0].toInt() and 0xff) or
            ((b[1].toInt() and 0xff) shl 8) or
            ((b[2].toInt() and 0xff) shl 16) or
            ((b[3].toInt() and 0xff) shl 24)
}

// ============================================================================
// UsbSerialHost：Android USB host API（枚举 → 权限 → bulk 读循环 → onData）。
// [云未编译·真机待验] 注：CP2102/CH340/FTDI 各自的波特率初始化 controlTransfer
// 序列在此为骨架占位，真机需按具体 USB-TTL 芯片补全（见 B3 方案 C）。
// ============================================================================
private class UsbSerialHost(val act: MainActivity) {
    private var conn: UsbDeviceConnection? = null
    private var readThread: Thread? = null
    @Volatile private var reading = false

    fun listDevices(): List<Map<String, Any>> {
        val usbMgr = act.getSystemService(Context.USB_SERVICE) as UsbManager
        return usbMgr.deviceList.values.map { d ->
            mapOf(
                "deviceId" to d.deviceId,
                "productName" to (d.productName ?: ""),
                "manufacturerName" to (d.manufacturerName ?: ""),
                "vidPid" to String.format("%04X:%04X", d.vendorId, d.productId)
            )
        }
    }

    fun open(deviceId: Int, baud: Int, channel: MethodChannel): Boolean {
        val usbMgr = act.getSystemService(Context.USB_SERVICE) as UsbManager
        val dev: UsbDevice = usbMgr.deviceList.values.firstOrNull { it.deviceId == deviceId }
            ?: return false
        if (!usbMgr.hasPermission(dev)) {
            val pi = PendingIntent.getBroadcast(
                act, 0, Intent(MainActivity.ACTION_USB_PERMISSION),
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
            )
            usbMgr.requestPermission(dev, pi)
            return false // 权限回调后由用户重试 open
        }
        conn = usbMgr.openDevice(dev) ?: return false
        // TODO(真机待验): 按芯片 vid:pid 发 controlTransfer 设 baud（CP2102/CH340/FTDI）。
        reading = true
        readThread = thread(start = true) {
            val endpoint = dev.getInterface(0).getEndpoint(0)
            val buf = ByteArray(64)
            while (reading) {
                val n = conn?.bulkTransfer(endpoint, buf, buf.size, 200) ?: -1
                if (n > 0) channel.invokeMethod("onData", mapOf("bytes" to buf.copyOf(n)))
            }
        }
        return true
    }

    fun close() {
        reading = false
        readThread?.interrupt(); readThread = null
        try { conn?.close() } catch (_: Throwable) {}
        conn = null
    }
}
