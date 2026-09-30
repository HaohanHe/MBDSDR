// SPDX-License-Identifier: MIT
#include "llm_client.h"

#include <QNetworkRequest>
#include <QNetworkReply>
#include <QJsonDocument>
#include <QJsonArray>
#include <QEventLoop>
#include <QTimer>
#include <QMap>

namespace mbdsdr {
namespace ai {

LLMClient::LLMClient(QObject* parent) : QObject(parent) {
    nam_ = new QNetworkAccessManager(this);
}

LLMResponse LLMClient::chat(const QList<ChatMessage>& messages,
                             const QList<ToolDef>& tools,
                             ChunkCallback onChunk) {
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
        // The UI/context layer uses role "summary" for compacted history; the
        // wire API only accepts system/user/assistant/tool, so surface it as a
        // system note. Content is preserved verbatim.
        const QString wireRole = (m.role == "summary") ? QStringLiteral("system") : m.role;
        mo["role"] = wireRole;
        mo["content"] = m.content;
        if (m.role == "tool" && !m.toolCallId.isEmpty())
            mo["tool_call_id"] = m.toolCallId;
        if (!m.toolCalls.isEmpty()) {
            QJsonArray tcArr;
            for (const auto& tc : m.toolCalls) {
                QJsonObject o;
                o["id"] = tc.id;
                o["type"] = "function";
                QJsonObject fn;
                fn["name"] = tc.name;
                fn["arguments"] = QString::fromUtf8(QJsonDocument(tc.arguments).toJson(QJsonDocument::Compact));
                o["function"] = fn;
                tcArr.append(o);
            }
            mo["tool_calls"] = tcArr;
        }
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

    if (onChunk) root["stream"] = true;

    QNetworkRequest req(QUrl(baseUrl_ + "/chat/completions"));
    req.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    req.setRawHeader("Authorization", ("Bearer " + apiKey_).toUtf8());

    QNetworkReply* reply = nam_->post(req, QJsonDocument(root).toJson());

    // Shared bookkeeping for the SSE stream (tool_call deltas are fragmented
    // across chunks and must be reassembled by their index).
    QString accumulated;
    QList<ToolCall> partialTools;
    QMap<int, QString> toolArgs;   // index -> accumulated arguments JSON string
    QString sseBuf;                // partial SSE line accumulator for this reply

    QEventLoop loop;
    if (onChunk) {
        // ---- Streaming (SSE) path: pump readyRead, emit accumulated content.
        QObject::connect(reply, &QNetworkReply::readyRead, &loop, [&]() {
            sseBuf += QString::fromUtf8(reply->readAll());
            int nl = 0;
            while ((nl = sseBuf.indexOf('\n')) >= 0) {
                QString line = sseBuf.left(nl).trimmed();
                sseBuf.remove(0, nl + 1);
                if (!line.startsWith("data:")) continue;
                QString data = line.mid(5).trimmed();
                if (data == "[DONE]") continue;
                QJsonParseError pe;
                QJsonDocument d = QJsonDocument::fromJson(data.toUtf8(), &pe);
                if (pe.error != QJsonParseError::NoError) continue;
                auto choicesArr = d.object()["choices"].toArray();
                if (choicesArr.isEmpty()) continue;
                auto choice = choicesArr.at(0).toObject();
                auto delta = choice["delta"].toObject();
                QString dc = delta["content"].toString();
                if (!dc.isEmpty()) {
                    accumulated += dc;
                    onChunk(accumulated);
                }
                for (const auto& v : delta["tool_calls"].toArray()) {
                    auto o = v.toObject();
                    int idx = o["index"].toInt();
                    while (partialTools.size() <= idx) partialTools.append(ToolCall{});
                    auto& pc = partialTools[idx];
                    if (o.contains("id")) pc.id = o["id"].toString();
                    auto fn = o["function"].toObject();
                    if (fn.contains("name")) pc.name = fn["name"].toString();
                    if (fn.contains("arguments"))
                        toolArgs[idx] += fn["arguments"].toString();
                }
            }
        });
    }

    QTimer::singleShot(30000, &loop, &QEventLoop::quit);
    connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    loop.exec();

    reply->deleteLater();

    if (reply->error() != QNetworkReply::NoError) {
        resp.error = reply->errorString();
        return resp;
    }

    if (onChunk) {
        // Streaming result: accumulated content + reassembled tool calls.
        resp.content = accumulated;
        for (int i = 0; i < partialTools.size(); ++i) {
            ToolCall call = partialTools[i];
            QJsonParseError pe;
            call.arguments = QJsonDocument::fromJson(
                                    toolArgs.value(i).toUtf8(), &pe).object();
            resp.toolCalls.append(call);
        }
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
        auto tcObj = tc.toObject();
        auto fn = tcObj["function"].toObject();
        ToolCall call;
        call.id = tcObj["id"].toString();
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
