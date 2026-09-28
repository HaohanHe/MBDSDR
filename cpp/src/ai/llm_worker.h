// SPDX-License-Identifier: MIT
#pragma once

#include "llm_client.h"
#include <QObject>
#include <QString>

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }

namespace ai {

// Lives in a worker thread. The LLMClient (and its QNetworkAccessManager) is
// created lazily inside doChat() so the NAM ends up bound to the worker
// thread, not the thread that constructed this object. Cached config setters
// from the GUI thread are stored and re-applied on first use.
class LLMWorker : public QObject {
    Q_OBJECT
public:
    explicit LLMWorker(QObject* parent = nullptr);
    ~LLMWorker();
    void setEngine(dsp::SpectrumEngine* e) { engine_ = e; }
    void setApiKey(const QString& k) { apiKey_ = k; if (client_) client_->setApiKey(k); }
    void setBaseUrl(const QString& u) { baseUrl_ = u; if (client_) client_->setBaseUrl(u); }
    void setModel(const QString& m) { model_ = m; if (client_) client_->setModel(m); }

public slots:
    void doChat(const QList<ChatMessage>& messages, const QList<ToolDef>& tools);
    void cleanup();

signals:
    void chatFinished(const QString& text);
    void toolCalled(const QString& tool, const QString& result);

private:
    LLMClient* client_ = nullptr;   // created on worker thread
    QString apiKey_, baseUrl_, model_;
    dsp::SpectrumEngine* engine_ = nullptr;
};

} // namespace ai
} // namespace mbdsdr
