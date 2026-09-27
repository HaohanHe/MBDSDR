// SPDX-License-Identifier: MIT
#pragma once

#include <QMainWindow>

class QLabel;
class QDoubleSpinBox;
class QComboBox;
class QSlider;
class QPushButton;
class QCheckBox;
class QTabWidget;
class QPlainTextEdit;
class QTableWidget;
class QLineEdit;
class QStackedWidget;
class QPushButton;

namespace mbdsdr {
namespace dsp  { class SpectrumEngine; struct AircraftInfo; }
namespace ui   { class SpectrumWidget; class SkyView; class WorldView; }
namespace ai   { class Agent; }

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override;

private slots:
    void onSourceChanged(const QString& name, bool connected);
    void onAudioLevel(float dbfs);
    void onRssiLevel(float dbfs);
    void onSquelchState(bool open);
    void onRecordingState(bool recording, const QString& path);
    void onRecordClicked();
    void onCwDecoded(const QString& text, double wpm);
    void onAdsbAircraft(const mbdsdr::dsp::AircraftInfo& info);

private:
    dsp::SpectrumEngine* engine_   = nullptr;
    ui::SpectrumWidget* spectrum_ = nullptr;

    QDoubleSpinBox* freqSpin_   = nullptr;
    QComboBox*      srCombo_    = nullptr;
    QSlider*        gainSlider_ = nullptr;
    QLabel*         gainValue_  = nullptr;
    QLabel*         sourceBanner_ = nullptr;
    QLabel*         statusLabel_ = nullptr;

    QComboBox*      demodCombo_  = nullptr;
    QComboBox*      bwCombo_     = nullptr;
    QSlider*        squelchSlider_ = nullptr;
    QCheckBox*      squelchCheck_ = nullptr;
    QLabel*         squelchValue_ = nullptr;
    QLabel*         levelLabel_   = nullptr;
    QLabel*         squelchState_ = nullptr;
    QPushButton*    recordBtn_   = nullptr;
    QCheckBox*      gatedCheck_   = nullptr;
    QLabel*         recStatus_   = nullptr;

    QTabWidget*     rightTabs_   = nullptr;
    QPlainTextEdit* cwText_      = nullptr;
    QLabel*         cwWpm_       = nullptr;
    QTableWidget*   adsbTable_   = nullptr;

    ai::Agent*      agent_       = nullptr;
    QPlainTextEdit* aiChat_      = nullptr;
    QLineEdit*      aiInput_     = nullptr;
    QLabel*         aiStatus_   = nullptr;

    // Phase 7: new views
    ui::SkyView*     skyView_   = nullptr;
    ui::WorldView*   worldView_ = nullptr;
    QStackedWidget* centerStack_ = nullptr;
    QLabel*         levelBar_   = nullptr;
    QLabel*         rssiLabel_  = nullptr;
    QPushButton*    connectBtn_ = nullptr;

    void setControlsEnabled(bool hardwareConnected);
    void saveUiState();
    void restoreUiState();
};

} // namespace mbdsdr
