// SPDX-License-Identifier: MIT
// L5 (Phase12 Wave-F): C++ tool-registry completeness / consistency audit.
// Fully offline and deterministic: no network, no API key, no real hardware.
// The engine defaults to the offline TestSignalSource, and the unknown-tool
// paths never touch the radio for a name the registry does not dispatch on.
//
// This test is what makes the registry refactor real: it introspects BOTH sides
// of the split instead of hardcoding the "supported" set in the test:
//   * registeredToolSpecs()  -> the declarative schema table (tool_schema.cpp)
//   * executorToolNames()    -> the dispatch table it self-registers (agent_tools.cpp)
// Adding a tool to one table but not the other now FAILS here, instead of
// silently drifting like the old if-else + parallel writeTools() set did.
#include <QtTest/QtTest>

#include <QSet>
#include <QStringList>
#include <QJsonDocument>
#include <QJsonObject>

#include "ai/agent_tools.h"
#include "ai/tool_schema.h"
#include "dsp/spectrum_engine.h"

using namespace mbdsdr;
using namespace mbdsdr::ai;

namespace {
// The C++ desktop tools, in their on-wire order. This is the FROZEN contract
// (also pinned by test_agent / test_tool_schema / ai_real_link); it is the
// expected value the introspected tables are checked against. Grew from 8 to 10
// with the frequency-calibration pair: calibrate_frequency (read-only measure)
// and apply_frequency_correction (write, gated).
const QSet<QString> kAllCxxTools = {
    "tune_frequency", "set_mode", "start_recording", "stop_recording",
    "scan_band", "set_bandwidth", "get_status", "predict_passes",
    "calibrate_frequency", "apply_frequency_correction",
};

// The mutating (write) tools -- the ONLY ones gated in manual mode. This is
// the C++ write gate; it must equal ToolSchemaSpec::write flags set in
// tool_schema.cpp (the registry is now the single source). Grew from 6 to 7
// with apply_frequency_correction (it persists QSettings + drives source->setPpm).
const QSet<QString> kExpectedWriteTools = {
    "tune_frequency", "set_mode", "set_bandwidth",
    "start_recording", "stop_recording", "scan_band",
    "apply_frequency_correction",
};

// The Flutter side (mobile/lib/app/ai_tools.dart, treated as READ-ONLY reference)
// gates these 6 mutating tools: set_frequency, set_mode, set_gain,
// set_sample_rate, start_recording, stop_recording. Its 8 tools minus those 6
// leave exactly two ungated read tools. THAT complement is the cross-platform
// read/read-only contract for the SHARED tools (desktop has
// scan_band/set_bandwidth/tune_frequency; mobile has set_gain/set_sample_rate/
// set_frequency -- the hardware differs, but the shared read-only complement
// must agree). calibrate_frequency is a DESKTOP-ONLY read tool (it drives
// SpectrumEngine::captureForCalibration); Flutter does not ship it, so there is
// nothing to gate on mobile -- on desktop it is ungated by construction and is
// listed here so the read-only set equality stays honest.
const QSet<QString> kFlutterUngatedReadTools = {
    "get_status", "predict_passes", "calibrate_frequency",
};
} // namespace

class TestToolRegistry : public QObject {
    Q_OBJECT
private slots:
    void completeness_everySchemaHasExecutor();
    void completeness_noOrphanExecutor();
    void writeReadSplit_registryMatchesContract();
    void readOnlySet_parityWithFlutter();
    void unknownTool_honestErrorPath();
    void notARegression_smoke();
};

// Every declarative schema name MUST have a wired executor. (The old code could
// add a spec without an if-else branch and tests would still pass, because the
// "supported" set was also hardcoded in the test.)
void TestToolRegistry::completeness_everySchemaHasExecutor() {
    QSet<QString> schemas;
    for (const ToolSchemaSpec& s : registeredToolSpecs()) schemas.insert(s.name);

    QSet<QString> executors;
    for (const QString& n : executorToolNames()) executors.insert(n);

    // Each schema declares an executor...
    QVERIFY2(schemas.size() == 10, qPrintable(QString("expected 10 tools, got %1").arg(schemas.size())));
    QCOMPARE(executors.size(), schemas.size());
    const QSet<QString> missingExec = schemas - executors;
    QVERIFY2(missingExec.isEmpty(),
             qPrintable("schema has no executor: " + missingExec.values().join(", ")));
}

// ...and every executor MUST correspond to a declared schema (no phantom branch
// that renders no JSON Schema and so the model can never be told about).
void TestToolRegistry::completeness_noOrphanExecutor() {
    QSet<QString> schemas;
    for (const ToolSchemaSpec& s : registeredToolSpecs()) schemas.insert(s.name);
    QSet<QString> executors;
    for (const QString& n : executorToolNames()) executors.insert(n);
    const QSet<QString> orphan = executors - schemas;
    QVERIFY2(orphan.isEmpty(),
             qPrintable("executor has no schema (phantom tool): " + orphan.values().join(", ")));
    // And the full set equals the frozen contract.
    QCOMPARE(schemas, kAllCxxTools);
}

// The write/read gate is read from the registry (ToolSchemaSpec::write). It must
// match the frozen 6-write / 2-read contract.
void TestToolRegistry::writeReadSplit_registryMatchesContract() {
    QSet<QString> actualWrite;
    for (const ToolSchemaSpec& s : registeredToolSpecs())
        if (s.write) actualWrite.insert(s.name);
    QCOMPARE(actualWrite, kExpectedWriteTools);

    // isWriteTool() agrees with the registry flags (it is the same data, just
    // accessed by name).
    for (const QString& n : kExpectedWriteTools)
        QVERIFY2(isWriteTool(n), qPrintable(n + " must be a write tool"));
    QVERIFY(!isWriteTool("get_status"));
    QVERIFY(!isWriteTool("predict_passes"));
}

// Cross-platform parity: the read-only complement on the C++ desktop side must
// equal the Flutter mobile side's ungated set. Shared mutating tools that exist
// on BOTH platforms (set_mode / start_recording / stop_recording) must be
// classified write=true here; shared read tools (get_status / predict_passes)
// must be write=false. If someone gates get_status or predict_passes by mistake,
// manual mode would wrongly block a read-only tool that Flutter keeps open.
void TestToolRegistry::readOnlySet_parityWithFlutter() {
    QSet<QString> readOnly;
    for (const ToolSchemaSpec& s : registeredToolSpecs())
        if (!s.write) readOnly.insert(s.name);

    QCOMPARE(readOnly, kFlutterUngatedReadTools);

    // Shared mutating tools -> write on both platforms.
    for (const char* n : {"set_mode", "start_recording", "stop_recording"})
        QVERIFY2(isWriteTool(QString::fromLatin1(n)),
                 qPrintable(QString::fromLatin1(n) + " is mutating on both C++ and Flutter"));
    // Shared read tools -> read on both platforms.
    for (const char* n : {"get_status", "predict_passes"})
        QVERIFY2(!isWriteTool(QString::fromLatin1(n)),
                 qPrintable(QString::fromLatin1(n) + " is read-only on both C++ and Flutter"));
}

// Unknown tool: honest error string, never a crash, never a fake ok. A null
// engine reports "error: no engine" before dispatch.
void TestToolRegistry::unknownTool_honestErrorPath() {
    dsp::SpectrumEngine engine;
    QString r = executeTool("definitely_not_a_real_tool", QJsonObject{}, &engine);
    QVERIFY2(r.contains(QString::fromUtf8("未知工具")),
             qPrintable("unknown tool must say so, got: " + r));
    QVERIFY2(r.contains("definitely_not_a_real_tool"),
             qPrintable("unknown tool must name it, got: " + r));
    // It is NOT a success object.
    QJsonObject o = QJsonDocument::fromJson(r.toUtf8()).object();
    QVERIFY2(o.isEmpty() || o.value("ok").toBool() == false,
             qPrintable("unknown tool must not report ok:true, got: " + r));

    // Null engine -> the pre-dispatch guard, byte-for-byte.
    QCOMPARE(executeTool("get_status", QJsonObject{}, nullptr),
             QString("error: no engine"));

    // Unknown names are never gated.
    QVERIFY(!isWriteTool("definitely_not_a_real_tool"));
}

// Not-a-regression smoke: the on-wire contract still holds (8 defs, order kept)
// and a known read tool still executes against the offline engine.
void TestToolRegistry::notARegression_smoke() {
    QList<ToolDef> defs = toolDefs();
    QCOMPARE(defs.size(), 10);
    QCOMPARE(defs[0].name, QString("tune_frequency"));
    QCOMPARE(defs[1].name, QString("set_mode"));

    dsp::SpectrumEngine engine;
    QJsonObject status = QJsonDocument::fromJson(
        executeTool("get_status", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY(!status.isEmpty());
    QCOMPARE(status.value("ok").toBool(), true);
}

QTEST_MAIN(TestToolRegistry)
#include "test_tool_registry.moc"
