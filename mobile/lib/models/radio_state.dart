// 收音机侧的共享状态枚举。
//
// 只放与硬件无关的纯枚举/值类型；具体可变状态在 RadioController 里维护。
library;

/// rtl_tcp 连接状态机。
enum ConnectionStatus {
  /// 未连接（初始 / 主动断开后）。
  disconnected,

  /// 正在建立 Socket 连接。
  connecting,

  /// 已连接并开始接收 IQ 流。
  connected,

  /// 曾连接过，但 IQ 流异常或对端断开，正在按指数退避自动重连。
  reconnecting,

  /// 连接或传输出错（详见 errorMessage）。
  error,
}

/// 解调模式。
enum DemodMode {
  /// 窄带调频（对讲机/业余段，12.5kHz 信道）。
  nfm,

  /// 宽带调频（广播 FM，200kHz 信道）。
  wfm;

  /// 面向 UI 的短标签。
  String get label => switch (this) {
    DemodMode.nfm => 'NFM',
    DemodMode.wfm => 'WFM',
  };
}

/// 范围扫描方向（对齐桌面 cpp/src/dsp/frequency_scanner.h `ScanDirection`）。
///
/// 桌面另有 PingPong（来回）；移动端本期先对齐任务要求的上行/下行两档，
/// 不硬造来回回绕。Up=从 startHz 向 endHz 递增；Down=从 endHz 向 startHz 递减。
enum ScanDirection {
  /// 从 startHz 向 endHz 递增扫。
  up,

  /// 从 endHz 向 startHz 递减扫。
  down;

  /// 面向 UI 的短标签。
  String get label => switch (this) {
    ScanDirection.up => '上行',
    ScanDirection.down => '下行',
  };
}
