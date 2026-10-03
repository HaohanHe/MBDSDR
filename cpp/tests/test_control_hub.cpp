// SPDX-License-Identifier: MIT
//
// *** SYNTHETIC TESTS -- 仅验证控制层接线，非真实接收 (NOT REAL RECEPTION) ***
//
// Deterministic offscreen tests for the headless ControlHub. The REAL
// SpectrumEngine is used (no QWidget anywhere), but the source is the offline
// TestSignalSource -- no hardware, no network. Most cases drive the engine via
// ControlHub WITHOUT starting its run() thread, exactly like
// test_engine_integration::statePropagates: the engine control slots are plain
// synchronous C++ calls, so "write command -> read-back" is fully deterministic.
// Recording is exercised with the thread started (mirrors test_wav_roundtrip).
//
// Covers:
//   1. write commands deterministically land in engine state and read-back agrees;
//   2. read commands are ALWAYS allowed; the write gate blocks writes in both states;
//   3. unknown commands / bad arguments are honest errors (never a crash, no fake ok);
//   4. no engine attached, and the no-telemetry empty state, are honest.
#include <QtTest/QtTest>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QDir>
#include <cmath>

#include "dsp/spectrum_engine.h"
#include "control/control_hub.h"
#include "core/tokens.h"

using namespace mbdsdr;
using namespace mbdsdr::dsp;

static QJsonObject parseObj(const QString& s) {
    QJsonParseError pe{};
    QJsonDocument d = QJsonDocument::fromJson(s.toUtf8(), &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) return QJsonObject();
    return d.object();
}

class TestControlHub : public QObject {
    Q_OBJECT
private slots:
    void commandsDriveEngineAndReadback();
    void readAlwaysAllowedWriteGateBothStates();
    void unknownCommandAndBadArgsAreHonest();
    void noEngineIsHonest();
    void telemetryEmptyStateIsHonest();
    void recordingStartStop();
    void commandTableIsClassified();
};

// 1) Every write command deterministically reaches the engine; the symmetric
//    read command returns the applied value.
void TestControlHub::commandsDriveEngineAndReadback() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // -- Tune + readback --
    QJsonObject r = parseObj(hub.execute("tune", {{"freq_hz", 145.0e6}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(r.value("frequency_hz").toDouble(), 145.0e6);
    QCOMPARE(parseObj(hub.execute("get_frequency", {})).value("frequency_hz").toDouble(),
             145.0e6);

    // Elastic clamp: an out-of-range frequency is clamped, not refused.
    r = parseObj(hub.execute("tune", {{"freq_hz", 5.0e9}}));   // above max
    QVERIFY(r.value("ok").toBool());
    QVERIFY(r.value("clamped").toBool());
    QCOMPARE(r.value("frequency_hz").toDouble(), 1700e6);

    // -- Mode + readback --
    r = parseObj(hub.execute("set_mode", {{"mode", "WFM"}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(parseObj(hub.execute("get_mode", {})).value("mode").toString(),
             QString("WFM"));

    // -- Bandwidth + readback --
    r = parseObj(hub.execute("set_bandwidth", {{"bandwidth_hz", 8000.0}}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(parseObj(hub.execute("get_bandwidth", {})).value("bandwidth_hz").toDouble(),
             8000.0);

    // -- Gain / squelch / mute / ANR (no synchronous getter; just assert the
    //    command is accepted and echoed, and that nothing crashes). --
    r = parseObj(hub.execute("set_gain", {{"gain_db", 20.0}}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(r.value("gain_db").toDouble(), 20.0);

    r = parseObj(hub.execute("set_squelch_enabled", {{"enabled", true}}));
    QVERIFY(r.value("ok").toBool());
    r = parseObj(hub.execute("set_squelch_threshold", {{"threshold_db", -55.0}}));
    QVERIFY(r.value("ok").toBool());

    r = parseObj(hub.execute("set_muted", {{"muted", false}}));
    QVERIFY(r.value("ok").toBool());
    r = parseObj(hub.execute("set_anr", {{"enabled", true}}));
    QVERIFY(r.value("ok").toBool());

    // -- Aggregate status, read at a deterministic point (before VFO/scan,
    //    which legitimately move the center frequency). --
    r = parseObj(hub.execute("get_status", {}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(r.value("frequency_hz").toDouble(), 1700e6);   // last tune clamped to max
    QCOMPARE(r.value("mode").toString(), QString("WFM"));
    QCOMPARE(r.value("bandwidth_hz").toDouble(), 8000.0);

    // -- VFO add / tune / readback --
    r = parseObj(hub.execute("vfo_add", {}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    const int selId = parseObj(hub.execute("get_vfos", {})).value("selected_vfo_id").toInt();
    r = parseObj(hub.execute("vfo_set_freq", {{"id", selId}, {"freq_hz", 120.0e6}}));
    QVERIFY(r.value("ok").toBool());
    QJsonArray vfos = parseObj(hub.execute("get_vfos", {})).value("vfos").toArray();
    bool found = false;
    for (const auto& v : vfos) {
        QJsonObject vv = v.toObject();
        if (vv.value("id").toInt() == selId) {
            found = true;
            QCOMPARE(vv.value("freq_hz").toDouble(), 120.0e6);
        }
    }
    QVERIFY(found);

    // -- Scan band (synthetic source: must return a structured hit, tagged) --
    r = parseObj(hub.execute("scan_band",
        {{"low_hz", 100.0e6}, {"high_hz", 102.0e6}, {"step_hz", 200e3}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QVERIFY(r.value("hits").toArray().size() == 1);
}

// 2) Read commands always pass; the write gate blocks writes in both states.
void TestControlHub::readAlwaysAllowedWriteGateBothStates() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    // Default gate policy must be explicit.
    QCOMPARE(hub.writeEnabled(), tokens::kControlHubWriteEnabledDefault);

    // Gate ON (default): a write lands.
    QJsonObject r = parseObj(hub.execute("tune", {{"freq_hz", 98.5e6}}));
    QVERIFY(r.value("ok").toBool());

    // Gate OFF: the SAME write is refused and the engine is untouched.
    hub.setWriteEnabled(false);
    r = parseObj(hub.execute("tune", {{"freq_hz", 150.0e6}}));
    QVERIFY(!r.value("ok").toBool());
    QVERIFY(r.value("gated").toBool());
    QVERIFY(r.value("error").isString());
    // Frequency must still be the pre-gate value (98.5 MHz), NOT 150 MHz.
    QCOMPARE(parseObj(hub.execute("get_frequency", {})).value("frequency_hz").toDouble(),
             98.5e6);
    // A read command is STILL allowed while the gate is closed.
    r = parseObj(hub.execute("get_mode", {}));
    QVERIFY2(r.value("ok").toBool(), "reads must never be gated");
    // Recording is a write too: refused under the gate.
    r = parseObj(hub.execute("start_recording", {}));
    QVERIFY(!r.value("ok").toBool());
    QVERIFY(r.value("gated").toBool());

    // Gate back ON: writes work again.
    hub.setWriteEnabled(true);
    r = parseObj(hub.execute("set_bandwidth", {{"bandwidth_hz", 12500.0}}));
    QVERIFY(r.value("ok").toBool());
}

// 3) Unknown commands and malformed arguments are honest errors.
void TestControlHub::unknownCommandAndBadArgsAreHonest() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Unknown command.
    QJsonObject r = parseObj(hub.execute("frobnicate", {}));
    QVERIFY(!r.value("ok").toBool());
    QVERIFY(r.value("error").toString().contains(QString::fromUtf8("未知命令")));

    // Missing required numeric argument.
    r = parseObj(hub.execute("tune", {}));
    QVERIFY(!r.value("ok").toBool());
    QVERIFY(r.value("error").toString().contains(QString::fromUtf8("freq_hz")));

    // Wrong-typed argument.
    r = parseObj(hub.execute("tune", {{"freq_hz", "not-a-number"}}));
    QVERIFY(!r.value("ok").toBool());

    // Out-of-vocabulary demodulation mode.
    r = parseObj(hub.execute("set_mode", {{"mode", "FM-ULTRA"}}));
    QVERIFY(!r.value("ok").toBool());
    QVERIFY(r.value("error").toString().contains(QString::fromUtf8("模式")));

    // vfo_set_freq missing its integer id.
    r = parseObj(hub.execute("vfo_set_freq", {{"freq_hz", 100e6}}));
    QVERIFY(!r.value("ok").toBool());
}

// 4a) No engine attached: every command is an honest failure, never a crash.
void TestControlHub::noEngineIsHonest() {
    control::ControlHub hub;   // setEngine never called
    QVERIFY(hub.engine() == nullptr);

    for (const QString& cmd : {QStringLiteral("get_frequency"),
                                QStringLiteral("tune"),
                                QStringLiteral("get_status"),
                                QStringLiteral("start_recording")}) {
        QJsonObject r = parseObj(hub.execute(cmd, {{}}));
        QVERIFY2(!r.value("ok").toBool(), cmd.toUtf8().constData());
        QVERIFY(r.value("error").isString());
    }
}

// 4b) Engine attached but never streamed: telemetry read-back is an explicit
//     empty state (never fabricated hardware numbers).
void TestControlHub::telemetryEmptyStateIsHonest() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    QJsonObject r = parseObj(hub.execute("get_telemetry", {}));
    QVERIFY(r.value("ok").toBool());
    QVERIFY2(!r.value("telemetry_available").toBool(),
             "no sourceTelemetry has fired yet -> must be the empty state");
    QVERIFY(r.value("note").isString());

    // get_status must also report the empty telemetry state, not fake hardware.
    r = parseObj(hub.execute("get_status", {}));
    QVERIFY(r.value("ok").toBool());
    QVERIFY(!r.value("telemetry_available").toBool());
    QVERIFY(!r.value("connected").toBool());
}

// Recording start/stop through the hub. Requires the engine run loop (like
// test_wav_roundtrip). Uses a temp recording dir; cleans up after itself.
void TestControlHub::recordingStartStop() {
    const QString recDir = QDir::tempPath() + "/mbdsdr_controlhub_rec";
    QDir().mkpath(recDir);

    SpectrumEngine eng;
    eng.setRecordingDir(recDir);
    eng.setRecTarget(RecTarget::DemodAudio);
    eng.setDemodMode("NFM");
    eng.start();
    QTest::qWait(400);   // let the run loop fill audio buffers

    control::ControlHub hub;
    hub.setEngine(&eng);

    QJsonObject r = parseObj(hub.execute("start_recording", {}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    const QString path = r.value("path").toString();
    QVERIFY(!path.isEmpty());

    QTest::qWait(400);   // accumulate a little audio

    r = parseObj(hub.execute("stop_recording", {}));
    QVERIFY(r.value("ok").toBool());

    eng.shutdown();
    eng.wait(2000);

    QFile::remove(path);
}

// The command table itself must classify reads vs writes consistently.
void TestControlHub::commandTableIsClassified() {
    const QList<control::ControlHub::CommandInfo> tbl =
        control::ControlHub{}.commandTable();
    QVERIFY(!tbl.isEmpty());

    bool sawTune = false, sawGetFreq = false, sawStartRec = false, sawGetStatus = false;
    for (const control::ControlHub::CommandInfo& c : tbl) {
        if (c.name == QStringLiteral("tune"))           { sawTune = true;          QVERIFY(c.write); }
        if (c.name == QStringLiteral("start_recording")){ sawStartRec = true;     QVERIFY(c.write); }
        if (c.name == QStringLiteral("get_frequency")) { sawGetFreq = true;         QVERIFY(!c.write); }
        if (c.name == QStringLiteral("get_status"))     { sawGetStatus = true;       QVERIFY(!c.write); }
    }
    QVERIFY(sawTune && sawGetFreq && sawStartRec && sawGetStatus);
}

QTEST_MAIN(TestControlHub)
#include "test_control_hub.moc"
