// ============================================================================
// 聊天消息模型 —— 与 OpenAI 兼容的 chat.completions 消息结构对齐
// ----------------------------------------------------------------------------
// 该文件只描述数据，不持有网络/UI 依赖；toApiJson() 直接用于请求体序列化。
// ============================================================================

/// 消息角色：system 指令、user 用户、assistant 助手、tool 工具回灌结果。
enum ChatRole {
  system,
  user,
  assistant,
  tool;

  /// 序列化为 OpenAI 兼容的 role 字符串。
  String get apiValue {
    switch (this) {
      case ChatRole.system:
        return 'system';
      case ChatRole.user:
        return 'user';
      case ChatRole.assistant:
        return 'assistant';
      case ChatRole.tool:
        return 'tool';
    }
  }
}

/// 一次工具调用的完整记录（流式分片累积后得到）。
class ToolCallRecord {
  /// OpenAI 返回的 call id，例如 call_xxx。
  final String id;

  /// 工具名，例如 set_frequency。
  final String name;

  /// 完整 arguments 的 JSON 字符串（注意：是字符串，不是 Map）。
  final String argumentsJson;

  const ToolCallRecord({
    required this.id,
    required this.name,
    required this.argumentsJson,
  });
}

/// 一条对话消息。
///
/// [streaming] 仅用于 UI 层表示 assistant 正在输出，不进入 API 请求体。
class ChatMessage {
  final ChatRole role;
  final String content;
  final DateTime time;

  /// role == tool 时必填：对应 assistant 消息里 tool_calls[].id。
  final String? toolCallId;

  /// role == assistant 且模型要求调工具时携带。
  final List<ToolCallRecord>? toolCalls;

  /// UI 层标记：assistant 气泡是否仍在流式接收。
  final bool streaming;

  ChatMessage({
    required this.role,
    required this.content,
    required this.time,
    this.toolCallId,
    this.toolCalls,
    this.streaming = false,
  });

  /// 序列化为 OpenAI / 硅基流动兼容的消息 JSON。
  Map<String, dynamic> toApiJson() {
    switch (role) {
      case ChatRole.system:
      case ChatRole.user:
        return <String, dynamic>{
          'role': role.apiValue,
          'content': content,
        };

      case ChatRole.assistant:
        final List<ToolCallRecord>? calls = toolCalls;
        if (calls != null && calls.isNotEmpty) {
          return <String, dynamic>{
            'role': role.apiValue,
            'content': content,
            'tool_calls': calls
                .map(
                  (ToolCallRecord t) => <String, dynamic>{
                    'id': t.id,
                    'type': 'function',
                    'function': <String, dynamic>{
                      'name': t.name,
                      'arguments': t.argumentsJson,
                    },
                  },
                )
                .toList(),
          };
        }
        return <String, dynamic>{
          'role': role.apiValue,
          'content': content,
        };

      case ChatRole.tool:
        return <String, dynamic>{
          'role': role.apiValue,
          'tool_call_id': toolCallId,
          'content': content,
        };
    }
  }
}
