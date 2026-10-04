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

    void setEngine(dsp::SpectrumEngine* e);
    void configureFromConfig();
    /// Set config directly (for tests / programmatic setup). Applies to worker.
    void setConfig(const AiConfig& c) { config_ = c; configureFromConfig(); }

    /// Manual mode ("AI 接管" off): write/state-changing tools are gated and
    /// never drive the radio; read-only tools (get_status) still run. The value
    /// is persisted via QSettings("MBDSDR","MBDSDR") under "aiManualMode"
    /// (bool, default false = AI takeover) and re-read on construction.
    /// Wiring for the UI toggle: call setManualMode() on toggle; observe
    /// toolCalled()/statusChanged() for feedback. This is the entire backend.
    void setManualMode(bool on);
    bool manualMode() const { return manualMode_; }

public slots:
    void sendMessage(const QString& userInput);

signals:
    void responseReady(const QString& text);
    void toolCalled(const QString& tool, const QString& result);
    void statusChanged(const QString& status);
    // Streaming partial (accumulated content so far); UI replaces its transient
    // line. Absent on the no-API-key local-command path.
    void partialReady(const QString& accumulated);
    // History was compacted into a summary entry; UI annotates 〔已摘要〕.
    void contextCompacted(const QString& note);

private slots:
    void onChatFinished(const QString& text);
    // Phase31 G2: a failed/interrupted chat does NOT append to history_ -- the
    // partial+error line is shown transiently but never pollutes the next LLM
    // request. Mirrors LLMWorker::chatError.
    void onChatError(const QString& displayText);

private:
    AiConfig config_;
    dsp::SpectrumEngine* engine_ = nullptr;
    QList<ChatMessage> history_;
    QThread* workerThread_ = nullptr;
    LLMWorker* worker_ = nullptr;
    bool manualMode_ = false;   // persisted; re-read from QSettings in ctor

    QString localCommand(const QString& input);
};

} // namespace ai
} // namespace mbdsdr
