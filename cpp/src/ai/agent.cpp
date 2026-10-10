// SPDX-License-Identifier: MIT
#include "agent.h"
#include "agent_tools.h"
#include "vna_onramp.h"
#include "llm_worker.h"
#include "dsp/spectrum_engine.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QSettings>

namespace mbdsdr {
namespace ai {

namespace {
const char* kManualModeKey = "aiManualMode";  // QSettings key, persisted
} // namespace

Agent::Agent(QObject* parent) : QObject(parent) {
    config_.load();
    // Restore persisted manual mode (default false = AI takeover).
    {
        QSettings s("MBDSDR", "MBDSDR");
        manualMode_ = s.value(kManualModeKey, false).toBool();
    }
    workerThread_ = new QThread(this);
    worker_ = new LLMWorker();
    worker_->moveToThread(workerThread_);
    worker_->setManualMode(manualMode_);
    connect(worker_, &LLMWorker::chatFinished, this, &Agent::onChatFinished);
    connect(worker_, &LLMWorker::chatError, this, &Agent::onChatError);
    connect(worker_, &LLMWorker::toolCalled, this, &Agent::toolCalled);
    connect(worker_, &LLMWorker::partialReady, this, &Agent::partialReady);
    connect(worker_, &LLMWorker::contextCompacted, this, &Agent::contextCompacted);
    // Drop the worker's NAM on the worker thread before we delete the worker.
    connect(workerThread_, &QThread::finished, worker_, &LLMWorker::cleanup);
    workerThread_->start();
}

void Agent::setEngine(dsp::SpectrumEngine* e) {
    engine_ = e;
    worker_->setEngine(e);
}

void Agent::setManualMode(bool on) {
    manualMode_ = on;
    QSettings("MBDSDR", "MBDSDR").setValue(kManualModeKey, on);
    worker_->setManualMode(on);
}

Agent::~Agent() {
    workerThread_->quit();
    workerThread_->wait();
    delete worker_;
}

void Agent::configureFromConfig() {
    worker_->setApiKey(config_.apiKey);
    worker_->setBaseUrl(config_.baseUrl);
    worker_->setModel(config_.model);
    emit statusChanged(config_.isConfigured() ? "LLM 已配置" : "未配置 API Key — 仅本地指令");
}

QString Agent::localCommand(const QString& input) {
    // "一句话测驻波"：识别到驻波/VSWR/天线驻波意图即走真实三通道工具编排。
    if (isVswrIntent(input))
        return runVswrOnramp(engine_, input);

    static QRegularExpression freqRe(
        R"(^\s*(\d+(\.\d+)?)\s*([mM]?[Hh]?[Zz]?)\s*$)");
    auto m = freqRe.match(input);
    if (m.hasMatch()) {
        double val = m.captured(1).toDouble();
        double hz = val * 1e6;
        if (engine_) engine_->onSetCenterFreq(hz);
        return QString("已调谐到 %1 MHz（本地指令）").arg(val, 0, 'f', 3);
    }

    QString low = input.trimmed().toLower();
    if (low == "am" || low == "fm" || low == "nfm" || low == "wfm" ||
        low == "usb" || low == "lsb" || low == "cw") {
        QString mode = low.toUpper();
        if (engine_) engine_->setDemodMode(mode);
        return QString("模式切换为 %1（本地指令）").arg(mode);
    }
    if (low == "record") {
        if (engine_) engine_->startRecording();
        return "开始录制（本地指令）";
    }
    if (low == "stop") {
        if (engine_) engine_->stopRecording();
        return "停止录制（本地指令）";
    }
    return "未配置 API Key，仅支持频率（如 98.5）、模式（am/nfm/wfm/cw）、录制（record/stop）等本地指令。";
}

void Agent::sendMessage(const QString& userInput) {
    history_.append(ChatMessage{"user", userInput});

    if (!config_.isConfigured()) {
        QString text = localCommand(userInput);
        history_.append(ChatMessage{"assistant", text});
        emit responseReady(text);
        return;
    }

    // Async: invoke worker thread to do LLM chat with tool loop
    QList<ChatMessage> msgs;
    msgs.append(ChatMessage{"system", "你是 SDR 接收控制助手。可以调谐频率、切换解调模式、控制录制、扫描频段。回答简洁。"});
    msgs.append(history_);
    auto tools = toolDefs();

    QMetaObject::invokeMethod(worker_, [this, msgs, tools]() {
        worker_->doChat(msgs, tools);
    }, Qt::QueuedConnection);
}

void Agent::onChatFinished(const QString& text) {
    // Record the assistant reply so the next request carries growing history
    // (which is what makes context compaction kick in once the budget trips).
    history_.append(ChatMessage{"assistant", text});
    emit responseReady(text);
}

void Agent::onChatError(const QString& displayText) {
    // G2: a failed/interrupted chat is surfaced to the operator (transient line)
    // but is NOT appended to history_ -- a half reply or an error string must
    // never be persisted as a normal assistant turn that the next request sends.
    emit responseReady(displayText);
}

} // namespace ai
} // namespace mbdsdr