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
#include <QFile>
#include <QFileInfo>
#include <QSettings>
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
    // Phase21: the engine no longer auto-falls back to the synthetic test source.
    // These cases drive the offline TestSignalSource (e.g. scan_band needs a
    // real synthetic tone; recordingStartStop needs flowing audio), so opt in
    // explicitly BEFORE any engine is constructed (read in the ctor). The
    // telemetry-empty slot still sees an honest empty state because it never
    // starts the run loop, so no sourceTelemetry ever fires.
    void initTestCase();
    void commandsDriveEngineAndReadback();
    void readAlwaysAllowedWriteGateBothStates();
    void unknownCommandAndBadArgsAreHonest();
    void noEngineIsHonest();
    void telemetryEmptyStateIsHonest();
    void recordingStartStop();
    void commandTableIsClassified();
    void digitalSnapshotReadsAreHonestAndUngated();
    void clearDigitalOutputsGateBothStates();
    void exportIqSegment_writesRealFile();
    void exportIqSegment_noDataHonestError();
    void exportIqSegment_gatedWhenWriteGateClosed();
    void exportIqSegment_badTuneArgHonestError();
    // ---- Phase26: 21 newly tool-ized capabilities ------------------------
    void fftParamsLandAndSpectrumStatusReadsBack();
    void squelchSetLandAndStatusReadsBack();
    void noiseBlankerSetLandAndStatusReadsBack();
    void vfoListAddSwitchRename();
    void bookmarksPersistToQSettings();
    void recordingsListEmptyThenRealDeleteExport();
    void scanLinkStartStopStatus();
    void networkAudioStatusHonest();
    void phase26WritesAreGatedAndBadArgsHonest();
    // ---- Phase60+ armed parallel VFO monitoring: engine / UI / Agent /
    // ControlHub / HTTP all drive ONE state via vfoSetArmed + set_vfo_armed
    // (index+enabled, resolved through vfoMarkers like the Agent executor).
    void vfoArmedLandAndReadbackSameState();
    // Read-only pass prediction exposed over ControlHub/HTTP exactly like the
    // Agent predict_passes tool; honest ok=false when no fresh TLE cache.
    void predictPassesReadOnlyAndHonestEmpty();
    // Read-only capability + recording-state snapshots land on the engine and
    // read back honestly (empty gains / not recording) on the offline source.
    void capabilitiesAndRecordingStateReadBack();
    // Phase63 D1: top-level set_bandwidth with hz<=0 must NOT pollute the
    // engine bandwidth_ cache. The write tool echoes the request (ok:true,
    // async-mailbox semantics), but get_bandwidth / get_status must keep the
    // previously settled positive value -- never 0 / negative.
    void topLevelSetBandwidthNonPositiveDoesNotDriveEngine();
    // Phase63 D1-D5 bilateral alias contract: pin the new dual-key / dual-type
    // acceptance on the CH side (channel_id alias, string fft window/average,
    // add_bookmark group, vfo_set_* index alias).
    void phase63BilateralAliasContract();
};

void TestControlHub::initTestCase() {
    qputenv("MBDSDR_TEST_SOURCE", "1");   // explicit synthetic offline source
}

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

    // -- VFO bandwidth boundary (Phase63 D3): a non-positive VFO bandwidth must
    //    be REJECTED here with ok:false (parity with the Agent set_vfo_bandwidth
    //    tool), NOT silently accepted as ok:true while the engine no-ops. Pin the
    //    regression: the engine bandwidth must NOT drift on rejection. --
    auto vfoBwById = [&](int idWant) -> double {
        QJsonArray vs = parseObj(hub.execute("get_vfos", {})).value("vfos").toArray();
        for (const auto& v : vs) {
            QJsonObject vv = v.toObject();
            if (vv.value("id").toInt() == idWant)
                return vv.value("bandwidth_hz").toDouble();
        }
        return -1.0;
    };
    r = parseObj(hub.execute("vfo_set_bandwidth", {{"id", selId}, {"bandwidth_hz", 12500.0}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(vfoBwById(selId), 12500.0);
    // Zero / negative bandwidth must be refused, not fake-ok.
    r = parseObj(hub.execute("vfo_set_bandwidth", {{"id", selId}, {"bandwidth_hz", 0.0}}));
    QVERIFY2(!r.value("ok").toBool(), "vfo_set_bandwidth(0) must be ok:false");
    r = parseObj(hub.execute("vfo_set_bandwidth", {{"id", selId}, {"bandwidth_hz", -500.0}}));
    QVERIFY2(!r.value("ok").toBool(), "vfo_set_bandwidth(-500) must be ok:false");
    // Engine bandwidth unchanged after both rejected writes (zero drift).
    QCOMPARE(vfoBwById(selId), 12500.0);

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
    bool sawClear = false, sawGetPocsag = false, sawGetM17 = false, sawGetVor = false;
    for (const control::ControlHub::CommandInfo& c : tbl) {
        if (c.name == QStringLiteral("tune"))           { sawTune = true;          QVERIFY(c.write); }
        if (c.name == QStringLiteral("start_recording")){ sawStartRec = true;     QVERIFY(c.write); }
        if (c.name == QStringLiteral("get_frequency")) { sawGetFreq = true;         QVERIFY(!c.write); }
        if (c.name == QStringLiteral("get_status"))     { sawGetStatus = true;       QVERIFY(!c.write); }
        // New digital surface: reset is a write (gated), the three snapshots are reads.
        if (c.name == QStringLiteral("clear_digital_outputs")) { sawClear = true;        QVERIFY(c.write); }
        if (c.name == QStringLiteral("get_pocsag_messages"))  { sawGetPocsag = true;    QVERIFY(!c.write); }
        if (c.name == QStringLiteral("get_m17_calls"))          { sawGetM17 = true;       QVERIFY(!c.write); }
        if (c.name == QStringLiteral("get_vor_radial"))         { sawGetVor = true;       QVERIFY(!c.write); }
    }
    QVERIFY(sawTune && sawGetFreq && sawStartRec && sawGetStatus);
    QVERIFY(sawClear && sawGetPocsag && sawGetM17 && sawGetVor);
}

// 5) The POCSAG / m17 / VOR read commands pull the engine's REAL decode snapshot.
//    With the engine attached but never run() there is nothing decoded yet, so the
//    honest result is an EXPLICIT empty state (empty list / locked=false) -- never a
//    fabricated message, callsign or bearing. These are READ commands: they must work
//    even when the write gate is closed (reads are never gated).
void TestControlHub::digitalSnapshotReadsAreHonestAndUngated() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Gate CLOSED: reads must still be allowed.
    hub.setWriteEnabled(false);

    // POCSAG: honest empty list.
    QJsonObject r = parseObj(hub.execute("get_pocsag_messages", {}));
    QVERIFY2(r.value("ok").toBool(), "pocsag snapshot read must never be gated");
    QVERIFY(!r.value("gated").toBool());
    QVERIFY(r.value("messages").isArray());
    QCOMPARE(r.value("messages").toArray().size(), 0);
    QCOMPARE(r.value("count").toInt(), 0);

    // m17: honest empty list.
    r = parseObj(hub.execute("get_m17_calls", {}));
    QVERIFY2(r.value("ok").toBool(), "m17 snapshot read must never be gated");
    QVERIFY(r.value("calls").isArray());
    QCOMPARE(r.value("calls").toArray().size(), 0);

    // VOR: honest no-lock (locked=false; a bearing must NOT be fabricated).
    r = parseObj(hub.execute("get_vor_radial", {}));
    QVERIFY2(r.value("ok").toBool(), "vor snapshot read must never be gated");
    QVERIFY(!r.value("locked").toBool());

    // An explicit unknown channel id must ALSO resolve to the honest empty state
    // (the engine maps unknown id -> empty list / unlocked result), never a crash.
    r = parseObj(hub.execute("get_pocsag_messages", {{QStringLiteral("channel"), 999}}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(r.value("channel").toInt(), 999);
    QCOMPARE(r.value("count").toInt(), 0);

    // A wrong-typed channel argument is an honest error.
    r = parseObj(hub.execute("get_vor_radial", {{QStringLiteral("channel"), QStringLiteral("abc")}}));
    QVERIFY(!r.value("ok").toBool());
    QVERIFY(r.value("error").toString().contains(QString::fromUtf8("channel")));
}

// 6) clear_digital_outputs is a WRITE: it lands with the gate open and is honestly
//    refused (engine untouched) with the gate closed -- same two-state contract as
//    every other gated write command.
void TestControlHub::clearDigitalOutputsGateBothStates() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Gate ON (default): the reset lands (no channel exists yet -> engine no-ops,
    // but the command itself is accepted and echoed).
    QJsonObject r = parseObj(hub.execute("clear_digital_outputs", {}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QVERIFY(r.value("channel").isDouble());

    // Gate OFF: refused, never touches the engine.
    hub.setWriteEnabled(false);
    r = parseObj(hub.execute("clear_digital_outputs", {}));
    QVERIFY(!r.value("ok").toBool());
    QVERIFY(r.value("gated").toBool());
}

// ---- Phase24 block2: one-shot IQ export (independent of continuous recording) --
// export_iq_segment really writes a cf32_le SigMF pair on the synthetic source.
void TestControlHub::exportIqSegment_writesRealFile() {
    const QString recDir = QDir::tempPath() + "/mbdsdr_chub_export";
    QDir().mkpath(recDir);

    SpectrumEngine eng;
    eng.setRecordingDir(recDir);
    control::ControlHub hub;
    hub.setEngine(&eng);

    QJsonObject r = parseObj(hub.execute("export_iq_segment", {{"sample_count", 16384}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    const QString dataPath = r.value("path").toString();
    QVERIFY2(dataPath.endsWith(QStringLiteral(".sigmf-data")),
             qPrintable("export must be .sigmf-data, got: " + dataPath));
    QVERIFY(QFileInfo::exists(dataPath));

    // Real size/header check: bytes == samples*8; sidecar records cf32_le + count.
    QCOMPARE(r.value("samples").toVariant().toLongLong(), qint64(16384));
    const qint64 bytes = r.value("bytes").toVariant().toLongLong();
    QVERIFY2(bytes == 16384 * 8, qPrintable("cf32_le size must be samples*8, got " +
                                             QString::number(bytes)));
    const QString metaPath = QString(dataPath).replace(".sigmf-data", ".sigmf-meta");
    QVERIFY(QFileInfo::exists(metaPath));
    QFile mf(metaPath);
    QVERIFY(mf.open(QIODevice::ReadOnly));
    QJsonObject meta = QJsonDocument::fromJson(mf.readAll()).object();
    QJsonObject global = meta.value("global").toObject();
    QCOMPARE(global.value("core:datatype").toString(), QString("cf32_le"));
    QCOMPARE(global.value("core:num_samples").toVariant().toLongLong(), qint64(16384));

    QFile::remove(dataPath);
    QFile::remove(metaPath);
}

// No source data -> honest ok:false (the engine lands on the empty NullSource
// when the synthetic opt-in is removed), never a fabricated file.
void TestControlHub::exportIqSegment_noDataHonestError() {
    qunsetenv("MBDSDR_TEST_SOURCE");    // THIS engine -> honest empty NullSource
    SpectrumEngine emptyEng;
    qputenv("MBDSDR_TEST_SOURCE", "1"); // restore for later engines

    control::ControlHub hub;
    hub.setEngine(&emptyEng);
    QJsonObject r = parseObj(hub.execute("export_iq_segment", {{"sample_count", 8192}}));
    QCOMPARE(r.value("ok").toBool(), false);
    QVERIFY2(r.value("error").toString().contains(QString::fromUtf8("数据")),
             qPrintable("no-data export must say so, got: " +
                        r.value("error").toString()));
    QVERIFY(!r.contains("path") || r.value("path").toString().isEmpty());
}

// export_iq_segment is a WRITE: it lands with the gate open and is honestly
// refused (gated:true, no file touched) with the gate closed.
void TestControlHub::exportIqSegment_gatedWhenWriteGateClosed() {
    const QString recDir = QDir::tempPath() + "/mbdsdr_chub_export_gate";
    QDir().mkpath(recDir);

    SpectrumEngine eng;
    eng.setRecordingDir(recDir);
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Gate CLOSED: refused, engine untouched, no file created.
    hub.setWriteEnabled(false);
    QJsonObject r = parseObj(hub.execute("export_iq_segment", {{"sample_count", 8192}}));
    QCOMPARE(r.value("ok").toBool(), false);
    QVERIFY(r.value("gated").toBool());

    // Gate OPEN: the same export now lands and writes a real file.
    hub.setWriteEnabled(true);
    r = parseObj(hub.execute("export_iq_segment", {{"sample_count", 8192}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    const QString dataPath = r.value("path").toString();
    QVERIFY(QFileInfo::exists(dataPath));
    QFile::remove(dataPath);
    QFile::remove(QString(dataPath).replace(".sigmf-data", ".sigmf-meta"));
}

// A wrongly-typed tune_hz is an honest argument error (never a silent retune to 0).
void TestControlHub::exportIqSegment_badTuneArgHonestError() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    QJsonObject r = parseObj(hub.execute("export_iq_segment",
                                         {{"tune_hz", "not-a-number"}}));
    QCOMPARE(r.value("ok").toBool(), false);
    QVERIFY(r.value("error").toString().contains(QString::fromUtf8("tune_hz")));
}

// ---- Phase26 -------------------------------------------------------------
// set_fft_params really changes fft_size/window/average; get_spectrum_status
// reads the REAL engine values back.
void TestControlHub::fftParamsLandAndSpectrumStatusReadsBack() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    QJsonObject r = parseObj(hub.execute("set_fft_params",
        {{"fft_size", 4096}, {"window", 2}, {"average", 1}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(r.value("fft_size").toInt(), 4096);

    QJsonObject s = parseObj(hub.execute("get_spectrum_status", {}));
    QVERIFY(s.value("ok").toBool());
    QCOMPARE(s.value("fft_size").toInt(), 4096);   // real engine fftSize_
    QCOMPARE(s.value("window").toInt(), 2);
    QCOMPARE(s.value("average").toInt(), 1);

    // Bad args: no parameter at all is an honest error.
    r = parseObj(hub.execute("set_fft_params", {}));
    QCOMPARE(r.value("ok").toBool(), false);
}

// set_squelch really moves the threshold; get_squelch_status reads it back.
void TestControlHub::squelchSetLandAndStatusReadsBack() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    QJsonObject r = parseObj(hub.execute("set_squelch",
        {{"enabled", true}, {"threshold_db", -70.0}, {"auto", false}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(r.value("threshold_db").toDouble(), -70.0);

    QJsonObject s = parseObj(hub.execute("get_squelch_status", {}));
    QVERIFY(s.value("ok").toBool());
    QCOMPARE(s.value("enabled").toBool(), true);
    QCOMPARE(s.value("threshold_db").toDouble(), -70.0);  // real cached threshold
    QCOMPARE(s.value("auto").toBool(), false);

    // auto=true latches; manual threshold disarms it.
    r = parseObj(hub.execute("set_squelch", {{"auto", true}}));
    QVERIFY(r.value("ok").toBool());
    s = parseObj(hub.execute("get_squelch_status", {}));
    QCOMPARE(s.value("auto").toBool(), true);
    r = parseObj(hub.execute("set_squelch", {{"threshold_db", -60.0}}));
    QVERIFY(r.value("ok").toBool());
    s = parseObj(hub.execute("get_squelch_status", {}));
    QCOMPARE(s.value("auto").toBool(), false);   // manual threshold disarmed auto
}

// set_noise_blanker really flips the engine switch; get_noise_blanker_status
// reads the real state back. Missing / non-boolean `on` are honest errors.
void TestControlHub::noiseBlankerSetLandAndStatusReadsBack() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Fresh engine: honest off.
    QJsonObject s0 = parseObj(hub.execute("get_noise_blanker_status", {}));
    QVERIFY2(s0.value("ok").toBool(), s0.value("error").toString().toUtf8().constData());
    QCOMPARE(s0.value("command").toString(), QStringLiteral("get_noise_blanker_status"));
    QCOMPARE(s0.value("enabled").toBool(), false);

    // Write lands on the engine.
    QJsonObject r = parseObj(hub.execute("set_noise_blanker", {{"on", true}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(r.value("enabled").toBool(), true);
    QCOMPARE(eng.noiseBlankerEnabled(), true);

    // Read-back reflects the real engine state.
    QJsonObject s = parseObj(hub.execute("get_noise_blanker_status", {}));
    QCOMPARE(s.value("enabled").toBool(), true);

    // Off again.
    parseObj(hub.execute("set_noise_blanker", {{"on", false}}));
    QCOMPARE(parseObj(hub.execute("get_noise_blanker_status", {}))
             .value("enabled").toBool(), false);

    // Missing / wrong-typed `on` -> honest ok:false.
    QCOMPARE(parseObj(hub.execute("set_noise_blanker", {})).value("ok").toBool(), false);
    QCOMPARE(parseObj(hub.execute("set_noise_blanker", {{"on", "yes"}}))
             .value("ok").toBool(), false);
}

// add_vfo / switch_vfo really move the selected channel; rename_vfo renames it.
void TestControlHub::vfoListAddSwitchRename() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    QJsonObject before = parseObj(hub.execute("list_vfos", {}));
    QVERIFY(before.value("ok").toBool());
    const int firstSel = before.value("selected_vfo_id").toInt();

    QJsonObject r = parseObj(hub.execute("add_vfo", {}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    const int newId = r.value("selected_vfo_id").toInt();
    QVERIFY(newId != firstSel);

    // Switch back to the original channel: the active point really moves.
    r = parseObj(hub.execute("switch_vfo", {{"index", firstSel}}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(r.value("selected_vfo_id").toInt(), firstSel);
    QCOMPARE(parseObj(hub.execute("get_vfos", {})).value("selected_vfo_id").toInt(),
             firstSel);

    // Rename the new VFO; list_vfos must show the real name.
    r = parseObj(hub.execute("rename_vfo", {{"index", newId}, {"name", QStringLiteral("我的VFO")}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    bool found = false;
    for (const auto& v : parseObj(hub.execute("list_vfos", {})).value("vfos").toArray()) {
        QJsonObject vv = v.toObject();
        if (vv.value("id").toInt() == newId) {
            found = true;
            QCOMPARE(vv.value("name").toString(), QStringLiteral("我的VFO"));
        }
    }
    QVERIFY(found);
}

// add_bookmark really persists to QSettings "ui/bookmarks"; delete_bookmark removes.
void TestControlHub::bookmarksPersistToQSettings() {
    control::ControlHub hub;   // engine not needed for bookmark bookkeeping, but
    SpectrumEngine eng;        // execute() requires an attached engine.
    hub.setEngine(&eng);

    QSettings rs(QString::fromUtf8("MBDSDR"), QString::fromUtf8("MBDSDR"));
    const int countBefore =
        parseObj(hub.execute("list_bookmarks", {})).value("count").toInt();

    QJsonObject r = parseObj(hub.execute("add_bookmark",
        {{"freq_hz", 144.5e6}, {"name", QStringLiteral("测试台")}, {"mode", "NFM"}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    const int idx = r.value("index").toInt();

    // QSettings really landed on disk.
    QJsonArray stored = QJsonDocument::fromJson(
        rs.value(QStringLiteral("ui/bookmarks")).toByteArray()).array();
    bool found = false;
    for (const auto& b : stored) {
        if (qAbs(b.toObject().value("freq").toDouble() - 144.5e6) < 1.0) found = true;
    }
    QVERIFY2(found, "add_bookmark must write QSettings ui/bookmarks");

    QCOMPARE(parseObj(hub.execute("list_bookmarks", {})).value("count").toInt(),
             countBefore + 1);

    // tune_to_bookmark really retunes the engine to the stored frequency.
    r = parseObj(hub.execute("tune_to_bookmark", {{"index", idx}}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(r.value("freq_hz").toDouble(), 144.5e6);
    QCOMPARE(parseObj(hub.execute("get_frequency", {})).value("frequency_hz").toDouble(),
             144.5e6);

    // delete_bookmark removes it.
    r = parseObj(hub.execute("delete_bookmark", {{"index", idx}}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(parseObj(hub.execute("list_bookmarks", {})).value("count").toInt(),
             countBefore);
}

// list_recordings: honest empty state on a fresh dir, then a real file appears;
// delete_recording only removes inside recDir; export_recording copies out.
void TestControlHub::recordingsListEmptyThenRealDeleteExport() {
    const QString recDir = QDir::tempPath() + "/mbdsdr_chub_recs";
    QDir(recDir).removeRecursively();
    QDir().mkpath(recDir);
    const QString outPath = QDir::tempPath() + "/mbdsdr_chub_exported.wav";

    SpectrumEngine eng;
    eng.setRecordingDir(recDir);
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Honest empty state.
    QJsonObject r = parseObj(hub.execute("list_recordings", {}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(r.value("count").toInt(), 0);

    // Drop a real file into the rec dir; list must show it.
    QFile f(recDir + "/tone.wav");
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write("fake");
    f.close();
    r = parseObj(hub.execute("list_recordings", {}));
    QCOMPARE(r.value("count").toInt(), 1);
    QCOMPARE(r.value("recordings").toArray().first().toObject().value("name").toString(),
             QStringLiteral("tone.wav"));

    // export_recording copies it out.
    r = parseObj(hub.execute("export_recording",
        {{"name", QStringLiteral("tone.wav")}, {"out_path", outPath}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QVERIFY(QFileInfo::exists(outPath));

    // Path-contained guard: a ".." escape is refused, nothing deleted.
    r = parseObj(hub.execute("delete_recording", {{"name", QStringLiteral("../tone.wav")}}));
    QCOMPARE(r.value("ok").toBool(), false);
    QVERIFY(QFileInfo::exists(recDir + "/tone.wav"));

    // delete_recording removes the contained file.
    r = parseObj(hub.execute("delete_recording", {{"name", QStringLiteral("tone.wav")}}));
    QVERIFY(r.value("ok").toBool());
    QVERIFY(!QFileInfo::exists(recDir + "/tone.wav"));

    QFile::remove(outPath);
    QDir(recDir).removeRecursively();
}

// start_scan_link arms the link (state -> scanning); stop -> idle.
void TestControlHub::scanLinkStartStopStatus() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    QJsonObject r = parseObj(hub.execute("start_scan_link", {{"target_freq_hz", 145.0e6}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(r.value("status").toString(), QStringLiteral("scanning"));

    QJsonObject s = parseObj(hub.execute("get_scan_link_status", {}));
    QVERIFY(s.value("ok").toBool());
    QVERIFY(s.value("status").toString() == QStringLiteral("scanning") ||
            s.value("status").toString() == QStringLiteral("dwell"));

    r = parseObj(hub.execute("stop_scan_link", {}));
    QVERIFY(r.value("ok").toBool());
    s = parseObj(hub.execute("get_scan_link_status", {}));
    QCOMPARE(s.value("status").toString(), QStringLiteral("idle"));

    // Bad arg: missing target.
    r = parseObj(hub.execute("start_scan_link", {}));
    QCOMPARE(r.value("ok").toBool(), false);
}

// Network-audio status: honest disabled empty state; enabling (UDP loopback, if
// the sandbox permits a socket) reads the port back; disabling returns to empty.
void TestControlHub::networkAudioStatusHonest() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    QJsonObject s = parseObj(hub.execute("get_network_audio_status", {}));
    QVERIFY(s.value("ok").toBool());
    QCOMPARE(s.value("enabled").toBool(), false);

    QJsonObject r = parseObj(hub.execute("set_network_audio_sink",
        {{"enable", true}, {"port", 49154}, {"format", QStringLiteral("udp")}}));
    if (r.value("ok").toBool()) {
        QCOMPARE(r.value("port").toInt(), 49154);
        s = parseObj(hub.execute("get_network_audio_status", {}));
        QCOMPARE(s.value("enabled").toBool(), true);
        QCOMPARE(s.value("port").toInt(), 49154);
    } // else: sandbox blocked the socket -> honest error, not a fake stream.

    // Disable always returns to the empty state.
    r = parseObj(hub.execute("set_network_audio_sink", {{"enable", false}}));
    QVERIFY(r.value("ok").toBool());
    s = parseObj(hub.execute("get_network_audio_status", {}));
    QCOMPARE(s.value("enabled").toBool(), false);
}

// Phase26 write commands are gated; reads are not; bad args are honest errors.
void TestControlHub::phase26WritesAreGatedAndBadArgsHonest() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Gate closed: a sample of new writes are refused, engine untouched.
    hub.setWriteEnabled(false);
    for (const char* cmd : {
            "set_fft_params", "add_bookmark", "start_scan_link",
            "delete_recording", "set_squelch", "rename_vfo", "set_color_map",
            "set_noise_blanker"}) {
        QJsonObject r = parseObj(hub.execute(QString::fromUtf8(cmd), {{}}));
        QVERIFY2(!r.value("ok").toBool() && r.value("gated").toBool(),
                 qPrintable(QString::fromUtf8(cmd)));
    }
    // Reads still pass with the gate closed.
    QVERIFY(parseObj(hub.execute("get_spectrum_status", {})).value("ok").toBool());
    QVERIFY(parseObj(hub.execute("list_bookmarks", {})).value("ok").toBool());

    hub.setWriteEnabled(true);

    // set_color_map really writes the QSettings persistence key.
    QJsonObject r = parseObj(hub.execute("set_color_map",
        {{"file_path", QStringLiteral("/tmp/wf_cmap_test.json")}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QSettings rs(QString::fromUtf8("MBDSDR"), QString::fromUtf8("MBDSDR"));
    QCOMPARE(rs.value(QLatin1String(tokens::kSettingsKeyColormapFile)).toString(),
             QStringLiteral("/tmp/wf_cmap_test.json"));
    rs.remove(QLatin1String(tokens::kSettingsKeyColormapFile));

    // Bad args are honest errors.
    QCOMPARE(parseObj(hub.execute("add_bookmark", {})).value("ok").toBool(), false);
    QCOMPARE(parseObj(hub.execute("rename_vfo", {{"index", 1}})).value("ok").toBool(), false);
    QCOMPARE(parseObj(hub.execute("set_squelch", {})).value("ok").toBool(), false);
    QCOMPARE(parseObj(hub.execute("set_color_map", {{}})).value("ok").toBool(), false);
}

// Armed parallel monitoring: ControlHub set_vfo_armed (index+enabled) lands in
// the engine, get_vfos reads it back as the SAME state (armed), and the engine
// direct path vfoSetArmed agrees -- the state a UI checkbox / Agent tool /
// ControlHub / HTTP POST /command all drive is one.
void TestControlHub::vfoArmedLandAndReadbackSameState() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    QJsonObject r = parseObj(hub.execute("vfo_add", {}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    // Index 0 is the pre-existing default VFO (vfo_add appends the new one at
    // the tail and selects it), so index 0's id comes from the marker list.
    QJsonArray vfos0 = parseObj(hub.execute("get_vfos", {})).value("vfos").toArray();
    QVERIFY(vfos0.size() >= 1);
    const int id = vfos0.at(0).toObject().value("id").toInt();

    // Default: not armed.
    {
        QJsonArray vfos = parseObj(hub.execute("get_vfos", {})).value("vfos").toArray();
        bool found = false;
        for (const auto& v : vfos) {
            QJsonObject vv = v.toObject();
            if (vv.value("id").toInt() == id) {
                found = true;
                QCOMPARE(vv.value("armed").toBool(), false);
            }
        }
        QVERIFY(found);
    }

    // ControlHub write: index 0 armed=true -> engine state flips, readback armed.
    r = parseObj(hub.execute("set_vfo_armed", {{"index", 0}, {"enabled", true}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(r.value("vfo_id").toInt(), id);
    QCOMPARE(r.value("armed").toBool(), true);
    {
        QJsonArray vfos = parseObj(hub.execute("get_vfos", {})).value("vfos").toArray();
        for (const auto& v : vfos) {
            QJsonObject vv = v.toObject();
            if (vv.value("id").toInt() == id)
                QCOMPARE(vv.value("armed").toBool(), true);
        }
        // Engine direct path agrees with the ControlHub write.
        QVERIFY(eng.vfoSetArmed(id, true));       // idempotent, stays armed
        vfos = parseObj(hub.execute("get_vfos", {})).value("vfos").toArray();
        for (const auto& v : vfos) {
            QJsonObject vv = v.toObject();
            if (vv.value("id").toInt() == id)
                QCOMPARE(vv.value("armed").toBool(), true);
        }
    }

    // Un-arm through ControlHub; readback follows.
    r = parseObj(hub.execute("set_vfo_armed", {{"index", 0}, {"enabled", false}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(r.value("armed").toBool(), false);
    {
        QJsonArray vfos = parseObj(hub.execute("get_vfos", {})).value("vfos").toArray();
        for (const auto& v : vfos) {
            QJsonObject vv = v.toObject();
            if (vv.value("id").toInt() == id)
                QCOMPARE(vv.value("armed").toBool(), false);
        }
    }

    // Honest errors: index out of range, enabled not a bool, missing args.
    r = parseObj(hub.execute("set_vfo_armed", {{"index", 99}, {"enabled", true}}));
    QVERIFY(!r.value("ok").toBool());
    r = parseObj(hub.execute("set_vfo_armed", {{"index", 0}, {"enabled", "yes"}}));
    QVERIFY(!r.value("ok").toBool());
    r = parseObj(hub.execute("set_vfo_armed", {{"index", 0}}));
    QVERIFY(!r.value("ok").toBool());

    // Write gate closed: refused, engine untouched (still un-armed).
    hub.setWriteEnabled(false);
    r = parseObj(hub.execute("set_vfo_armed", {{"index", 0}, {"enabled", true}}));
    QVERIFY(!r.value("ok").toBool() && r.value("gated").toBool());
    hub.setWriteEnabled(true);
    {
        QJsonArray vfos = parseObj(hub.execute("get_vfos", {})).value("vfos").toArray();
        for (const auto& v : vfos) {
            QJsonObject vv = v.toObject();
            if (vv.value("id").toInt() == id)
                QCOMPARE(vv.value("armed").toBool(), false);
        }
    }
}

// predict_passes over ControlHub is read-only (works with the gate closed) and
// honest: ok is a real bool, an ok=false always carries an error+source, and an
// ok=true carries source + a passes array (whatever the TLE cache holds at the
// moment -- we assert structure, never fabricate a pass).
void TestControlHub::predictPassesReadOnlyAndHonestEmpty() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Read passes even with the write gate closed.
    hub.setWriteEnabled(false);
    QJsonObject r = parseObj(hub.execute("predict_passes", {
        {"satellite_name", QStringLiteral("ISS (ZARYA)")},
        {"station_lat_deg", 43.8}, {"station_lon_deg", 125.3}}));
    QVERIFY2(r.contains("ok") && r.value("ok").isBool(),
             qPrintable(r.value("error").toString()));
    if (r.value("ok").toBool()) {
        QVERIFY(r.contains("source"));
        QVERIFY(r.value("passes").isArray());
    } else {
        // Honest empty: error text + a source label, never a fabricated pass.
        QVERIFY(r.contains("error") && !r.value("error").toString().isEmpty());
        QVERIFY(r.contains("source"));
    }

    // Bad args (missing satellite name) are still an honest structured result.
    r = parseObj(hub.execute("predict_passes", {{"station_lat_deg", 43.8}}));
    QVERIFY(r.value("ok").isBool());
}

// get_capabilities / get_recording_state are READ commands: they land on the
// engine and read back honestly on the offline synthetic source -- connected is
// false, gains_db is an EMPTY array (no fabricated gain table), provenance labels
// the test source, and recording=false with an empty path. Reads are never gated.
void TestControlHub::capabilitiesAndRecordingStateReadBack() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Reads work even with the write gate closed.
    hub.setWriteEnabled(false);

    QJsonObject c = parseObj(hub.execute("get_capabilities", {}));
    QVERIFY2(c.value("ok").toBool(), "capabilities read must never be gated");
    QVERIFY(!c.value("gated").toBool());
    QCOMPARE(c.value("command").toString(), QStringLiteral("get_capabilities"));
    QVERIFY(c.value("connected").isBool());
    QVERIFY(!c.value("connected").toBool());          // synthetic source, not real HW
    QVERIFY(c.value("gains_db").isArray());
    QCOMPARE(c.value("gains_db").toArray().size(), 0); // honest empty gain table
    QVERIFY(c.value("tunable_min_hz").isDouble());
    QVERIFY(c.value("tunable_max_hz").isDouble());
    QVERIFY(c.value("sample_rate_min_hz").isDouble());
    QVERIFY(c.value("sample_rate_max_hz").isDouble());
    QVERIFY2(c.value("provenance").toString().contains(QStringLiteral("测试信号")),
             qPrintable("test source must be labelled, got: " +
                        c.value("provenance").toString()));

    QJsonObject rs = parseObj(hub.execute("get_recording_state", {}));
    QVERIFY2(rs.value("ok").toBool(), "recording-state read must never be gated");
    QVERIFY(!rs.value("gated").toBool());
    QCOMPARE(rs.value("command").toString(), QStringLiteral("get_recording_state"));
    QVERIFY(!rs.value("recording").toBool());          // nothing recording yet
    QVERIFY(rs.value("recording_path").toString().isEmpty());
    QVERIFY(rs.value("watch_enabled").isBool());
    QVERIFY(rs.value("recording_dir").isString());

    hub.setWriteEnabled(true);
}

// Phase63 D1: top-level set_bandwidth{0.0 / -500.0} must be dropped by the
// engine guard (parity with onSetCenterFreq), not silently enqueued. The CH
// write tool still echoes the request with ok:true (async-mailbox semantics),
// but the engine bandwidth_ cache -- and therefore get_bandwidth / get_status
// -- must keep the previously settled positive value. UI (vfoList / bwCombo /
// spectrum band-edge box) reads the same engine cache, so this pins the
// get_status-vs-UI consistency.
void TestControlHub::topLevelSetBandwidthNonPositiveDoesNotDriveEngine() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Land a known positive bandwidth first.
    QJsonObject r = parseObj(hub.execute("set_bandwidth", {{"bandwidth_hz", 8000.0}}));
    QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
    QCOMPARE(parseObj(hub.execute("get_bandwidth", {})).value("bandwidth_hz").toDouble(),
             8000.0);

    // Zero bandwidth: tool echoes request with ok:true (write semantics), but
    // the engine cache must NOT move to 0.
    r = parseObj(hub.execute("set_bandwidth", {{"bandwidth_hz", 0.0}}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(r.value("bandwidth_hz").toDouble(), 0.0);   // echo, not applied
    QVERIFY2(parseObj(hub.execute("get_bandwidth", {}))
                 .value("bandwidth_hz").toDouble() > 0.0,
             "engine bandwidth must stay positive after set_bandwidth{0}");
    QCOMPARE(parseObj(hub.execute("get_bandwidth", {})).value("bandwidth_hz").toDouble(),
             8000.0);

    // Negative bandwidth: same expectation.
    r = parseObj(hub.execute("set_bandwidth", {{"bandwidth_hz", -500.0}}));
    QVERIFY(r.value("ok").toBool());
    QCOMPARE(r.value("bandwidth_hz").toDouble(), -500.0);  // echo, not applied
    QCOMPARE(parseObj(hub.execute("get_bandwidth", {})).value("bandwidth_hz").toDouble(),
             8000.0);

    // Aggregate status must also reflect the unchanged positive bandwidth.
    QJsonObject st = parseObj(hub.execute("get_status", {}));
    QVERIFY(st.value("ok").toBool());
    QVERIFY2(st.value("bandwidth_hz").toDouble() > 0.0,
             "get_status bandwidth_hz must not be polluted by a rejected write");
    QCOMPARE(st.value("bandwidth_hz").toDouble(), 8000.0);
}

// Phase63 D1-D5 bilateral alias contract (CH side).
void TestControlHub::phase63BilateralAliasContract() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    // --- D2: get_pocsag_messages accepts "channel_id" (Agent key). ---
    {
        QJsonObject r = parseObj(hub.execute("get_pocsag_messages", {{"channel_id", 0}}));
        QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
        QCOMPARE(r.value("channel").toInt(), 0);
    }

    // --- D3: set_fft_params accepts string window/average (Agent enum). ---
    {
        QJsonObject r = parseObj(hub.execute("set_fft_params",
            {{"fft_size", 4096}, {"window", QStringLiteral("Blackman")},
             {"average", QStringLiteral("Slow")}}));
        QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
        QCOMPARE(eng.windowType(), 2);
        QCOMPARE(eng.averageMode(), 1);
        // And raw int still works.
        r = parseObj(hub.execute("set_fft_params",
            {{"fft_size", 2048}, {"window", 1}, {"average", 0}}));
        QVERIFY(r.value("ok").toBool());
        QCOMPARE(eng.windowType(), 1);
        QCOMPARE(eng.averageMode(), 0);
    }

    // --- D4: add_bookmark consumes `group`. ---
    {
        QJsonObject r = parseObj(hub.execute("add_bookmark",
            {{"freq_hz", 145.0e6}, {"group", QStringLiteral("vfo")},
             {"name", QStringLiteral("test")}}));
        QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
        QJsonObject lst = parseObj(hub.execute("list_bookmarks", {}));
        QVERIFY(lst.value("ok").toBool());
        bool saw = false;
        for (const auto& b : lst.value("bookmarks").toArray()) {
            QJsonObject bo = b.toObject();
            if (bo.value("freq_hz").toDouble() == 145.0e6) {
                QCOMPARE(bo.value("group").toString(), QStringLiteral("vfo"));
                saw = true;
            }
        }
        QVERIFY(saw);
    }

    // --- D1: vfo_set_freq accepts "index" (Agent key) instead of "id". ---
    {
        QJsonObject before = parseObj(hub.execute("list_vfos", {}));
        const int idx = 0;
        QJsonObject r = parseObj(hub.execute("vfo_set_freq",
            {{"index", idx}, {"freq_hz", 101100000.0}}));
        QVERIFY2(r.value("ok").toBool(), r.value("error").toString().toUtf8().constData());
        QVERIFY(r.value("id").toInt() >= 0);
        // Readback: marker 0 moved to the new frequency.
        QJsonObject after = parseObj(hub.execute("list_vfos", {}));
        QCOMPARE(after.value("vfos").toArray().at(0).toObject().value("freq_hz").toDouble(),
                 101100000.0);
    }
}

QTEST_MAIN(TestControlHub)
#include "test_control_hub.moc"