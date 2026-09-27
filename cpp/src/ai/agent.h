// SPDX-License-Identifier: MIT
#pragma once

#include "llm_client.h"
#include "ai_config.h"
#include <QObject>
#include <QString>
#include <QList>

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }
namespace ai {

class Agent : public QObject {
    Q_OBJECT
public:
    explicit Agent(QObject* parent = nullptr);

    void setEngine(dsp::SpectrumEngine* e) { engine_ = e; }
    void configureFromConfig();

public slots:
    void chat(const QString& userInput);

signals:
    void reply(const QString& text);
    void toolCalled(const QString& tool, const QString& result);
    void statusChanged(const QString& status);

private:
    LLMClient client_;
    AiConfig config_;
    dsp::SpectrumEngine* engine_ = nullptr;
    QList<ChatMessage> history_;

    QString localCommand(const QString& input);
};

} // namespace ai
} // namespace mbdsdr
