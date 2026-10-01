// SPDX-License-Identifier: MIT
// M4: real function-calling loop integration. Fully offline -- a canned mock
// transport is injected (LLMClient::setDefaultTransportForTests) so LLMWorker's
// lazily-created client never opens a socket and no API key is required.
//
// Covers impl-spec §6:
//   1  multi-round loop: assistant reasoning_content round-trips verbatim,
//      role=tool messages pair strictly by tool_call_id and follow the assistant
//   2  out-of-range arguments -> rejected, NOT executed, errorJson fed back
//   3  hallucinated (schema-external) argument -> rejected, NOT executed
//   4  finish_reason=stop terminates the loop immediately
//   5  round cap (kAiMaxToolRounds) force-stops a tool-calling model
//   6  streaming SSE: tool_calls reassembled by index slot, reasoning_content
//      accumulated verbatim, finish_reason surfaced
//   7  thinking config per model: MiMo tool-round request has NO enable_thinking,
//      DeepSeek always sends enable_thinking + thinking_budget
//   8  Anthropic protocol: tool_use -> tool_result request/response shape
#include <QtTest/QtTest>
#include <QSignalSpy>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QNetworkRequest>

#include "ai/llm_client.h"
#include "ai/llm_worker.h"
#include "ai/agent_tools.h"
#include "core/tokens.h"

using namespace mbdsdr;

namespace {

struct FakeCall { QString id; QString name; QString argsJson; };

// Build an OpenAI streaming (SSE) body for one assistant turn that requests tools.
QByteArray sseToolCalls(const QString& reasoning, const QList<FakeCall>& calls) {
    QByteArray out;
    auto emitChunk = [&](const QJsonObject& chunk) {
        out += "data: " + QJsonDocument(chunk).toJson(QJsonDocument::Compact) + "\n\n";
    };
    if (!reasoning.isEmpty()) {
        QJsonObject delta; delta["reasoning_content"] = reasoning;
        QJsonObject ch; ch["delta"] = delta;
        QJsonObject root; root["choices"] = QJsonArray{ch};
        emitChunk(root);
    }
    for (int i = 0; i < calls.size(); ++i) {
        QJsonObject fn; fn["name"] = calls[i].name; fn["arguments"] = calls[i].argsJson;
        QJsonObject tc; tc["index"] = i; tc["id"] = calls[i].id;
        tc["type"] = "function"; tc["function"] = fn;
        QJsonObject delta; delta["tool_calls"] = QJsonArray{tc};
        QJsonObject ch; ch["delta"] = delta;
        QJsonObject root; root["choices"] = QJsonArray{ch};
        emitChunk(root);
    }
    QJsonObject ch; ch["delta"] = QJsonObject{}; ch["finish_reason"] = "tool_calls";
    QJsonObject root; root["choices"] = QJsonArray{ch};
    emitChunk(root);
    out += "data: [DONE]\n\n";
    return out;
}

QByteArray sseStop(const QString& text) {
    QByteArray out;
    QJsonObject delta; if (!text.isEmpty()) delta["content"] = text;
    QJsonObject ch; ch["delta"] = delta; ch["finish_reason"] = "stop";
    QJsonObject root; root["choices"] = QJsonArray{ch};
    out += "data: " + QJsonDocument(root).toJson(QJsonDocument::Compact) + "\n\n";
    out += "data: [DONE]\n\n";
    return out;
}

QJsonArray messagesOf(const QByteArray& body) {
    return QJsonDocument::fromJson(body).object().value("messages").toArray();
}

QList<ai::ChatMessage> baseChat() {
    QList<ai::ChatMessage> msgs;
    msgs.append(ai::ChatMessage{"system", "你是测试助手。"});
    msgs.append(ai::ChatMessage{"user", "测一下"});
    return msgs;
}

// Scripted responder: records every request body, returns the canned response
// for that call index (clamped to the last one once exhausted).
struct ScriptedMock {
    QVector<QByteArray> calls;
    QVector<QByteArray> responses;
    int next = 0;
    QByteArray run(const QNetworkRequest&, const QByteArray& body) {
        calls.append(body);
        QByteArray r = responses.at(qMin(next, responses.size() - 1));
        ++next;
        return r;
    }
    void arm() {
        ai::LLMClient::setDefaultTransportForTests(
            [this](const QNetworkRequest& req, const QByteArray& b) { return run(req, b); });
    }
};

} // namespace

class TestAiToolLoop : public QObject {
    Q_OBJECT
private slots:
    void multiRound_reasoningAndPairing();
    void outOfRangeArgs_notExecuted();
    void hallucinatedArg_rejected();
    void stop_finishesImmediately();
    void roundCap_forceStops();
    void sse_streams_reassembles();
    void thinking_config_perModel();
    void anthropic_toolUseResult();
};

// ---------------------------------------------------------------------------
void TestAiToolLoop::multiRound_reasoningAndPairing() {
    ScriptedMock mock;
    mock.responses = {
        sseToolCalls("思考链A", {FakeCall{"call_1", "get_status", "{}"}}),
        sseStop("最终回答B"),
    };
    mock.arm();

    ai::LLMWorker worker;
    worker.setApiKey("test-key");
    worker.setBaseUrl("http://127.0.0.1:9");
    worker.setModel("Qwen/Qwen2.5-7B-Instruct");

    QSignalSpy finished(&worker, &ai::LLMWorker::chatFinished);
    QSignalSpy tooled(&worker, &ai::LLMWorker::toolCalled);

    worker.doChat(baseChat(), ai::toolDefs());

    QCOMPARE(finished.count(), 1);
    QCOMPARE(finished.takeFirst().at(0).toString(), QString("最终回答B"));
    QCOMPARE(tooled.count(), 1);

    // Second request = the tool-feedback round; inspect the assembled messages.
    QJsonArray msgs = messagesOf(mock.calls.at(1));
    int asstIdx = -1, toolIdx = -1;
    for (int i = 0; i < msgs.size(); ++i) {
        QJsonObject o = msgs.at(i).toObject();
        if (o.value("role").toString() == "assistant") {
            QCOMPARE(o.value("reasoning_content").toString(), QString("思考链A"));
            asstIdx = i;
        }
        if (o.value("role").toString() == "tool") {
            QCOMPARE(o.value("tool_call_id").toString(), QString("call_1"));
            toolIdx = i;
        }
    }
    QVERIFY(asstIdx >= 0);
    QVERIFY(toolIdx >= 0);
    // assistant message immediately followed by its paired tool result.
    QCOMPARE(toolIdx, asstIdx + 1);
}

// ---------------------------------------------------------------------------
void TestAiToolLoop::outOfRangeArgs_notExecuted() {
    ScriptedMock mock;
    mock.responses = {
        sseToolCalls("", {FakeCall{"call_bad", "tune_frequency", "{\"freq_hz\":3e99}"}}),
        sseStop("已纠正"),
    };
    mock.arm();

    ai::LLMWorker worker;
    worker.setApiKey("k");
    worker.setBaseUrl("http://127.0.0.1:9");
    worker.setModel("Qwen/Qwen2.5-7B-Instruct");
    QSignalSpy tooled(&worker, &ai::LLMWorker::toolCalled);
    worker.doChat(baseChat(), ai::toolDefs());

    QCOMPARE(tooled.count(), 1);
    QString result = tooled.takeFirst().at(1).toString();
    // Validation error JSON, NOT an engine result and NOT silent.
    // (QJsonObject compact output orders keys alphabetically: error/ok/reasons/tool.)
    QJsonDocument errDoc = QJsonDocument::fromJson(result.toUtf8());
    QVERIFY2(errDoc.isObject(), qPrintable("expected validation errorJson object, got: " + result));
    QVERIFY2(errDoc.object().value("ok").toBool() == false,
             qPrintable("expected ok:false, got: " + result));
    QVERIFY2(!errDoc.object().value("reasons").toArray().isEmpty(),
             qPrintable("expected non-empty reasons, got: " + result));

    // The fed-back role=tool message must carry the range error to the model.
    QJsonArray msgs = messagesOf(mock.calls.at(1));
    bool sawError = false;
    for (const QJsonValue& v : msgs) {
        QJsonObject o = v.toObject();
        if (o.value("role").toString() != "tool") continue;
        QJsonObject c = QJsonDocument::fromJson(
            o.value("content").toString().toUtf8()).object();
        QCOMPARE(c.value("ok").toBool(), false);
        QString reasons = QString::fromUtf8(
            c.value("reasons").toArray().size()
                ? QJsonDocument(c.value("reasons").toArray()).toJson(QJsonDocument::Compact)
                : QByteArray());
        QVERIFY2(reasons.contains("freq_hz"),
                 qPrintable("expected freq_hz range error, reasons=" + reasons));
        sawError = true;
    }
    QVERIFY(sawError);
}

// ---------------------------------------------------------------------------
void TestAiToolLoop::hallucinatedArg_rejected() {
    ScriptedMock mock;
    mock.responses = {
        sseToolCalls("", {FakeCall{"call_h", "tune_frequency",
                                  "{\"freq_hz\":100000000,\"bogus_param\":123}"}}),
        sseStop("已纠正"),
    };
    mock.arm();

    ai::LLMWorker worker;
    worker.setApiKey("k");
    worker.setBaseUrl("http://127.0.0.1:9");
    QSignalSpy tooled(&worker, &ai::LLMWorker::toolCalled);
    worker.doChat(baseChat(), ai::toolDefs());

    QCOMPARE(tooled.count(), 1);
    QString result = tooled.takeFirst().at(1).toString();
    QVERIFY2(result.contains("bogus_param"),
             qPrintable("expected hallucinated-param rejection, got: " + result));
}

// ---------------------------------------------------------------------------
void TestAiToolLoop::stop_finishesImmediately() {
    ScriptedMock mock;
    mock.responses = { sseStop("你好") };
    mock.arm();

    ai::LLMWorker worker;
    worker.setApiKey("k");
    worker.setBaseUrl("http://127.0.0.1:9");
    QSignalSpy finished(&worker, &ai::LLMWorker::chatFinished);
    worker.doChat(baseChat(), ai::toolDefs());

    QCOMPARE(mock.calls.size(), 1);   // no second round
    QCOMPARE(finished.count(), 1);
    QCOMPARE(finished.takeFirst().at(0).toString(), QString("你好"));
}

// ---------------------------------------------------------------------------
void TestAiToolLoop::roundCap_forceStops() {
    ScriptedMock mock;
    // Every round: a (valid) tune_frequency call. The model never stops on its own.
    QByteArray turn = sseToolCalls("", {FakeCall{"c", "tune_frequency", "{\"freq_hz\":100000000}"}});
    for (int i = 0; i < 32; ++i) mock.responses.append(turn);
    mock.arm();

    ai::LLMWorker worker;
    worker.setApiKey("k");
    worker.setBaseUrl("http://127.0.0.1:9");
    QSignalSpy finished(&worker, &ai::LLMWorker::chatFinished);
    worker.doChat(baseChat(), ai::toolDefs());

    QCOMPARE(mock.calls.size(), (int)tokens::kAiMaxToolRounds);
    QCOMPARE(finished.count(), 1);
    QCOMPARE(finished.takeFirst().at(0).toString(), QString("工具调用轮次用尽"));
}

// ---------------------------------------------------------------------------
void TestAiToolLoop::sse_streams_reassembles() {
    // Direct client-level streaming: fragments split reasoning / content / and
    // the tool-call arguments string across chunks.
    QByteArray sse =
        "data: {\"choices\":[{\"delta\":{\"reasoning_content\":\"思\"}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"reasoning_content\":\"考\"}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"content\":\"最终\"}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"c1\","
        "\"type\":\"function\",\"function\":{\"name\":\"tune_frequency\",\"arguments\":\"{\\\"freq\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,"
        "\"function\":{\"arguments\":\"_hz\\\":100000000}\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"tool_calls\"}]}\n\n"
        "data: [DONE]\n\n";

    ai::LLMClient client;
    client.setApiKey("k");
    client.setBaseUrl("http://127.0.0.1:9");
    client.setTransport([&](const QNetworkRequest&, const QByteArray&) { return sse; });

    QList<ai::ChatMessage> msgs;
    msgs.append(ai::ChatMessage{"user", "hi"});

    QString accumulated;
    ai::LLMResponse r = client.chat(msgs, {}, [&](const QString& a) { accumulated = a; });

    QCOMPARE(r.reasoningContent, QString("思考"));
    QCOMPARE(r.content, QString("最终"));
    QCOMPARE(accumulated, QString("最终"));
    QCOMPARE(r.toolCalls.size(), 1);
    QCOMPARE(r.toolCalls.at(0).id, QString("c1"));
    QCOMPARE(r.toolCalls.at(0).name, QString("tune_frequency"));
    QCOMPARE(r.toolCalls.at(0).arguments.value("freq_hz").toDouble(), 100000000.0);
    QCOMPARE(r.finishReason, QString("tool_calls"));
}

// ---------------------------------------------------------------------------
void TestAiToolLoop::thinking_config_perModel() {
    // --- MiMo: tool round (round>=1) must NOT carry enable_thinking. ---
    {
        ScriptedMock mock;
        mock.responses = {
            sseToolCalls("", {FakeCall{"c1", "get_status", "{}"}}),
            sseStop("好了"),
        };
        mock.arm();
        ai::LLMWorker worker;
        worker.setApiKey("k");
        worker.setBaseUrl("http://127.0.0.1:9");
        worker.setModel("mimo-v2.6-pro");
        worker.doChat(baseChat(), ai::toolDefs());

        QJsonObject toolRound = QJsonDocument::fromJson(mock.calls.at(1)).object();
        QVERIFY2(!toolRound.contains("enable_thinking"),
                 "MiMo tool round must disable thinking (no enable_thinking key)");
    }
    // --- DeepSeek-V3.2: thinking always on with the configured budget. ---
    {
        ScriptedMock mock;
        mock.responses = {
            sseToolCalls("", {FakeCall{"c1", "get_status", "{}"}}),
            sseStop("好了"),
        };
        mock.arm();
        ai::LLMWorker worker;
        worker.setApiKey("k");
        worker.setBaseUrl("http://127.0.0.1:9");
        worker.setModel("deepseek-ai/DeepSeek-V3.2");
        worker.doChat(baseChat(), ai::toolDefs());

        QJsonObject first = QJsonDocument::fromJson(mock.calls.at(0)).object();
        QCOMPARE(first.value("enable_thinking").toBool(), true);
        QCOMPARE(first.value("thinking_budget").toInt(),
                 (int)tokens::kAiThinkingBudgetTokens);
    }
}

// ---------------------------------------------------------------------------
void TestAiToolLoop::anthropic_toolUseResult() {
    ai::LLMClient client;
    client.setApiKey("k");
    client.setBaseUrl("http://127.0.0.1:9");

    QVector<QByteArray> sent;
    client.setTransport([&](const QNetworkRequest&, const QByteArray& body) {
        sent.append(body);
        if (sent.size() == 1) {
            // Anthropic response: one tool_use block, stop_reason=tool_use.
            return QByteArray("{\"content\":[{\"type\":\"tool_use\",\"id\":\"tu_1\","
                               "\"name\":\"get_status\",\"input\":{}}],\"stop_reason\":\"tool_use\"}");
        }
        return QByteArray("{\"content\":[{\"type\":\"text\",\"text\":\"done\"}],"
                          "\"stop_reason\":\"end_turn\"}");
    });

    ai::RequestOptions opts;
    opts.protocol = ai::Protocol::Anthropic;
    opts.model = "mimo-v2.6-pro";
    opts.thinkingEnabled = true;

    QList<ai::ChatMessage> msgs;
    msgs.append(ai::ChatMessage{"system", "sys"});
    msgs.append(ai::ChatMessage{"user", "hi"});

    ai::LLMResponse r = client.chat(msgs, ai::toolDefs(), nullptr, opts);
    QCOMPARE(r.toolCalls.size(), 1);
    QCOMPARE(r.toolCalls.at(0).id, QString("tu_1"));
    QCOMPARE(r.toolCalls.at(0).name, QString("get_status"));
    QCOMPARE(r.finishReason, QString("tool_calls"));

    // Request wire shape: system top-level, tools as custom/input_schema.
    QJsonObject req1 = QJsonDocument::fromJson(sent.at(0)).object();
    QVERIFY(req1.contains("system"));
    QJsonArray tools = req1.value("tools").toArray();
    QCOMPARE(tools.at(0).toObject().value("type").toString(), QString("custom"));
    QVERIFY(tools.at(0).toObject().contains("input_schema"));

    // Feed the assistant tool_use + tool result back; Anthropic merges the
    // tool_result into a user content block.
    QList<ai::ChatMessage> msgs2 = msgs;
    ai::ChatMessage a; a.role = "assistant"; a.toolCalls = r.toolCalls;
    msgs2.append(a);
    ai::ChatMessage tr; tr.role = "tool"; tr.toolCallId = "tu_1"; tr.content = "{}";
    msgs2.append(tr);
    ai::LLMResponse r2 = client.chat(msgs2, ai::toolDefs(), nullptr, opts);
    QCOMPARE(r2.content, QString("done"));

    QJsonObject req2 = QJsonDocument::fromJson(sent.at(1)).object();
    bool sawToolResult = false;
    for (const QJsonValue& v : req2.value("messages").toArray()) {
        QJsonObject o = v.toObject();
        if (o.value("role").toString() != "user") continue;
        for (const QJsonValue& bv : o.value("content").toArray()) {
            QJsonObject b = bv.toObject();
            if (b.value("type").toString() == "tool_result") {
                QCOMPARE(b.value("tool_use_id").toString(), QString("tu_1"));
                sawToolResult = true;
            }
        }
    }
    QVERIFY(sawToolResult);
}

QTEST_MAIN(TestAiToolLoop)
#include "test_ai_tool_loop.moc"
