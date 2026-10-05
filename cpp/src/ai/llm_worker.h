// SPDX-License-Identifier: MIT
#pragma once

#include "llm_client.h"
#include <QObject>
#include <QString>
#include <functional>

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }

namespace ai {

// Lives in a worker thread. The LLMClient (and its QNetworkAccessManager) is
// created lazily inside doChat() so the NAM ends up bound to the worker
// thread, not the thread that constructed this object. Cached config setters
// from the GUI thread are stored and re-applied on first use.
class LLMWorker : public QObject {
    Q_OBJECT
public:
    explicit LLMWorker(QObject* parent = nullptr);
    ~LLMWorker();
    void setEngine(dsp::SpectrumEngine* e) { engine_ = e; }
    void setApiKey(const QString& k) { apiKey_ = k; if (client_) client_->setApiKey(k); }
    void setBaseUrl(const QString& u) { baseUrl_ = u; if (client_) client_->setBaseUrl(u); }
    void setModel(const QString& m) { model_ = m; if (client_) client_->setModel(m); }
    /// Toggle manual mode (write-tool gate). Cheap setter cached here; applied on
    /// the next tool call. Default false = AI takeover.
    void setManualMode(bool on) { manualMode_ = on; }

    /// Read/write gate applied to one tool call with manual mode. Mirrors the
    /// static dispatchToolCall() gate semantics (see below).
    // (dispatchToolCall documented below.)

    // ---- Phase31 Wave2 error-recovery primitives (pure, testable) -----------
    // Classify an upstream LLM error string:
    //   Terminal  -> 400/401/403/422/parse/no-key: never retried, honest PENDING.
    //   Retryable -> 429/503/504/stream-timeout/connection: transient, backoff.
    //   Ok        -> empty error string.
    enum class LlmErrorClass { Ok, Terminal, Retryable };
    // Phase31 heuristic (retained verbatim as the FALLBACK): string matching.
    static LlmErrorClass classifyLlmError(const QString& errorString);
    // Phase32 block2: classify by the REAL HTTP status code first:
    //   429            -> Retryable (exponential backoff, existing seam)
    //   500/502/503/504/408 -> Retryable (server/timeout transient)
    //   401/403        -> Terminal (key hint; client-side, won't self-heal)
    //   400/404/422    -> Terminal (bad request shape; honest PENDING)
    // other 4xx -> Terminal, other 5xx -> Retryable. httpStatus <= 0 (no HTTP
    // reply: conn fail / timeout) defers to the string heuristic above.
    static LlmErrorClass classifyLlmError(int httpStatus, const QString& errorString);
    // Exponential backoff delay (ms) for the n-th transient retry (1-based),
    // clamped to [kAiBackoffBaseMs, kAiBackoffMaxMs]. No real network sleep here.
    static int backoffDelayMs(int attemptOneBased);
    // Assemble the user-facing line for a failed chat: keep whatever partial text
    // already streamed, then append an honest error note. Never fabricates a reply.
    // retriesExhausted > 0 means the bounded transient-retry budget
    // (kAiMaxTransientRetries on 429/5xx) was spent: the line appends an honest
    // "已重试 N 次后放弃" tail. The raw transport error already carries the code.
    static QString formatChatError(const QString& partialContent,
                                   const QString& error,
                                   int retriesExhausted = 0);
    // Test seam: override the backoff sleep (production = QThread::msleep). Tests
    // inject a no-op so the bounded retry loop runs instantly offline. Pass null
    // to restore the real sleep.
    static void setBackoffSleepForTests(std::function<void(int)> fn);
    static void sleepBackoffMs(int ms);

    /// Run one tool call with the manual-mode write gate applied. Extracted as a
    /// static, instance-free helper so the gate is unit-testable without an LLM:
    /// in manual mode a write tool returns gatedToolResult() WITHOUT calling
    /// executeTool / touching the engine; read-only tools always run. With
    /// manualMode=false this is identical to executeTool().
    static QString dispatchToolCall(const QString& name, const QJsonObject& args,
                                     dsp::SpectrumEngine* engine, bool manualMode);

public slots:
    void doChat(const QList<ChatMessage>& messages, const QList<ToolDef>& tools);
    void cleanup();

signals:
    void chatFinished(const QString& text);
    // Phase31 G2: a failed/interrupted chat emits THIS (not chatFinished). The
    // text keeps the partial streamed content + an honest error note; the Agent
    // must NOT append it to the normal conversation history (no pollution).
    void chatError(const QString& displayText);
    void toolCalled(const QString& tool, const QString& result);
    // Streaming: fired with the ACCUMULATED partial content on every SSE chunk
    // (the UI replaces its single transient line with it). No network when the
    // request is not streaming.
    void partialReady(const QString& accumulated);
    // Fired when the worker compacted old history into a summary entry, so the
    // UI can annotate the chat with the restrained 〔已摘要〕 marker.
    void contextCompacted(const QString& note);

private:
    LLMClient* client_ = nullptr;   // created on worker thread
    QString apiKey_, baseUrl_, model_;
    dsp::SpectrumEngine* engine_ = nullptr;
    bool manualMode_ = false;       // write tools gated when true
};

} // namespace ai
} // namespace mbdsdr
