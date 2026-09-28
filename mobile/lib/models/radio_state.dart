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
