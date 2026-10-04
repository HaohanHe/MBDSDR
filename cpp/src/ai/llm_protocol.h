// SPDX-License-Identifier: MIT
// M3 dual-protocol layer (OpenAI-compatible + Anthropic-compatible).
//
// Pure functions: NO QNetwork, NO network, NO QObject. Builds request JSON and
// parses response / SSE-stream chunks for two wire shapes. The canonical
// in-memory shapes below mirror impl-spec §2; M4 will align llm_client.h to
// these same field names (reasoningContent / finishReason are NEW here).
//
// Field-shape evidence:
//  - OpenAI: docs/learn/model-tool-calling/06-mimo-openai-api.md
//            04-sf-chat-completions-api.md (enable_thinking/thinking_budget)
//            14-streaming-best-practices.md (delta.* / index slots)
//  - Anthropic (MiMo /anthropic/v1/messages):
//            11-mimo-anthropic-protocol.md (system top-level, content blocks,
//            tool_use/tool_result, input_schema, stop_reason)
#pragma once

#include <QString>
#include <QList>
#include <QJsonObject>
#include <QByteArray>

namespace mbdsdr {
namespace ai {

enum class Protocol { OpenAI, Anthropic };

// Canonical message (impl-spec §2). reasoningContent is carried verbatim back
// on assistant turns; the upstream APIs require it to be preserved across the
// tool-calling loop (missing -> 400 on MiMo OpenAI; degraded on Anthropic).
struct ChatMessage {
    QString role;                     // "system" | "user" | "assistant" | "tool"
    QString content;
    QString reasoningContent;        // assistant thinking text (may be empty)
    QString toolCallId;               // set when role == "tool"
    QList<struct ToolCall> toolCalls; // assistant message with pending calls
};

struct ToolCall {
    QString id;
    QString name;
    QJsonObject arguments;            // already-parsed JSON object
};

struct ToolDef {
    QString name;
    QString description;
    QJsonObject parameters;           // JSON Schema
};

struct LLMResponse {
    QString content;
    QString reasoningContent;
    QList<ToolCall> toolCalls;
    QString finishReason;             // "stop" | "tool_calls" | "length" | ...
    QString error;
    // Phase32 block2: the REAL HTTP status code from the transport layer
    // (QNetworkRequest::HttpStatusCodeAttribute). 0 = no HTTP status line was
    // ever received (connection refused / DNS / TLS / request timeout) -- the
    // worker then falls back to the Phase31 string heuristic. 2xx on success.
    int httpStatus = 0;
};

struct RequestOptions {
    Protocol protocol = Protocol::OpenAI;
    QString model;
    bool stream = false;
    QString toolChoice = "auto";      // always "auto" (backend strips others)
    bool thinkingEnabled = false;
    int thinkingBudget = 0;
};

// Build the wire request body for the chosen protocol.
QJsonObject buildChatRequest(const QList<ChatMessage>& messages,
                             const QList<ToolDef>& tools,
                             const RequestOptions& opts);

// Parse a non-streaming response body. Returns false and fills *err on
// malformed JSON; on success fills *out (content / reasoningContent /
// toolCalls / finishReason).
bool parseChatResponse(const QByteArray& body, Protocol proto,
                       LLMResponse* out, QString* err);

struct ToolCallDelta {
    int index = 0;                    // OpenAI: slot index from 0
    QString id;
    QString name;
    QString argumentsFragment;        // partial JSON string; caller accumulates
};

struct StreamChunk {
    QString contentDelta;
    QString reasoningDelta;
    QList<ToolCallDelta> toolDeltas;
    QString finishReason;             // non-empty on the final chunk
};

// Parse ONE SSE payload (a `data: {...}` line, with or without the prefix).
// OpenAI is the authoritative shape; Anthropic event mapping is best-effort
// (fields marked 推断 in the comments where the notes did not expand them).
StreamChunk parseStreamChunk(const QByteArray& payload, Protocol proto);

} // namespace ai
} // namespace mbdsdr
