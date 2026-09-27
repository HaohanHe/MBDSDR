// SPDX-License-Identifier: MIT
#pragma once

#include "llm_client.h"
#include <QObject>

namespace mbdsdr {
namespace ai {

class LLMWorker : public QObject {
    Q_OBJECT
public:
    explicit LLMWorker(QObject* parent = nullptr);

public slots:
    void doChat(const QList<ChatMessage>& messages, const QList<ToolDef>& tools);

signals:
    void chatFinished(const LLMResponse& resp);

private:
    LLMClient client_;
};

} // namespace ai
} // namespace mbdsdr
