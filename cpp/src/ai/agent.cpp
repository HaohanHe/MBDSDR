// SPDX-License-Identifier: MIT
#include "agent.h"
#include "agent_tools.h"
#include "dsp/spectrum_engine.h"

#include <QRegularExpression>

namespace mbdsdr {
namespace ai {

Agent::Agent(QObject* parent) : QObject(parent) {
    config_.load();
}

void Agent::configureFromConfig() {
    client_.setApiKey(config_.apiKey);
    client_.setBaseUrl(config_.baseUrl);
    client_.setModel(config_.model);
    emit statusChanged(config_.isConfigured() ? "LLM 已配置" : "未配置 API Key — 仅本地指令");
}

QString Agent::localCommand(const QString& input) {
    // Frequency: 98.5, 98.5M, 100MHz
    static QRegularExpression freqRe(
        R"(^\s*(\d+(\.\d+)?)\s*([mM]?[Hh]?[Zz]?)\s*$)");
    auto m = freqRe.match(input);
    if (m.hasMatch()) {
        double val = m.captured(1).toDouble();
        QString unit = m.captured(3).toLower();
        double hz = unit.startsWith('m') ? val * 1e6 : val * 1e6;
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

void Agent::chat(const QString& userInput) {
    history_.append(ChatMessage{"user", userInput});

    if (!config_.isConfigured()) {
        QString text = localCommand(userInput);
        history_.append(ChatMessage{"assistant", text});
        emit reply(text);
        return;
    }

    QList<ChatMessage> msgs;
    msgs.append(ChatMessage{"system", "你是 SDR 接收控制助手。可以调谐频率、切换解调模式、控制录制。回答简洁。"});
    msgs.append(history_);

    auto tools = toolDefs();

    for (int round = 0; round < 3; ++round) {
        auto resp = client_.chat(msgs, tools);
        if (!resp.error.isEmpty()) {
            emit reply("LLM 错误: " + resp.error);
            return;
        }

        if (resp.toolCalls.isEmpty()) {
            history_.append(ChatMessage{"assistant", resp.content});
            emit reply(resp.content);
            return;
        }

        // Execute tools
        for (const auto& tc : resp.toolCalls) {
            emit toolCalled(tc.name, "执行中...");
            QString result = executeTool(tc.name, tc.arguments, engine_);
            emit toolCalled(tc.name, result);
            msgs.append(ChatMessage{"assistant", resp.content});
            msgs.append(ChatMessage{"user", result});
        }
    }

    emit reply("工具调用轮次用尽");
}

} // namespace ai
} // namespace mbdsdr
