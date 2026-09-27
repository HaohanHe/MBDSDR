// SPDX-License-Identifier: MIT
#include "llm_client.h"

#include <QNetworkRequest>
#include <QNetworkReply>
#include <QJsonDocument>
#include <QJsonArray>
#include <QEventLoop>
#include <QTimer>

namespace mbdsdr {
namespace ai {

LLMClient::LLMClient(QObject* parent) : QObject(parent) {
    nam_ = new QNetworkAccessManager(this);
}

LLMResponse LLMClient::chat(const QList<ChatMessage>& messages,
                             const QList<ToolDef>& tools) {
    LLMResponse resp;
    if (apiKey_.isEmpty()) {
        resp.error = "API key not configured";
        return resp;
    }

    QJsonObject root;
    root["model"] = model_;

    QJsonArray msgs;
    for (const auto& m : messages) {
        QJsonObject mo;
        mo["role"] = m.role;
        mo["content"] = m.content;
        msgs.append(mo);
    }
    root["messages"] = msgs;

    if (!tools.isEmpty()) {
        QJsonArray toolArr;
        for (const auto& t : tools) {
            QJsonObject fn;
            fn["name"] = t.name;
            fn["description"] = t.description;
            fn["parameters"] = t.parameters;
            QJsonObject tool;
            tool["type"] = "function";
            tool["function"] = fn;
            toolArr.append(tool);
        }
        root["tools"] = toolArr;
        root["tool_choice"] = "auto";
    }

    QNetworkRequest req(QUrl(baseUrl_ + "/chat/completions"));
    req.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    req.setRawHeader("Authorization", ("Bearer " + apiKey_).toUtf8());

    QNetworkReply* reply = nam_->post(req, QJsonDocument(root).toJson());

    QEventLoop loop;
    QTimer::singleShot(30000, &loop, &QEventLoop::quit);
    connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    loop.exec();

    reply->deleteLater();

    if (reply->error() != QNetworkReply::NoError) {
        resp.error = reply->errorString();
        return resp;
    }

    QJsonParseError parseErr;
    auto doc = QJsonDocument::fromJson(reply->readAll(), &parseErr);
    if (parseErr.error != QJsonParseError::NoError) {
        resp.error = "JSON parse error: " + parseErr.errorString();
        return resp;
    }

    auto choices = doc.object()["choices"].toArray();
    if (choices.isEmpty()) {
        resp.error = "no choices in response";
        return resp;
    }

    auto msg = choices[0].toObject()["message"].toObject();
    resp.content = msg["content"].toString();

    auto toolCalls = msg["tool_calls"].toArray();
    for (const auto& tc : toolCalls) {
        auto fn = tc.toObject()["function"].toObject();
        ToolCall call;
        call.name = fn["name"].toString();
        QString argsStr = fn["arguments"].toString();
        QJsonParseError pe;
        call.arguments = QJsonDocument::fromJson(argsStr.toUtf8(), &pe).object();
        resp.toolCalls.append(call);
    }

    return resp;
}

} // namespace ai
} // namespace mbdsdr
