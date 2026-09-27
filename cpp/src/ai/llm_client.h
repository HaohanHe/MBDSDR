// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <QList>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QObject>

namespace mbdsdr {
namespace ai {

struct ChatMessage {
    QString role;
    QString content;
};

struct ToolCall {
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

    LLMResponse chat(const QList<ChatMessage>& messages,
                     const QList<ToolDef>& tools);

private:
    QNetworkAccessManager* nam_;
    QString apiKey_;
    QString baseUrl_ = "https://api.siliconflow.cn/v1";
    QString model_ = "Qwen/Qwen2.5-7B-Instruct";
};

} // namespace ai
} // namespace mbdsdr
