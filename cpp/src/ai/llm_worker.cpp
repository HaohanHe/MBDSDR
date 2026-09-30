// SPDX-License-Identifier: MIT
#include "llm_worker.h"
#include "agent_tools.h"
#include "ai_context.h"
#include "dsp/spectrum_engine.h"

namespace mbdsdr {
namespace ai {

LLMWorker::LLMWorker(QObject* parent) : QObject(parent) {}

QString LLMWorker::dispatchToolCall(const QString& name, const QJsonObject& args,
                                    dsp::SpectrumEngine* engine, bool manualMode) {
    // Manual-mode gate: a write action must NOT reach the radio. Skip
    // executeTool entirely (no engine touch) and hand back the gated result so it
    // still enters the conversation context and surfaces via toolCalled().
    if (manualMode && isWriteTool(name)) {
        return gatedToolResult(name);
    }
    return executeTool(name, args, engine);
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

    for (int round = 0; round < 3; ++round) {
        // Streaming: forward the accumulated partial content as it arrives so
        // the UI can update its single transient line (no duplication).
        auto onChunk = [this](const QString& acc) { emit partialReady(acc); };
        LLMResponse resp = client_->chat(msgs, tools, onChunk);
        if (!resp.error.isEmpty()) {
            emit chatFinished("LLM 错误: " + resp.error);
            return;
        }

        if (resp.toolCalls.isEmpty()) {
            emit chatFinished(resp.content);
            return;
        }

        ChatMessage asst;
        asst.role = "assistant";
        asst.content = resp.content;
        asst.toolCalls = resp.toolCalls;
        msgs.append(asst);

        for (const auto& tc : resp.toolCalls) {
            QString result = dispatchToolCall(tc.name, tc.arguments, engine_, manualMode_);
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
