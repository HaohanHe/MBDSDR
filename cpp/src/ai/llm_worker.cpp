// SPDX-License-Identifier: MIT
#include "llm_worker.h"

namespace mbdsdr {
namespace ai {

LLMWorker::LLMWorker(QObject* parent) : QObject(parent) {}

void LLMWorker::doChat(const QList<ChatMessage>& messages,
                         const QList<ToolDef>& tools) {
    LLMResponse resp = client_.chat(messages, tools);
    emit chatFinished(resp);
}

} // namespace ai
} // namespace mbdsdr
