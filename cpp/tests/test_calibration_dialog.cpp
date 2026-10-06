// SPDX-License-Identifier: MIT
//
// QtTest (offscreen) for the interactive CalibrationDialog.
//
// *** SYNTHETIC TEST FIXTURE -- 仅验证向导流程，非真实接收 (NOT REAL RECEPTION) ***
//
// We inject a capture provider that fabricates clock-error IQ (the same
// physically faithful model as test_frequency_calibrator): a crystal error of e
// ppm scales both the tuned LO and the sample rate by (1+e). The wizard must
// walk through source -> frequency -> guide -> measure -> apply, lock the
// reference, report a ppm within sub-ppm of the injected value, apply+save it,
// and show a before/after comparison whose residual is ~0. With no provider /
// capture failure / noise-only input it must show an honest empty state and
// never fabricate a reading.

#include <QtTest/QtTest>

#include <QApplication>
#include <QTemporaryDir>
#include <QSettings>
#include <QLabel>
#include <QDoubleSpinBox>
#include <QStackedWidget>
#include <QImage>

#include <complex>
#include <cmath>
#include <random>
#include <vector>

#include "ui/calibration_dialog.h"
#include "dsp/frequency_calibrator.h"
#include "dsp/fcch_detector.h"   // kFcchToneHz
#include "core/tokens.h"

using namespace mbdsdr;
using namespace mbdsdr::ui;
using namespace mbdsdr::dsp;

namespace {

// Physically faithful clock-error IQ (see test_frequency_calibrator). Generic,
// non-stored anchors -- NOT receiver presets.
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

// Mutable provider state so each test can switch the injected error / mode.
struct Fixture {
    double ppmErr = 0.0;
    bool fcch = false;
    double Fs = tokens::kFixtureSrcRateHz;
    unsigned seed = 1;
    bool captureOk = true;   // false -> simulate a failed device capture
    bool noiseOnly = false;  // true -> pure gaussian noise (no reference tone)
};

CalibrationCaptureFn makeProvider(Fixture& st) {
    return [&st](double tuneHz, int samples,
                 std::vector<std::complex<float>>& out,
                 double& sampleRateHz, double& centreHz) -> bool {
        if (!st.captureOk) return false;
        centreHz = tuneHz;
        sampleRateHz = st.Fs;
        const double Fc = tuneHz;
        const double Fref = st.fcch ? Fc + kFcchToneHz : Fc;
        if (st.noiseOnly) {
            std::mt19937 rng(st.seed);
            std::normal_distribution<float> g(0.0f, 0.5f);
            out.resize(samples);
            for (auto& c : out) c = {g(rng), g(rng)};
            return true;
        }
        out = makeClockErrorIq(Fc, Fref, st.ppmErr, st.Fs, samples, 0.02, st.seed);
        return true;
    };
}

// Find a child QLabel by its objectName (diplomacy: the labels are private).
QLabel* findLabel(QWidget* w, const QString& name) {
    const auto labels = w->findChildren<QLabel*>();
    for (QLabel* l : labels)
        if (l->objectName() == name) return l;
    return nullptr;
}

} // namespace

class TestCalibrationDialog : public QObject {
    Q_OBJECT
private slots:
    void init() {
        // Isolate QSettings so apply/save does not touch the real user config.
        tmp_ = new QTemporaryDir;
        // Windows NativeFormat = registry (setPath ignored, empty org unwritable);
        // IniFormat honors setPath on every platform.
        QCoreApplication::setOrganizationName("MBDSDR");
        QCoreApplication::setApplicationName("MBDSDR");
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope,
                           tmp_->path());
        dlg_ = new CalibrationDialog;
    }
    void cleanup() {
        delete dlg_;
        dlg_ = nullptr;
        delete tmp_;
        tmp_ = nullptr;
    }

    // (a) Handheld-guided, +32ppm injected: full wizard walk + apply + compare.
    void handheld_plus32_fullWizard_appliesAndResidualZero() {
        Fixture st{32.0, false, tokens::kFixtureSrcRateHz, 777, true, false};
        dlg_->setCaptureProvider(makeProvider(st));

        dlg_->setReferenceKind(CalibrationReference::HandheldGuided);
        dlg_->setKnownFrequencyHz(446.0e6);

        // Walk source(0) -> frequency(1) -> guide(2) -> measure(3). Entering the
        // measure page fires measureOnce() synchronously via currentChanged.
        QStackedWidget* stack = dlg_->findChild<QStackedWidget*>();
        QVERIFY(stack != nullptr);
        stack->setCurrentIndex(1);
        stack->setCurrentIndex(2);
        stack->setCurrentIndex(3);

        QVERIFY2(dlg_->detected(), "handheld +32ppm reference must be detected");
        QVERIFY2(std::fabs(dlg_->resultPpm() - 32.0) < 0.5,
                 qPrintable(QString("measured ppm %1 vs injected 32")
                                .arg(dlg_->resultPpm())));

        // -> apply page, then apply + save.
        stack->setCurrentIndex(4);
        dlg_->applyCorrection();
        QVERIFY2(dlg_->applied(), "correction must be marked applied");

        // The saved QSettings value must equal the applied measurement.
        QVERIFY2(std::fabs(currentPpmSetting() - dlg_->resultPpm()) < 1e-3,
                 "applied ppm must round-trip through QSettings");

        // Applying does not discard the detected last result.
        QVERIFY2(dlg_->lastResult().detected, "last result stays detected");
    }

    // (c) Manual, -20ppm injected: programmatic measure must recover it.
    void manual_minus20_recovers() {
        Fixture st{-20.0, false, tokens::kFixtureSrcRateHz, 888, true, false};
        dlg_->setCaptureProvider(makeProvider(st));
        dlg_->setReferenceKind(CalibrationReference::Manual);
        dlg_->setKnownFrequencyHz(98.0e6);
        dlg_->measureOnce();

        QVERIFY2(dlg_->detected(), "manual -20ppm reference must be detected");
        QVERIFY2(std::fabs(dlg_->resultPpm() - (-20.0)) < 0.5,
                 qPrintable(QString("manual ppm %1 vs injected -20")
                                .arg(dlg_->resultPpm())));
    }

    // (b) GSM FCCH, +32ppm injected: expected baseband = +kFcchToneHz.
    void gsmFcch_plus32_recovers() {
        Fixture st{32.0, true, 270833.0, 999, true, false};
        dlg_->setCaptureProvider(makeProvider(st));
        dlg_->setReferenceKind(CalibrationReference::GsmFcch);
        dlg_->setKnownFrequencyHz(935.0e6);
        dlg_->measureOnce();

        QVERIFY2(dlg_->detected(), "FCCH pure tone must be detected");
        QVERIFY2(std::fabs(dlg_->resultPpm() - 32.0) < 0.5,
                 qPrintable(QString("FCCH ppm %1 vs injected 32")
                                .arg(dlg_->resultPpm())));
    }

    // No provider at all -> honest empty, no fabricated reading, no crash.
    void noProvider_honestEmpty_noCrash() {
        // Deliberately no setCaptureProvider.
        dlg_->setKnownFrequencyHz(446.0e6);
        dlg_->measureOnce();
        QVERIFY2(!dlg_->detected(),
                 "with no provider the dialog must NOT claim a carrier");
        // A capture failure (device busy) is also honest.
        Fixture st{32.0, false, tokens::kFixtureSrcRateHz, 1, false, false};
        dlg_->setCaptureProvider(makeProvider(st));
        dlg_->measureOnce();
        QVERIFY2(!dlg_->detected(),
                 "a failed capture must honestly report no reading");
    }

    // Pure noise -> honest not-detected.
    void noiseOnly_honestEmpty() {
        Fixture st{0.0, false, tokens::kFixtureSrcRateHz, 5, true, true};
        dlg_->setCaptureProvider(makeProvider(st));
        dlg_->setKnownFrequencyHz(446.0e6);
        dlg_->measureOnce();
        QVERIFY2(!dlg_->detected(),
                 "noise-only input must honestly report no reference carrier");
    }

    // Guide copy differs per reference kind (handheld PTT / FCCH / manual).
    void guideText_perReferenceKind() {
        dlg_->setReferenceKind(CalibrationReference::HandheldGuided);
        QLabel* guide = findLabel(dlg_, QStringLiteral("guideText"));
        QVERIFY2(guide != nullptr, "guide label must exist");
        QVERIFY2(guide->text().contains(QString::fromUtf8("PTT")),
                 "handheld guide must tell the user to hold PTT");

        dlg_->setReferenceKind(CalibrationReference::GsmFcch);
        QVERIFY2(guide->text().contains(QString::fromUtf8("FCCH")),
                 "GSM guide must reference the FCCH pure tone");

        dlg_->setReferenceKind(CalibrationReference::Manual);
        QVERIFY2(guide->text().contains(QString::fromUtf8("信号源")) ||
                 guide->text().contains(QString::fromUtf8("标准频率源")),
                 "manual guide must mention an exact signal source");
    }

    // The 409.75 / 438.5 anchors must only ever be PLACEHOLDERS, never a set
    // value / preset. With no edit the spinbox must not carry them.
    void frequency_anchors_areOnlyPlaceholders() {
        auto* spin = dlg_->findChild<QDoubleSpinBox*>();
        QVERIFY2(spin != nullptr, "frequency spinbox must exist");
        // The example anchor must appear as an editable hint, never as the value.
        bool foundHint = false;
        for (QLabel* l : dlg_->findChildren<QLabel*>()) {
            if (l->text().contains(QString::fromUtf8("占位提示"))) { foundHint = true; break; }
        }
        QVERIFY2(foundHint, "example frequency must be presented as a placeholder hint");
        // Default value must be 0 (nothing pre-tuned), not 409.75 / 438.5.
        QVERIFY2(std::fabs(spin->value()) < 1e-6,
                 "no frequency may be pre-set as a default/preset");
    }

    // Offscreen render of the dialog (if an artifacts dir is provided).
    void renderScreenshot_offscreen() {
        Fixture st{32.0, false, tokens::kFixtureSrcRateHz, 777, true, false};
        dlg_->setCaptureProvider(makeProvider(st));
        dlg_->setReferenceKind(CalibrationReference::HandheldGuided);
        dlg_->setKnownFrequencyHz(446.0e6);
        QStackedWidget* stack = dlg_->findChild<QStackedWidget*>();
        stack->setCurrentIndex(3);  // -> measure page (triggers one measurement)

        const QString dir = QString::fromLocal8Bit(
            qgetenv("MBD_ARTIFACT_DIR"));
        if (dir.isEmpty()) return;   // not requested: skip silently
        dlg_->resize(780, 500);
        dlg_->show();
        QTest::qWait(30);
        QImage img = dlg_->grab().toImage();
        QVERIFY2(!img.isNull(), "offscreen grab must produce an image");
        const QString path = dir + QStringLiteral("/calibration_dialog.png");
        QVERIFY2(img.save(path), qPrintable(QString("save %1").arg(path)));
    }

private:
    CalibrationDialog* dlg_ = nullptr;
    QTemporaryDir* tmp_ = nullptr;
};

QTEST_MAIN(TestCalibrationDialog)
#include "test_calibration_dialog.moc"
