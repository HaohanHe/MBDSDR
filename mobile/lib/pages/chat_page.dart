import 'dart:async';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../connection.dart';
import '../theme.dart';

// ---------------------------------------------------------------------------
// 消息模型
// ---------------------------------------------------------------------------
class ChatMessage {
  final String text;
  final bool isUser;
  final DateTime time;

  ChatMessage({
    required this.text,
    required this.isUser,
    required this.time,
  });
}

// ---------------------------------------------------------------------------
// AI 对话页面
// ---------------------------------------------------------------------------
class ChatPage extends StatefulWidget {
  final ConnectionService connection;

  const ChatPage({super.key, required this.connection});

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final List<ChatMessage> _messages = <ChatMessage>[];
  final TextEditingController _inputCtrl = TextEditingController();
  final ScrollController _scrollCtrl = ScrollController();
  late final StreamSubscription<String> _aiSub;

  static final DateFormat _timeFmt = DateFormat('HH:mm');

  @override
  void initState() {
    super.initState();
    _aiSub = widget.connection.aiCommandStream.listen(_onAiReply);
  }

  @override
  void dispose() {
    _aiSub.cancel();
    _inputCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  void _onAiReply(String text) {
    if (!mounted || text.trim().isEmpty) return;
    setState(() {
      _messages.add(ChatMessage(
        text: text,
        isUser: false,
        time: DateTime.now(),
      ));
    });
    _scrollToBottom();
  }

  void _handleSend() {
    final text = _inputCtrl.text.trim();
    if (text.isEmpty) return;
    widget.connection.sendChat(text);
    setState(() {
      _messages.add(ChatMessage(
        text: text,
        isUser: true,
        time: DateTime.now(),
      ));
      _inputCtrl.clear();
    });
    _scrollToBottom();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollCtrl.hasClients) return;
      _scrollCtrl.animateTo(
        _scrollCtrl.position.maxScrollExtent,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('AI 对话')),
      body: Column(
        children: [
          Expanded(child: _buildMessageList()),
          _buildInputArea(),
        ],
      ),
    );
  }

  Widget _buildMessageList() {
    if (_messages.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Text(
            '和 AI 对话，指挥 SDR 操作...',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: AppTheme.textSecondary,
              fontSize: 15,
            ),
          ),
        ),
      );
    }
    return ListView.builder(
      controller: _scrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
      itemCount: _messages.length,
      itemBuilder: (_, i) => _buildBubble(_messages[i]),
    );
  }

  Widget _buildBubble(ChatMessage m) {
    final isUser = m.isUser;
    final bgColor = isUser ? AppTheme.accent : AppTheme.card;
    final fgColor = isUser ? Colors.white : AppTheme.text;
    final alignment =
        isUser ? Alignment.centerRight : Alignment.centerLeft;

    return Align(
      alignment: alignment,
      child: Container(
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.75,
        ),
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: bgColor,
          borderRadius: BorderRadius.circular(16),
          border: isUser
              ? null
              : Border.all(color: AppTheme.border),
        ),
        child: Column(
          crossAxisAlignment:
              isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            Text(m.text, style: TextStyle(color: fgColor, fontSize: 15)),
            const SizedBox(height: 4),
            Text(
              _timeFmt.format(m.time),
              style: TextStyle(
                color: isUser
                    ? Colors.white.withOpacity(0.7)
                    : AppTheme.textSecondary,
                fontSize: 11,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildInputArea() {
    final connected = widget.connection.isConnected;
    return Container(
      padding: EdgeInsets.fromLTRB(
        12,
        8,
        12,
        8 + MediaQuery.of(context).viewInsets.bottom,
      ),
      color: AppTheme.bg,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: TextField(
              controller: _inputCtrl,
              minLines: 1,
              maxLines: 4,
              enabled: connected,
              textInputAction: TextInputAction.newline,
              decoration: InputDecoration(
                hintText: connected ? '输入消息...' : '未连接',
                isDense: true,
              ),
            ),
          ),
          const SizedBox(width: 8),
          IconButton.filled(
            onPressed: connected ? _handleSend : null,
            icon: const Icon(Icons.send),
          ),
        ],
      ),
    );
  }
}
