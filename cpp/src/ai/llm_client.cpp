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

namespace {
// Phase32 block2: build an honest user-facing error line for an HTTP error
// status. QNetworkReply::errorString() drops the numeric code ("server replied:
// Too Many Requests"), so we read the real code from HttpStatusCodeAttribute and
// carry it here. Append the server's own reason when the body follows the OpenAI
// shape {"error":{"message":...}}, and an explicit API-key hint on 401/403.
QString httpStatusErrorMessage(int code, const QByteArray& body) {
    QString msg = QString::fromUtf8("HTTP %1").arg(code);
    QJsonDocument d = QJsonDocument::fromJson(body);
    if (d.isObject()) {
        QJsonObject err = d.object().value(QStringLiteral("error")).toObject();
        QString reason = err.value(QStringLiteral("message")).toString();
        if (!reason.isEmpty()) msg += QStringLiteral(": ") + reason;
    }
    if (code == 401 || code == 403)
        msg += QString::fromUtf8("（请检查 API Key 是否有效/是否有额度）");
    return msg;
}
} // namespace

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

LLMResponse LLMClient::errorResponseWithPartial(const QString& partialContent,
                                                const QString& error) {
    LLMResponse r;
    r.content = partialContent;   // the half-sentence already on screen survives
    r.error = error;
    return r;
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
    // Phase32 block2: real HTTP status code from the transport. Stays 0 on the
    // offline-mock path (no socket, no status line) -> the worker then classifies
    // by the Phase31 string heuristic instead.
    int httpStatus = 0;

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

        // Read the REAL HTTP status code. This is the only place the numeric code
        // survives: QNetworkReply::errorString() renders it as a reason phrase
        // ("server replied: Too Many Requests") with no number. The attribute is
        // invalid when no HTTP status line arrived (conn refused / DNS / TLS /
        // timeout), in which case httpStatus correctly stays 0.
        {
            QVariant sv = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute);
            if (sv.isValid()) httpStatus = sv.toInt();
        }
        const QNetworkReply::NetworkError netErr = reply->error();

        if (onChunk) {
            feedSseText(sseBuf, opts.protocol, acc, nullptr);   // flush trailing
            // Phase31 G1: keep whatever partial content already streamed (the user
            // saw the half sentence on screen); attach the error + the real code.
            LLMResponse r = acc.toResponse();
            r.httpStatus = httpStatus;
            if (netErr != QNetworkReply::NoError) {
                // QNAM maps non-2xx statuses onto error(), but its errorString()
                // drops the numeric code ("server replied: Too Many Requests").
                // When we DO have a code, render it (with the key hint) ourselves.
                r.error = (httpStatus > 0)
                              ? httpStatusErrorMessage(httpStatus, QByteArray())
                              : reply->errorString();
            } else if (httpStatus >= 300) {
                r.error = httpStatusErrorMessage(httpStatus, QByteArray());
            }
            reply->deleteLater();
            return r;
        }

        raw = reply->readAll();
        const QString netErrString = reply->errorString();
        reply->deleteLater();

        if (netErr != QNetworkReply::NoError) {
            // Transport-layer abort (timeout / reset / DNS / TLS). httpStatus may
            // be 0 (no status line) or a code seen before the abort -- the worker
            // classifies on whichever is present. Render the code (+server reason
            // body) when known; else fall back to the network error string.
            resp.httpStatus = httpStatus;
            resp.error = (httpStatus > 0)
                             ? httpStatusErrorMessage(httpStatus, raw)
                             : netErrString;
            return resp;
        }
        if (httpStatus >= 300) {
            // The server answered with an error status but QNAM did not abort the
            // request: surface the numeric code + server reason so the worker can
            // classify (429/503 retry, 401 key hint, 400 terminal).
            resp.httpStatus = httpStatus;
            resp.error = httpStatusErrorMessage(httpStatus, raw);
            return resp;
        }
    }

    if (onChunk) {
        LLMResponse r = acc.toResponse();
        r.httpStatus = httpStatus;   // 0 on the offline-mock path
        return r;
    }

    resp.httpStatus = httpStatus;
    QString err;
    if (!parseChatResponse(raw, opts.protocol, &resp, &err)) {
        resp.error = err.isEmpty() ? QStringLiteral("response parse error") : err;
    }
    return resp;
}

} // namespace ai
} // namespace mbdsdr
