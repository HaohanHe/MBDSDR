// SPDX-License-Identifier: MIT
#pragma once

#include <QMainWindow>
#include <QList>
#include <limits>

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
class QSplitter;
class QTimer;
class QListWidget;
class QListWidgetItem;
class QLineEdit;
class QSpinBox;

namespace mbdsdr {
namespace ui { class BookmarkManager; }
namespace dsp  { class SpectrumEngine; struct AircraftInfo; class TleClient; struct SatPass; }
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
    void onRecordingProgress(const QString& path, int seconds, qint64 bytes);
    void onRecordClicked();
    void onCwDecoded(const QString& text, double wpm);
    void onAdsbAircraft(const dsp::AircraftInfo& info);
    void saveSettings();   // immediate persistence (writes QSettings)
    void scheduleSave();   // debounced persistence: arms the 500 ms save timer
    void onPassesReady(QList<dsp::SatPass> passes);
    void onTleFetchFailed(const QString& reason);
    void onPassRowClicked(int row);

private:
    dsp::SpectrumEngine* engine_   = nullptr;
    ui::SpectrumWidget* spectrum_ = nullptr;
    ui::SkyView*     skyView_   = nullptr;
    ui::WorldView*   worldView_ = nullptr;

    // Satellite pass forecast (sky tab).
    dsp::TleClient* tleClient_   = nullptr;
    QTableWidget*   passTable_   = nullptr;
    QLabel*         tleBadge_    = nullptr;
    QLabel*         skyEmptyLabel_ = nullptr;
    ui::BookmarkManager* bookmarkManager_ = nullptr;
    QListWidget*    bmList_      = nullptr;
    // Band scanner state.
    QDoubleSpinBox* scanStartSpin_ = nullptr;
    QDoubleSpinBox* scanStopSpin_  = nullptr;
    QComboBox*      scanStepCombo_ = nullptr;
    QPushButton*    scanStartBtn_  = nullptr;
    QPushButton*    scanStopBtn_   = nullptr;
    QListWidget*    scanResultList_= nullptr;
    QTimer*         scanTimer_     = nullptr;
    double          scanFreq_      = 0.0;
    float           lastRssi_      = -200.0f;
    QList<dsp::SatPass> passes_;
    QTimer*         tleTimer_    = nullptr;
    bool            tleFetchActive_ = false;
    // Current station position (degrees), refreshed on startup and when the
    // settings dialog is accepted.
    double          stationLat_ = std::numeric_limits<double>::quiet_NaN();
    double          stationLon_ = std::numeric_limits<double>::quiet_NaN();
    bool            stationSet_ = false;
    QTimer*         liveTimer_   = nullptr;
    int             liveRow_     = -1;   // selected row tracked live, or -1
    QTabWidget* centerTabs_ = nullptr;

    // Left panel controls
    QDoubleSpinBox* freqSpin_   = nullptr;
    QComboBox*      stepCombo_  = nullptr;
    int             currentStepHz_ = 10000;   // tuning nudge / spinbox step
    QComboBox*      srCombo_    = nullptr;
    QSlider*        gainSlider_ = nullptr;
    QLabel*         gainValue_  = nullptr;
    QLabel*         sourceBanner_ = nullptr;
    QLabel*         statusLabel_ = nullptr;
    QPushButton*    connectBtn_ = nullptr;
    QComboBox*      srcTypeCombo_ = nullptr;
    QLineEdit*      tcpHostEdit_ = nullptr;
    QSpinBox*       tcpPortSpin_ = nullptr;
    QLabel*         rssiLabel_  = nullptr;

    // Collapsible advanced front-end options (RTL-SDR only; disabled w/o HW)
    QPushButton*    advToggle_   = nullptr;
    QWidget*        advPanel_    = nullptr;
    QComboBox*      dsCombo_     = nullptr;
    QCheckBox*      offsetChk_   = nullptr;
    QCheckBox*      rtlAgcChk_   = nullptr;
    QCheckBox*      tunerAgcChk_ = nullptr;
    QCheckBox*      biasTeeChk_  = nullptr;
    QDoubleSpinBox* ppmSpin_     = nullptr;

    QComboBox*      demodCombo_  = nullptr;
    QComboBox*      bwCombo_     = nullptr;
    double          currentBwHz_ = 12500.0;   // live RF bandwidth (Up/Down nudge)
    QSlider*        squelchSlider_ = nullptr;
    QCheckBox*      squelchCheck_ = nullptr;
    QLabel*         squelchValue_ = nullptr;
    QLabel*         levelLabel_   = nullptr;
    QLabel*         squelchState_ = nullptr;
    QPushButton*    recordBtn_   = nullptr;
    QCheckBox*      gatedCheck_   = nullptr;
    QLabel*         recStatus_   = nullptr;
    QComboBox*      recTargetCombo_ = nullptr;
    QLineEdit*      recTemplateEdit_ = nullptr;
    QCheckBox*      recStereoCheck_ = nullptr;
    QCheckBox*      recIgnoreSqlChk_ = nullptr;
    QLabel*         levelBar_   = nullptr;

    // Right tabs
    QTabWidget*     rightTabs_   = nullptr;
    QSplitter*      mainSplitter_ = nullptr;
    QPlainTextEdit* cwText_      = nullptr;
    QLabel*         cwWpm_       = nullptr;
    QLabel*         cwEmpty_     = nullptr;
    QTableWidget*   adsbTable_   = nullptr;
    QLabel*         adsbEmpty_   = nullptr;
    QMap<QString,int> adsbRow_;

    // Permanent status strip.
    QLabel*         sbMode_ = nullptr;
    QLabel*         sbSr_   = nullptr;
    QLabel*         sbVfo_  = nullptr;
    QLabel*         sbSdr_  = nullptr;
    QLabel*         sbRec_  = nullptr;

    // AI
    ai::Agent*      agent_       = nullptr;
    QPlainTextEdit* aiChat_      = nullptr;
    QLineEdit*      aiInput_     = nullptr;
    QLabel*         aiStatus_   = nullptr;

    void setControlsEnabled(bool hardwareConnected);
    void saveUiState();
    void restoreUiState();
    void fillPassTable();
    void refreshCountdowns();       // 1s: update the "距今" column
    void updateTleBadge();          // freshness label above the table
    void refetchTle();              // re-fetch TLE for the current station
    void updateLiveSatellite();     // 1s timer: propagate selected pass live

    // Debounced QSettings writer: high-frequency signals (zoom/pan, slider
    // drags, spinbox edits) call scheduleSave() which (re)arms this one-shot
    // timer; the actual disk write happens 500 ms after the last change.
    QTimer* saveTimer_ = nullptr;
};

} // namespace mbdsdr
