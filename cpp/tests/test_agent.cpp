// SPDX-License-Identifier: MIT
#include <QtTest/QtTest>
#include <QRegularExpression>
#include <QSettings>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>

#include "ai/agent_tools.h"
#include "ai/tool_schema.h"
#include "ai/agent.h"
#include "ai/llm_worker.h"
#include "ai/ai_config.h"
#include "dsp/spectrum_engine.h"
#include "dsp/frequency_calibrator.h"
#include "dsp/fcch_detector.h"
#include "core/tokens.h"
#include "synthetic_iq_fixture.h"

using namespace mbdsdr;

class TestAgent : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void testToolParse();
    void testTuneTool();
    void testModeTool();
    void testConfigFields();
    void testLocalFrequency();
    void testLocalMode();
    void testLocalUnknown();
    // --- Manual-mode write-tool gate (desktop AI takeover / handoff) ---
    void testWriteToolClassification();
    void testManualModeGatesWriteTool();
    void testManualModeAllowsReadTool();
    void testAiTakeoverRunsWriteTool();
    void testManualModePersistence();
    // --- Phase55: Costas lock read-out + Doppler control surface ------------
    void testDigitalLockStatusHonestEmpty();
    void testDopplerSurfaceNullHonestUnavailable();
    // --- Frequency calibration agent tools (offline synthetic e2e) ---------
    void calibrate_handheld_syntheticInjectedPpm();
    void calibrate_fcch_syntheticInjectedPpm();
    void calibrate_manual_syntheticInjectedNegativePpm();
    void calibrate_noiseOnly_honestlyNoCarrier();
    void applyCorrection_writesSettingAndApplies();
    void manualMode_gatesApplyCorrection();
    void manualMode_allowsCalibrateRead();
    // --- VFO fine-grained edit tools (set_vfo_frequency/mode/bandwidth) -----
    void vfoEditToolsLandAndReadback();
    void vfoEditToolsBadArgsAndGate();
    // --- Read-only capability + recording-state snapshot tools -------------
    void capabilitiesAndRecordingStateHonestEmptyThenRecording();
    // --- Phase62 audit: 10-tool manual-gate spot-check (5 write / 5 read) ---
    void manualMode_gateSpotCheckTenTools();
    // --- Phase62 gate full-coverage: ALL 28 writes gated / ALL 17 reads open ---
    void manualMode_gateSpotCheckAllWrites();
};

void TestAgent::initTestCase() {
    // Default QSettings has no org/app name; Windows NativeFormat (registry)
    // ignores setPath and rejects empty keys, so apply_frequency_correction's
    // savePpmSetting would be silently dropped. Isolate to a PID-unique
    // IniFormat dir (honors setPath on every platform).
    const QString cfg = QDir::tempPath() + "/mbdsdr_cfg_agent_" +
                        QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(cfg);
    QCoreApplication::setOrganizationName("MBDSDR");
    QCoreApplication::setApplicationName("MBDSDR");
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, cfg);
}

void TestAgent::testToolParse() {
    auto tools = ai::toolDefs();
    QCOMPARE(tools.size(), 45);
    QCOMPARE(tools[0].name, "tune_frequency");
    QCOMPARE(tools[1].name, "set_mode");
}

void TestAgent::testTuneTool() {
    dsp::SpectrumEngine engine;
    QJsonObject args;
    args["freq_hz"] = 103300000;
    QString result = ai::executeTool("tune_frequency", args, &engine);
    QVERIFY2(result.contains("103.3"), qPrintable(result));
}

void TestAgent::testModeTool() {
    dsp::SpectrumEngine engine;
    QJsonObject args;
    args["mode"] = "AM";
    QString result = ai::executeTool("set_mode", args, &engine);
    QVERIFY2(result.contains("AM"), qPrintable(result));
}

void TestAgent::testConfigFields() {
    ai::AiConfig cfg;
    cfg.apiKey = "test-key-123";
    cfg.baseUrl = "https://test.example.com/v1";
    cfg.model = "test-model";
    QVERIFY(cfg.isConfigured());
    QCOMPARE(cfg.baseUrl, QString("https://test.example.com/v1"));
}

void TestAgent::testLocalFrequency() {
    QRegularExpression freqRe(
        R"(^\s*(\d+(\.\d+)?)\s*([mM]?[Hh]?[Zz]?)\s*$)");
    auto m = freqRe.match("98.5");
    QVERIFY(m.hasMatch());
    QCOMPARE(m.captured(1).toDouble(), 98.5);

    m = freqRe.match("100M");
    QVERIFY(m.hasMatch());
    QCOMPARE(m.captured(1).toDouble(), 100.0);

    m = freqRe.match("439.850");
    QVERIFY(m.hasMatch());
    QCOMPARE(m.captured(1).toDouble(), 439.850);
}

void TestAgent::testLocalMode() {
    QString low = "am";
    QCOMPARE(low.toUpper(), QString("AM"));
    low = "nfm";
    QCOMPARE(low.toUpper(), QString("NFM"));
    low = "ssb";
    QCOMPARE(low.toUpper(), QString("SSB"));
}

void TestAgent::testLocalUnknown() {
    QRegularExpression freqRe(
        R"(^\s*(\d+(\.\d+)?)\s*([mM]?[Hh]?[Zz]?)\s*$)");
    QVERIFY(!freqRe.match("hello world").hasMatch());
}

// The write/read split must match the registered tool set in agent_tools.cpp.
void TestAgent::testWriteToolClassification() {
    QVERIFY(ai::isWriteTool("tune_frequency"));
    QVERIFY(ai::isWriteTool("set_mode"));
    QVERIFY(ai::isWriteTool("set_bandwidth"));
    QVERIFY(ai::isWriteTool("start_recording"));
    QVERIFY(ai::isWriteTool("stop_recording"));
    QVERIFY(ai::isWriteTool("scan_band"));
    QVERIFY(ai::isWriteTool("apply_frequency_correction"));   // persists + drives source
    // read-only / unknown are never gated
    QVERIFY(!ai::isWriteTool("get_status"));
    QVERIFY(!ai::isWriteTool("calibrate_frequency"));       // measurement, no state change
    QVERIFY(!ai::isWriteTool("some_future_read_tool"));
    QVERIFY(!ai::isWriteTool("totally_unknown"));
}

// Manual mode: a write tool returns the gated JSON and does NOT touch the engine.
void TestAgent::testManualModeGatesWriteTool() {
    dsp::SpectrumEngine engine;
    const double freqBefore = engine.centerFreq();
    const QString modeBefore = engine.demodMode();
    const double bwBefore = engine.bandwidth();

    QJsonObject tune; tune["freq_hz"] = 98500000;
    QString r1 = ai::LLMWorker::dispatchToolCall("tune_frequency", tune, &engine, /*manualMode=*/true);
    QVERIFY2(r1.contains("\"gated\":true"), qPrintable(r1));
    QVERIFY2(r1.contains("\"ok\":false"), qPrintable(r1));
    QVERIFY2(r1.contains("手动模式：未执行 tune_frequency"), qPrintable(r1));

    QJsonObject mode; mode["mode"] = "AM";
    QString r2 = ai::LLMWorker::dispatchToolCall("set_mode", mode, &engine, /*manualMode=*/true);
    QVERIFY2(r2.contains("\"gated\":true"), qPrintable(r2));
    QVERIFY2(r2.contains("手动模式：未执行 set_mode"), qPrintable(r2));

    // No engine state may have changed.
    QCOMPARE(engine.centerFreq(), freqBefore);
    QCOMPARE(engine.demodMode(), modeBefore);
    QCOMPARE(engine.bandwidth(), bwBefore);
}

// Manual mode: the read-only get_status still executes against the live engine.
void TestAgent::testManualModeAllowsReadTool() {
    dsp::SpectrumEngine engine;
    // Put the engine into a known state via a real (AI-takeover) write call.
    QJsonObject m; m["mode"] = "WFM";
    ai::LLMWorker::dispatchToolCall("set_mode", m, &engine, /*manualMode=*/false);

    // In manual mode the read tool must run (not be gated).
    QString status = ai::LLMWorker::dispatchToolCall("get_status", QJsonObject{}, &engine, /*manualMode=*/true);
    QVERIFY2(!status.contains("gated"), qPrintable(status));
    QVERIFY2(status.contains("频率"), qPrintable(status));
    QVERIFY2(status.contains("WFM"), qPrintable(status));
}

// AI takeover (manualMode=false): write tool really executes and changes engine.
void TestAgent::testAiTakeoverRunsWriteTool() {
    dsp::SpectrumEngine engine;
    QJsonObject tune; tune["freq_hz"] = 98500000;
    QString r = ai::LLMWorker::dispatchToolCall("tune_frequency", tune, &engine, /*manualMode=*/false);
    QVERIFY2(!r.contains("gated"), qPrintable(r));
    QVERIFY2(r.contains("98.5"), qPrintable(r));
    QCOMPARE(engine.centerFreq(), 98500000.0);
}

// Manual mode persists through QSettings and is re-read on a fresh Agent.
void TestAgent::testManualModePersistence() {
    QSettings s("MBDSDR", "MBDSDR");
    const QVariant prev = s.value("aiManualMode");  // preserve user value
    s.remove("aiManualMode");
    s.sync();

    {
        ai::Agent a;
        QCOMPARE(a.manualMode(), false);            // default = AI takeover
        a.setManualMode(true);
        QCOMPARE(a.manualMode(), true);
        QCOMPARE(QSettings("MBDSDR", "MBDSDR").value("aiManualMode").toBool(), true);
    }
    {
        ai::Agent a;
        QCOMPARE(a.manualMode(), true);             // re-read from QSettings
        a.setManualMode(false);
    }
    {
        ai::Agent a;
        QCOMPARE(a.manualMode(), false);
    }

    if (prev.isValid()) s.setValue("aiManualMode", prev);
    else s.remove("aiManualMode");
    s.sync();
}

// Phase55 block2: a fresh engine's Costas lock read-out is the honest empty
// state (no carrier lock, no EVM) -- never a fabricated lock.
void TestAgent::testDigitalLockStatusHonestEmpty() {
    dsp::SpectrumEngine engine;
    const dsp::DigitalLockStatus lock = engine.digitalLockStatus();
    QVERIFY2(!lock.carrierLocked, "fresh engine must report carrier not locked");
    QVERIFY2(!lock.symbolLocked, "fresh engine must report symbol not locked");
    QVERIFY2(lock.evmPercent == 0.0f, "fresh engine EVM must be 0 (honest empty)");
}

// Phase55 block3: with no UI control surface registered (headless/test), the
// set_doppler_compensation tool reports an honest unavailable state instead of
// fabricating a toggle. The surface defaults to null on a fresh engine.
void TestAgent::testDopplerSurfaceNullHonestUnavailable() {
    dsp::SpectrumEngine engine;
    QVERIFY2(engine.dopplerControlSurface() == nullptr,
             "fresh engine has no Doppler control surface");
    QJsonObject args; args["enable"] = true;
    const QString r = ai::executeTool("set_doppler_compensation", args, &engine);
    const QJsonObject rj = QJsonDocument::fromJson(r.toUtf8()).object();
    QVERIFY2(rj.value("ok").toBool() == false,
             "set_doppler_compensation must refuse when no UI surface exists");
    QVERIFY2(rj.value("available").toBool() == false,
             "must report available=false without a UI surface");
}

// ===========================================================================
// Frequency-calibration agent tools -- offline SYNTHETIC end-to-end.
// *** SYNTHETIC FIXTURE / 非真实接收 (NOT REAL RECEPTION) ***
// These drive the REAL SpectrumEngine (openOfflineFile -> FileSource ->
// captureForCalibration -> calibrateFromCapture) with a known-ppm crystal-error
// IQ so we can assert the tool recovers the injected ppm. Nothing here touches
// hardware or a real antenna.
// ===========================================================================
namespace {
// Physical model of a crystal running off by `injectedPpm`: the ADC really runs
// at FsNom*(1+e), and a reference at true RF (centre + expectedBaseband)
// down-converts to fAnalog = (centre+expectedBaseband) - centre*(1+e). We write
// that IQ at the nominal length and re-open it at FsNom; the calibrator must
// recover e from the baseband offset. Returns the temp cf32 path.
QString writeCalibFixture(double fsNom, double centreHz, double expectedBasebandHz,
                          double injectedPpm, double seconds, unsigned seed) {
    const double e = injectedPpm / 1e6;
    const double fsAct = fsNom * (1.0 + e);
    const double fAnalog = (centreHz + expectedBasebandHz) - centreHz * (1.0 + e);
    const long n = static_cast<long>(fsNom * seconds);
    std::mt19937 rng(seed);
    std::normal_distribution<float> gauss(0.0f, 0.05f);
    std::vector<std::complex<float>> x(n);
    double phase = 0.0;
    for (long i = 0; i < n; ++i) {
        phase += 2.0 * M_PI * fAnalog / fsAct;
        const float re = static_cast<float>(std::cos(phase)) + 0.05f * gauss(rng);
        const float im = static_cast<float>(std::sin(phase)) + 0.05f * gauss(rng);
        x[i] = std::complex<float>(re, im);
    }
    const QString path = QDir::tempPath() + QString("/mbdsdr_cal_e2e_%1_%2.cf32")
                             .arg(QCoreApplication::applicationPid())
                             .arg(seed);
    dsp::fixture::writeRawCf32(path, x);
    return path;
}

QJsonObject runTool(dsp::SpectrumEngine& engine, const QString& name,
                    const QJsonObject& args) {
    return QJsonDocument::fromJson(
               ai::executeTool(name, args, &engine).toUtf8()).object();
}
} // namespace

// handheld: inject +32 ppm, expect recovered ≈ +32 (sub-ppm), not applied.
void TestAgent::calibrate_handheld_syntheticInjectedPpm() {
    dsp::SpectrumEngine engine;
    const double fsNom = tokens::kFixtureSrcRateHz;   // 2.048e6 (fixture rate)
    const double centre = 438.5e6;                    // test input, NOT a stored station
    const QString path = writeCalibFixture(fsNom, centre, 0.0, +32.0, 0.06, 1101);
    QVERIFY(engine.openOfflineFile(path, fsNom));

    QJsonObject args;
    args["reference_freq_hz"] = centre;
    args["reference_type"] = "handheld";
    QJsonObject o = runTool(engine, "calibrate_frequency", args);

    QVERIFY2(o.value("ok").toBool(), qPrintable(QString::fromUtf8(
        QJsonDocument(o).toJson(QJsonDocument::Compact))));
    QVERIFY2(o.value("detected").toBool(),
             qPrintable("未检测到参考载波，hint=" + o.value("hint").toString()));
    const double measured = o.value("measured_ppm").toDouble();
    QVERIFY2(std::fabs(measured - 32.0) < 0.5,
             qPrintable(QString("handheld: measured %1 ppm vs injected +32").arg(measured)));
    QVERIFY2(o.value("applied").toBool() == false, "measurement must not apply");
    QVERIFY(o.value("confidence").toDouble() > 0.5);
    QFile::remove(path);
}

// GSM FCCH: inject +32 ppm, expected tone at +kFcchToneHz above the ARFCN centre.
void TestAgent::calibrate_fcch_syntheticInjectedPpm() {
    dsp::SpectrumEngine engine;
    const double fsNom = tokens::kFixtureSrcRateHz;
    const double centre = 938.4e6;                    // generic ARFCN downlink, test input
    const QString path = writeCalibFixture(
        fsNom, centre, dsp::kFcchToneHz, +32.0, 0.06, 2202);
    QVERIFY(engine.openOfflineFile(path, fsNom));

    QJsonObject args;
    args["reference_freq_hz"] = centre;
    args["reference_type"] = "gsm_fcch";
    QJsonObject o = runTool(engine, "calibrate_frequency", args);

    QVERIFY2(o.value("detected").toBool(),
             qPrintable("FCCH: 未检测到载波, hint=" + o.value("hint").toString()));
    const double measured = o.value("measured_ppm").toDouble();
    QVERIFY2(std::fabs(measured - 32.0) < 0.5,
             qPrintable(QString("FCCH: measured %1 ppm vs injected +32").arg(measured)));
    QFile::remove(path);
}

// manual: inject -20 ppm (negative), generic exact frequency.
void TestAgent::calibrate_manual_syntheticInjectedNegativePpm() {
    dsp::SpectrumEngine engine;
    const double fsNom = tokens::kFixtureSrcRateHz;
    const double centre = 100.0e6;
    const QString path = writeCalibFixture(fsNom, centre, 0.0, -20.0, 0.06, 3303);
    QVERIFY(engine.openOfflineFile(path, fsNom));

    QJsonObject args;
    args["reference_freq_hz"] = centre;
    args["reference_type"] = "manual";
    QJsonObject o = runTool(engine, "calibrate_frequency", args);

    QVERIFY2(o.value("detected").toBool(),
             qPrintable("manual: 未检测到载波, hint=" + o.value("hint").toString()));
    const double measured = o.value("measured_ppm").toDouble();
    QVERIFY2(std::fabs(measured - (-20.0)) < 0.5,
             qPrintable(QString("manual: measured %1 ppm vs injected -20").arg(measured)));
    QFile::remove(path);
}

// Pure noise: honestly detected=false, never a fabricated ppm.
void TestAgent::calibrate_noiseOnly_honestlyNoCarrier() {
    dsp::SpectrumEngine engine;
    const double fsNom = tokens::kFixtureSrcRateHz;
    auto noise = dsp::fixture::makeNoiseIq(fsNom, 0.06, 0.05f, 4404);
    const QString path = QDir::tempPath() +
        QString("/mbdsdr_cal_e2e_noise_%1.cf32").arg(QCoreApplication::applicationPid());
    QVERIFY(dsp::fixture::writeRawCf32(path, noise));
    QVERIFY(engine.openOfflineFile(path, fsNom));

    QJsonObject args;
    args["reference_freq_hz"] = 100.0e6;
    args["reference_type"] = "manual";
    QJsonObject o = runTool(engine, "calibrate_frequency", args);

    QVERIFY2(o.value("detected").toBool() == false,
             qPrintable("pure noise must NOT report a carrier"));
    QVERIFY(!o.contains("measured_ppm"));   // no invented ppm
    QFile::remove(path);
}

// apply_frequency_correction: persists QSettings("rtl/ppm") and forwards to the
// engine. Preserves + restores the user's prior setting.
void TestAgent::applyCorrection_writesSettingAndApplies() {
    QSettings s;
    const QVariant prev = s.value("rtl/ppm", 0.0);

    dsp::SpectrumEngine engine;
    const double fsNom = tokens::kFixtureSrcRateHz;
    const double centre = 100.0e6;
    const QString path = writeCalibFixture(fsNom, centre, 0.0, +32.0, 0.06, 5505);
    QVERIFY(engine.openOfflineFile(path, fsNom));

    // Measure first (read), then apply the measured value.
    QJsonObject mArgs;
    mArgs["reference_freq_hz"] = centre;
    mArgs["reference_type"] = "manual";
    QJsonObject m = runTool(engine, "calibrate_frequency", mArgs);
    QVERIFY(m.value("detected").toBool());
    const double measured = m.value("measured_ppm").toDouble();

    QJsonObject aArgs;
    aArgs["ppm"] = measured;
    aArgs["reference_freq_hz"] = centre;
    QJsonObject a = runTool(engine, "apply_frequency_correction", aArgs);

    QVERIFY2(a.value("applied").toBool(), qPrintable(QString::fromUtf8(
        QJsonDocument(a).toJson(QJsonDocument::Compact))));
    QVERIFY2(std::fabs(a.value("applied_ppm").toDouble() - measured) < 1e-6,
             "applied_ppm must echo the measured value");
    // Persisted setting now equals the applied value.
    QCOMPARE(QSettings().value("rtl/ppm", 0.0).toDouble(), measured);
    QFile::remove(path);

    if (prev.isValid()) s.setValue("rtl/ppm", prev);
    else s.remove("rtl/ppm");
    s.sync();
}

// Manual mode MUST gate the write apply (gated:true, no setting change), while
// the read-only calibrate_frequency is allowed through.
void TestAgent::manualMode_gatesApplyCorrection() {
    QSettings s;
    const QVariant prev = s.value("rtl/ppm", 0.0);

    dsp::SpectrumEngine engine;
    QJsonObject args;
    args["ppm"] = 42.5;
    QString r = ai::LLMWorker::dispatchToolCall(
        "apply_frequency_correction", args, &engine, /*manualMode=*/true);
    QVERIFY2(r.contains("\"gated\":true"), qPrintable(r));
    QVERIFY2(r.contains("\"ok\":false"), qPrintable(r));
    QVERIFY2(r.contains(QString::fromUtf8("手动模式：未执行 apply_frequency_correction")),
             qPrintable(r));
    // The setting must be untouched.
    QCOMPARE(QSettings().value("rtl/ppm", 0.0).toDouble(), prev.toDouble());

    if (prev.isValid()) s.setValue("rtl/ppm", prev);
    else s.remove("rtl/ppm");
    s.sync();
}

void TestAgent::manualMode_allowsCalibrateRead() {
    dsp::SpectrumEngine engine;
    const double fsNom = tokens::kFixtureSrcRateHz;
    const double centre = 100.0e6;
    const QString path = writeCalibFixture(fsNom, centre, 0.0, +32.0, 0.06, 6606);
    QVERIFY(engine.openOfflineFile(path, fsNom));

    QJsonObject args;
    args["reference_freq_hz"] = centre;
    args["reference_type"] = "manual";
    // manualMode=true: the read measurement must still run (not be gated).
    QString r = ai::LLMWorker::dispatchToolCall(
        "calibrate_frequency", args, &engine, /*manualMode=*/true);
    QVERIFY2(!r.contains("gated"),
             qPrintable("calibrate_frequency is read-only and must run in manual mode: " + r));
    QFile::remove(path);
}

// VFO fine-grained edit tools really land in the engine and read back through
// list_vfos / vfoMarkers (the same snapshot all channels read).
void TestAgent::vfoEditToolsLandAndReadback() {
    dsp::SpectrumEngine engine;
    const QString add = ai::executeTool("add_vfo", QJsonObject{}, &engine);
    QVERIFY2(add.contains("\"ok\":true"), qPrintable(add));

    const auto markers = engine.vfoMarkers();
    QVERIFY(!markers.isEmpty());
    const int id0 = markers.at(0).id;   // default VFO is index 0

    QJsonObject freq; freq["index"] = 0; freq["freq_hz"] = 101100000.0;
    QString r = ai::executeTool("set_vfo_frequency", freq, &engine);
    QVERIFY2(r.contains("\"ok\":true"), qPrintable(r));
    QVERIFY2(r.contains("\"vfo_id\":" + QByteArray::number(id0)), qPrintable(r));
    // Readback through the snapshot.
    bool saw = false;
    for (const auto& m : engine.vfoMarkers())
        if (m.id == id0) { QCOMPARE(m.freqHz, 101100000.0); saw = true; }
    QVERIFY(saw);

    QJsonObject mode; mode["index"] = 0; mode["mode"] = "WFM";
    r = ai::executeTool("set_vfo_mode", mode, &engine);
    QVERIFY2(r.contains("\"ok\":true"), qPrintable(r));
    saw = false;
    for (const auto& m : engine.vfoMarkers())
        if (m.id == id0) { QCOMPARE(m.mode, QStringLiteral("WFM")); saw = true; }
    QVERIFY(saw);

    QJsonObject bw; bw["index"] = 0; bw["bandwidth_hz"] = 200000.0;
    r = ai::executeTool("set_vfo_bandwidth", bw, &engine);
    QVERIFY2(r.contains("\"ok\":true"), qPrintable(r));
    saw = false;
    for (const auto& m : engine.vfoMarkers())
        if (m.id == id0) { QCOMPARE(m.bandwidthHz, 200000.0); saw = true; }
    QVERIFY(saw);
}

// Bad args are honest errors; manual mode gates the writes and leaves the
// engine untouched.
void TestAgent::vfoEditToolsBadArgsAndGate() {
    dsp::SpectrumEngine engine;

    // Out-of-range index, missing args, unknown mode, non-positive bandwidth.
    QJsonObject oob; oob["index"] = 99; oob["freq_hz"] = 1.0e8;
    QVERIFY(!ai::executeTool("set_vfo_frequency", oob, &engine).contains("\"ok\":true"));
    QVERIFY(!ai::executeTool("set_vfo_mode", {{"index", 0}}, &engine).contains("\"ok\":true"));
    QJsonObject badm; badm["index"] = 0; badm["mode"] = "XYZ";
    QVERIFY(!ai::executeTool("set_vfo_mode", badm, &engine).contains("\"ok\":true"));
    QJsonObject badbw; badbw["index"] = 0; badbw["bandwidth_hz"] = -100.0;
    QVERIFY(!ai::executeTool("set_vfo_bandwidth", badbw, &engine).contains("\"ok\":true"));

    // Manual mode gates the writes (engine untouched).
    const double bwBefore = engine.vfoMarkers().at(0).bandwidthHz;
    QJsonObject args; args["index"] = 0; args["bandwidth_hz"] = 500000.0;
    QString g = ai::LLMWorker::dispatchToolCall(
        "set_vfo_bandwidth", args, &engine, /*manualMode=*/true);
    QVERIFY2(g.contains("\"gated\":true"), qPrintable(g));
    QVERIFY2(g.contains("\"ok\":false"), qPrintable(g));
    QCOMPARE(engine.vfoMarkers().at(0).bandwidthHz, bwBefore);
}

// Read-only source-capability + recording-state snapshot tools. HONEST empty
// state on a fresh no-hardware engine: connected=false, gains_db empty array,
// recording=false + empty path. After arming the synthetic test source and really
// starting a recording, the state reads back truthfully. Nothing fabricated.
void TestAgent::capabilitiesAndRecordingStateHonestEmptyThenRecording() {
    // --- Honest empty state: fresh engine lands on the empty NullSource. ---
    {
        dsp::SpectrumEngine eng;
        QJsonObject caps = runTool(eng, "get_capabilities", {});
        QVERIFY2(caps.value("ok").toBool(), qPrintable(QString::fromUtf8(
            QJsonDocument(caps).toJson(QJsonDocument::Compact))));
        QVERIFY(caps.value("connected").isBool());
        QVERIFY(!caps.value("connected").toBool());          // no real hardware
        QVERIFY(caps.value("gains_db").isArray());
        QCOMPARE(caps.value("gains_db").toArray().size(), 0); // honest empty gain table
        QVERIFY(caps.value("tunable_min_hz").isDouble());
        QVERIFY(caps.value("tunable_max_hz").isDouble());
        QVERIFY(caps.value("provenance").isString());
        QVERIFY(!caps.value("provenance").toString().isEmpty());

        QJsonObject rs = runTool(eng, "get_recording_state", {});
        QVERIFY(rs.value("ok").toBool());
        QVERIFY(!rs.value("recording").toBool());            // not recording
        QVERIFY(rs.value("recording_path").toString().isEmpty());
        QVERIFY(rs.value("watch_enabled").isBool());
        QVERIFY(rs.value("recording_dir").isString());
    }

    // --- Populated: arm the synthetic test source + really start a recording. ---
    {
        const QString recDir = QDir::tempPath() + "/mbdsdr_agent_cap_rec";
        QDir().mkpath(recDir);
        dsp::SpectrumEngine eng;
        eng.setRecordingDir(recDir);
        eng.setTestSourceEnabled(true);   // synthetic IQ, explicitly opted in

        // On the test source capabilities stay honestly empty but are labelled.
        QJsonObject caps = runTool(eng, "get_capabilities", {});
        QVERIFY(caps.value("ok").toBool());
        QVERIFY(!caps.value("connected").toBool());          // still no real hardware
        QVERIFY(caps.value("gains_db").toArray().isEmpty());
        QVERIFY2(caps.value("provenance").toString().contains(QString::fromUtf8("测试信号")),
                 qPrintable("test source must be labelled, got: " +
                            caps.value("provenance").toString()));

        // Not recording yet.
        QJsonObject rs0 = runTool(eng, "get_recording_state", {});
        QVERIFY(!rs0.value("recording").toBool());
        QVERIFY(rs0.value("recording_path").toString().isEmpty());
        QCOMPARE(rs0.value("recording_dir").toString(), recDir);

        // Really start a recording (the test source feeds the recorder).
        QJsonObject start = runTool(eng, "start_recording", {});
        QVERIFY2(start.value("ok").toBool(), qPrintable(QString::fromUtf8(
            QJsonDocument(start).toJson(QJsonDocument::Compact))));
        QJsonObject rs = runTool(eng, "get_recording_state", {});
        QVERIFY2(rs.value("recording").toBool(), qPrintable(QString::fromUtf8(
            QJsonDocument(rs).toJson(QJsonDocument::Compact))));
        QVERIFY2(!rs.value("recording_path").toString().isEmpty(),
                 "a live recording must report its real path");
        QVERIFY(QFileInfo::exists(rs.value("recording_path").toString()));
        // Tidy up. (The recorder finalises on the engine thread, so we do NOT
        // re-assert recording flips back to false here -- that transition is async
        // and the honest-empty state is already covered by the first block.)
        runTool(eng, "stop_recording", {});
    }
}

// Phase62 three-channel audit: 10-tool manual-gate spot-check. The gate itself
// is uniform (LLMWorker::dispatchToolCall: manualMode && isWriteTool(name) ->
// gated, BEFORE any engine touch; isWriteTool reads the spec table). This slot
// pins the requested sample: the 5 write tools must come back
// {"gated":true,"ok":false} with engine/QSettings untouched, and the 5 read
// tools must actually execute (no "gated") even in manual mode.
void TestAgent::manualMode_gateSpotCheckTenTools() {
    dsp::SpectrumEngine engine;

    // --- Snapshot every back-end the gated writes would otherwise touch ----
    const double vfoFreqBefore = engine.vfoMarkers().at(0).freqHz;
    const bool sqEnBefore = engine.squelchEnabled();
    const float sqThBefore = engine.squelchThresholdDb();
    const QString recPathBefore = engine.recordingPath();
    const double ppmBefore = QSettings().value("rtl/ppm", 0.0).toDouble();

    auto expectGated = [&](const QString& name, const QJsonObject& args) {
        const QString r = ai::LLMWorker::dispatchToolCall(name, args, &engine,
                                                         /*manualMode=*/true);
        QVERIFY2(r.contains("\"gated\":true"),
                 qPrintable(name + " must be gated: " + r));
        QVERIFY2(r.contains("\"ok\":false"),
                 qPrintable(name + " must be ok:false: " + r));
    };

    // 5 write tools (task spot-check list), all must be intercepted:
    QJsonObject vfo; vfo["index"] = 0; vfo["freq_hz"] = 145000000.0;
    expectGated("set_vfo_frequency", vfo);
    expectGated("start_recording", QJsonObject{});
    QJsonObject ppm; ppm["ppm"] = 42.5;
    expectGated("apply_frequency_correction", ppm);
    QJsonObject sq; sq["enabled"] = true; sq["threshold_db"] = -10.0;
    expectGated("set_squelch", sq);
    QJsonObject del; del["name"] = QString::fromUtf8("phase62_audit_nonexistent.sigmf-data");
    expectGated("delete_recording", del);

    // ... and none of them may have reached engine or settings.
    QCOMPARE(engine.vfoMarkers().at(0).freqHz, vfoFreqBefore);
    QCOMPARE(engine.squelchEnabled(), sqEnBefore);
    QCOMPARE(engine.squelchThresholdDb(), sqThBefore);
    QCOMPARE(engine.recordingPath(), recPathBefore);
    QCOMPARE(QSettings().value("rtl/ppm", 0.0).toDouble(), ppmBefore);

    // 5 read tools: manual mode must NOT gate them; they run and report ok.
    auto expectRuns = [&](const QString& name) {
        const QString r = ai::LLMWorker::dispatchToolCall(name, QJsonObject{}, &engine,
                                                         /*manualMode=*/true);
        QVERIFY2(!r.contains("\"gated\""),
                 qPrintable(name + " must not be gated: " + r));
        QVERIFY2(r.contains("\"ok\":true"),
                 qPrintable(name + " must still execute: " + r));
    };
    expectRuns("get_capabilities");
    expectRuns("get_recording_state");
    expectRuns("get_status");
    expectRuns("get_vor_radial");
    expectRuns("list_vfos");
}

// Phase62 gate FULL-COVERAGE: iterate the DECLARATIVE spec table itself
// (registeredToolSpecs) -- no hardcoded name list -- and prove that the
// manual-mode write gate intercepts EVERY write tool and that EVERY read tool
// still runs. The gate is uniform: LLMWorker::dispatchToolCall returns
// gatedToolResult(name) on `manualMode && isWriteTool(name)` BEFORE any engine
// touch and BEFORE argument validation, so valid args are supplied per write tool
// (the only possible ok:false is the gate itself, never a param error). This is
// the systematic version of the 10-tool spot check above; it also pins the
// frozen 28-write / 17-read split and proves zero engine/QSettings drift.
void TestAgent::manualMode_gateSpotCheckAllWrites() {
    dsp::SpectrumEngine engine;

    // --- Snapshot every observable back-end a gated write could otherwise touch --
    const double freqBefore = engine.centerFreq();
    const QString modeBefore = engine.demodMode();
    const double bwBefore = engine.bandwidth();
    const int vfoCountBefore = engine.vfoMarkers().size();
    const int selVfoBefore = engine.selectedVfoId();
    const bool sqEnBefore = engine.squelchEnabled();
    const float sqThBefore = engine.squelchThresholdDb();
    const QString recPathBefore = engine.recordingPath();
    const QString recDirBefore = engine.recordingDir();
    const bool watchBefore = engine.watchEnabled();
    const int fftBefore = engine.fftSize();
    const int winBefore = engine.windowType();
    const int avgBefore = engine.averageMode();
    const double ppmBefore = QSettings().value("rtl/ppm", 0.0).toDouble();
    const QVariant cmapBefore = QSettings().value("view/wfColormapFile");

    // Honest, schema-valid args per write tool. The gate fires before validation,
    // so these also demonstrate "gated" precedes arg checking. Write tools with
    // no params (start/stop_recording, stop_scan_link, add_vfo) stay empty.
    auto argsFor = [](const QString& name) -> QJsonObject {
        QJsonObject a;
        if (name == "tune_frequency") a["freq_hz"] = 98500000.0;
        else if (name == "set_mode") a["mode"] = "AM";
        else if (name == "scan_band") { a["low_hz"] = 88e6; a["high_hz"] = 108e6; a["step_hz"] = 200000; }
        else if (name == "set_bandwidth") a["bandwidth_hz"] = 8000.0;
        else if (name == "apply_frequency_correction") a["ppm"] = 32.0;
        else if (name == "export_iq_segment") a["sample_count"] = 4096.0;
        else if (name == "set_network_audio_sink") { a["enable"] = true; a["port"] = 12345; a["format"] = "s16le"; }
        else if (name == "start_scan_link") a["target_freq_hz"] = 100e6;
        else if (name == "set_squelch") { a["enabled"] = true; a["threshold_db"] = -10.0; }
        else if (name == "add_bookmark") { a["freq_hz"] = 100e6; a["name"] = "gate_probe"; a["mode"] = "NFM"; }
        else if (name == "tune_to_bookmark") a["index"] = 0;
        else if (name == "delete_bookmark") a["index"] = 0;
        else if (name == "switch_vfo") a["index"] = 0;
        else if (name == "rename_vfo") { a["index"] = 0; a["name"] = "gate_probe"; }
        else if (name == "set_vfo_armed") { a["index"] = 0; a["enabled"] = true; }
        else if (name == "set_vfo_frequency") { a["index"] = 0; a["freq_hz"] = 145e6; }
        else if (name == "set_vfo_mode") { a["index"] = 0; a["mode"] = "NFM"; }
        else if (name == "set_vfo_bandwidth") { a["index"] = 0; a["bandwidth_hz"] = 12500.0; }
        else if (name == "delete_recording") a["name"] = "phase62_gate_probe.sigmf-data";
        else if (name == "export_recording") { a["name"] = "phase62_gate_probe.sigmf-data"; a["out_path"] = "/tmp/mbdsdr_phase62_gate_probe.sigmf-data"; }
        else if (name == "set_fft_params") a["fft_size"] = 2048.0;
        else if (name == "set_color_map") a["file_path"] = "/tmp/mbdsdr_phase62_gate_probe.cmap";
        else if (name == "set_doppler_compensation") a["enable"] = true;
        else if (name == "connect_network_source") { a["host"] = "127.0.0.1"; a["port"] = 1234; }
        return a;
    };

    int writes = 0, reads = 0;
    for (const ai::ToolSchemaSpec& s : ai::registeredToolSpecs()) {
        const QString r = ai::LLMWorker::dispatchToolCall(
            s.name, argsFor(s.name), &engine, /*manualMode=*/true);
        if (s.write) {
            ++writes;
            QVERIFY2(r.contains("\"gated\":true"),
                     qPrintable(s.name + " (write) must be gated: " + r));
            QVERIFY2(r.contains("\"ok\":false"),
                     qPrintable(s.name + " (write) must be ok:false: " + r));
            QVERIFY2(r.contains(QString::fromUtf8("手动模式：未执行 %1").arg(s.name)),
                     qPrintable(s.name + " (write) must name the gated tool: " + r));
        } else {
            ++reads;
            // Reads must never be blocked by the write gate.
            QVERIFY2(!r.contains("\"gated\""),
                     qPrintable(s.name + " (read) must NOT be gated: " + r));
            // Every read deterministically returns ok:true on a fresh engine
            // EXCEPT predict_passes, which honestly returns ok:false with no
            // fresh TLE cache (honest empty state) -- that is NOT a gate block,
            // which we already asserted above.
            if (s.name != "predict_passes") {
                QVERIFY2(r.contains("\"ok\":true"),
                         qPrintable(s.name + " (read) must execute ok:true: " + r));
            }
        }
    }

    // The frozen split must be exactly 28 writes / 17 reads.
    QCOMPARE(writes, 28);
    QCOMPARE(reads, 17);

    // Every observable back-end must be byte-for-byte unchanged: the gated writes
    // never reached executeTool(), so no frequency/mode/bandwidth/VFO/squelch/
    // recording/FFT/settings field may have drifted.
    QCOMPARE(engine.centerFreq(), freqBefore);
    QCOMPARE(engine.demodMode(), modeBefore);
    QCOMPARE(engine.bandwidth(), bwBefore);
    QCOMPARE(engine.vfoMarkers().size(), vfoCountBefore);
    QCOMPARE(engine.selectedVfoId(), selVfoBefore);
    QCOMPARE(engine.squelchEnabled(), sqEnBefore);
    QCOMPARE(engine.squelchThresholdDb(), sqThBefore);
    QCOMPARE(engine.recordingPath(), recPathBefore);
    QCOMPARE(engine.recordingDir(), recDirBefore);
    QCOMPARE(engine.watchEnabled(), watchBefore);
    QCOMPARE(engine.fftSize(), fftBefore);
    QCOMPARE(engine.windowType(), winBefore);
    QCOMPARE(engine.averageMode(), avgBefore);
    QCOMPARE(QSettings().value("rtl/ppm", 0.0).toDouble(), ppmBefore);
    QCOMPARE(QSettings().value("view/wfColormapFile"), cmapBefore);
}

#include <QCoreApplication>
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    TestAgent t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_agent.moc"
