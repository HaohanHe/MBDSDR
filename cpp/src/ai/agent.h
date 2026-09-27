// SPDX-License-Identifier: MIT
#pragma once

#include "llm_client.h"
#include "ai_config.h"
#include <QObject>
#include <QThread>
#include <QString>
#include <QList>

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }
namespace ai { class LLMWorker; }

namespace ai {

class Agent : public QObject {
    Q_OBJECT
public:
    explicit Agent(QObject* parent = nullptr);
    ~Agent();

    void setEngine(dsp::SpectrumEngine* e) { engine_ = e; }
    void configureFromConfig();

public slots:
    void sendMessage(const QString& userInput);

signals:
    void responseReady(const QString& text);
    void toolCalled(const QString& tool, const QString& result);
    void statusChanged(const QString& status);

private:
    AiConfig config_;
    dsp::SpectrumEngine* engine_ = nullptr;
    QList<ChatMessage> history_;
    QThread* workerThread_ = nullptr;
    LLMWorker* worker_ = nullptr;

    QString localCommand(const QString& input);
};

} // namespace ai
} // namespace mbdsdr
