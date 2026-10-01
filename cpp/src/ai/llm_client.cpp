// SPDX-License-Identifier: MIT
#include "ai/llm_client.h"
#include "ai/llm_protocol.h"
#include "core/tokens.h"

#include <QNetworkRequest>
#include <QNetworkReply>
#include <QJsonDocument>
#include <QJsonArray>
#include <QEventLoop>
#include <QTimer>
#include <QMap>

namespace mbdsdr {
namespace ai {

// static process-wide test transport (null in production).
LLMClient::TransportFn LLMClient::s_testTransport;

void applyThinkingConfig(RequestOptions& opts, const QString& model, bool toolRound) {
    opts.thinkingEnabled = false;
    opts.thinkingBudget = 0;
    const bool deepseekV32 = model.contains(QStringLiteral("DeepSeek-V3.2"), Qt::CaseInsensitive);
    const bool glm47       = model.contains(QStringLiteral("GLM-4.7"),        Qt::CaseInsensitive);
    const bool mimo        = model.contains(QStringLiteral("mimo"),            Qt::CaseInsensitive);
    if (deepseekV32 || glm47) {
        // SiliconFlow interleaved-thinking models: thinking always on.
        opts.thinkingEnabled = true;
        opts.thinkingBudget = tokens::kAiThinkingBudgetTokens;
    } else if (mimo) {
        // MiMo: thinking on for the conversational first turn, off on tool rounds.
        opts.thinkingEnabled = !toolRound;
        if (opts.thinkingEnabled)
            opts.thinkingBudget = tokens::kAiThinkingBudgetTokens;
    }
    // Qwen / others: off.
}

// --- Shared SSE accumulator ------------------------------------------------
// Three physically-separated channels (content / reasoning / tool_call toolSlots),
// exactly per the streaming state machine (16-tool-loop-state-machine.md §4.2):
// reasoning_content and content are NEVER merged; tool_calls are reassembled by
// their `index` slot and arguments are parsed ONCE at stream end (never
// mid-fragment, where a half-JSON slice is guaranteed invalid).
struct StreamAccumulator {
    QString content;
    QString reasoning;
    QString finishReason;
    QMap<int, ToolCall> toolSlots;     // index -> partial tool call
    QMap<int, QString>  args;      // index -> accumulated arguments JSON string

    void feed(const StreamChunk& c, LLMClient::ChunkCallback onChunk) {
        content  += c.contentDelta;
        reasoning += c.reasoningDelta;
        for (const ToolCallDelta& d : c.toolDeltas) {
            while (toolSlots.size() <= d.index) toolSlots[d.index] = ToolCall{};
            ToolCall& s = toolSlots[d.index];
            if (!d.id.isEmpty())   s.id = d.id;
            if (!d.name.isEmpty()) s.name = d.name;
            args[d.index] += d.argumentsFragment;
        }
        if (!c.finishReason.isEmpty()) finishReason = c.finishReason;
        if (onChunk) onChunk(content);
    }

    LLMResponse toResponse() const {
        LLMResponse r;
        r.content = content;
        r.reasoningContent = reasoning;
        r.finishReason = finishReason;
        for (int i = 0; i < toolSlots.size(); ++i) {
            ToolCall tc = toolSlots.value(i);
            // Parse arguments ONCE here. A non-object / malformed slice yields an
            // empty object; the arguments validator (M2) then rejects it on the
            // next loop turn and feeds the errorJson back as role=tool -- we never
            // silently swallow a bad arguments blob, but we also never abort the
            // whole loop for it (impl-spec §4/§6).
            QJsonDocument ad = QJsonDocument::fromJson(args.value(i).toUtf8());
            tc.arguments = ad.isObject() ? ad.object() : QJsonObject();
            r.toolCalls.append(tc);
        }
        return r;
    }
};

void LLMClient::feedSseText(const QString& text, Protocol proto,
                            StreamAccumulator& acc, ChunkCallback onChunk) {
    int nl = 0;
    QString s = text;
    while ((nl = s.indexOf(QLatin1Char('\n'))) >= 0) {
        QString line = s.left(nl).trimmed();
        s.remove(0, nl + 1);
        if (!line.startsWith(QStringLiteral("data:"))) continue;
        QString data = line.mid(5).trimmed();
        if (data.isEmpty() || data == QLatin1String("[DONE]")) continue;
        acc.feed(parseStreamChunk(data.toUtf8(), proto), onChunk);
    }
    // trailing line without a newline
    QString tail = s.trimmed();
    if (tail.startsWith(QStringLiteral("data:"))) {
        QString data = tail.mid(5).trimmed();
        if (!data.isEmpty() && data != QLatin1String("[DONE]"))
            acc.feed(parseStreamChunk(data.toUtf8(), proto), onChunk);
    }
}

LLMClient::LLMClient(QObject* parent) : QObject(parent) {
    nam_ = new QNetworkAccessManager(this);
    transport_ = s_testTransport;   // offline test seam (null in production)
}

LLMResponse LLMClient::chat(const QList<ChatMessage>& messages,
                            const QList<ToolDef>& tools,
                            ChunkCallback onChunk,
                            const RequestOptions& optsIn) {
    LLMResponse resp;
    if (apiKey_.isEmpty()) {
        resp.error = "API key not configured";
        return resp;
    }

    RequestOptions opts = optsIn;
    if (opts.model.isEmpty()) opts.model = model_;
    opts.stream = (onChunk != nullptr);

    // The UI/context layer uses role "summary" for compacted history; the wire
    // API only accepts system/user/assistant/tool, so surface it as a system
    // note. Content (and reasoning) is preserved verbatim.
    QList<ChatMessage> wire;
    wire.reserve(messages.size());
    for (const ChatMessage& m : messages) {
        ChatMessage mm = m;
        if (mm.role == QLatin1String("summary")) mm.role = QStringLiteral("system");
        wire.append(mm);
    }

    // Build the wire request through the M3 protocol layer (OpenAI / Anthropic).
    QJsonObject root = buildChatRequest(wire, tools, opts);
    QByteArray payload = QJsonDocument(root).toJson(QJsonDocument::Compact);

    QNetworkRequest req(QUrl(baseUrl_ + QStringLiteral("/chat/completions")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    req.setRawHeader("Authorization", ("Bearer " + apiKey_).toUtf8());

    StreamAccumulator acc;
    QByteArray raw;

    if (transport_) {
        // ---- Offline / injected transport: fully synchronous, no socket. ----
        raw = transport_(req, payload);
        if (onChunk) feedSseText(QString::fromUtf8(raw), opts.protocol, acc, onChunk);
    } else {
        // ---- Real QNAM POST (production). -----------------------------------
        QNetworkReply* reply = nam_->post(req, payload);
        QEventLoop loop;
        QObject::connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
        QTimer::singleShot(tokens::kAiRequestTimeoutMs, &loop, &QEventLoop::quit);

        QString sseBuf;
        if (onChunk) {
            QObject::connect(reply, &QNetworkReply::readyRead, &loop, [&]() {
                sseBuf += QString::fromUtf8(reply->readAll());
                int nl = 0;
                while ((nl = sseBuf.indexOf(QLatin1Char('\n'))) >= 0) {
                    QString line = sseBuf.left(nl).trimmed();
                    sseBuf.remove(0, nl + 1);
                    if (!line.startsWith(QStringLiteral("data:"))) continue;
                    QString data = line.mid(5).trimmed();
                    if (data.isEmpty() || data == QLatin1String("[DONE]")) continue;
                    acc.feed(parseStreamChunk(data.toUtf8(), opts.protocol), onChunk);
                }
            });
        }
        loop.exec();

        if (reply->error() != QNetworkReply::NoError) {
            resp.error = reply->errorString();
            reply->deleteLater();
            return resp;
        }
        if (onChunk) {
            feedSseText(sseBuf, opts.protocol, acc, nullptr);   // flush trailing lines
        } else {
            raw = reply->readAll();
        }
        reply->deleteLater();
    }

    if (onChunk) return acc.toResponse();

    QString err;
    if (!parseChatResponse(raw, opts.protocol, &resp, &err)) {
        resp.error = err.isEmpty() ? QStringLiteral("response parse error") : err;
    }
    return resp;
}

} // namespace ai
} // namespace mbdsdr
