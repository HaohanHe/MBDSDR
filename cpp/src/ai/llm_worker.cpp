// SPDX-License-Identifier: MIT
#include "llm_worker.h"
#include "agent_tools.h"
#include "dsp/spectrum_engine.h"

namespace mbdsdr {
namespace ai {

LLMWorker::LLMWorker(QObject* parent) : QObject(parent) {}

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

    for (int round = 0; round < 3; ++round) {
        LLMResponse resp = client_->chat(msgs, tools);
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
            QString result = executeTool(tc.name, tc.arguments, engine_);
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
