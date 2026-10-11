// SPDX-License-Identifier: MIT
// Headless MCP server tests: in-process JSON-RPC line handling AND a real
// process-level stdio round-trip against the built mbdsdr_mcp binary.
//
// No mocks: in-process cases drive agentapi::handleRequestLine() against a real
// headless dsp::SpectrumEngine; the process-level case spawns the real binary
// (path injected via the MBDSDR_MCP_BIN compile definition, mounted by the
// orchestrator's CMake) and exchanges stdin/stdout lines with it.
#include <QtTest/QtTest>

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QRegularExpression>

#include "agentapi/mcp_server.h"
#include "ai/agent_tools.h"
#include "dsp/spectrum_engine.h"
#include "ui/bookmark_manager.h"

using namespace mbdsdr;

namespace {

// Post a raw request line to the in-process handler; return the parsed reply
// object (empty object when the handler elects not to reply).
QJsonObject post(const QString& line, dsp::SpectrumEngine* eng) {
    const QString reply = agentapi::handleRequestLine(line, eng, nullptr);
    if (reply.isEmpty()) return QJsonObject();
    QJsonDocument d = QJsonDocument::fromJson(reply.toUtf8());
    return d.isObject() ? d.object() : QJsonObject();
}

// Extract the tools/call text payload (a JSON string) as an object.
QJsonObject callText(const QJsonObject& reply) {
    const QJsonArray content = reply.value(QStringLiteral("result")).toObject()
                                   .value(QStringLiteral("content")).toArray();
    if (content.isEmpty()) return QJsonObject();
    const QString text = content.at(0).toObject().value(QStringLiteral("text")).toString();
    QJsonDocument d = QJsonDocument::fromJson(text.toUtf8());
    return d.isObject() ? d.object() : QJsonObject();
}

} // namespace

class TestMcpServer : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void initializeHandshake();
    void toolsListSingleSourceConsistency();
    void toolsCallGetCapabilitiesRealRoundtrip();
    void writeToolGatedWithoutArmAndStateUnchanged();
    void writeToolRunsWhenArmed();
    void notificationsInitializedNoReply();
    void malformedJsonNoReplyNoCrash();
    void unknownMethodError();
    void processLevelStdioRoundtrip();
};

void TestMcpServer::initTestCase() {
    // Headless engine (NullSource + null audio under offscreen/qputenv in main).
    qputenv("QT_QPA_PLATFORM", "offscreen");
    qputenv("MBDSDR_NULL_AUDIO", "1");
}

void TestMcpServer::initializeHandshake() {
    dsp::SpectrumEngine engine;
    QJsonObject r = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\"}"), &engine);
    QVERIFY(!r.isEmpty());
    QCOMPARE(r.value(QStringLiteral("jsonrpc")).toString(), QStringLiteral("2.0"));
    QCOMPARE(r.value(QStringLiteral("id")).toInt(), 1);
    QJsonObject result = r.value(QStringLiteral("result")).toObject();
    QCOMPARE(result.value(QStringLiteral("protocolVersion")).toString(),
             QStringLiteral("2024-11-05"));
    QJsonObject serverInfo = result.value(QStringLiteral("serverInfo")).toObject();
    QCOMPARE(serverInfo.value(QStringLiteral("name")).toString(), QStringLiteral("mbdsdr-core"));
    QCOMPARE(serverInfo.value(QStringLiteral("version")).toString(), QStringLiteral("0.1.0"));
    QVERIFY(result.value(QStringLiteral("capabilities")).toObject()
                .contains(QStringLiteral("tools")));
}

void TestMcpServer::toolsListSingleSourceConsistency() {
    dsp::SpectrumEngine engine;
    QJsonObject r = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}"), &engine);
    QJsonArray tools = r.value(QStringLiteral("result")).toObject()
                           .value(QStringLiteral("tools")).toArray();
    // Single-source consistency: MCP list == declarative registry == dispatch
    // table. Do NOT hardcode the count (it grows as tools are added).
    QCOMPARE(tools.size(), ai::toolDefs().size());
    QCOMPARE(tools.size(), ai::executorToolNames().size());
    // Every entry has name/description/inputSchema, and inputSchema carries a
    // "required" array (empty when the schema had none).
    for (const QJsonValue& v : tools) {
        QJsonObject t = v.toObject();
        QVERIFY(t.contains(QStringLiteral("name")));
        QVERIFY(t.contains(QStringLiteral("description")));
        QJsonObject schema = t.value(QStringLiteral("inputSchema")).toObject();
        QVERIFY(schema.contains(QStringLiteral("required")));
    }
}

void TestMcpServer::toolsCallGetCapabilitiesRealRoundtrip() {
    dsp::SpectrumEngine engine;
    QJsonObject r = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\","
                       "\"params\":{\"name\":\"get_capabilities\",\"arguments\":{}}}"),
        &engine);
    QJsonObject text = callText(r);
    // Honest readback: ok:true and a provenance field naming the real source
    // state (offline/null source) — never fabricated hardware values.
    QCOMPARE(text.value(QStringLiteral("ok")).toBool(), true);
    QVERIFY(text.contains(QStringLiteral("provenance")) ||
            text.contains(QStringLiteral("source")) ||
            text.contains(QStringLiteral("source_name")));
}

void TestMcpServer::writeToolGatedWithoutArmAndStateUnchanged() {
    dsp::SpectrumEngine engine;
    // Baseline frequency (read tool, not gated).
    QJsonObject before = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":10,\"method\":\"tools/call\","
                       "\"params\":{\"name\":\"get_frequency\",\"arguments\":{}}}"),
        &engine);
    QJsonObject beforeText = callText(before);
    const double freqBefore = beforeText.value(QStringLiteral("frequency_hz")).toDouble();

    // Write tool WITHOUT arm_manual -> gated envelope, tool must NOT run.
    QJsonObject gated = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":11,\"method\":\"tools/call\","
                       "\"params\":{\"name\":\"tune_frequency\","
                       "\"arguments\":{\"freq_hz\":99900000}}}"),
        &engine);
    QJsonObject gatedText = callText(gated);
    QCOMPARE(gatedText.value(QStringLiteral("gated")).toBool(), true);
    QCOMPARE(gatedText.value(QStringLiteral("ok")).toBool(), false);

    // Engine frequency unchanged: the gated tune never executed.
    QJsonObject after = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":12,\"method\":\"tools/call\","
                       "\"params\":{\"name\":\"get_frequency\",\"arguments\":{}}}"),
        &engine);
    QJsonObject afterText = callText(after);
    QCOMPARE(afterText.value(QStringLiteral("frequency_hz")).toDouble(), freqBefore);
}

void TestMcpServer::writeToolRunsWhenArmed() {
    dsp::SpectrumEngine engine;
    // set_mode is a write tool; arm it explicitly -> real execution, no gate.
    QJsonObject r = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":20,\"method\":\"tools/call\","
                       "\"params\":{\"name\":\"set_mode\","
                       "\"arguments\":{\"mode\":\"AM\",\"arm_manual\":true}}}"),
        &engine);
    QJsonObject text = callText(r);
    QVERIFY2(!text.value(QStringLiteral("gated")).toBool(),
             qPrintable(QString::fromUtf8(QJsonDocument(text).toJson(QJsonDocument::Compact))));
    QCOMPARE(text.value(QStringLiteral("ok")).toBool(), true);
    // arm_manual must not leak into the result envelope.
    QVERIFY(!text.contains(QStringLiteral("arm_manual")));
}

void TestMcpServer::notificationsInitializedNoReply() {
    dsp::SpectrumEngine engine;
    QJsonObject r = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}"),
        &engine);
    QVERIFY(r.isEmpty());   // handler elected not to write stdout
}

void TestMcpServer::malformedJsonNoReplyNoCrash() {
    dsp::SpectrumEngine engine;
    QVERIFY(post(QStringLiteral("{not json"), &engine).isEmpty());
    QVERIFY(post(QStringLiteral(""), &engine).isEmpty());
    QVERIFY(post(QStringLiteral("42"), &engine).isEmpty());   // not an object
}

void TestMcpServer::unknownMethodError() {
    dsp::SpectrumEngine engine;
    QJsonObject r = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":30,\"method\":\"nope/foo\"}"),
        &engine);
    QVERIFY(!r.isEmpty());
    QCOMPARE(r.value(QStringLiteral("id")).toInt(), 30);
    QJsonObject e = r.value(QStringLiteral("error")).toObject();
    QCOMPARE(e.value(QStringLiteral("code")).toInt(), -32601);
    QVERIFY(e.value(QStringLiteral("message")).toString().contains(QStringLiteral("nope/foo")));
}

void TestMcpServer::processLevelStdioRoundtrip() {
#ifndef MBDSDR_MCP_BIN
    QSKIP("MBDSDR_MCP_BIN not defined; orchestrator CMake mounts the mbdsdr_mcp target.");
#else
    const QString bin = QStringLiteral(MBDSDR_MCP_BIN);
    QProcess proc;
    proc.setProgram(bin);
    proc.setProcessChannelMode(QProcess::SeparateChannels);
    proc.start();
    QVERIFY2(proc.waitForStarted(5000), qPrintable(proc.errorString()));

    auto exchange = [&](const QByteArray& request) -> QByteArray {
        proc.write(request + "\n");
        proc.waitForBytesWritten(2000);
        // Read one line off stdout.
        if (!proc.waitForReadyRead(5000)) return QByteArray();
        QByteArray line = proc.readLine();
        while (line.endsWith('\n') || line.endsWith('\r')) line.chop(1);
        return line;
    };

    // 1) initialize -> jsonrpc 2.0 + serverInfo.name == mbdsdr-core
    QByteArray initLine = exchange(
        "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\"}");
    QVERIFY2(!initLine.isEmpty(), "no initialize reply on stdout");
    QJsonObject initObj = QJsonDocument::fromJson(initLine).object();
    QCOMPARE(initObj.value(QStringLiteral("jsonrpc")).toString(), QStringLiteral("2.0"));
    QCOMPARE(initObj.value(QStringLiteral("result")).toObject()
                 .value(QStringLiteral("serverInfo")).toObject()
                 .value(QStringLiteral("name")).toString(),
             QStringLiteral("mbdsdr-core"));

    // 2) tools/list -> count equals the declarative registry size
    QByteArray listLine = exchange(
        "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}");
    QVERIFY2(!listLine.isEmpty(), "no tools/list reply");
    QJsonObject listObj = QJsonDocument::fromJson(listLine).object();
    QJsonArray tools = listObj.value(QStringLiteral("result")).toObject()
                           .value(QStringLiteral("tools")).toArray();
    QCOMPARE(tools.size(), ai::toolDefs().size());

    // 3) tools/call get_capabilities -> ok:true
    QByteArray callLine = exchange(
        "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\","
        "\"params\":{\"name\":\"get_capabilities\",\"arguments\":{}}}");
    QVERIFY2(!callLine.isEmpty(), "no tools/call reply");
    QJsonObject callObj = QJsonDocument::fromJson(callLine).object();
    QJsonArray content = callObj.value(QStringLiteral("result")).toObject()
                             .value(QStringLiteral("content")).toArray();
    QJsonObject text = QJsonDocument::fromJson(
        content.at(0).toObject().value(QStringLiteral("text")).toString().toUtf8()).object();
    QCOMPARE(text.value(QStringLiteral("ok")).toBool(), true);

    proc.terminate();
    proc.waitForFinished(2000);
#endif
}

QTEST_MAIN(TestMcpServer)
#include "test_mcp_server.moc"
