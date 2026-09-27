// SPDX-License-Identifier: MIT
#pragma once

#include <QMainWindow>

class QLabel;
class QDoubleSpinBox;
class QComboBox;
class QSlider;

namespace mbdsdr {
namespace dsp  { class SpectrumEngine; }
namespace ui   { class SpectrumWidget; }

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override;

private slots:
    void onSourceChanged(const QString& name, bool connected);
    void onAudioLevel(float dbfs);
    void onSquelchState(bool open);

private:
    dsp::SpectrumEngine* engine_   = nullptr;
    ui::SpectrumWidget* spectrum_ = nullptr;

    QDoubleSpinBox* freqSpin_   = nullptr;
    QComboBox*      srCombo_    = nullptr;
    QSlider*        gainSlider_ = nullptr;
    QLabel*         gainValue_  = nullptr;
    QLabel*         sourceBanner_ = nullptr;
    QLabel*         statusLabel_ = nullptr;

    // Demod / squelch controls
    QComboBox*      demodCombo_  = nullptr;
    QSlider*        squelchSlider_ = nullptr;
    QLabel*         squelchValue_ = nullptr;
    QLabel*         levelLabel_   = nullptr;
    QLabel*         squelchState_ = nullptr;

    void setControlsEnabled(bool hardwareConnected);
};

} // namespace mbdsdr
