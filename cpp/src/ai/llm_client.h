// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <QList>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QObject>
#include <functional>

namespace mbdsdr {
namespace ai {

struct ChatMessage {
    QString role;
    QString content;
    QString toolCallId;              // set when role == "tool"
    QList<struct ToolCall> toolCalls; // assistant message with pending calls
};

struct ToolCall {
    QString id;
    QString name;
    QJsonObject arguments;
};

struct ToolDef {
    QString name;
    QString description;
    QJsonObject parameters;  // JSON Schema
};

struct LLMResponse {
    QString content;
    QList<ToolCall> toolCalls;
    QString error;
};

class LLMClient : public QObject {
    Q_OBJECT
public:
    explicit LLMClient(QObject* parent = nullptr);

    void setApiKey(const QString& key) { apiKey_ = key; }
    void setBaseUrl(const QString& url) { baseUrl_ = url; }
    void setModel(const QString& m) { model_ = m; }

    // Blocking one-shot chat. If `onChunk` is non-null the request is made in
    // streaming (SSE) mode and `onChunk` is called with the ACCUMULATED
    // content so far on every chunk (the UI can replace its transient line
    // with it, never duplicating). With a null callback the legacy whole-JSON
    // path is used. On the wire a message with role "summary" is mapped to a
    // "system" note so the upstream API only ever sees valid roles.
    using ChunkCallback = std::function<void(const QString& accumulatedSoFar)>;
    LLMResponse chat(const QList<ChatMessage>& messages,
                     const QList<ToolDef>& tools,
                     ChunkCallback onChunk = nullptr);

private:
    QNetworkAccessManager* nam_;
    QString apiKey_;
    QString baseUrl_ = "https://api.siliconflow.cn/v1";
    QString model_ = "Qwen/Qwen2.5-7B-Instruct";
};

} // namespace ai
} // namespace mbdsdr
