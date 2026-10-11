// SPDX-License-Identifier: MIT
// Real-HTTP LLM loopback: QNAM <-> in-process QTcpServer roundtrip, no network/API key.
// Server returns a hand-written OpenAI ChatCompletion; we assert the real wire
// request reached the socket, the response's tool_calls parsed, and the plan
// parser whitelist accepted an atomic read-only tool.
#include <QtTest/QtTest>
#include <QTcpServer>
#include <QTcpSocket>
#include <QJsonDocument>
#include <QJsonObject>
#include "ai/llm_client.h"
#include "ai/llm_protocol.h"
#include "ai/plan_parser.h"
#include "ai/agent_tools.h"
#include "dsp/spectrum_engine.h"

using namespace mbdsdr;

class TestLlmLoopback : public QObject {
    Q_OBJECT
private slots:
    void realHttpRoundtripParsesToolCalls();
    void errorBranchHonest();
    void parserWhitelistAcceptsAtomicTool();
    void endToEndRealExecuteTool();
};

// Hand-written OpenAI ChatCompletion with one tool call.
static QByteArray openaiBody(const QByteArray& name) {
    QJsonObject tc; tc["id"] = "call_1"; tc["type"] = "function";
    QJsonObject fn; fn["name"] = QString::fromUtf8(name); fn["arguments"] = QStringLiteral("{}");
    tc["function"] = fn;
    QJsonObject toolMsg; toolMsg["role"] = "assistant"; toolMsg["tool_calls"] =
        QJsonArray{tc};
    QJsonObject choice; choice["index"] = 0; choice["message"] = toolMsg;
    choice["finish_reason"] = "tool_calls";
    QJsonObject root; root["id"] = "chatcmpl-1"; root["object"] = "chat.completion";
    root["choices"] = QJsonArray{choice};
    return QJsonDocument(root).toJson(QJsonDocument::Compact);
}

static QByteArray httpReply(const QByteArray& body, int code = 200,
                            const char* status = "OK") {
    return QByteArray("HTTP/1.1 ") + QByteArray::number(code) + " " + status +
           "\r\nContent-Type: application/json\r\nContent-Length: " +
           QByteArray::number(body.size()) + "\r\nConnection: close\r\n\r\n" + body;
}

void TestLlmLoopback::realHttpRoundtripParsesToolCalls() {
    QTcpServer srv;
    QVERIFY(srv.listen(QHostAddress::LocalHost, 0));
    quint16 port = srv.serverPort();

    QByteArray gotRequest;
    connect(&srv, &QTcpServer::newConnection, this, [&] {
        QTcpSocket* c = srv.nextPendingConnection();
        connect(c, &QTcpSocket::readyRead, this, [&, c] {
            gotRequest += c->readAll();
            if (gotRequest.contains("\r\n\r\n")) {   // headers complete
                c->write(httpReply(openaiBody("get_capabilities")));
                c->disconnectFromHost();
            }
        });
    });

    ai::LLMClient llm;
    llm.setBaseUrl(QString("http://127.0.0.1:%1").arg(port));
    llm.setModel("offscreen-loopback");
    llm.setApiKey(QStringLiteral("offscreen-test-key"));  // bypass empty-key guard; real wire still used
    QList<ai::ChatMessage> msgs;
    ai::ChatMessage um; um.role = QStringLiteral("user");
    um.content = QStringLiteral("what can you do");
    msgs.append(um);
    ai::LLMResponse r = llm.chat(msgs, {});

    // Real wire request arrived at the socket. QNAM may normalize header casing
    // (observed lowercase 'authorization:'), so compare case-insensitively.
    const QByteArray lowerReq = gotRequest.toLower();
    QVERIFY(lowerReq.contains("post"));
    QVERIFY(lowerReq.contains("authorization"));
    // Response parsed: one tool call named get_capabilities.
    QCOMPARE(r.toolCalls.size(), 1);
    QCOMPARE(r.toolCalls[0].name, QStringLiteral("get_capabilities"));
}

void TestLlmLoopback::errorBranchHonest() {
    QTcpServer srv;
    QVERIFY(srv.listen(QHostAddress::LocalHost, 0));
    quint16 port = srv.serverPort();
    connect(&srv, &QTcpServer::newConnection, this, [&] {
        QTcpSocket* c = srv.nextPendingConnection();
        connect(c, &QTcpSocket::readyRead, this, [&, c] {
            c->readAll();
            c->write(httpReply("not json {{{", 500, "Internal Server Error"));
            c->disconnectFromHost();
        });
    });
    ai::LLMClient llm;
    llm.setBaseUrl(QString("http://127.0.0.1:%1").arg(port));
    llm.setModel("offscreen-loopback");
    llm.setApiKey(QStringLiteral("offscreen-test-key"));  // bypass empty-key guard; real wire still used
    QList<ai::ChatMessage> msgs;
    ai::ChatMessage xm; xm.role = QStringLiteral("user");
    xm.content = QStringLiteral("x");
    msgs.append(xm);
    ai::LLMResponse r = llm.chat(msgs, {});
    QVERIFY(!r.error.isEmpty());   // honest error, no crash
}

void TestLlmLoopback::parserWhitelistAcceptsAtomicTool() {
    QString text = QStringLiteral("{\"steps\":[{\"tool\":\"get_capabilities\","
                                  "\"args\":{},\"description\":\"caps\"}]}");
    ai::ParsedPlan p = ai::parsePlanFromLlm(text);
    QVERIFY(p.ok);
    QCOMPARE(p.plan.steps.size(), 1);
    QCOMPARE(p.plan.steps[0].tool, QStringLiteral("get_capabilities"));
}

// Full chain over a REAL socket: server tool_calls response -> LLMClient parses
// -> each parsed ToolCall is executed for real against a real SpectrumEngine via
// executeTool; we assert the tool ran (ok:true) and returned a real field.
void TestLlmLoopback::endToEndRealExecuteTool() {
    QTcpServer srv;
    QVERIFY(srv.listen(QHostAddress::LocalHost, 0));
    quint16 port = srv.serverPort();
    QByteArray gotRequest;
    connect(&srv, &QTcpServer::newConnection, this, [&] {
        QTcpSocket* c = srv.nextPendingConnection();
        connect(c, &QTcpSocket::readyRead, this, [&, c] {
            gotRequest += c->readAll();
            if (gotRequest.contains("\r\n\r\n")) {
                c->write(httpReply(openaiBody("get_capabilities")));
                c->disconnectFromHost();
            }
        });
    });

    ai::LLMClient llm;
    llm.setBaseUrl(QString("http://127.0.0.1:%1").arg(port));
    llm.setModel("offscreen-loopback");
    llm.setApiKey(QStringLiteral("offscreen-test-key"));  // bypass empty-key guard; real wire still used
    QList<ai::ChatMessage> msgs;
    ai::ChatMessage um; um.role = QStringLiteral("user");
    um.content = QStringLiteral("what can you do");
    msgs.append(um);
    ai::LLMResponse r = llm.chat(msgs, {});
    QCOMPARE(r.toolCalls.size(), 1);

    dsp::SpectrumEngine engine;   // real engine, honest empty state
    int executed = 0;
    for (const auto& tc : r.toolCalls) {
        const QString out = ai::executeTool(tc.name, tc.arguments, &engine);
        const QJsonObject o = QJsonDocument::fromJson(out.toUtf8()).object();
        QCOMPARE(o.value("ok").toBool(), true);          // tool really executed
        QVERIFY(!o.value("provenance").toString().isEmpty()); // real field ("未连接")
        ++executed;
    }
    QCOMPARE(executed, 1);
}

QTEST_MAIN(TestLlmLoopback)
#include "test_llm_loopback.moc"
