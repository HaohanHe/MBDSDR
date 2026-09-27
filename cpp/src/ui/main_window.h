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

namespace mbdsdr {
namespace dsp  { class SpectrumEngine; struct AircraftInfo; }
namespace ui   { class SpectrumWidget; class SkyView; class WorldView; class WaterfallWidget; }
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
    void onAdsbAircraft(const dsp::AircraftInfo& info);

private:
    dsp::SpectrumEngine* engine_   = nullptr;
    ui::SpectrumWidget* spectrum_ = nullptr;
    ui::SkyView*     skyView_   = nullptr;
    ui::WorldView*   worldView_ = nullptr;
    ui::WaterfallWidget* waterfall_ = nullptr;
    QTabWidget* centerTabs_ = nullptr;

    // Left panel controls
    QDoubleSpinBox* freqSpin_   = nullptr;
    QComboBox*      srCombo_    = nullptr;
    QSlider*        gainSlider_ = nullptr;
    QLabel*         gainValue_  = nullptr;
    QLabel*         sourceBanner_ = nullptr;
    QLabel*         statusLabel_ = nullptr;
    QPushButton*    connectBtn_ = nullptr;
    QLabel*         rssiLabel_  = nullptr;

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
    QLabel*         levelBar_   = nullptr;

    // Right tabs
    QTabWidget*     rightTabs_   = nullptr;
    QPlainTextEdit* cwText_      = nullptr;
    QLabel*         cwWpm_       = nullptr;
    QTableWidget*   adsbTable_   = nullptr;

    // AI
    ai::Agent*      agent_       = nullptr;
    QPlainTextEdit* aiChat_      = nullptr;
    QLineEdit*      aiInput_     = nullptr;
    QLabel*         aiStatus_   = nullptr;

    void setControlsEnabled(bool hardwareConnected);
    void saveUiState();
    void restoreUiState();
};

} // namespace mbdsdr
