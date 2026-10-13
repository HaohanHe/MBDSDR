// SPDX-License-Identifier: MIT
#include "agent.h"
#include "ui/workspace_controller.h"
#include "agent_tools.h"
#include "memory_store.h"        // G3: chat-start memory injection block
#include "vna_onramp.h"
#include "llm_worker.h"
#include "dsp/spectrum_engine.h"
#include "dsp/band_preset.h"

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

void Agent::setWorkspaceController(aigui::WorkspaceController* wc) {
    worker_->setWorkspaceController(wc);
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
    // 统一 VNA 意图入口：驻波/谐振晶体/TDR 电缆/L-C，内部按问题分发。
    if (isVnaOnrampIntent(input))
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

    // "听手台"类：我想听 409.750MHz 的手台信号 -> 调谐 + NFM + 亚音/静噪提示。
    static QRegularExpression listenRe(
        R"((听|收|监控|守).*?(\d+(?:\.\d+)?)\s*(?:MHz|兆赫))");
    auto lm = listenRe.match(input.trimmed());
    if (lm.hasMatch()) {
        double mhz = lm.captured(2).toDouble();
        if (engine_) {
            engine_->onSetCenterFreq(mhz * 1e6);
            engine_->setDemodMode("NFM");
        }
        return QString::fromUtf8(
            "已调谐到 %1 MHz 并切到 NFM。\n"
            "- 若无声：请先在「源与连接」启动 SDR（USB 未识别时检查 udev/dialout 组权限）。\n"
            "- 手台多带亚音：按需要在静噪/亚音设置开启对应 CTCSS；先把静噪调低以听到信号。")
            .arg(mhz, 0, 'f', 3);
    }

    // 频段一键：调到 20米/2米/70厘米/航空/FM广播/中波 -> 中心+模式+带宽。
    static QRegularExpression bandRe(
        R"((调到|切换到|去|听)?\s*(20米|2米|70厘米|航空|FM广播|中波|fm|airband|mw))");
    auto bm = bandRe.match(input.trimmed());
    if (bm.hasMatch()) {
        auto p = mbdsdr::bandPresetByName(bm.captured(2).toUtf8().constData());
        if (!p.has_value())
            return QString::fromUtf8("未识别该频段。可选：FM广播、航空、中波、20米、2米、70厘米。");
        double mid = (p->lowHz + p->highHz) / 2.0;
        if (engine_) {
            engine_->onSetCenterFreq(mid);
            engine_->setDemodMode(QString::fromUtf8(p->mode));
            engine_->setBandwidth(p->bwHz);
        }
        return QString::fromUtf8(
            "已切到 %1：中心 %2 MHz，模式 %3，带宽 %4 kHz。")
            .arg(QString::fromUtf8(p->name))
            .arg(mid / 1e6, 0, 'f', 4)
            .arg(QString::fromUtf8(p->mode))
            .arg(p->bwHz / 1e3, 0, 'f', 1);
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
    // G3: chat-start memory injection (OpenHands discipline). The injected
    // block lives ONLY in this fresh system message -- history_ (the persisted
    // conversation) never contains it, so untrusted memory text does not leak
    // into cross-session transcript persistence.
    QString sysPrompt = QStringLiteral(
        "你是 SDR 接收控制助手。可以调谐频率、切换解调模式、控制录制、扫描频段。回答简洁。");
    const QString memBlock = buildMemoryInjectionBlock();
    if (!memBlock.isEmpty()) sysPrompt += QStringLiteral("\n\n") + memBlock;
    msgs.append(ChatMessage{"system", sysPrompt});
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