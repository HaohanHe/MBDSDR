// SPDX-License-Identifier: MIT
#include "agent.h"
#include "agent_tools.h"
#include "llm_worker.h"
#include "dsp/spectrum_engine.h"

#include <QRegularExpression>

namespace mbdsdr {
namespace ai {

Agent::Agent(QObject* parent) : QObject(parent) {
    config_.load();
    workerThread_ = new QThread(this);
    worker_ = new LLMWorker();
    worker_->moveToThread(workerThread_);
    workerThread_->start();
}

Agent::~Agent() {
    workerThread_->quit();
    workerThread_->wait();
    delete worker_;
}

void Agent::configureFromConfig() {
    emit statusChanged(config_.isConfigured() ? "LLM 已配置" : "未配置 API Key — 仅本地指令");
}

QString Agent::localCommand(const QString& input) {
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

    // Async LLM call via worker thread. For now, simplified: just emit a status.
    // Full async tool-calling loop is TODO; local commands work immediately.
    emit responseReady("LLM 模式已配置，但完整异步工具循环待实现。当前可使用本地指令（频率/模式/录制）。");
}

} // namespace ai
} // namespace mbdsdr
