// SPDX-License-Identifier: MIT
#include "ai/llm_protocol.h"

#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonValue>

namespace mbdsdr {
namespace ai {

namespace {

// File-local defaults (M4 will promote these to tokens.h; kept local here so
// M3 stays a self-contained pure-function module).
constexpr int kDefaultMaxTokens = 4096;
// 推断: Anthropic/MiMo max_tokens must cover BODY + thinking (SF note says SF's
// max_tokens excludes the thinking budget; MiMo Anthropic page does not state
// one way or the other). When thinking is on we hand max_tokens a generous
// multiple of the budget so neither the chain-of-thought nor the final answer
// gets truncated. Chosen per task spec: thinkingBudget * 4.
constexpr int kThinkingMaxTokensMultiplier = 4;

QJsonObject openAiAssistantMessage(const ChatMessage& m) {
    QJsonObject o;
    o["role"] = m.role;
    o["content"] = m.content;
    if (!m.reasoningContent.isEmpty())
        o["reasoning_content"] = m.reasoningContent;
    if (!m.toolCalls.isEmpty()) {
        QJsonArray calls;
        for (const ToolCall& tc : m.toolCalls) {
            QJsonObject fn;
            fn["name"] = tc.name;
            // OpenAI wire: arguments is a JSON *string*, not an object.
            fn["arguments"] = QString::fromUtf8(
                QJsonDocument(tc.arguments).toJson(QJsonDocument::Compact));
            QJsonObject c;
            c["id"] = tc.id;
            c["type"] = QStringLiteral("function");
            c["function"] = fn;
            calls.append(c);
        }
        o["tool_calls"] = calls;
    }
    return o;
}

QJsonObject buildOpenAiRequest(const QList<ChatMessage>& messages,
                               const QList<ToolDef>& tools,
                               const RequestOptions& opts) {
    QJsonObject body;
    body["model"] = opts.model;
    body["stream"] = opts.stream;

    QJsonArray msgs;
    for (const ChatMessage& m : messages) {
        if (m.role == QLatin1String("assistant")) {
            msgs.append(openAiAssistantMessage(m));
        } else if (m.role == QLatin1String("tool")) {
            QJsonObject o;
            o["role"] = QStringLiteral("tool");
            o["tool_call_id"] = m.toolCallId;   // strict pairing with tool_calls[].id
            o["content"] = m.content;
            msgs.append(o);
        } else {
            // system / user
            QJsonObject o;
            o["role"] = m.role;
            o["content"] = m.content;
            msgs.append(o);
        }
    }
    body["messages"] = msgs;

    if (!tools.isEmpty()) {
        QJsonArray t;
        for (const ToolDef& d : tools) {
            QJsonObject fn;
            fn["name"] = d.name;
            fn["description"] = d.description;
            fn["parameters"] = d.parameters;
            QJsonObject wrap;
            wrap["type"] = QStringLiteral("function");
            wrap["function"] = fn;
            t.append(wrap);
        }
        body["tools"] = t;
        // tool_choice is always "auto" on these backends (others are stripped).
        body["tool_choice"] = opts.toolChoice.isEmpty()
                                  ? QStringLiteral("auto")
                                  : opts.toolChoice;
    }

    if (opts.thinkingEnabled) {
        // SiliconFlow shape (note 04): top-level enable_thinking + thinking_budget
        // (budget range 128..32768 enforced by M4 / tokens.h).
        body["enable_thinking"] = true;
        body["thinking_budget"] = opts.thinkingBudget;
    }
    return body;
}

// --- Anthropic (MiMo /anthropic/v1/messages) -------------------------------

QJsonObject anthropicTextBlock(const QString& text) {
    QJsonObject b;
    b["type"] = QStringLiteral("text");
    b["text"] = text;
    return b;
}

QJsonObject buildAnthropicRequest(const QList<ChatMessage>& messages,
                                 const QList<ToolDef>& tools,
                                 const RequestOptions& opts) {
    QJsonObject body;
    body["model"] = opts.model;
    body["stream"] = opts.stream;

    // system is a TOP-LEVEL parameter, not a message (note 11 §1.2).
    QStringList sysParts;
    QJsonArray msgs;
    // Consecutive role=="tool" results are merged into ONE user message whose
    // content[] holds N tool_result blocks (note 11 §1.4). We buffer them and
    // flush when the role changes.
    QJsonArray pendingToolResults;

    auto flushToolResults = [&]() {
        if (pendingToolResults.isEmpty()) return;
        QJsonObject u;
        u["role"] = QStringLiteral("user");
        u["content"] = pendingToolResults;
        msgs.append(u);
        pendingToolResults = QJsonArray();
    };

    for (const ChatMessage& m : messages) {
        if (m.role == QLatin1String("system")) {
            sysParts << m.content;
            continue;
        }
        if (m.role == QLatin1String("tool")) {
            QJsonObject tr;
            tr["type"] = QStringLiteral("tool_result");
            tr["tool_use_id"] = m.toolCallId;   // pairs with assistant tool_use.id
            tr["content"] = m.content;
            pendingToolResults.append(tr);
            continue;
        }
        flushToolResults();

        if (m.role == QLatin1String("user")) {
            QJsonObject u;
            u["role"] = QStringLiteral("user");
            u["content"] = QJsonArray{anthropicTextBlock(m.content)};
            msgs.append(u);
        } else if (m.role == QLatin1String("assistant")) {
            QJsonObject a;
            a["role"] = QStringLiteral("assistant");
            QJsonArray blocks;
            if (!m.reasoningContent.isEmpty()) {
                // 推断: thinking block shape {type:"thinking", thinking:...};
                // MiMo SPA did not expand sub-fields (note 11 §1.6). Preserved
                // verbatim so the model's own chain-of-thought round-trips.
                QJsonObject tb;
                tb["type"] = QStringLiteral("thinking");
                tb["thinking"] = m.reasoningContent;
                blocks.append(tb);
            }
            if (!m.content.isEmpty())
                blocks.append(anthropicTextBlock(m.content));
            for (const ToolCall& tc : m.toolCalls) {
                QJsonObject tu;
                tu["type"] = QStringLiteral("tool_use");
                tu["id"] = tc.id;
                tu["name"] = tc.name;
                // Anthropic: input is a JSON OBJECT directly (not a string).
                tu["input"] = tc.arguments;
                blocks.append(tu);
            }
            a["content"] = blocks;
            msgs.append(a);
        }
    }
    flushToolResults();

    if (!sysParts.isEmpty())
        body["system"] = sysParts.join(QStringLiteral("\n\n"));
    body["messages"] = msgs;

    if (!tools.isEmpty()) {
        QJsonArray t;
        for (const ToolDef& d : tools) {
            QJsonObject o;
            o["type"] = QStringLiteral("custom");
            o["name"] = d.name;
            o["description"] = d.description;
            o["input_schema"] = d.parameters;
            t.append(o);
        }
        body["tools"] = t;
        // 推断: tool_choice on Anthropic is {type:"auto", disable_parallel_tool_use:..};
        // we only ever send auto (note 11 §1.5), so omit it to stay minimal.
    }

    // max_tokens (Anthropic) vs max_completion_tokens (OpenAI).
    int maxTokens = kDefaultMaxTokens;
    if (opts.thinkingEnabled)
        maxTokens = opts.thinkingBudget * kThinkingMaxTokensMultiplier;
    body["max_tokens"] = maxTokens;

    // MiMo Anthropic thinking switch (note 11 §1.6): {type: enabled|disabled}.
    QJsonObject thinking;
    thinking["type"] = opts.thinkingEnabled ? QStringLiteral("enabled")
                                            : QStringLiteral("disabled");
    body["thinking"] = thinking;
    return body;
}

} // namespace

QJsonObject buildChatRequest(const QList<ChatMessage>& messages,
                             const QList<ToolDef>& tools,
                             const RequestOptions& opts) {
    if (opts.protocol == Protocol::Anthropic)
        return buildAnthropicRequest(messages, tools, opts);
    return buildOpenAiRequest(messages, tools, opts);
}

// --- Non-streaming response parsing ----------------------------------------

bool parseChatResponse(const QByteArray& body, Protocol proto,
                       LLMResponse* out, QString* err) {
    if (!out) return false;
    QJsonParseError pe{};
    QJsonDocument doc = QJsonDocument::fromJson(body, &pe);
    if (doc.isNull() || !doc.isObject()) {
        if (err) *err = QStringLiteral("invalid JSON: %1").arg(pe.errorString());
        return false;
    }
    QJsonObject root = doc.object();
    out->content.clear();
    out->reasoningContent.clear();
    out->toolCalls.clear();
    out->finishReason.clear();
    out->error.clear();

    if (proto == Protocol::OpenAI) {
        QJsonArray choices = root.value("choices").toArray();
        if (choices.isEmpty()) {
            if (err) *err = QStringLiteral("no choices in response");
            return false;
        }
        QJsonObject ch = choices.at(0).toObject();
        out->finishReason = ch.value("finish_reason").toString();
        QJsonObject msg = ch.value("message").toObject();
        out->content = msg.value("content").toString();
        out->reasoningContent = msg.value("reasoning_content").toString();
        QJsonArray calls = msg.value("tool_calls").toArray();
        for (const QJsonValue& cv : calls) {
            QJsonObject c = cv.toObject();
            QJsonObject fn = c.value("function").toObject();
            ToolCall tc;
            tc.id = c.value("id").toString();
            tc.name = fn.value("name").toString();
            // OpenAI: arguments is a JSON *string* that must be parsed.
            QString argsStr = fn.value("arguments").toString();
            QJsonDocument ad = QJsonDocument::fromJson(argsStr.toUtf8());
            tc.arguments = ad.isObject() ? ad.object() : QJsonObject();
            out->toolCalls.append(tc);
        }
    } else { // Anthropic
        QJsonArray content = root.value("content").toArray();
        for (const QJsonValue& bv : content) {
            QJsonObject b = bv.toObject();
            QString type = b.value("type").toString();
            if (type == QLatin1String("text")) {
                out->content += b.value("text").toString();
            } else if (type == QLatin1String("thinking")) {
                // 推断: thinking text lives under "thinking" (note 11 §1.6 did
                // not expand sub-fields; signature ignored).
                out->reasoningContent += b.value("thinking").toString();
            } else if (type == QLatin1String("tool_use")) {
                ToolCall tc;
                tc.id = b.value("id").toString();
                tc.name = b.value("name").toString();
                // Anthropic: input is already an object -> straight into args.
                tc.arguments = b.value("input").toObject();
                out->toolCalls.append(tc);
            }
        }
        QString stop = root.value("stop_reason").toString();
        // Map Anthropic stop_reason onto the canonical finishReason vocabulary.
        out->finishReason = (stop == QLatin1String("tool_use"))
                                ? QStringLiteral("tool_calls")
                                : (stop.isEmpty() ? QString() : stop);
    }
    return true;
}

// --- Streaming chunk parsing -----------------------------------------------

namespace {

QByteArray stripSseDataPrefix(const QByteArray& line) {
    QByteArray s = line.trimmed();
    if (s.startsWith("data:")) s = s.mid(5).trimmed();
    return s;
}

} // namespace

StreamChunk parseStreamChunk(const QByteArray& payload, Protocol proto) {
    StreamChunk chunk;
    QByteArray s = stripSseDataPrefix(payload);
    if (s.isEmpty()) return chunk;
    if (s == "[DONE]") return chunk;

    QJsonParseError pe{};
    QJsonDocument doc = QJsonDocument::fromJson(s, &pe);
    if (doc.isNull() || !doc.isObject()) return chunk;
    QJsonObject root = doc.object();

    if (proto == Protocol::OpenAI) {
        QJsonArray choices = root.value("choices").toArray();
        if (choices.isEmpty()) return chunk;
        QJsonObject ch = choices.at(0).toObject();
        chunk.finishReason = ch.value("finish_reason").toString();
        QJsonObject delta = ch.value("delta").toObject();
        chunk.contentDelta = delta.value("content").toString();
        chunk.reasoningDelta = delta.value("reasoning_content").toString();
        QJsonArray tcs = delta.value("tool_calls").toArray();
        for (const QJsonValue& tv : tcs) {
            QJsonObject t = tv.toObject();
            ToolCallDelta d;
            d.index = t.value("index").toInt();
            d.id = t.value("id").toString();
            QJsonObject fn = t.value("function").toObject();
            d.name = fn.value("name").toString();
            d.argumentsFragment = fn.value("arguments").toString();
            chunk.toolDeltas.append(d);
        }
    } else { // Anthropic event stream
        // Anthropic SSE carries `event:` lines plus `data:` bodies. We only
        // receive the data body here; its "type" tells us which delta this is.
        // 推断: event naming follows Anthropic standard / note 11 §1.6
        // (thinking_delta, input_json_delta+partial_json). MiMo did not expand
        // the streaming block shapes in the captured SPA page.
        QString type = root.value("type").toString();
        if (type == QLatin1String("content_block_start")) {
            QJsonObject block = root.value("content_block").toObject();
            if (block.value("type").toString() == QLatin1String("tool_use")) {
                ToolCallDelta d;
                d.index = static_cast<int>(root.value("index").toDouble());
                d.id = block.value("id").toString();
                d.name = block.value("name").toString();
                chunk.toolDeltas.append(d);
            }
        } else if (type == QLatin1String("content_block_delta")) {
            QJsonObject d = root.value("delta").toObject();
            QString dt = d.value("type").toString();
            if (dt == QLatin1String("text_delta")) {
                chunk.contentDelta = d.value("text").toString();
            } else if (dt == QLatin1String("thinking_delta")) {
                chunk.reasoningDelta = d.value("thinking").toString();
            } else if (dt == QLatin1String("input_json_delta")) {
                ToolCallDelta td;
                td.index = static_cast<int>(root.value("index").toDouble());
                td.argumentsFragment = d.value("partial_json").toString();
                chunk.toolDeltas.append(td);
            }
        } else if (type == QLatin1String("message_delta")) {
            QJsonObject d = root.value("delta").toObject();
            QString stop = d.value("stop_reason").toString();
            chunk.finishReason = (stop == QLatin1String("tool_use"))
                                     ? QStringLiteral("tool_calls")
                                     : stop;
        }
    }
    return chunk;
}

} // namespace ai
} // namespace mbdsdr
