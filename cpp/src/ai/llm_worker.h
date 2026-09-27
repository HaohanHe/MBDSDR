// SPDX-License-Identifier: MIT
#pragma once

#include "llm_client.h"
#include <QObject>

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }

namespace ai {

class LLMWorker : public QObject {
    Q_OBJECT
public:
    explicit LLMWorker(QObject* parent = nullptr);
    void setEngine(dsp::SpectrumEngine* e) { engine_ = e; }
    void setApiKey(const QString& k) { client_.setApiKey(k); }
    void setBaseUrl(const QString& u) { client_.setBaseUrl(u); }
    void setModel(const QString& m) { client_.setModel(m); }

public slots:
    void doChat(const QList<ChatMessage>& messages, const QList<ToolDef>& tools);

signals:
    void chatFinished(const QString& text);
    void toolCalled(const QString& tool, const QString& result);

private:
    LLMClient client_;
    dsp::SpectrumEngine* engine_ = nullptr;
};

} // namespace ai
} // namespace mbdsdr
