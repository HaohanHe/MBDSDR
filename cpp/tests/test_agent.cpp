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
#include "ui/bookmark_manager.h"
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
    // --- Phase62 gate full-coverage: ALL 29 writes gated / ALL 18 reads open ---
    void manualMode_gateSpotCheckAllWrites();
    // --- Noise blanker three-channel tool: land / readback / honest empty / gate ---
    void noiseBlankerLandReadbackAndGate();
    // --- Phase63 D1: get_squelch_status returns REAL engine getters, not nulls ---
    void squelchStatusRealReadback();
    // --- Phase63 CTCSS three-channel tool: land / readback / out-of-range reject / gate ---
    void ctcssLandReadbackOutOfRangeRejectAndGate();
    // --- Phase63 CDCSS/DCS three-channel tool: land / readback / illegal-code reject / gate ---
    void cdcssLandReadbackIllegalCodeRejectAndGate();
    void ft8LandReadbackHonestEmpty();
    void lrptLandReadbackHonestEmpty();
    // --- Phase63 readback-loop audit: D-2/D-3/D-5 honest-repair round-trip -----
    void readbackLoopHonestRejectsAndAutoLand();
    // --- Phase63: bookmark tools execute for real against injected store -------
    void bookmarkToolsRealExecutionWithInjectedStore();
    // --- Phase63 no-engine audit: null SpectrumEngine must return honest JSON
    //     error envelope (aligned with ControlHub::execute :353), never a plain
    //     string, never {ok:true} with fabricated values, never a null deref. ---
    void nullEngineReturnsHonestErrorEnvelope();
    // --- Phase63 D1-D5 bilateral alias contract: pin the new dual-key / dual-type
    //     acceptance so future refactors cannot silently regress the drift fix. ---
    void phase63BilateralAliasContract();
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
    QCOMPARE(tools.size(), 55);
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
    const bool nbBefore = engine.noiseBlankerEnabled();
    const bool ctcssEnBefore = engine.ctcssEnabled();
    const double ctcssFreqBefore = engine.ctcssFreqHz();
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
        else if (name == "set_ctcss") { a["enabled"] = true; a["frequency_hz"] = 88.5; }
        else if (name == "set_cdcss") { a["enabled"] = true; a["code"] = "023"; }
        else if (name == "set_ft8") a["enabled"] = true;
        else if (name == "set_noise_blanker") a["on"] = true;
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

    // The frozen split must be exactly 33 writes / 22 reads.
    QCOMPARE(writes, 33);
    QCOMPARE(reads, 22);

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
    QCOMPARE(engine.noiseBlankerEnabled(), nbBefore);
    QCOMPARE(engine.ctcssEnabled(), ctcssEnBefore);
    QCOMPARE(engine.ctcssFreqHz(), ctcssFreqBefore);
    QCOMPARE(engine.recordingPath(), recPathBefore);
    QCOMPARE(engine.recordingDir(), recDirBefore);
    QCOMPARE(engine.watchEnabled(), watchBefore);
    QCOMPARE(engine.fftSize(), fftBefore);
    QCOMPARE(engine.windowType(), winBefore);
    QCOMPARE(engine.averageMode(), avgBefore);
    QCOMPARE(QSettings().value("rtl/ppm", 0.0).toDouble(), ppmBefore);
    QCOMPARE(QSettings().value("view/wfColormapFile"), cmapBefore);
}

// Noise blanker three-channel tool: honest off on a fresh engine, the write
// really flips the engine switch, the read returns the real state back, the
// manual-mode gate intercepts the write without touching the engine, and
// missing / non-boolean `on` is an honest error rather than a default.
void TestAgent::noiseBlankerLandReadbackAndGate() {
    dsp::SpectrumEngine engine;

    // Empty state: a fresh engine reports the real (off) switch, no fabrication.
    QJsonObject off = QJsonDocument::fromJson(
        ai::executeTool("get_noise_blanker_status", QJsonObject{}, &engine)
            .toUtf8()).object();
    QVERIFY2(off.value("ok").toBool(), qPrintable(ai::executeTool(
        "get_noise_blanker_status", QJsonObject{}, &engine)));
    QCOMPARE(off.value("enabled").toBool(), false);

    // AI takeover (manualMode=false): the write really lands on the engine.
    QJsonObject on; on["on"] = true;
    QString r = ai::LLMWorker::dispatchToolCall("set_noise_blanker", on, &engine,
                                               /*manualMode=*/false);
    QVERIFY2(!r.contains(QString::fromUtf8("\"gated\":true")), qPrintable(r));
    QVERIFY2(engine.noiseBlankerEnabled(), "set_noise_blanker(true) must flip the engine");

    // Real read-back through the tool now reports enabled=true.
    QJsonObject st = QJsonDocument::fromJson(
        ai::executeTool("get_noise_blanker_status", QJsonObject{}, &engine)
            .toUtf8()).object();
    QCOMPARE(st.value("enabled").toBool(), true);

    // Back off.
    QJsonObject off2; off2["on"] = false;
    ai::LLMWorker::dispatchToolCall("set_noise_blanker", off2, &engine,
                                    /*manualMode=*/false);
    QVERIFY2(!engine.noiseBlankerEnabled(), "set_noise_blanker(false) must clear the engine");

    // Manual mode: the write is intercepted, engine untouched.
    QString g = ai::LLMWorker::dispatchToolCall("set_noise_blanker", on, &engine,
                                                /*manualMode=*/true);
    QVERIFY2(g.contains("\"gated\":true"), qPrintable(g));
    QVERIFY2(g.contains("\"ok\":false"), qPrintable(g));
    QVERIFY2(!engine.noiseBlankerEnabled(),
             "manual-mode gate must not flip the noise-blanker engine");

    // Missing / non-boolean `on` are honest errors, never a silent default.
    QJsonObject miss = QJsonDocument::fromJson(
        ai::executeTool("set_noise_blanker", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY2(!miss.value("ok").toBool(), "missing `on` must be an error");
    QJsonObject bad; bad["on"] = "yes";
    QJsonObject badO = QJsonDocument::fromJson(
        ai::executeTool("set_noise_blanker", bad, &engine).toUtf8()).object();
    QVERIFY2(!badO.value("ok").toBool(), "non-boolean `on` must be an error");
}

// Phase63 D1: get_squelch_status must read the REAL engine getters
// (squelchEnabled/ThresholdDb/Auto/Open), matching ControlHub's wire shape --
// NOT the old hardcoded nulls. Fresh/no-device engine drives honest defaults.
void TestAgent::squelchStatusRealReadback() {
    dsp::SpectrumEngine engine;

    QJsonObject r = QJsonDocument::fromJson(
        ai::executeTool("get_squelch_status", QJsonObject{}, &engine)
            .toUtf8()).object();
    QVERIFY2(r.value("ok").toBool(), qPrintable(
        ai::executeTool("get_squelch_status", QJsonObject{}, &engine)));
    // No longer null: every field is driven by the engine getter.
    QVERIFY2(!r.value("enabled").isNull(), "enabled must be a real bool, not null");
    QVERIFY2(!r.value("threshold_db").isNull(), "threshold_db must be a real number, not null");
    QVERIFY2(!r.value("auto").isNull(), "auto must be a real bool, not null");
    QVERIFY2(!r.value("open").isNull(), "open must be a real bool, not null");
    // Values match the engine getters exactly (and the no-device defaults).
    QCOMPARE(r.value("enabled").toBool(), engine.squelchEnabled());
    QCOMPARE(r.value("threshold_db").toDouble(),
             static_cast<double>(engine.squelchThresholdDb()));
    QCOMPARE(r.value("auto").toBool(), engine.squelchAuto());
    QCOMPARE(r.value("open").toBool(), engine.squelchOpen());
    // The stale "no readback interface" note must be gone.
    QVERIFY2(!r.contains(QString::fromUtf8("note")),
             "stale 'engine has no squelch readback' note must be removed");

    // Flip the engine through the write tool; the read tool follows for real.
    QJsonObject on; on["enabled"] = true; on["threshold_db"] = -10.0;
    QString w = ai::LLMWorker::dispatchToolCall("set_squelch", on, &engine,
                                                /*manualMode=*/false);
    QVERIFY2(!w.contains("\"gated\":true"), qPrintable(w));
    QJsonObject r2 = QJsonDocument::fromJson(
        ai::executeTool("get_squelch_status", QJsonObject{}, &engine)
            .toUtf8()).object();
    QCOMPARE(r2.value("enabled").toBool(), engine.squelchEnabled());
    QCOMPARE(r2.value("enabled").toBool(), true);
    QCOMPARE(r2.value("threshold_db").toDouble(),
             static_cast<double>(engine.squelchThresholdDb()));
    QCOMPARE(r2.value("threshold_db").toDouble(), -10.0);
}

// Phase63 CTCSS three-channel tool: fresh engine reads honest defaults
// (disabled, 88.5 Hz, active=false -- never a fabricated tone); the write lands
// on the real engine setters and the read follows; an out-of-domain frequency is
// REJECTED with ok:false rather than silently clamped; missing/non-bool `enabled`
// is an honest error; and the manual-mode gate intercepts the write untouched.
void TestAgent::ctcssLandReadbackOutOfRangeRejectAndGate() {
    dsp::SpectrumEngine engine;

    // Fresh engine: honest defaults, active=false (no tone / disabled).
    QJsonObject off = QJsonDocument::fromJson(
        ai::executeTool("get_ctcss_status", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY2(off.value("ok").toBool(), qPrintable(
        ai::executeTool("get_ctcss_status", QJsonObject{}, &engine)));
    QCOMPARE(off.value("enabled").toBool(), false);
    QCOMPARE(off.value("frequency_hz").toDouble(), engine.ctcssFreqHz());
    QCOMPARE(off.value("frequency_hz").toDouble(), 88.5);   // default PL
    QCOMPARE(off.value("active").toBool(), false);           // no signal -> honest false
    QCOMPARE(off.value("gate_audio").toBool(), false);      // speaker gate defaults off

    // AI takeover (manualMode=false): enabled only (omit frequency) lands, keeps
    // the current/default tuning.
    QJsonObject on; on["enabled"] = true;
    QString r = ai::LLMWorker::dispatchToolCall("set_ctcss", on, &engine,
                                                /*manualMode=*/false);
    QVERIFY2(!r.contains("\"gated\":true"), qPrintable(r));
    QVERIFY2(engine.ctcssEnabled(), "set_ctcss{enabled:true} must flip the engine");
    QJsonObject st = QJsonDocument::fromJson(
        ai::executeTool("get_ctcss_status", QJsonObject{}, &engine).toUtf8()).object();
    QCOMPARE(st.value("enabled").toBool(), true);
    QCOMPARE(st.value("frequency_hz").toDouble(), 88.5);     // unchanged default

    // Set an explicit legal tone; it lands and reads back.
    QJsonObject fq; fq["enabled"] = true; fq["frequency_hz"] = 100.0;
    QString r2 = ai::LLMWorker::dispatchToolCall("set_ctcss", fq, &engine,
                                                 /*manualMode=*/false);
    QVERIFY2(!r2.contains("\"gated\":true"), qPrintable(r2));
    QCOMPARE(engine.ctcssFreqHz(), 100.0);
    QJsonObject st2 = QJsonDocument::fromJson(
        ai::executeTool("get_ctcss_status", QJsonObject{}, &engine).toUtf8()).object();
    QCOMPARE(st2.value("frequency_hz").toDouble(), 100.0);

    // Optional speaker gate: arm it explicitly, it lands on the real engine
    // setter and reads back; a later set that OMITS gate_audio leaves it armed.
    QJsonObject gateOn; gateOn["enabled"] = true; gateOn["gate_audio"] = true;
    QString rg = ai::LLMWorker::dispatchToolCall("set_ctcss", gateOn, &engine,
                                                /*manualMode=*/false);
    QVERIFY2(!rg.contains("\"gated\":true"), qPrintable(rg));
    QVERIFY2(engine.ctcssGateAudio(), "set_ctcss{gate_audio:true} must arm the speaker gate");
    QJsonObject stg = QJsonDocument::fromJson(
        ai::executeTool("get_ctcss_status", QJsonObject{}, &engine).toUtf8()).object();
    QCOMPARE(stg.value("gate_audio").toBool(), true);
    // Omit gate_audio -> current gate kept (still armed).
    QJsonObject noGate; noGate["enabled"] = true; noGate["frequency_hz"] = 120.0;
    ai::LLMWorker::dispatchToolCall("set_ctcss", noGate, &engine,
                                   /*manualMode=*/false);
    QVERIFY2(engine.ctcssGateAudio(), "omitting gate_audio must keep the current gate state");
    // Non-bool gate_audio -> honest error, never a silent toggle.
    QJsonObject badGate; badGate["enabled"] = true; badGate["gate_audio"] = "yes";
    QJsonObject badGateO = QJsonDocument::fromJson(
        ai::executeTool("set_ctcss", badGate, &engine).toUtf8()).object();
    QVERIFY2(!badGateO.value("ok").toBool(), "non-boolean `gate_audio` must be an error");

    // Out-of-domain frequency is REJECTED (the engine clamps; the tool layer is
    // the honest gate that refuses instead of fake-success at a clamped tone).
    const double freqBefore = engine.ctcssFreqHz();
    QJsonObject low; low["enabled"] = true; low["frequency_hz"] = 30.0;
    QJsonObject rLow = QJsonDocument::fromJson(
        ai::executeTool("set_ctcss", low, &engine).toUtf8()).object();
    QVERIFY2(!rLow.value("ok").toBool(), qPrintable(rLow.value("error").toString()));
    QCOMPARE(engine.ctcssFreqHz(), freqBefore);   // untouched
    QJsonObject high; high["enabled"] = true; high["frequency_hz"] = 300.0;
    QJsonObject rHigh = QJsonDocument::fromJson(
        ai::executeTool("set_ctcss", high, &engine).toUtf8()).object();
    QVERIFY2(!rHigh.value("ok").toBool(), qPrintable(rHigh.value("error").toString()));
    QCOMPARE(engine.ctcssFreqHz(), freqBefore);

    // Missing / non-bool `enabled` -> honest error, never a silent toggle.
    QJsonObject miss = QJsonDocument::fromJson(
        ai::executeTool("set_ctcss", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY2(!miss.value("ok").toBool(), "missing `enabled` must be an error");
    QJsonObject bad; bad["enabled"] = "yes";
    QJsonObject badO = QJsonDocument::fromJson(
        ai::executeTool("set_ctcss", bad, &engine).toUtf8()).object();
    QVERIFY2(!badO.value("ok").toBool(), "non-boolean `enabled` must be an error");

    // Manual mode: the write is intercepted, engine untouched.
    QString g = ai::LLMWorker::dispatchToolCall("set_ctcss", on, &engine,
                                                /*manualMode=*/true);
    QVERIFY2(g.contains("\"gated\":true"), qPrintable(g));
    QVERIFY2(g.contains("\"ok\":false"), qPrintable(g));
    QVERIFY2(engine.ctcssEnabled(),
             "manual-mode gate must not flip the CTCSS engine");
}

// CDCSS/DCS three-channel tool: set_cdcss lands on the engine; get_cdcss_status
// readback is honest; an illegal (non-table) DCS code string is REJECTED with
// ok:false; missing/non-bool `enabled` is an error; manual-mode gates the write.
void TestAgent::cdcssLandReadbackIllegalCodeRejectAndGate() {
    dsp::SpectrumEngine engine;

    QJsonObject off = QJsonDocument::fromJson(
        ai::executeTool("get_cdcss_status", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY2(off.value("ok").toBool(), qPrintable(
        ai::executeTool("get_cdcss_status", QJsonObject{}, &engine)));
    QCOMPARE(off.value("enabled").toBool(), false);
    QCOMPARE(off.value("active").toBool(), false);
    QCOMPARE(off.value("gate_audio").toBool(), false);

    QJsonObject on; on["enabled"] = true; on["code"] = "023";
    QString r = ai::LLMWorker::dispatchToolCall("set_cdcss", on, &engine,
                                                /*manualMode=*/false);
    QVERIFY2(!r.contains("\"gated\":true"), qPrintable(r));
    QVERIFY2(engine.cdcssEnabled(), "set_cdcss{enabled:true} must flip the engine");
    QCOMPARE(engine.cdcssCode(), 023);
    QJsonObject st = QJsonDocument::fromJson(
        ai::executeTool("get_cdcss_status", QJsonObject{}, &engine).toUtf8()).object();
    QCOMPARE(st.value("enabled").toBool(), true);
    QCOMPARE(st.value("code").toString(), QStringLiteral("023"));

    // Illegal DCS code (not in the 104-code table) -> rejected, engine untouched.
    QJsonObject bad; bad["enabled"] = true; bad["code"] = "777";
    QJsonObject rBad = QJsonDocument::fromJson(
        ai::executeTool("set_cdcss", bad, &engine).toUtf8()).object();
    QVERIFY2(!rBad.value("ok").toBool(), qPrintable(rBad.value("error").toString()));
    QCOMPARE(engine.cdcssCode(), 023);   // untouched

    // Missing / non-bool `enabled` -> honest error.
    QJsonObject miss = QJsonDocument::fromJson(
        ai::executeTool("set_cdcss", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY2(!miss.value("ok").toBool(), "missing `enabled` must be an error");

    // Manual mode: the write is intercepted, engine untouched.
    QString g = ai::LLMWorker::dispatchToolCall("set_cdcss", on, &engine,
                                                /*manualMode=*/true);
    QVERIFY2(g.contains("\"gated\":true"), qPrintable(g));
    QVERIFY2(g.contains("\"ok\":false"), qPrintable(g));
}

void TestAgent::ft8LandReadbackHonestEmpty() {
    dsp::SpectrumEngine engine;

    QJsonObject off = QJsonDocument::fromJson(
        ai::executeTool("get_ft8_status", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY2(off.value("ok").toBool(), qPrintable(
        ai::executeTool("get_ft8_status", QJsonObject{}, &engine)));
    QCOMPARE(off.value("enabled").toBool(), false);
    QCOMPARE(off.value("active").toBool(), false);   // 诚实空态

    QJsonObject on; on["enabled"] = true;
    QString r = ai::LLMWorker::dispatchToolCall("set_ft8", on, &engine,
                                               /*manualMode=*/false);
    QVERIFY2(!r.contains("\"gated\":true"), qPrintable(r));
    QVERIFY2(engine.ft8Enabled(), "set_ft8{enabled:true} must flip the engine");

    QJsonObject st = QJsonDocument::fromJson(
        ai::executeTool("get_ft8_status", QJsonObject{}, &engine).toUtf8()).object();
    QCOMPARE(st.value("enabled").toBool(), true);
    // 无信号 -> active 仍诚实为 false
    QCOMPARE(st.value("active").toBool(), false);

    // Missing / non-bool `enabled` -> honest error.
    QJsonObject miss = QJsonDocument::fromJson(
        ai::executeTool("set_ft8", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY2(!miss.value("ok").toBool(), "missing `enabled` must be an error");

    // Manual mode: the write is intercepted, engine untouched.
    QString g = ai::LLMWorker::dispatchToolCall("set_ft8", on, &engine,
                                               /*manualMode=*/true);
    QVERIFY2(g.contains("\"gated\":true"), qPrintable(g));
}

// P2 LRPT step-3: set_lrpt arms the engine flag; get_lrpt_status is honest empty
// (no C++ decoder yet -> sync_locked=false, decoded_frames=0). Missing enabled is
// an error; manual mode gates the write.
void TestAgent::lrptLandReadbackHonestEmpty() {
    dsp::SpectrumEngine engine;

    QJsonObject off = QJsonDocument::fromJson(
        ai::executeTool("get_lrpt_status", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY2(off.value("ok").toBool(), qPrintable(
        ai::executeTool("get_lrpt_status", QJsonObject{}, &engine)));
    QCOMPARE(off.value("enabled").toBool(), false);
    QCOMPARE(off.value("sync_locked").toBool(), false);   // 诚实空态
    QCOMPARE(off.value("decoded_frames").toInt(), 0);

    QJsonObject on; on["enabled"] = true;
    QString r = ai::LLMWorker::dispatchToolCall("set_lrpt", on, &engine,
                                                /*manualMode=*/false);
    QVERIFY2(!r.contains("\"gated\":true"), qPrintable(r));
    QVERIFY2(engine.lrptEnabled(), "set_lrpt{enabled:true} must flip the engine");

    QJsonObject st = QJsonDocument::fromJson(
        ai::executeTool("get_lrpt_status", QJsonObject{}, &engine).toUtf8()).object();
    QCOMPARE(st.value("enabled").toBool(), true);
    QCOMPARE(st.value("sync_locked").toBool(), false);   // decoder not ported yet
    QCOMPARE(st.value("decoded_frames").toInt(), 0);

    // Missing / non-bool `enabled` -> honest error.
    QJsonObject miss = QJsonDocument::fromJson(
        ai::executeTool("set_lrpt", QJsonObject{}, &engine).toUtf8()).object();
    QVERIFY2(!miss.value("ok").toBool(), "missing `enabled` must be an error");

    // Manual mode: the write is intercepted, engine untouched.
    QString g = ai::LLMWorker::dispatchToolCall("set_lrpt", on, &engine,
                                               /*manualMode=*/true);
    QVERIFY2(g.contains("\"gated\":true"), qPrintable(g));
    QVERIFY2(engine.lrptEnabled(), "gated write must not flip the engine");
}

//   D-2: tune_frequency rejects non-positive / non-finite freq_hz with ok:false
//        (previously the engine dropped it silently while the ack echoed it as
//        success and get_status kept the old value -- fake success).
//   D-3: set_mode rejects whitelist-unknown modes with ok:false (the engine's
//        VfoManager accepts ANY string, so the ack was the only real gate).
//   D-5: set_squelch{auto:true} really lands on engine->setSquelchAuto, so
//        get_squelch_status.auto reads back true (previously echoed, never set).
void TestAgent::readbackLoopHonestRejectsAndAutoLand() {
    dsp::SpectrumEngine engine;
    auto run = [&](const QString& tool, const QJsonObject& args) {
        return QJsonDocument::fromJson(
            ai::executeTool(tool, args, &engine).toUtf8()).object();
    };
    const auto compactJson = [](const QJsonObject& o) -> QString {
        return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
    };

    // --- D-2: illegal frequency is an honest error, engine untouched ----------
    const double oldFreq = engine.centerFreq();
    QJsonObject badNeg; badNeg["freq_hz"] = -1e6;
    QJsonObject rNeg = run("tune_frequency", badNeg);
    QVERIFY2(!rNeg.value("ok").toBool(), qPrintable(compactJson(rNeg)));
    QVERIFY2(rNeg.value("error").toString().contains(QString::fromUtf8("频率")),
             qPrintable(rNeg.value("error").toString()));
    QCOMPARE(engine.centerFreq(), oldFreq);

    QJsonObject badNaN; badNaN["freq_hz"] = qQNaN();
    QJsonObject rNaN = run("tune_frequency", badNaN);
    QVERIFY2(!rNaN.value("ok").toBool(), qPrintable(compactJson(rNaN)));
    QCOMPARE(engine.centerFreq(), oldFreq);

    // Regression guard: a valid positive frequency is acked honestly (on the
    // headless NullSource setCenterFreq is a no-op and the settled read-back
    // stays at its constant 98.5 MHz -- pre-existing source semantics, not this
    // gate's concern; we pin the ack contract, not the phantom landing value).
    QJsonObject okF; okF["freq_hz"] = 100e6;
    QJsonObject rOk = run("tune_frequency", okF);
    QVERIFY2(rOk.value("ok").toBool(), qPrintable(compactJson(rOk)));
    QCOMPARE(rOk.value("frequency_hz").toDouble(), 100e6);

    // --- D-3: unknown mode is an honest error, engine demodMode untouched ------
    const QString oldMode = engine.demodMode();
    QJsonObject badMode; badMode["mode"] = "XYZ";
    QJsonObject rMode = run("set_mode", badMode);
    QVERIFY2(!rMode.value("ok").toBool(), qPrintable(compactJson(rMode)));
    QCOMPARE(engine.demodMode(), oldMode);

    // A whitelisted mode still lands, echoed canonical upper-case.
    QJsonObject goodMode; goodMode["mode"] = "nfm";
    QJsonObject rGood = run("set_mode", goodMode);
    QVERIFY2(rGood.value("ok").toBool(), qPrintable(compactJson(rGood)));
    QCOMPARE(rGood.value("mode").toString(), QStringLiteral("NFM"));
    QCOMPARE(engine.demodMode(), QStringLiteral("NFM"));

    // --- RAW direct-listen: whitelisted mode lands (read back "RAW"), and
    //     set_bandwidth on RAW is honestly refused (a passthrough has no demod
    //     filter to retune -- the tool must not echo a fake success). ----------
    QJsonObject rawMode; rawMode["mode"] = "RAW";
    QJsonObject rRaw = run("set_mode", rawMode);
    QVERIFY2(rRaw.value("ok").toBool(), qPrintable(compactJson(rRaw)));
    QCOMPARE(engine.demodMode(), QStringLiteral("RAW"));

    QJsonObject bwRaw; bwRaw["bandwidth_hz"] = 8000.0;
    QJsonObject rBwRaw = run("set_bandwidth", bwRaw);
    QVERIFY2(!rBwRaw.value("ok").toBool(),
             qPrintable(compactJson(rBwRaw)));
    QVERIFY2(rBwRaw.value("error").toString().contains(QString::fromUtf8("RAW")),
             qPrintable(rBwRaw.value("error").toString()));

    // --- D-5: set_squelch{auto:true} really lands, readback follows -----------
    QJsonObject autoOn; autoOn["auto"] = true;
    QString w = ai::LLMWorker::dispatchToolCall("set_squelch", autoOn, &engine,
                                                /*manualMode=*/false);
    QVERIFY2(!w.contains("\"gated\":true"), qPrintable(w));
    QJsonObject st = QJsonDocument::fromJson(
        ai::executeTool("get_squelch_status", QJsonObject{}, &engine)
            .toUtf8()).object();
    QVERIFY2(st.value("auto").toBool(),
             "set_squelch{auto:true} must land on engine->setSquelchAuto");
    QCOMPARE(st.value("auto").toBool(), engine.squelchAuto());

    // Back off: auto:false also lands.
    QJsonObject autoOff; autoOff["auto"] = false;
    ai::LLMWorker::dispatchToolCall("set_squelch", autoOff, &engine,
                                    /*manualMode=*/false);
    QJsonObject st2 = QJsonDocument::fromJson(
        ai::executeTool("get_squelch_status", QJsonObject{}, &engine)
            .toUtf8()).object();
    QVERIFY2(!st2.value("auto").toBool(), "auto:false must clear the latch");
}

// Phase63: the three bookmark tools (add_bookmark / tune_to_bookmark /
// delete_bookmark) execute for REAL against an injected ui::BookmarkManager --
// the Agent layer, not a routed stub. Honest error paths: missing args,
// out-of-range index, freq<=0 rejected by the manager, and a null (un-injected)
// store. No GUI, no mock; QSettings is isolated by initTestCase().
void TestAgent::bookmarkToolsRealExecutionWithInjectedStore() {
    QSettings("MBDSDR", "MBDSDR").remove("ui/bookmarks");

    dsp::SpectrumEngine engine;
    ui::BookmarkManager bm;
    bm.load();
    QCOMPARE(bm.count(), 0);

    auto run = [&](const QString& tool, const QJsonObject& args) {
        return QJsonDocument::fromJson(
            ai::executeTool(tool, args, &engine, &bm).toUtf8()).object();
    };

    // --- add_bookmark: real persistence, sorted landing index + count --------
    QJsonObject a1; a1["freq_hz"] = 145.05e6; a1["mode"] = "NFM";
    a1["bandwidth_hz"] = 12500.0; a1["group"] = "VHF"; a1["name"] = "中继";
    QJsonObject r1 = run("add_bookmark", a1);
    QVERIFY2(r1.value("ok").toBool(), qPrintable(QString::fromUtf8(
        QJsonDocument(r1).toJson(QJsonDocument::Compact))));
    QCOMPARE(r1.value("index").toInt(), 0);
    QCOMPARE(r1.value("count").toInt(), 1);
    QCOMPARE(bm.count(), 1);
    QCOMPARE(bm.list()[0].frequencyHz, 145.05e6);
    QCOMPARE(bm.list()[0].mode, QString("NFM"));
    QCOMPARE(bm.list()[0].bandwidthHz, 12500.0);
    QCOMPARE(bm.list()[0].group, QString("VHF"));
    QCOMPARE(bm.list()[0].name, QString("中继"));

    QJsonObject a2; a2["freq_hz"] = 98.5e6; a2["name"] = "FM";
    QJsonObject r2 = run("add_bookmark", a2);
    QVERIFY2(r2.value("ok").toBool(), qPrintable(QString::fromUtf8(
        QJsonDocument(r2).toJson(QJsonDocument::Compact))));
    QCOMPARE(bm.count(), 2);
    // Sorted by (group, freq): empty-group 98.5e6 lands at index 0.
    QCOMPARE(bm.list()[0].frequencyHz, 98.5e6);
    QCOMPARE(bm.list()[1].frequencyHz, 145.05e6);

    // --- honest errors: missing freq_hz / freq_hz<=0 -------------------------
    QJsonObject miss = QJsonDocument::fromJson(
        ai::executeTool("add_bookmark", QJsonObject{}, &engine, &bm).toUtf8()).object();
    QVERIFY2(!miss.value("ok").toBool(), "missing freq_hz must be an error");
    QJsonObject zero; zero["freq_hz"] = 0.0;
    QJsonObject rzero = run("add_bookmark", zero);
    QVERIFY2(!rzero.value("ok").toBool(), "freq_hz=0 must be rejected by the manager");
    QCOMPARE(bm.count(), 2);   // rejected input inserted nothing

    // --- null store: honest "未注入" error, zero effect ------------------------
    QJsonObject nullAdd = QJsonDocument::fromJson(
        ai::executeTool("add_bookmark", a1, &engine, nullptr).toUtf8()).object();
    QVERIFY2(!nullAdd.value("ok").toBool(), qPrintable(nullAdd.value("error").toString()));
    QVERIFY2(nullAdd.value("error").toString().contains(
                 QString::fromUtf8("书签管理器未注入")),
             qPrintable(nullAdd.value("error").toString()));
    QCOMPARE(bm.count(), 2);

    // --- tune_to_bookmark: retunes the selected VFO to the bookmark freq -----
    engine.setTestSourceEnabled(true);   // real synthetic IQ source (offline)
    QJsonObject t0; t0["index"] = 0;
    QJsonObject rt = run("tune_to_bookmark", t0);
    QVERIFY2(rt.value("ok").toBool(), qPrintable(QString::fromUtf8(
        QJsonDocument(rt).toJson(QJsonDocument::Compact))));
    QCOMPARE(rt.value("freq_hz").toDouble(), 98.5e6);
    QVERIFY(rt.contains("vfo_index"));
    // Readback: the selected VFO marker now sits at the bookmark frequency.
    bool saw = false;
    for (const auto& m : engine.vfoMarkers())
        if (m.selected) { QCOMPARE(m.freqHz, 98.5e6); saw = true; }
    QVERIFY(saw);

    // out-of-range index -> honest error; null store -> honest error.
    QJsonObject oob; oob["index"] = 99;
    QJsonObject roob = run("tune_to_bookmark", oob);
    QVERIFY2(!roob.value("ok").toBool(), qPrintable(roob.value("error").toString()));
    QJsonObject nullT = QJsonDocument::fromJson(
        ai::executeTool("tune_to_bookmark", t0, &engine, nullptr).toUtf8()).object();
    QVERIFY2(!nullT.value("ok").toBool(), qPrintable(nullT.value("error").toString()));

    // --- delete_bookmark: real removal + remaining count ---------------------
    QJsonObject d0; d0["index"] = 0;
    QJsonObject rd = run("delete_bookmark", d0);
    QVERIFY2(rd.value("ok").toBool(), qPrintable(QString::fromUtf8(
        QJsonDocument(rd).toJson(QJsonDocument::Compact))));
    QCOMPARE(rd.value("remaining").toInt(), 1);
    QCOMPARE(bm.count(), 1);
    QCOMPARE(bm.list()[0].frequencyHz, 145.05e6);

    QJsonObject rd2 = run("delete_bookmark", oob);
    QVERIFY2(!rd2.value("ok").toBool(), "out-of-range delete must be an error");
    QCOMPARE(bm.count(), 1);
    QJsonObject nullD = QJsonDocument::fromJson(
        ai::executeTool("delete_bookmark", d0, &engine, nullptr).toUtf8()).object();
    QVERIFY2(!nullD.value("ok").toBool(), qPrintable(nullD.value("error").toString()));

    // --- the LLMWorker dispatch seam forwards the injected bm too -------------
    QJsonObject throughGate = QJsonDocument::fromJson(
        ai::LLMWorker::dispatchToolCall("add_bookmark", a1, &engine,
                                        /*manualMode=*/false, &bm).toUtf8()).object();
    QVERIFY2(throughGate.value("ok").toBool(), qPrintable(QString::fromUtf8(
        QJsonDocument(throughGate).toJson(QJsonDocument::Compact))));
    QCOMPARE(bm.count(), 2);

    bm.clear();
    QSettings("MBDSDR", "MBDSDR").remove("ui/bookmarks");
}

// Phase63 no-engine audit (D2): when executeTool() is handed a null SpectrumEngine
// (Agent constructed without setEngine, or headless embedding), the dispatch-layer
// guard at agent_tools.cpp:1393 must return the SAME honest JSON error envelope as
// ControlHub::execute() (control_hub.cpp:353): {ok:false, error:"..."}. It must NOT
// (a) return a plain string, (b) return {ok:true} with fabricated frequency/squelch
// values, or (c) crash on a null deref (the executor bodies all dereference engine->
// without their own guard). Spot-check the three read-only tools named in the audit.
void TestAgent::nullEngineReturnsHonestErrorEnvelope() {
    for (const char* tool : {
            "get_status",
            "get_squelch_status",
            "get_spectrum_status" }) {
        const QString raw = ai::executeTool(QString::fromUtf8(tool), QJsonObject{},
                                            nullptr /*engine*/, nullptr /*bookmarks*/);
        // Must be parseable JSON (not the old plain-string "error: no engine").
        QJsonParseError pe{};
        const QJsonDocument doc = QJsonDocument::fromJson(raw.toUtf8(), &pe);
        QVERIFY2(pe.error == QJsonParseError::NoError && doc.isObject(),
                 qPrintable(QString("%1 -> not JSON: %2").arg(QString::fromUtf8(tool), raw)));
        const QJsonObject o = doc.object();
        QVERIFY2(o.value("ok").isBool() && !o.value("ok").toBool(),
                 qPrintable(QString("%1 -> ok must be false: %2").arg(QString::fromUtf8(tool), raw)));
        QVERIFY2(o.value("error").isString() && !o.value("error").toString().isEmpty(),
                 qPrintable(QString("%1 -> error must be non-empty string: %2").arg(QString::fromUtf8(tool), raw)));
        // No fake-success business fields may leak through on the error path.
        for (const char* fake : {
                "frequency_hz", "threshold_db", "fft_size",
                "enabled", "open", "auto", "connected" }) {
            QVERIFY2(!o.contains(QString::fromUtf8(fake)),
                     qPrintable(QString("%1 -> must not contain fake field '%2': %3")
                                .arg(QString::fromUtf8(tool), QString::fromUtf8(fake), raw)));
        }
    }
}

// Phase63 D1-D5 bilateral alias contract. Pins:
//   D2: get_pocsag_messages accepts "channel" (CH key) in addition to "channel_id".
//   D3: set_fft_params accepts raw int for window/average (in addition to string enum).
//   D5: set_network_audio_sink echoes host/stereo (no silent drop).
//   D1: set_vfo_frequency accepts "id" (CH key) in addition to "index".
void TestAgent::phase63BilateralAliasContract() {
    dsp::SpectrumEngine eng;

    // --- D2: "channel" alias on the Agent read tools. ---
    {
        QJsonObject a; a["channel"] = 0;
        QJsonObject r = runTool(eng, "get_pocsag_messages", a);
        QVERIFY2(r.value("ok").toBool(), qPrintable(QString::fromUtf8(
            QJsonDocument(r).toJson(QJsonDocument::Compact))));
        QCOMPARE(r.value("channel_id").toInt(), 0);   // resolves marker channel 0
    }

    // --- D3: set_fft_params accepts raw int window/average (CH-style). ---
    {
        QJsonObject a; a["fft_size"] = 2048; a["window"] = 2; a["average"] = 1;
        QJsonObject r = runTool(eng, "set_fft_params", a);
        QVERIFY2(r.value("ok").toBool(), qPrintable(QString::fromUtf8(
            QJsonDocument(r).toJson(QJsonDocument::Compact))));
        QCOMPARE(r.value("window").toInt(), 2);
        QCOMPARE(r.value("average").toInt(), 1);
        QCOMPARE(eng.windowType(), 2);
        QCOMPARE(eng.averageMode(), 1);
        // And string enum still works.
        QJsonObject b; b["fft_size"] = 4096; b["window"] = "Flattop"; b["average"] = "Slow";
        r = runTool(eng, "set_fft_params", b);
        QVERIFY2(r.value("ok").toBool(), qPrintable(QString::fromUtf8(
            QJsonDocument(r).toJson(QJsonDocument::Compact))));
        QCOMPARE(eng.windowType(), 1);
        QCOMPARE(eng.averageMode(), 1);
    }

    // --- D5: set_network_audio_sink echoes host/stereo. ---
    {
        QJsonObject a; a["enable"] = true; a["port"] = 12345;
        a["host"] = "192.168.1.5"; a["stereo"] = true;
        QString raw = ai::executeTool("set_network_audio_sink", a, &eng);
        QVERIFY2(raw.contains("\"host\":\"192.168.1.5\""), qPrintable(raw));
        QVERIFY2(raw.contains("\"stereo\":true"), qPrintable(raw));
    }

    // --- D1: set_vfo_frequency accepts "id" directly (CH-style). ---
    {
        const auto markers = eng.vfoMarkers();
        QVERIFY(!markers.isEmpty());
        const int id0 = markers.at(0).id;
        QJsonObject a; a["id"] = id0; a["freq_hz"] = 99100000.0;
        QString raw = ai::executeTool("set_vfo_frequency", a, &eng);
        QVERIFY2(raw.contains("\"ok\":true"), qPrintable(raw));
        bool saw = false;
        for (const auto& m : eng.vfoMarkers())
            if (m.id == id0) { QCOMPARE(m.freqHz, 99100000.0); saw = true; }
        QVERIFY(saw);
    }

    // --- OF2 (output-contract): get_spectrum_status readback keys are
    //     window/average (int), matching the schema description, the CH readback,
    //     and the set_fft_params write echo. The old window_type/average_mode keys
    //     must no longer be emitted. ---
    {
        QJsonObject setA; setA["fft_size"] = 2048; setA["window"] = 2; setA["average"] = 1;
        QVERIFY(runTool(eng, "set_fft_params", setA).value("ok").toBool());
        QJsonObject r = runTool(eng, "get_spectrum_status", QJsonObject{});
        QVERIFY2(r.value("ok").toBool(), qPrintable(QString::fromUtf8(
            QJsonDocument(r).toJson(QJsonDocument::Compact))));
        QCOMPARE(r.value("window").toInt(), 2);
        QCOMPARE(r.value("average").toInt(), 1);
        QCOMPARE(r.value("fft_size").toInt(), 2048);
        QVERIFY2(!r.contains("window_type") && !r.contains("average_mode"),
                 "OF2: legacy window_type/average_mode keys must not be emitted");
    }
}

#include <QCoreApplication>
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    TestAgent t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_agent.moc"
