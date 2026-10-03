// SPDX-License-Identifier: MIT
//
// Graphical, interactive frequency-calibration wizard -- self-contained QDialog.
//
// Steps the user through measuring the radio's crystal ppm error against a
// signal of exactly known frequency:
//   1. pick a reference kind (handheld-guided / GSM FCCH / manual),
//   2. confirm or edit the known frequency,
//   3. follow the on-screen operation guide (e.g. hold the handheld's PTT),
//   4. watch a live spectrum with the locked peak, the measured delta (Hz) and
//      the ppm estimate,
//   5. apply + save, with a before/after comparison.
//
// The dialog is hardware-agnostic: it does not touch the engine directly. A
// CaptureFn is injected (the main window wires it to SpectrumEngine, tests wire
// it to a synthetic provider). With no provider / no signal it shows an honest
// empty state instead of fabricating a reading. All sizing goes through the
// core tokens so the layout stays elastic.
#pragma once

#include <QDialog>
#include <QString>
#include <functional>
#include <vector>
#include <complex>

#include "dsp/frequency_calibrator.h"

class QStackedWidget;
class QWidget;
class QComboBox;
class QDoubleSpinBox;
class QLabel;
class QPushButton;
class QTimer;

namespace mbdsdr {
namespace ui {

// Prototype of a spectrum capture provider. It tunes (when tuneHz >= 0) and
// returns ~`samples` complex samples, plus the nominal sample rate and the
// tuned centre. Return false when no capture is available (the dialog then
// shows the honest no-device / no-signal empty state).
using CalibrationCaptureFn =
    std::function<bool(double tuneHz, int samples,
                       std::vector<std::complex<float>>& out,
                       double& sampleRateHz, double& centreHz)>;

class CalibrationDialog : public QDialog {
    Q_OBJECT
public:
    explicit CalibrationDialog(QWidget* parent = nullptr);

    // Inject the capture provider (main window -> engine; tests -> synthetic).
    void setCaptureProvider(CalibrationCaptureFn fn);

    // Programmatic setup used by tests (the wizard pages drive these too).
    void setReferenceKind(dsp::CalibrationReference ref);
    void setKnownFrequencyHz(double hz);

    // Result accessors (valid after a measurement / apply).
    bool detected() const { return lastResult_.detected; }
    double resultPpm() const { return lastResult_.ppm; }
    bool applied() const { return applied_; }
    dsp::CalibrationResult lastResult() const { return lastResult_; }

public slots:
    // Perform one capture + measurement and refresh every readout. The live
    // QTimer calls this; tests call it directly for deterministic runs.
    void measureOnce();
    void applyCorrection();

private slots:
    void goNext();
    void goBack();
    void startLive();
    void stopLive();

private:
    void buildUi();
    void buildPageSource();
    void buildPageFrequency();
    void buildPageGuide();
    void buildPageMeasure();
    void buildPageApply();
    void updateNavButtons();
    void updateGuideText();
    QString frequencyPlaceholderHint() const;

    // Capture one configured block; returns an empty vector (and sets why)
    // when no provider / no data is available.
    bool captureBlock(std::vector<std::complex<float>>& out,
                      double& sr, double& centre);

    CalibrationCaptureFn captureFn_;
    dsp::CalibrationReference ref_ = dsp::CalibrationReference::HandheldGuided;
    double knownFreqHz_ = 0.0;       // known exact RF reference
    double priorPpm_ = 0.0;          // correction in effect before this wizard
    double predictedResidualHz_ = 0.0;

    dsp::CalibrationResult lastResult_;
    bool applied_ = false;

    QStackedWidget* stack_ = nullptr;
    QComboBox* refCombo_ = nullptr;
    QDoubleSpinBox* freqSpin_ = nullptr;
    QLabel* guideLabel_ = nullptr;
    QLabel* measureLabel_ = nullptr;
    QLabel* emptyLabel_ = nullptr;
    QWidget* spectrumHolder_ = nullptr;   // hosts the live spectrum widget
    QLabel* deltaLabel_ = nullptr;
    QLabel* ppmLabel_ = nullptr;
    QLabel* confidenceLabel_ = nullptr;
    QLabel* compareLabel_ = nullptr;
    QPushButton* backBtn_ = nullptr;
    QPushButton* nextBtn_ = nullptr;
    QPushButton* applyBtn_ = nullptr;
    QTimer* liveTimer_ = nullptr;

    // Small live-spectrum canvas (nested QWidget, implemented in the .cpp).
    QWidget* spectrumCanvas_ = nullptr;
};

} // namespace ui
} // namespace mbdsdr
