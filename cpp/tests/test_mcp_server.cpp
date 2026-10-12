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
#include <QSet>
#include <QElapsedTimer>

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
    void toolsListGroupedBranches();
    void toolsListDetailLevels();
    void listChangedCapabilityAndNotification();
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
    // Cache-friendly: advertise listChanged so clients may cache tools/list.
    QCOMPARE(result.value(QStringLiteral("capabilities")).toObject()
                 .value(QStringLiteral("tools")).toObject()
                 .value(QStringLiteral("listChanged")).toBool(), true);
}

void TestMcpServer::toolsListSingleSourceConsistency() {
    dsp::SpectrumEngine engine;
    QJsonObject r = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}"), &engine);
    // Default view = 8 frozen domain cards; each card carries names only.
    QJsonArray cards = r.value(QStringLiteral("result")).toObject()
                           .value(QStringLiteral("tools")).toArray();
    QCOMPARE(cards.size(), 8);

    // Single-source pin: flattening every card's name list == the declarative
    // registry name set, each tool appearing in EXACTLY one domain (no loss,
    // no dup). Do NOT hardcode the count (it grows as tools are added).
    QSet<QString> flattened;
    int total = 0;
    for (const QJsonValue& c : cards) {
        QJsonObject card = c.toObject();
        QVERIFY(card.contains(QStringLiteral("domain")));
        QVERIFY(card.contains(QStringLiteral("summary")));
        for (const QJsonValue& n : card.value(QStringLiteral("tools")).toArray()) {
            flattened.insert(n.toString());
            ++total;
        }
    }
    QSet<QString> registry;
    for (const ai::ToolDef& d : ai::toolDefs()) registry.insert(d.name);
    QCOMPARE(total, ai::toolDefs().size());     // nothing lost
    QCOMPARE(flattened.size(), registry.size()); // each tool in exactly one domain
    QCOMPARE(flattened, registry);
    // ...and the dispatch executor names agree with the registry, as before.
    QCOMPARE(registry.size(), ai::executorToolNames().size());
}

void TestMcpServer::toolsListGroupedBranches() {
    dsp::SpectrumEngine engine;

    // -- params.domain -> that domain's FULL tool list (schema from toolDefs()).
    QJsonObject byDom = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":40,\"method\":\"tools/list\","
                       "\"params\":{\"domain\":\"VNA 测量\"}}"), &engine);
    QJsonArray vna = byDom.value(QStringLiteral("result")).toObject()
                         .value(QStringLiteral("tools")).toArray();
    QCOMPARE(vna.size(), 4);   // set_vna_sweep / get_vna_data / get_vna_status / vna_device
    QStringList vnaNames;
    for (const QJsonValue& t : vna) {
        QJsonObject o = t.toObject();
        QVERIFY(o.contains(QStringLiteral("name")));
        QVERIFY(o.contains(QStringLiteral("description")));
        QVERIFY(o.value(QStringLiteral("inputSchema")).toObject()
                    .contains(QStringLiteral("required")));
        vnaNames.append(o.value(QStringLiteral("name")).toString());
    }
    vnaNames.sort();
    QStringList expectVna;
    expectVna << QStringLiteral("get_vna_data") << QStringLiteral("get_vna_status")
              << QStringLiteral("set_vna_sweep") << QStringLiteral("vna_device");
    QCOMPARE(vnaNames, expectVna);

    // Fuzzy match (lowercase) resolves to the same domain.
    QJsonObject fuzzy = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":41,\"method\":\"tools/list\","
                       "\"params\":{\"domain\":\"vna 测量\"}}"), &engine);
    QCOMPARE(fuzzy.value(QStringLiteral("result")).toObject()
                 .value(QStringLiteral("tools")).toArray().size(), 4);

    // -- unknown domain -> honest JSON-RPC error listing legal domain names.
    QJsonObject bad = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":42,\"method\":\"tools/list\","
                       "\"params\":{\"domain\":\"不存在的域\"}}"), &engine);
    QJsonObject e = bad.value(QStringLiteral("error")).toObject();
    QCOMPARE(e.value(QStringLiteral("code")).toInt(), -32602);
    QVERIFY(e.value(QStringLiteral("message")).toString().contains(QStringLiteral("VNA 测量")));

    // -- detailed:true -> all domains with full per-tool schema; nothing lost.
    QJsonObject det = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":43,\"method\":\"tools/list\","
                       "\"params\":{\"detailed\":true}}"), &engine);
    QJsonArray doms = det.value(QStringLiteral("result")).toObject()
                         .value(QStringLiteral("domains")).toArray();
    QCOMPARE(doms.size(), 8);
    int detTotal = 0;
    for (const QJsonValue& c : doms)
        detTotal += c.toObject().value(QStringLiteral("tools")).toArray().size();
    QCOMPARE(detTotal, ai::toolDefs().size());
}

void TestMcpServer::toolsListDetailLevels() {
    dsp::SpectrumEngine engine;

    // -- detail=name: cards, each tool is a bare name string (no description).
    QJsonObject byName = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":50,\"method\":\"tools/list\","
                       "\"params\":{\"detail\":\"name\"}}"), &engine);
    QJsonArray nameCards = byName.value(QStringLiteral("result")).toObject()
                               .value(QStringLiteral("tools")).toArray();
    QCOMPARE(nameCards.size(), 8);
    for (const QJsonValue& c : nameCards)
        for (const QJsonValue& t : c.toObject().value(QStringLiteral("tools")).toArray())
            QVERIFY(t.isString());   // bare name, NOT an object

    // -- detail=desc: cards, each tool {name, description}, NO inputSchema.
    QJsonObject byDesc = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":51,\"method\":\"tools/list\","
                       "\"params\":{\"detail\":\"desc\"}}"), &engine);
    QJsonArray descCards = byDesc.value(QStringLiteral("result")).toObject()
                               .value(QStringLiteral("tools")).toArray();
    int descCount = 0;
    for (const QJsonValue& c : descCards)
        for (const QJsonValue& t : c.toObject().value(QStringLiteral("tools")).toArray()) {
            QJsonObject o = t.toObject();
            QVERIFY(o.contains(QStringLiteral("name")));
            QVERIFY(o.contains(QStringLiteral("description")));
            QVERIFY(!o.contains(QStringLiteral("inputSchema")));
            ++descCount;
        }
    QCOMPARE(descCount, ai::toolDefs().size());

    // -- detail=schema: cards under "domains", each tool carries inputSchema.
    QJsonObject bySchema = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":52,\"method\":\"tools/list\","
                       "\"params\":{\"detail\":\"schema\"}}"), &engine);
    QJsonArray schemaCards = bySchema.value(QStringLiteral("result")).toObject()
                                 .value(QStringLiteral("domains")).toArray();
    QCOMPARE(schemaCards.size(), 8);
    for (const QJsonValue& c : schemaCards)
        for (const QJsonValue& t : c.toObject().value(QStringLiteral("tools")).toArray())
            QVERIFY(t.toObject().contains(QStringLiteral("inputSchema")));

    // -- detailed:true is a kept alias == detail=schema (same card count).
    QJsonObject detAlias = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":53,\"method\":\"tools/list\","
                       "\"params\":{\"detailed\":true}}"), &engine);
    QCOMPARE(detAlias.value(QStringLiteral("result")).toObject()
                 .value(QStringLiteral("domains")).toArray().size(), 8);

    // -- unknown detail value -> honest -32602 listing legal levels.
    QJsonObject bad = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":54,\"method\":\"tools/list\","
                       "\"params\":{\"detail\":\"banana\"}}"), &engine);
    QJsonObject e = bad.value(QStringLiteral("error")).toObject();
    QCOMPARE(e.value(QStringLiteral("code")).toInt(), -32602);
    QVERIFY(e.value(QStringLiteral("message")).toString().contains(QStringLiteral("schema")));
}

void TestMcpServer::listChangedCapabilityAndNotification() {
    dsp::SpectrumEngine engine;
    // notifications/tools/list_changed (no id) -> no stdout reply.
    QJsonObject r = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"method\":\"notifications/tools/list_changed\"}"),
        &engine);
    QVERIFY(r.isEmpty());
    // Misused as a request (with id) -> honest result:null, not -32601.
    QJsonObject r2 = post(
        QStringLiteral("{\"jsonrpc\":\"2.0\",\"id\":70,\"method\":\"notifications/tools/list_changed\"}"),
        &engine);
    QCOMPARE(r2.value(QStringLiteral("id")).toInt(), 70);
    QVERIFY(!r2.contains(QStringLiteral("error")));
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
        // Block until a COMPLETE newline-terminated line arrives on stdout. The
        // reply can be delivered in chunks; a single waitForReadyRead()+readLine()
        // may then return a partial line (no trailing '\n'), which makes
        // QJsonDocument::fromJson fail and intermittently flake the assert.
        // Accumulate until canReadLine(), bounded by a total timeout.
        QElapsedTimer total;
        total.start();
        while (!proc.canReadLine()) {
            if (total.elapsed() > 5000) return QByteArray();  // timeout -> caller QVERIFY fails
            proc.waitForReadyRead(50);
        }
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

    // 2) tools/list (default) -> 8 domain cards; flattening all cards' tool
    //    names loses nothing vs the declarative registry.
    QByteArray listLine = exchange(
        "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}");
    QVERIFY2(!listLine.isEmpty(), "no tools/list reply");
    QJsonObject listObj = QJsonDocument::fromJson(listLine).object();
    QJsonArray cards = listObj.value(QStringLiteral("result")).toObject()
                           .value(QStringLiteral("tools")).toArray();
    QCOMPARE(cards.size(), 8);
    int flatTotal = 0;
    for (const QJsonValue& c : cards)
        flatTotal += c.toObject().value(QStringLiteral("tools")).toArray().size();
    QCOMPARE(flatTotal, ai::toolDefs().size());

    // 2b) On-the-wire size: detail=name directory must be markedly smaller than
    //     detail=schema (the whole point of the tiered, cache-friendly view).
    QByteArray nameLine = exchange(
        "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/list\","
        "\"params\":{\"detail\":\"name\"}}");
    QByteArray schemaLine = exchange(
        "{\"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"tools/list\","
        "\"params\":{\"detail\":\"schema\"}}");
    QVERIFY2(!nameLine.isEmpty() && !schemaLine.isEmpty(), "detail replies missing");
    QVERIFY2(nameLine.size() < schemaLine.size(),
             qPrintable(QString("name %1B should be < schema %2B")
                            .arg(nameLine.size()).arg(schemaLine.size())));

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
