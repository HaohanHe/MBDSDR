import Flutter
import UIKit
import AVFoundation

// ============================================================================
// [云未编译·真机待验] 本文件 Swift 由 B3 契约生成。云 VM 无 Xcode/iOS SDK，
// 未编译、未链接、未真机出声。待真机验证：①实时流出声 ②文件播完 onComplete
// ③进度 onPosition。注意：本工程用 Flutter 3.47 的 FlutterImplicitEngineDelegate
// 新脚手架，channel 注册点与 rootViewController 取法需按真机 Xcode 实际 API 校准。
// 未在真机验证前，UI 不得宣称"文件回放已可用"。
// ============================================================================

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    // [云未编译·真机待验]：binaryMessenger 的具体取法按本脚手架 API 校准。
    // 契约：mbdsdr/audio 通道 = AVAudioEngine 实时流 + AVAudioFile 文件回放。
    // guard let binary = engineBridge.engine?.binaryMessenger else { return }
    // AudioEngineSwift.attach(to: binary)
  }
}

// ============================================================================
// AudioEngineSwift：AVAudioEngine + AVAudioPlayerNode 实时 16-bit 流与文件回放。
// [云未编译·真机待验] 契约见 Dart 侧 audio/file_player.dart 与
// audio/platform_pcm_sink.dart。本类为契约级实现，真机接入时取消注释 attach。
// ============================================================================
/*
private final class AudioEngineSwift {
  private let engine = AVAudioEngine()
  private let player = AVAudioPlayerNode()
  private var channel: FlutterMethodChannel?

  static func attach(to messenger: FlutterBinaryMessenger) {
    let me = AudioEngineSwift()
    let ch = FlutterMethodChannel(name: "mbdsdr/audio", binaryMessenger: messenger)
    me.channel = ch
    // 输出格式：16-bit 整型、单声道 48k。
    let fmt = AVAudioFormat(commonFormat: .pcmFormatInt16,
                            sampleRate: 48000, channels: 1, interleaved: false)!
    engine.attach(player)
    engine.connect(player, to: engine.mainMixerNode, format: fmt)
    try? AVAudioSession.sharedInstance().setCategory(.playback)
    try? AVAudioSession.sharedInstance().setActive(true)

    ch.setMethodCallHandler { call, result in
      switch call.method {
      case "start": try? self.engine.start(); result(nil)
      case "dispose": self.player.stop(triggerCompletion: false); self.engine.pause(); result(nil)
      case "setVolume": self.player.volume = (call.arguments as? [String: Any])?["volume"] as? Float ?? 1.0; result(nil)
      case "write":
        // FlutterStandardTypedData -> AVAudioPCMBuffer，scheduleBuffer。
        result(nil)
      case "startFile":
        // AVAudioFile(forReading:) 解析头，player.scheduleFile(...)，
        // completion 回调里 ch.invokeMethod("onComplete", arguments: nil)。
        result(nil)
      case "pauseFile": self.player.pause(); result(nil)
      case "resumeFile": self.player.play(); result(nil)
      case "stopFile": self.player.stop(triggerCompletion: false); result(nil)
      case "filePosition":
        // 用 player.lastRenderTime 换算 positionMs/durationMs 回传。
        result(["positionMs": 0, "durationMs": 0, "playing": false])
      default: result(FlutterError(code: "NA", message: nil, details: nil))
      }
    }
  }
}
*/
