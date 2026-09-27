// SPDX-License-Identifier: MIT
#include "llm_worker.h"
#include "agent_tools.h"
#include "dsp/spectrum_engine.h"

namespace mbdsdr {
namespace ai {

LLMWorker::LLMWorker(QObject* parent) : QObject(parent) {}

void LLMWorker::doChat(const QList<ChatMessage>& messages,
                         const QList<ToolDef>& tools) {
    QList<ChatMessage> msgs = messages;

    for (int round = 0; round < 3; ++round) {
        LLMResponse resp = client_.chat(msgs, tools);
        if (!resp.error.isEmpty()) {
            emit chatFinished("LLM 错误: " + resp.error);
            return;
        }

        if (resp.toolCalls.isEmpty()) {
            emit chatFinished(resp.content);
            return;
        }

        for (const auto& tc : resp.toolCalls) {
            QString result = executeTool(tc.name, tc.arguments, engine_);
            emit toolCalled(tc.name, result);
            msgs.append(ChatMessage{"assistant", resp.content});
            msgs.append(ChatMessage{"user", result});
        }
    }

    emit chatFinished("工具调用轮次用尽");
}

} // namespace ai
} // namespace mbdsdr
