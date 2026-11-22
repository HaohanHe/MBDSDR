package com.mbdsdr.spatial.audio

import android.media.MediaPlayer

/** 播放接口：便于在 UI 层替换实现，真机/单测都不依赖具体播放器。 */
interface AudioPlayback {
    /** @return 开始播放返回 true；路径不可读或格式不支持返回 false，不抛异常。 */
    fun play(path: String): Boolean

    fun stop()

    fun isPlaying(): Boolean
}

/**
 * 用 [MediaPlayer] 播放包内录音。
 *
 * 失败口径统一为 false：任何构造/设置数据源/准备/启动异常都释放实例并返回 false，
 * 由界面显示「播放失败」而不是让应用崩溃。
 */
class MediaPlayerAudioPlayback : AudioPlayback {
    private var player: MediaPlayer? = null

    override fun play(path: String): Boolean {
        stop()
        val created = MediaPlayer()
        return try {
            created.setDataSource(path)
            created.prepare()
            created.start()
            player = created
            true
        } catch (e: Exception) {
            runCatching { created.reset() }
            runCatching { created.release() }
            false
        }
    }

    override fun stop() {
        val current = player ?: return
        player = null
        runCatching { current.reset() }
        runCatching { current.release() }
    }

    override fun isPlaying(): Boolean =
        try {
            player?.isPlaying == true
        } catch (e: Exception) {
            false
        }
}
