// SPDX-License-Identifier: MIT
// W2d A4 real-link closed loop (C++ side). Fully offline / deterministic:
//   * no network, no API key (mock transport not even needed -- we call
//     executeTool / the planner directly),
//   * the engine defaults to the offline TestSignalSource (no RTL present),
//   * pass prediction uses the pure predictPassesFromEntries seam with a FIXED
//     known TLE (the celestrak AIAA-2006 SGP4 verification set, same vector the
//     sat_task_planner test uses) -- never the on-disk cache, never a live pull.
//
// Covers the A4 frozen decisions:
//   P1  every tool result honestly discloses connected / source (test signal,
//       not real hardware); get_status aligned with Flutter.
//   P1  scan_band on the synthetic signal is labelled synthetic (no fake RF hit).
//   P1  start_recording propagates the engine bool (no unconditional success).
//   P2  predict_passes read-only tool over fresh cache; deterministic seam +
//       honest empty states (no fresh TLE / bad coords).
//   P2  tool-count audit: registeredToolSpecs() names == the set executeTool
//       actually supports (registry == schema, no omissions).
#include <QtTest/QtTest>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QSet>
#include <QDateTime>
#include <QDate>
#include <QTime>
#include <cmath>
#include <ctime>

#include "ai/agent_tools.h"
#include "ai/tool_schema.h"
#include "ai/sat_task_planner.h"
#include "dsp/spectrum_engine.h"
#include "dsp/tle_client.h"

using namespace mbdsdr;

namespace {
// CBERS 2 (28057) verification epoch, identical to test_sat_task_planner.
QDateTime cbersEpochStart() {
    QDate d0(2006, 1, 1);
    double dayFrac = 177.78615833 - 1.0;
    qint64 dayInt = static_cast<qint64>(std::floor(dayFrac));
    double secs = (dayFrac - dayInt) * 86400.0;
    QDateTime midnight(d0.addDays(dayInt), QTime(0, 0, 0), Qt::UTC);
    return QDateTime::fromMSecsSinceEpoch(
        midnight.toMSecsSinceEpoch() + static_cast<qint64>(secs * 1000.0),
        Qt::UTC).addSecs(240 * 60);
}

QJsonObject parseObj(const QString& s) {
    return QJsonDocument::fromJson(s.toUtf8()).object();
}
} // namespace

class TestAiRealLink : public QObject {
    Q_OBJECT
private slots:
    void sourceDisclosure_testSignalHonest();
    void scanBand_marksSynthetic();
    void startRecording_reportsBoolNotUnconditional();
    void getStatus_alignedWithFlutter();
    void predictPasses_deterministicKnownTle();
    void predictPasses_emptyEntriesHonest();
    void predictPasses_badStationHonest();
    void toolCount_registryEqualsExecution();
};

// Default engine = offline TestSignalSource. Every result must disclose
// connected:false and a 测试信号 source -- never claim real hardware.
void TestAiRealLink::sourceDisclosure_testSignalHonest() {
    dsp::SpectrumEngine engine;
    QJsonObject args; args["freq_hz"] = 103300000;
    QString r = ai::executeTool("tune_frequency", args, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("connected").toBool(), false);
    QCOMPARE(o.value("test_signal").toBool(), true);
    QVERIFY2(o.value("source").toString().contains(QString::fromUtf8("测试信号")),
             qPrintable("source must honestly say test signal, got: " + r));
    QVERIFY2(r.contains("103.3"), qPrintable("human-readable MHz kept, got: " + r));
}

// scan_band on the synthetic source must NOT present a fixed-tone peak as a real
// RF station hit: it carries synthetic:true + an honest note.
void TestAiRealLink::scanBand_marksSynthetic() {
    dsp::SpectrumEngine engine;
    QJsonObject args;
    args["low_hz"] = 88000000;
    args["high_hz"] = 108000000;
    args["step_hz"] = 1000000;
    QString r = ai::executeTool("scan_band", args, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("ok").toBool(), true);
    QCOMPARE(o.value("synthetic").toBool(), true);
    QVERIFY2(o.value("note").toString().contains(QString::fromUtf8("合成")),
             qPrintable("scan on test signal must note it is synthetic, got: " + r));
    QCOMPARE(o.value("connected").toBool(), false);
}

// start_recording must propagate the engine bool: result is a JSON object with an
// "ok" boolean, never the bare string "开始录制".
void TestAiRealLink::startRecording_reportsBoolNotUnconditional() {
    dsp::SpectrumEngine engine;
    QString r = ai::executeTool("start_recording", QJsonObject{}, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable(
        "start_recording must return JSON with an ok bool, got: " + r));
    QVERIFY(o.contains("ok"));
    QVERIFY(o.value("ok").isBool());
    // On the offline test source the recorder really does open a SigMF file, so
    // ok is true here -- but it is the engine's real verdict, not an unconditional.
    QVERIFY(o.value("ok").toBool());
    QVERIFY(o.contains("path"));
    // tidy up
    ai::executeTool("stop_recording", QJsonObject{}, &engine);
}

// get_status carries connected/source + read-back values, aligned with Flutter
// ai_tools.dart get_status (connected first), and keeps a human summary.
void TestAiRealLink::getStatus_alignedWithFlutter() {
    dsp::SpectrumEngine engine;
    QString r = ai::executeTool("get_status", QJsonObject{}, &engine);
    QJsonObject o = parseObj(r);
    QVERIFY2(!o.isEmpty(), qPrintable("expected JSON, got: " + r));
    QCOMPARE(o.value("connected").toBool(), false);
    QVERIFY(o.contains("source"));
    QVERIFY(o.contains("frequency_hz"));
    QVERIFY(o.contains("mode"));
    QVERIFY2(o.value("summary").toString().contains(QString::fromUtf8("频率")),
             qPrintable("summary keeps human 频率 text, got: " + r));
}

// Deterministic core: inject the fixed known CBERS-2 TLE into the pure seam and
// assert a stable pass list (rise/set/max-elevation), no disk / no network.
void TestAiRealLink::predictPasses_deterministicKnownTle() {
    const QList<dsp::TleEntry> known = dsp::TleClient::builtinTle();
    QVERIFY(!known.isEmpty());
    ai::SatPassListResult r = ai::predictPassesFromEntries(
        known, "CBERS", 40.0, -100.0, cbersEpochStart(), 6);
    QVERIFY2(r.ok, qPrintable(r.error));
    QVERIFY2(!r.passes.isEmpty(), "known CBERS orbit must yield a pass in window");
    const ai::SatPassEntry& p = r.passes.first();
    QVERIFY(p.aosUtc.isValid());
    QVERIFY(p.losUtc.isValid());
    QVERIFY2(p.maxEl > 0.0, qPrintable("pass must rise above the horizon"));
    QVERIFY(p.azAos >= 0.0 && p.azAos < 360.0);
    QCOMPARE(p.catalogNumber, 28057);
    QCOMPARE(p.name, QString::fromLatin1("CBERS 2"));
    // Same inputs -> same outputs (determinism).
    ai::SatPassListResult r2 = ai::predictPassesFromEntries(
        known, "CBERS", 40.0, -100.0, cbersEpochStart(), 6);
    QCOMPARE(r2.passes.size(), r.passes.size());
    QCOMPARE(r2.passes.first().aosUtc, p.aosUtc);
    QCOMPARE(r2.passes.first().losUtc, p.losUtc);
}

// Empty TLE list -> honest ok:false (we never fabricate a pass).
void TestAiRealLink::predictPasses_emptyEntriesHonest() {
    ai::SatPassListResult r = ai::predictPassesFromEntries(
        QList<dsp::TleEntry>{}, "ISS", 40.0, -100.0, cbersEpochStart(), 6);
    QVERIFY2(!r.ok, "empty TLE must be honest empty, not fabricated");
    QVERIFY(!r.error.isEmpty());
}

// The predict_passes tool branch: bad/missing station coordinates -> honest
// error BEFORE any disk/cache read (deterministic).
void TestAiRealLink::predictPasses_badStationHonest() {
    dsp::SpectrumEngine engine;
    QJsonObject args;
    args["satellite_name"] = "ISS";
    args["station_lat_deg"] = 999.0;   // out of range
    args["station_lon_deg"] = 116.0;
    QString r = ai::executeTool("predict_passes", args, &engine);
    QJsonObject o = parseObj(r);
    QCOMPARE(o.value("ok").toBool(), false);
    QVERIFY2(o.value("error").toString().contains(QString::fromUtf8("站点")),
             qPrintable("bad station must be honest, got: " + r));

    // Missing station coords likewise.
    QJsonObject noStation; noStation["satellite_name"] = "ISS";
    QJsonObject o2 = parseObj(ai::executeTool("predict_passes", noStation, &engine));
    QCOMPARE(o2.value("ok").toBool(), false);
}

// Tool-count audit: the declarative registry must list EXACTLY the tools
// executeTool supports -- no missing registration, no phantom schema entry.
void TestAiRealLink::toolCount_registryEqualsExecution() {
    const QList<ai::ToolSchemaSpec> specs = ai::registeredToolSpecs();
    QSet<QString> registered;
    for (const ai::ToolSchemaSpec& s : specs) registered.insert(s.name);

    // The exact set executeTool() dispatches on (agent_tools.cpp).
    const QSet<QString> supported = {
        "tune_frequency", "set_mode", "start_recording", "stop_recording",
        "scan_band", "set_bandwidth", "get_status", "predict_passes",
        "calibrate_frequency", "apply_frequency_correction",
    };
    QCOMPARE(registered.size(), supported.size());
    QCOMPARE(registered, supported);

    // predict_passes is read-only (never gated in manual mode).
    QVERIFY(!ai::isWriteTool("predict_passes"));
}

QTEST_MAIN(TestAiRealLink)
#include "test_ai_real_link.moc"
