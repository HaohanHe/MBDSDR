// SPDX-License-Identifier: MIT
#include "llm_worker.h"
#include "agent_tools.h"
#include "ai_context.h"
#include "arguments_validator.h"
#include "dsp/spectrum_engine.h"
#include "core/tokens.h"

#include <QHash>
#include <QThread>

namespace mbdsdr {
namespace ai {

LLMWorker::LLMWorker(QObject* parent) : QObject(parent) {}

LLMWorker::LlmErrorClass LLMWorker::classifyLlmError(const QString& e) {
    if (e.isEmpty()) return LlmErrorClass::Ok;
    const QString s = e.toLower();
    // Terminal: bad request / bad key / forbidden / unprocessable / local config.
    // These are NEVER retried (honest PENDING, no mock output).
    static const char* kTerminal[] = {
        "400", "401", "403", "422", "invalid token", "api key not configured",
        "api key", "unauthorized", "forbidden", "parse error",
    };
    for (const char* t : kTerminal)
        if (s.contains(QLatin1String(t))) return LlmErrorClass::Terminal;
    // Retryable: rate limit / server overload / gateway / timeout / transient net.
    static const char* kRetry[] = {
        "429", "500", "502", "503", "504", "rate limit", "overloaded",
        "timed out", "timeout", "refused", "temporarily", "try again",
    };
    for (const char* t : kRetry)
        if (s.contains(QLatin1String(t))) return LlmErrorClass::Retryable;
    // Unknown errors default to terminal: we never silently retry something we
    // don't understand (avoids a retry storm on a genuine config bug).
    return LlmErrorClass::Terminal;
}

LLMWorker::LlmErrorClass LLMWorker::classifyLlmError(int httpStatus, const QString& e) {
    // Phase32 block2: the real HTTP status code wins when the transport actually
    // saw one. Only when there is NO status line (httpStatus <= 0: connection
    // refused / DNS / TLS / request timeout) do we fall back to the Phase31
    // string heuristic, so offline mocks and no-key errors keep their old shape.
    if (httpStatus > 0) {
        switch (httpStatus) {
            case 429:  // rate limited -> existing exponential backoff seam
            case 408:  // request timeout -> retryable
            case 500:  // internal error (often transient for upstreams)
            case 502:  // bad gateway
            case 503:  // service unavailable -> retry
            case 504:  // gateway timeout
                return LlmErrorClass::Retryable;
            case 401:  // bad/missing key -> key hint, never self-heals
            case 403:  // forbidden / no quota
            case 400:  // bad request shape (model/args) -> terminal, honest
            case 404:  // unknown route/model
            case 422:  // unprocessable entity
                return LlmErrorClass::Terminal;
            default: break;
        }
        if (httpStatus >= 500 && httpStatus < 600) return LlmErrorClass::Retryable;
        if (httpStatus >= 400 && httpStatus < 500) return LlmErrorClass::Terminal;
        // 1xx/2xx/3xx with an attached error string (shouldn't happen): defer.
    }
    return classifyLlmError(e);
}

int LLMWorker::backoffDelayMs(int attemptOneBased) {
    if (attemptOneBased < 1) attemptOneBased = 1;
    long delay = tokens::kAiBackoffBaseMs;
    // Double each attempt, clamping AT the max so a huge attempt number cannot
    // overflow back to 0.
    for (int i = 1; i < attemptOneBased; ++i) {
        delay *= 2;
        if (delay >= tokens::kAiBackoffMaxMs) {
            delay = tokens::kAiBackoffMaxMs;
            break;
        }
    }
    return static_cast<int>(delay);
}

QString LLMWorker::formatChatError(const QString& partialContent,
                                   const QString& error,
                                   int retriesExhausted) {
    QString out = partialContent;
    if (!out.isEmpty()) out += QString::fromUtf8("\n\n[生成中断] ");
    else out = QString::fromUtf8("LLM 请求失败：");
    out += error;
    // Transient-retry budget spent (429/503/timeout after backoff): the raw
    // transport `error` already carries the HTTP code; append an honest tail so
    // the operator knows we already retried N times and are giving up -- never a
    // silent retry storm, never a fabricated answer.
    if (retriesExhausted > 0) {
        out += QString::fromUtf8("（服务端繁忙，已自动重试 %1 次后放弃）")
                   .arg(retriesExhausted);
    }
    return out;
}

namespace {
std::function<void(int)> s_backoffHook;   // null in production
}
void LLMWorker::setBackoffSleepForTests(std::function<void(int)> fn) {
    s_backoffHook = std::move(fn);
}
void LLMWorker::sleepBackoffMs(int ms) {
    if (s_backoffHook) s_backoffHook(ms);
    else QThread::msleep(static_cast<unsigned long>(ms));
}

QString LLMWorker::dispatchToolCall(const QString& name, const QJsonObject& args,
                                    dsp::SpectrumEngine* engine, bool manualMode,
                                    ui::BookmarkManager* bookmarks) {
    // Manual-mode gate: a write action must NOT reach the radio. Skip
    // executeTool entirely (no engine touch) and hand back the gated result so it
    // still enters the conversation context and surfaces via toolCalled().
    if (manualMode && isWriteTool(name)) {
        return gatedToolResult(name);
    }
    return executeTool(name, args, engine, bookmarks);
}

LLMWorker::~LLMWorker() {
    if (client_) { delete client_; client_ = nullptr; }
}

void LLMWorker::cleanup() {
    if (client_) { client_->deleteLater(); client_ = nullptr; }
}

void LLMWorker::doChat(const QList<ChatMessage>& messages,
                         const QList<ToolDef>& tools) {
    // Lazily create the client on THIS (worker) thread so the
    // QNetworkAccessManager lives here and no cross-thread child warnings fire.
    if (!client_) {
        client_ = new LLMClient();
        client_->setApiKey(apiKey_);
        client_->setBaseUrl(baseUrl_);
        client_->setModel(model_);
    }
    QList<ChatMessage> msgs = messages;

    // Name -> JSON-Schema lookup for the pre-dispatch arguments validation. The
    // tools list already carries the M1-generated schema (min/max/enum).
    QHash<QString, QJsonObject> schemaByName;
    for (const ToolDef& t : tools) schemaByName.insert(t.name, t.parameters);

    // Split the leading system prompt from the conversation history so the
    // pure compaction pass can decide what to fold into a summary.
    QString systemPrompt;
    if (!msgs.isEmpty() && msgs.first().role == "system")
        systemPrompt = msgs.takeFirst().content;

    // Context compaction on THIS (worker) thread: if an API key is configured
    // the injected callback asks the LLM for a real summary; otherwise the
    // compactContext fallback produces an honest rule-based count summary.
    auto summarize = [this](const QList<ChatMessage>& old) -> QString {
        if (!client_ || apiKey_.isEmpty()) return QString();
        QList<ChatMessage> p;
        p.append(ChatMessage{"system",
            QString::fromUtf8("把下面的对话压缩成一两句中文摘要：保留用户诉求与已执行的操作，只输出摘要本身。")});
        for (const auto& m : old)
            if (m.role != QStringLiteral("tool")) p.append(m);
        LLMResponse r = client_->chat(p, {});
        return r.error.isEmpty() ? r.content : QString();
    };
    auto compacted = compactContext(msgs, systemPrompt, {}, summarize);
    if (compacted.didCompact)
        emit contextCompacted(compacted.summaryText);
    msgs = compacted.messages;

    // --- The real function-calling loop (impl-spec §6 / state machine) ------
    // Watchdog: at most kAiMaxToolRounds assistant turns. Every tool call is
    // validated against its schema BEFORE dispatch; a validation failure does not
    // touch the hardware -- the errorJson is fed back as a role=tool message so
    // the model self-corrects on the next turn. The assistant message (content +
    // reasoningContent + toolCalls) is appended verbatim; reasoning is NEVER
    // trimmed or rewritten. N tool_calls produce exactly N role=tool messages,
    // paired strictly by tool_call_id.
    for (int round = 0; round < tokens::kAiMaxToolRounds; ++round) {
        // Streaming: forward the accumulated partial content as it arrives so
        // the UI can update its single transient line (no duplication).
        auto onChunk = [this](const QString& acc) { emit partialReady(acc); };

        RequestOptions opts;
        opts.model = model_;
        opts.toolChoice = QStringLiteral("auto");
        // toolRound == the requests made AFTER tool results have been fed back.
        applyThinkingConfig(opts, model_, /*toolRound=*/round > 0);

        LLMResponse resp;
        // Bounded transient-retry (error-recovery layer 5): retry only RETRYABLE
        // upstream errors -- now classified by the REAL HTTP status code
        // (429/503/504/408/5xx); no-HTTP-reply (conn fail/timeout) and offline
        // mocks fall back to the Phase31 string heuristic -- up to
        // kAiMaxTransientRetries with exponential backoff. Terminal errors
        // (400/401/403/no-key/parse) and the exhausted budget return as-is so the
        // caller surfaces an honest PENDING -- never a mock, never a retry storm.
        int retriesUsed = 0;          // transient retries actually performed
        bool budgetExhausted = false; // retryable error hit the budget ceiling
        for (int attempt = 0; ; ++attempt) {
            resp = client_->chat(msgs, tools, onChunk, opts);
            if (resp.error.isEmpty()) break;
            const LlmErrorClass cls = classifyLlmError(resp.httpStatus, resp.error);
            if (cls != LlmErrorClass::Retryable ||
                attempt >= tokens::kAiMaxTransientRetries) {
                // Terminal on first try, OR a retryable error that has now eaten
                // the whole bounded budget (429/503 after kAiMaxTransientRetries).
                // In the latter case flag it so the surfaced line can honestly say
                // "retried N times, giving up" -- the operator never sees a bare
                // transport error and wonders why we stopped.
                if (cls == LlmErrorClass::Retryable &&
                    attempt >= tokens::kAiMaxTransientRetries)
                    budgetExhausted = true;
                break;   // terminal or budget exhausted -> return the honest error
            }
            retriesUsed = attempt + 1;   // this attempt will be followed by a retry
            sleepBackoffMs(backoffDelayMs(attempt + 1));
        }
        if (!resp.error.isEmpty()) {
            // G2: surface on the SEPARATE chatError signal (partial content kept),
            // NOT chatFinished -- the Agent must not persist it as a normal reply.
            emit chatError(formatChatError(resp.content, resp.error,
                                           budgetExhausted ? retriesUsed : 0));
            return;
        }

        // No pending calls (finish_reason=stop / plain answer) => terminal.
        if (resp.toolCalls.isEmpty()) {
            emit chatFinished(resp.content);
            return;
        }

        // Append the whole assistant turn verbatim (content + reasoning + calls).
        ChatMessage asst;
        asst.role = "assistant";
        asst.content = resp.content;
        asst.reasoningContent = resp.reasoningContent;   // round-trip verbatim
        asst.toolCalls = resp.toolCalls;
        msgs.append(asst);

        for (const ToolCall& tc : resp.toolCalls) {
            // Validate BEFORE touching hardware. Unknown tool names yield an empty
            // schema and are rejected by the validator (missing properties).
            ValidationResult vr = validateArguments(tc.name, tc.arguments,
                                                    schemaByName.value(tc.name));
            if (!vr.ok) {
                emit toolCalled(tc.name, vr.errorJson);
                ChatMessage tr;
                tr.role = "tool";
                tr.toolCallId = tc.id;
                tr.content = vr.errorJson;
                msgs.append(tr);
                continue;
            }

            QString result = dispatchToolCall(tc.name, tc.arguments, engine_,
                                              manualMode_, bookmarks_);
            emit toolCalled(tc.name, result);
            ChatMessage tr;
            tr.role = "tool";
            tr.toolCallId = tc.id;
            tr.content = result;
            msgs.append(tr);
        }
    }

    emit chatFinished("工具调用轮次用尽");
}

} // namespace ai
} // namespace mbdsdr
