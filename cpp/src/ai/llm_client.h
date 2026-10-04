// SPDX-License-Identifier: MIT
#pragma once

#include "ai/llm_protocol.h"   // canonical ChatMessage/ToolCall/ToolDef/LLMResponse/RequestOptions
#include <QNetworkAccessManager>
#include <QObject>
#include <functional>

namespace mbdsdr {
namespace ai {

// NOTE on types: ChatMessage / ToolCall / ToolDef / LLMResponse / RequestOptions
// are defined in ai/llm_protocol.h (M3) -- the canonical wire shapes. M4 aligns
// this header to them exactly (reasoningContent on ChatMessage, finishReason on
// LLMResponse). Do NOT re-declare them here, or the two translation units would
// disagree on the layout and the loop history would desync.

// Decide the thinking configuration for ONE chat request, given the model id and
// whether this request happens AFTER tool results have been fed back (a "tool
// round") vs. the first conversational turn. Per the model×task matrix
// (docs/learn/model-tool-calling/13-thinking-config-stability.md):
//   - DeepSeek-V3.2 / GLM-4.7 (SiliconFlow interleaved thinking): thinking is
//     ALWAYS on, with kAiThinkingBudgetTokens budget; reasoning must round-trip.
//   - Qwen / other SiliconFlow models: thinking off by default.
//   - MiMo v2.6-pro: thinking ON for the conversational first turn, OFF on tool
//     rounds (official guidance: thinking + tool calls is unstable/incomplete).
// Fills opts->thinkingEnabled / opts->thinkingBudget in place. toolChoice and
// protocol are left untouched.
void applyThinkingConfig(RequestOptions& opts, const QString& model, bool toolRound);

class LLMClient : public QObject {
    Q_OBJECT
public:
    explicit LLMClient(QObject* parent = nullptr);
    ~LLMClient() override = default;

    void setApiKey(const QString& key) { apiKey_ = key; }
    void setBaseUrl(const QString& url) { baseUrl_ = url; }
    void setModel(const QString& m) { model_ = m; }

    // The transport seam (impl-spec §6). Given a prepared QNetworkRequest and the
    // serialized request body, return the raw response bytes. The default (when
    // no transport is installed) performs a real blocking QNAM POST with a
    // QEventLoop + kAiRequestTimeoutMs timeout. Tests inject a canned lambda so
    // the whole loop runs fully offline with no socket.
    using TransportFn = std::function<QByteArray(const QNetworkRequest&,
                                                const QByteArray& body)>;

    // Install an instance-level transport. When set, chat() never touches QNAM.
    void setTransport(TransportFn fn) { transport_ = std::move(fn); }

    // Process-wide transport handed to every NEW LLMClient instance. This is the
    // seam that lets the offline loop tests drive LLMWorker (which lazily builds
    // its own client inside doChat) without a socket. It is NEVER set in
    // production; it stays a null std::function there.
    static void setDefaultTransportForTests(TransportFn fn) { s_testTransport = std::move(fn); }

    // Blocking one-shot chat. If `onChunk` is non-null the request is made in
    // streaming (SSE) mode and `onChunk` is called with the ACCUMULATED content
    // so far on every content delta (the UI replaces its transient line, never
    // duplicating). With a null callback the whole-JSON path is used.
    //
    // `opts` selects protocol / model / thinking / toolChoice. When opts.model is
    // empty the client's configured model_ is used; opts.stream is forced from
    // the presence of onChunk. A message with role "summary" (context compaction
    // marker) is mapped to a "system" note on the wire so the upstream API only
    // ever sees valid roles. Backward compatible: existing 2- and 3-argument
    // callers keep compiling (opts defaults to OpenAI, thinking off).
    using ChunkCallback = std::function<void(const QString& accumulatedSoFar)>;
    LLMResponse chat(const QList<ChatMessage>& messages,
                     const QList<ToolDef>& tools,
                     ChunkCallback onChunk = nullptr,
                     const RequestOptions& opts = {});

    // Phase31 Wave2 (streaming.md G1): assemble an error response that RETAINS
    // whatever partial content already streamed before the failure, instead of
    // wiping the half-sentence the user already saw. Production calls the same
    // assembly on the QNAM error branch; exposed as a pure seam so tests can pin
    // the honesty contract without a socket.
    static LLMResponse errorResponseWithPartial(const QString& partialContent,
                                                const QString& error);

private:
    // Feed one complete SSE text blob (already received bytes) into the shared
    // stream accumulator, splitting on line boundaries and honoring `data:`
    // prefixes / [DONE]. Shared by the async QNAM pump and the sync mock path so
    // both produce identical assistant messages.
    static void feedSseText(const QString& text, Protocol proto, class StreamAccumulator& acc,
                            ChunkCallback onChunk);

    QNetworkAccessManager* nam_;
    QString apiKey_;
    QString baseUrl_ = "https://api.siliconflow.cn/v1";
    QString model_ = "Qwen/Qwen2.5-7B-Instruct";
    TransportFn transport_;

    static TransportFn s_testTransport;
};

} // namespace ai
} // namespace mbdsdr
