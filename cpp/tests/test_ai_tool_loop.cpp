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
#include <QTcpServer>
#include <QTcpSocket>
#include <QHostAddress>

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

// Phase32 block2: a REAL loopback HTTP server (QTcpServer) so the test exercises
// the production QNAM path end-to-end -- no injected transport, no socket mocking.
// It answers every accepted connection with a chosen HTTP status line + body.
// Connection: close means one connection == one request, so `hits` counts calls.
class LoopbackHttp : public QObject {
    Q_OBJECT
public:
    int statusCode = 200;
    QJsonObject errorBody;          // {"error":{...}} sent for >=400
    int hits = 0;

    explicit LoopbackHttp(QObject* parent = nullptr) : QObject(parent) {
        QObject::connect(&server, &QTcpServer::newConnection, this, [this]() {
            while (server.hasPendingConnections()) {
                QTcpSocket* sock = server.nextPendingConnection();
                ++hits;
                // Per-connection request accumulator + one-shot guard.
                QByteArray* req = new QByteArray;
                bool* responded = new bool(false);
                QObject::connect(sock, &QTcpSocket::readyRead, sock,
                    [this, sock, req, responded]() {
                        if (*responded) return;
                        *req += sock->readAll();
                        if (!req->contains("\r\n\r\n")) return;   // need full headers
                        *responded = true;
                        QByteArray body;
                        if (statusCode >= 400) {
                            QJsonObject root; root["error"] = errorBody;
                            body = QJsonDocument(root).toJson(QJsonDocument::Compact);
                        } else {
                            body = "{\"choices\":[{\"message\":{\"role\":\"assistant\","
                                   "\"content\":\"ok\"},\"finish_reason\":\"stop\"}]}";
                        }
                        QByteArray resp =
                            "HTTP/1.1 " + QByteArray::number(statusCode) + " " +
                            reasonPhrase(statusCode) + "\r\n"
                            "Content-Type: application/json\r\n"
                            "Content-Length: " + QByteArray::number(body.size()) + "\r\n"
                            "Connection: close\r\n\r\n" + body;
                        sock->write(resp);
                        sock->disconnectFromHost();
                        delete req; delete responded;
                    });
                QObject::connect(sock, &QTcpSocket::disconnected,
                                 sock, &QTcpSocket::deleteLater);
            }
        });
        QVERIFY(server.listen(QHostAddress::LocalHost, 0));
    }

    QUrl url() const {
        return QUrl(QStringLiteral("http://127.0.0.1:") +
                    QString::number(server.serverPort()));
    }

private:
    static QByteArray reasonPhrase(int c) {
        switch (c) {
            case 400: return "Bad Request";
            case 401: return "Unauthorized";
            case 408: return "Request Timeout";
            case 429: return "Too Many Requests";
            case 500: return "Internal Server Error";
            case 503: return "Service Unavailable";
            default:  return "OK";
        }
    }
    QTcpServer server;
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

    // Phase31 Wave2: A3 streaming-error honesty + A4 error-recovery.
    void errorResponseKeepsPartialContent();
    void chatErrorSignalNotFinished();
    void manualGatedWriteFedBackAsToolMessage();
    void errorClassTerminalVsRetryable();
    void backoffDelayExponentialAndClamped();

    // Phase32 block2: real HTTP status code transported + classified by code.
    void realHttpStatus_transportedAndClassified();
    void retryBudget_exhaustedOn429();
    void terminalOn401_noRetryWithKeyHint();
    void noKey_honestPending_noTransport();
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

// A3/G1: an error response KEEPS the partial content already streamed (the user
// saw the half sentence), and carries the error string alongside -- never wiped.
void TestAiToolLoop::errorResponseKeepsPartialContent() {
    auto r = ai::LLMClient::errorResponseWithPartial(QString::fromUtf8("已生成一半"),
                                                    QString("timed out"));
    QCOMPARE(r.content, QString::fromUtf8("已生成一半"));
    QCOMPARE(r.error, QString("timed out"));
}

// A3/G2: a failed chat emits chatError (not chatFinished), so the Agent does not
// persist a half/error line as a normal assistant reply. No API key => the
// client returns an honest terminal error immediately, fully offline.
void TestAiToolLoop::chatErrorSignalNotFinished() {
    ai::LLMWorker w;
    ai::LLMWorker::setBackoffSleepForTests([](int) {});   // no-op: no real sleep
    QSignalSpy errSpy(&w, &ai::LLMWorker::chatError);
    QSignalSpy finSpy(&w, &ai::LLMWorker::chatFinished);
    w.doChat(baseChat(), ai::toolDefs());
    QCOMPARE(errSpy.size(), 1);
    QCOMPARE(finSpy.size(), 0);                    // NOT a normal terminal answer
    const QString line = errSpy.takeFirst().at(0).toString();
    QVERIFY(line.contains(QString::fromUtf8("LLM")));   // honest, not a mock reply
    ai::LLMWorker::setBackoffSleepForTests(nullptr);    // restore real sleep
}

// A4 error-recovery layer 2: with the write gate on, a blocked write tool call's
// gated result is fed back to the model as a role=tool message (self-correction
// channel), not dropped and not auto-retried.
void TestAiToolLoop::manualGatedWriteFedBackAsToolMessage() {
    ScriptedMock mock;
    mock.responses = {
        sseToolCalls("", {FakeCall{"call1", "tune_frequency", "{\"freq_hz\":100000000}"}}),
        sseStop(QString::fromUtf8("写动作已被手动模式拦截")),
    };
    mock.arm();

    ai::LLMWorker w;
    w.setBaseUrl("https://example.com");
    w.setApiKey("test-key");
    w.setManualMode(true);   // gate ON: write tools refuse shut

    QSignalSpy fin(&w, &ai::LLMWorker::chatFinished);
    w.doChat(baseChat(), ai::toolDefs());

    QCOMPARE(fin.size(), 1);
    QVERIFY(mock.calls.size() >= 2);
    // The second request must carry the gated result as a role=tool message.
    QJsonObject req2 = QJsonDocument::fromJson(mock.calls.at(1)).object();
    bool sawGated = false;
    for (const QJsonValue& v : req2.value("messages").toArray()) {
        QJsonObject o = v.toObject();
        if (o.value("role").toString() != "tool") continue;
        QJsonObject body = QJsonDocument::fromJson(o.value("content").toString().toUtf8()).object();
        if (body.value("ok").toBool() == false &&
            (body.contains("gated") || body.contains(QStringLiteral("error")))) {
            sawGated = true;
        }
    }
    QVERIFY2(sawGated, "gated write result must be fed back as a tool message");
}

// A4: error classification -- terminal (never retried) vs retryable (backoff).
void TestAiToolLoop::errorClassTerminalVsRetryable() {
    using E = ai::LLMWorker::LlmErrorClass;
    QCOMPARE(ai::LLMWorker::classifyLlmError(""), E::Ok);
    QCOMPARE(ai::LLMWorker::classifyLlmError("API key not configured"), E::Terminal);
    QCOMPARE(ai::LLMWorker::classifyLlmError("HTTP 401 unauthorized"), E::Terminal);
    QCOMPARE(ai::LLMWorker::classifyLlmError("http 400 bad request"), E::Terminal);
    QCOMPARE(ai::LLMWorker::classifyLlmError("http 429 rate limited"), E::Retryable);
    QCOMPARE(ai::LLMWorker::classifyLlmError("server 503 overloaded"), E::Retryable);
    QCOMPARE(ai::LLMWorker::classifyLlmError("connection timed out"), E::Retryable);
    QCOMPARE(ai::LLMWorker::classifyLlmError("some unknown failure"), E::Terminal);
}

// A4: exponential backoff doubles per attempt, clamped at the max budget.
void TestAiToolLoop::backoffDelayExponentialAndClamped() {
    QCOMPARE(ai::LLMWorker::backoffDelayMs(1), 1000);
    QCOMPARE(ai::LLMWorker::backoffDelayMs(2), 2000);
    QCOMPARE(ai::LLMWorker::backoffDelayMs(3), 4000);
    QCOMPARE(ai::LLMWorker::backoffDelayMs(99), 8000);   // clamped to max
}

// Phase32 block2: a REAL loopback HTTP server answers with a chosen status. The
// production QNAM path must surface the numeric code on the response and the
// worker must classify it by that code (not by string guessing).
void TestAiToolLoop::realHttpStatus_transportedAndClassified() {
    using E = ai::LLMWorker::LlmErrorClass;
    // Ensure no prior test's offline mock transport is installed: this exercises
    // the real QNAM path against loopback.
    ai::LLMClient::setDefaultTransportForTests(nullptr);

    struct Case { int code; E expected; };
    const Case cases[] = {
        {429, E::Retryable},
        {503, E::Retryable},
        {500, E::Retryable},
        {401, E::Terminal},
        {400, E::Terminal},
    };
    for (const Case& c : cases) {
        LoopbackHttp srv;
        srv.statusCode = c.code;
        QJsonObject err;
        err["message"] = QStringLiteral("boom-%1").arg(c.code);
        err["type"] = "error";
        srv.errorBody = err;

        ai::LLMClient client;
        client.setApiKey("k");
        client.setBaseUrl(srv.url().toString());

        QList<ai::ChatMessage> msgs;
        msgs.append(ai::ChatMessage{"user", "hi"});
        ai::LLMResponse r = client.chat(msgs, {});

        QCOMPARE(r.httpStatus, c.code);                 // real code transported
        QVERIFY2(!r.error.isEmpty(), qPrintable("HTTP " +
                  QString::number(c.code) + " must set an error"));
        QVERIFY2(r.error.contains(QString::number(c.code)),
                 qPrintable("error line must carry the numeric code, got: " + r.error));
        QCOMPARE(ai::LLMWorker::classifyLlmError(r.httpStatus, r.error), c.expected);
    }
    // A 2xx success carries the code and no error.
    {
        LoopbackHttp srv;
        srv.statusCode = 200;
        ai::LLMClient client;
        client.setApiKey("k");
        client.setBaseUrl(srv.url().toString());
        QList<ai::ChatMessage> msgs; msgs.append(ai::ChatMessage{"user", "hi"});
        ai::LLMResponse r = client.chat(msgs, {});
        QCOMPARE(r.httpStatus, 200);
        QVERIFY(r.error.isEmpty());
        QCOMPARE(r.content, QStringLiteral("ok"));
    }
}

// Phase32 block2: 429 hits the bounded retry loop (existing exponential backoff
// seam) up to kAiMaxTransientRetries, then surfaces chatError honestly -- never
// chatFinished, never a mock answer.
void TestAiToolLoop::retryBudget_exhaustedOn429() {
    ai::LLMClient::setDefaultTransportForTests(nullptr);
    LoopbackHttp srv;
    srv.statusCode = 429;
    QJsonObject err; err["message"] = "rate limit"; srv.errorBody = err;

    ai::LLMWorker w;
    w.setApiKey("k");
    w.setBaseUrl(srv.url().toString());
    ai::LLMWorker::setBackoffSleepForTests([](int) {});   // no real sleep

    QSignalSpy errSpy(&w, &ai::LLMWorker::chatError);
    QSignalSpy finSpy(&w, &ai::LLMWorker::chatFinished);
    w.doChat(baseChat(), ai::toolDefs());

    // 1 initial + kAiMaxTransientRetries retries, then give up honestly.
    QCOMPARE(srv.hits, 1 + (int)tokens::kAiMaxTransientRetries);
    QCOMPARE(errSpy.size(), 1);
    QCOMPARE(finSpy.size(), 0);
    ai::LLMWorker::setBackoffSleepForTests(nullptr);
}

// Phase32 block2: 401 is terminal -- exactly ONE request, no retry, and the error
// line carries the API-key hint.
void TestAiToolLoop::terminalOn401_noRetryWithKeyHint() {
    ai::LLMClient::setDefaultTransportForTests(nullptr);
    LoopbackHttp srv;
    srv.statusCode = 401;
    QJsonObject err; err["message"] = "invalid key"; srv.errorBody = err;

    ai::LLMWorker w;
    w.setApiKey("k");
    w.setBaseUrl(srv.url().toString());
    ai::LLMWorker::setBackoffSleepForTests([](int) {});

    QSignalSpy errSpy(&w, &ai::LLMWorker::chatError);
    QSignalSpy finSpy(&w, &ai::LLMWorker::chatFinished);
    w.doChat(baseChat(), ai::toolDefs());

    QCOMPARE(srv.hits, 1);                            // no retry on 401
    QCOMPARE(errSpy.size(), 1);
    QCOMPARE(finSpy.size(), 0);
    const QString line = errSpy.takeFirst().at(0).toString();
    QVERIFY2(line.contains(QStringLiteral("401")),
             qPrintable("401 must surface in honest line, got: " + line));
    QVERIFY2(line.contains(QStringLiteral("API Key")),
             qPrintable("401 must carry the API-key hint, got: " + line));
    ai::LLMWorker::setBackoffSleepForTests(nullptr);
}

// Phase32 block2: with NO key the client returns an honest terminal error BEFORE
// any socket. httpStatus stays 0 (no HTTP reply) and classification falls back to
// the string heuristic -> Terminal (no retry storm, no mock).
void TestAiToolLoop::noKey_honestPending_noTransport() {
    ai::LLMClient client;   // no setApiKey -> empty key
    QList<ai::ChatMessage> msgs; msgs.append(ai::ChatMessage{"user", "hi"});
    ai::LLMResponse r = client.chat(msgs, {});
    QVERIFY(!r.error.isEmpty());
    QCOMPARE(r.httpStatus, 0);                          // no HTTP reply at all
    QCOMPARE(ai::LLMWorker::classifyLlmError(r.httpStatus, r.error),
             ai::LLMWorker::LlmErrorClass::Terminal);
}

QTEST_MAIN(TestAiToolLoop)
#include "test_ai_tool_loop.moc"
