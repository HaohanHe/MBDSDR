// Frequency calibrator offline (NOT HARDWARE / 非硬件) synthetic tests.
//
// *** SYNTHETIC TEST FIXTURE -- 仅验证算法，非真实接收 (NOT REAL RECEPTION) ***
//
// These fabricate complex IQ from a clock-error model and feed the pure-logic
// frequency calibrator. The model is physically faithful: a crystal error of e
// ppm scales BOTH the tuned LO and the sample rate by (1+e), so a reference at
// RF F_ref, with nominal centre Fc and nominal rate Fs, is sampled with
//     f_analog = F_ref - Fc*(1+e),  Fs_actual = Fs*(1+e),
//     phase_n  = 2*pi*f_analog*n/Fs_actual.
// The calibrator (assuming the nominal rate) must recover e to sub-ppm. No RTL-
// SDR / no transmitter is touched; on pure noise it honestly reports no
// reference carrier.

#include <QtTest/QtTest>

#include <QCoreApplication>
#include <QDir>
#include <QSettings>

#include <complex>
#include <cmath>
#include <random>
#include <vector>

#include "dsp/frequency_calibrator.h"
#include "dsp/fcch_detector.h"   // kFcchToneHz (the FCCH expected baseband)
#include "core/tokens.h"         // kFixtureSrcRateHz / kPeakThresholdDefault

using namespace mbdsdr;
using namespace mbdsdr::dsp;

namespace {

// Physically faithful clock-error IQ (see file header). Generic, non-stored
// test anchors -- these are NOT receiver presets.
std::vector<std::complex<float>>
makeClockErrorIq(double Fc, double Fref, double ppmErr,
                 double FsNom, long N, double noiseAmp, unsigned seed) {
    const double e = ppmErr / 1.0e6;
    const double FsAct = FsNom * (1.0 + e);
    const double fAnalog = Fref - Fc * (1.0 + e);
    std::mt19937 rng(seed);
    std::normal_distribution<float> gauss(0.0f, 1.0f);
    std::vector<std::complex<float>> x(N);
    for (long n = 0; n < N; ++n) {
        const double ph = 2.0 * M_PI * fAnalog * static_cast<double>(n) / FsAct;
        x[n] = std::complex<float>(
            static_cast<float>(std::cos(ph)) + noiseAmp * gauss(rng),
            static_cast<float>(std::sin(ph)) + noiseAmp * gauss(rng));
    }
    return x;
}

CalibratorConfig configFor(double Fc, double expectedBb) {
    CalibratorConfig cfg;
    cfg.centreFreqHz = Fc;
    cfg.expectedBasebandHz = expectedBb;
    cfg.thresholdDb = tokens::kPeakThresholdDefault;
    return cfg;
}

} // namespace

class TestFrequencyCalibrator : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void handheld_plus32_recoversAndResidualZero();
    void manual_minus20_recovers();
    void smallHandheldOffsets_recoveredToWithinFewHz();
    void gsmFcch_plus32_recovers();
    void remeasureAfterCorrection_isZero();
    void noiseOnly_notDetected();
    void weakSignal_notDetected();
    void confidenceAndSpread_areSane();
    void settingRoundtrip_andRefName();
};

void TestFrequencyCalibrator::handheld_plus32_recoversAndResidualZero() {
    const double F = 446.0e6;            // generic handheld anchor
    const double Fs = tokens::kFixtureSrcRateHz;
    const double injected = 32.0;
    auto iq = makeClockErrorIq(F, F, injected, Fs, 4 * 16384, 0.02, 777);
    CalibrationResult r = calibrateFromCapture(
        iq, Fs, CalibrationReference::HandheldGuided, configFor(F, 0.0), 4);

    QVERIFY2(r.detected, "handheld reference carrier must be detected");
    QVERIFY2(std::fabs(r.ppm - injected) < 0.2,
             qPrintable(QString("handheld ppm %1 vs injected %2")
                            .arg(r.ppm).arg(injected)));
    // Predicted residual after applying the measured correction must be ~0.
    const double resid = predictedResidualHz(r.meanOffsetHz, F, r.ppm);
    QVERIFY2(std::fabs(resid) < 50.0,
             qPrintable(QString("post-apply residual %1 Hz").arg(resid)));
}

void TestFrequencyCalibrator::manual_minus20_recovers() {
    const double F = 98.0e6;             // generic manual anchor (FM band)
    const double Fs = tokens::kFixtureSrcRateHz;
    const double injected = -20.0;
    auto iq = makeClockErrorIq(F, F, injected, Fs, 4 * 16384, 0.02, 888);
    CalibrationResult r = calibrateFromCapture(
        iq, Fs, CalibrationReference::Manual, configFor(F, 0.0), 4);

    QVERIFY2(r.detected, "manual reference carrier must be detected");
    QVERIFY2(std::fabs(r.ppm - injected) < 0.2,
             qPrintable(QString("manual ppm %1 vs injected %2")
                            .arg(r.ppm).arg(injected)));
}

// The handheld graphical demo anchors: a carrier sitting a small KNOWN baseband
// offset away from the VFO centre (the SDR's crystal error + the handheld's
// tuning error combined). measureCarrier must read that residual to within a few
// Hz on both demo UHF channel centres, at +500 Hz / -250 Hz / zero offset. This
// pins the Hz-level precision the wizard's delta-Hz readout promises.
void TestFrequencyCalibrator::smallHandheldOffsets_recoveredToWithinFewHz() {
    const double Fs = tokens::kFixtureSrcRateHz;
    // Generic, non-preset UHF channel centres (the demo anchors only).
    const double centres[2] = {409.75e6, 438.5e6};
    const double offsets[3]  = {+500.0, -250.0, 0.0};
    for (double F : centres) {
        for (double want : offsets) {
            // Carrier placed at expectedBaseband(0) + want Hz.
            std::vector<std::complex<float>> iq(16384);
            std::mt19937 rng(9000 + (long)F + (long)want);
            std::normal_distribution<float> gauss(0.0f, 0.02f);
            for (long n = 0; n < 16384; ++n) {
                const double ph = 2.0 * M_PI * want * n / Fs;
                iq[n] = {static_cast<float>(std::cos(ph)) + gauss(rng),
                         static_cast<float>(std::sin(ph)) + gauss(rng)};
            }
            CalibrationMeasurement m = measureCarrier(iq, Fs, configFor(F, 0.0));
            QVERIFY2(m.detected, qPrintable(
                QString("centre %1 offset %2 Hz must lock")
                    .arg(F).arg(want)));
            QVERIFY2(std::fabs(m.offsetHz - want) < 5.0, qPrintable(
                QString("centre %1: measured offset %2 Hz vs injected %3 Hz")
                    .arg(F).arg(m.offsetHz).arg(want)));
        }
    }
}

void TestFrequencyCalibrator::gsmFcch_plus32_recovers() {
    const double Fc = 935.0e6;           // generic ARFCN downlink centre
    const double Fref = Fc + kFcchToneHz;
    const double Fs = 270833.0;
    const double injected = 32.0;
    auto iq = makeClockErrorIq(Fc, Fref, injected, Fs, 4 * 8192, 0.02, 999);
    CalibrationResult r = calibrateFromCapture(
        iq, Fs, CalibrationReference::GsmFcch,
        configFor(Fc, kFcchToneHz), 4);

    QVERIFY2(r.detected, "FCCH pure tone must be detected");
    QVERIFY2(std::fabs(r.ppm - injected) < 0.2,
             qPrintable(QString("FCCH ppm %1 vs injected %2")
                            .arg(r.ppm).arg(injected)));
}

void TestFrequencyCalibrator::remeasureAfterCorrection_isZero() {
    // Strongest proof of the round trip: after applying +32, a capture made with
    // the corrected effective error (injected - applied) must measure ~0 ppm.
    const double F = 446.0e6, Fs = tokens::kFixtureSrcRateHz;
    const double injected = 32.0, applied = 31.999;
    auto iq = makeClockErrorIq(F, F, injected - applied, Fs, 4 * 16384, 0.02, 1010);
    CalibrationResult r = calibrateFromCapture(
        iq, Fs, CalibrationReference::HandheldGuided, configFor(F, 0.0), 4);
    QVERIFY2(r.detected, "corrected capture must still be measurable");
    QVERIFY2(std::fabs(r.ppm) < 0.2,
             qPrintable(QString("post-correction ppm %1 should be ~0").arg(r.ppm)));
}

void TestFrequencyCalibrator::noiseOnly_notDetected() {
    const double Fs = tokens::kFixtureSrcRateHz;
    std::mt19937 rng(2024);
    std::normal_distribution<float> gauss(0.0f, 1.0f);
    std::vector<std::complex<float>> iq(4 * 16384);
    for (auto& c : iq) c = {gauss(rng), gauss(rng)};
    CalibrationResult r = calibrateFromCapture(
        iq, Fs, CalibrationReference::Manual, configFor(446.0e6, 0.0), 4);
    QVERIFY2(!r.detected, "noise-only must honestly report no reference carrier");
}

void TestFrequencyCalibrator::weakSignal_notDetected() {
    // A tone far below the noise cannot honestly be locked. A 16384 FFT gives a
    // coherent tone ~36..42 dB of processing gain, so the noise must be large
    // enough that the post-FFT peak still falls under the gate (input SNR well
    // below -threshold - gain, i.e. noise std ~ several dozen here).
    const double F = 446.0e6, Fs = tokens::kFixtureSrcRateHz;
    auto iq = makeClockErrorIq(F, F, 5.0, Fs, 4 * 16384, 60.0, 3030);
    CalibrationResult r = calibrateFromCapture(
        iq, Fs, CalibrationReference::HandheldGuided, configFor(F, 0.0), 4);
    QVERIFY2(!r.detected, "tone buried in noise must honestly be not detected");
}

void TestFrequencyCalibrator::confidenceAndSpread_areSane() {
    const double F = 446.0e6, Fs = tokens::kFixtureSrcRateHz;
    auto iq = makeClockErrorIq(F, F, 32.0, Fs, 4 * 16384, 0.02, 4040);
    CalibrationResult r = calibrateFromCapture(
        iq, Fs, CalibrationReference::HandheldGuided, configFor(F, 0.0), 4);
    QVERIFY2(r.confidence > 0.5,
             qPrintable(QString("confidence %1 should be high").arg(r.confidence)));
    QVERIFY2(r.spreadPpm < 1.0,
             qPrintable(QString("spread %1 should be sub-ppm").arg(r.spreadPpm)));
    QVERIFY2(r.measurementsUsed == 4, "all four clean segments must be accepted");
}

void TestFrequencyCalibrator::initTestCase() {
    // Default QSettings under QTEST_MAIN has no org/app name and Windows
    // NativeFormat (registry) ignores setPath and rejects empty keys. Isolate to
    // a PID-unique IniFormat dir (honors setPath on every platform).
    const QString cfg = QDir::tempPath() + "/mbdsdr_cfg_freqcal_" +
                        QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(cfg);
    QCoreApplication::setOrganizationName("MBDSDR");
    QCoreApplication::setApplicationName("MBDSDR");
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, cfg);
}

void TestFrequencyCalibrator::settingRoundtrip_andRefName() {
    savePpmSetting(31.999);
    QVERIFY2(std::fabs(currentPpmSetting() - 31.999) < 1e-6,
             "ppm must round-trip through QSettings");
    QVERIFY2(!calibrationReferenceName(CalibrationReference::GsmFcch).isEmpty(),
             "reference kinds must have human-readable names");
}

QTEST_MAIN(TestFrequencyCalibrator)
#include "test_frequency_calibrator.moc"
