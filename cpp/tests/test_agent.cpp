// SPDX-License-Identifier: MIT
#include <QtTest/QtTest>
#include <QRegularExpression>
#include <QSettings>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>

#include "ai/agent_tools.h"
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
    QCOMPARE(tools.size(), 38);
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

#include <QCoreApplication>
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    TestAgent t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_agent.moc"
