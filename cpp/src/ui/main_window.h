// SPDX-License-Identifier: MIT
#pragma once

#include <QMainWindow>
#include <QList>
#include <limits>

// GnssFix is a value member (lastGnssFix_), so its layout must be visible here.
#include "gnss/gnss_types.h"

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
class QElapsedTimer;
class QListWidget;
class QListWidgetItem;
class QLineEdit;
class QSpinBox;

namespace mbdsdr {
namespace ui { class BookmarkManager; }
namespace dsp  { class SpectrumEngine; struct AircraftInfo; class TleClient; struct SatPass; struct VfoMarker; class FrequencyScanner; }
namespace ui   { class SpectrumWidget; class SkyView; class WorldView; class ConstellationView;
                 class ElevationPlot; struct AircraftPoint; class AircraftTracker;
                 class WeatherSatPanel; }
namespace ai   { class Agent; }
namespace gnss { class GnssReceiver; struct GnssFix; }

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override;

private slots:
    void onSourceChanged(const QString& name, bool connected);
    void onAudioLevel(float dbfs);
    void onRssiLevel(float dbfs);
    void onSnrLevel(float snrDb);
    // ~1 Hz hardware-readback refresh of the permanent status strip.
    void onSourceTelemetry(const QString& name, bool connected,
                           double centerHz, double sampleRateHz, double gainDb);
    void onSquelchState(bool open);
    void onRecordingState(bool recording, const QString& path);
    void onRecordingProgress(const QString& path, int seconds, qint64 bytes);
    void onRecordClicked();
    void onCwDecoded(const QString& text, double wpm);
    void onAdsbAircraft(const dsp::AircraftInfo& info);
    // RDS status strip: locked=true shows "RDS: <PS> · PTY <n> · <RT>";
    // locked=false clears the label to empty (never a placeholder station).
    void onRdsUpdated(const QString& programService, int pty,
                      const QString& radioText, bool locked);
    // WFM stereo badge: driven ONLY by the engine's real recovered pilot.
    // stereo=true -> "立体声" (success green), otherwise "单声道" (secondary).
    void onStereoState(bool stereo, float blend, float pilotQuality);
    void saveSettings();   // immediate persistence (writes QSettings)
    void scheduleSave();   // debounced persistence: arms the 500 ms save timer
    void onPassesReady(QList<dsp::SatPass> passes);
    void onTleFetchFailed(const QString& reason);
    void onPassRowClicked(int row);
    // ---- GNSS serial receiver ----
    void onNewFix(gnss::GnssFix fix);
    void onGnssConnectionChanged(bool connected, QString description);
    void onGnssConnectClicked();
    // ---- bidirectional satellite selection sync (no re-emit loop) ----
    void selectSatelliteByName(const QString& name);
    void copyClockBias();

private:
    dsp::SpectrumEngine* engine_   = nullptr;
    ui::SpectrumWidget* spectrum_ = nullptr;
    ui::SkyView*     skyView_   = nullptr;
    ui::WorldView*   worldView_ = nullptr;
    ui::ConstellationView* constellationView_ = nullptr;
    ui::ElevationPlot* elevationPlot_ = nullptr;
    ui::WeatherSatPanel* weatherPanel_ = nullptr;

    // ---- GNSS serial receiver + compact toolbar (world tab) ----
    gnss::GnssReceiver* gnssRx_      = nullptr;
    QLineEdit*   gnssDeviceEdit_  = nullptr;
    QComboBox*   gnssBaudCombo_   = nullptr;
    QPushButton* gnssConnectBtn_   = nullptr;
    QLabel*      gnssStatusLabel_ = nullptr;
    QLabel*      gnssFixLabel_     = nullptr;
    QCheckBox*   layerGnssChk_     = nullptr;
    QCheckBox*   layerAdsbChk_     = nullptr;
    QCheckBox*   layerSatChk_      = nullptr;
    // Sky-tab clock-bias readout + copy button.
    QLabel*      clockInfoLabel_ = nullptr;
    QPushButton* copyClockBtn_   = nullptr;
    // ADS-B aircraft tracker: merges decoded frames keyed by ICAO, applies TTL
    // expiry, and emits only real-position points to the map. Lives on the UI
    // thread; fed by onAdsbAircraft(), pruned by adsbTimer_ every second.
    ui::AircraftTracker* adsbTracker_ = nullptr;
    QTimer*             adsbTimer_  = nullptr;
    gnss::GnssFix lastGnssFix_;
    bool   gnssHasFix_ = false;
    double lastGnssAppliedLat_ = std::numeric_limits<double>::quiet_NaN();
    double lastGnssAppliedLon_ = std::numeric_limits<double>::quiet_NaN();
    double clockBiasSec_ = 0.0;

    // Satellite pass forecast (sky tab).
    dsp::TleClient* tleClient_   = nullptr;
    QTableWidget*   passTable_   = nullptr;
    QLabel*         tleBadge_    = nullptr;
    QLabel*         skyEmptyLabel_ = nullptr;
    ui::BookmarkManager* bookmarkManager_ = nullptr;
    // ---- SDR++-style scan / bookmark panel (right "书签" tab) ----
    // Headless state machine + the QTimer that drives it.  Hits come only from
    // the REAL engine RSSI (lastRssi_); the scanner fabricates nothing.
    dsp::FrequencyScanner* scanner_     = nullptr;
    QTimer*         scanTimer_         = nullptr;
    QElapsedTimer*  scanTickClock_     = nullptr;  // per-tick elapsed ms clock
    float           lastRssi_          = -200.0f;
    // Scan-control group "频率扫描".
    QDoubleSpinBox* scanStartSpin_      = nullptr;  // MHz
    QDoubleSpinBox* scanStopSpin_       = nullptr;  // MHz
    QComboBox*      scanStepCombo_      = nullptr;  // 10k/12.5k/100k/1M
    QSpinBox*       scanDwellSpin_      = nullptr;  // ms per step
    QDoubleSpinBox* scanThrSpin_        = nullptr;  // dBFS hit gate
    QComboBox*      scanDirCombo_       = nullptr;  // Up/Down/PingPong
    QComboBox*      scanHoldCombo_      = nullptr;  // UntilSignalGone/FixedMs
    QSpinBox*       scanLingerSpin_     = nullptr;  // ms (UntilSignalGone)
    QSpinBox*       scanHoldMsSpin_     = nullptr;  // ms (FixedMs)
    QCheckBox*      scanBmOnlyChk_     = nullptr;  // scan bookmark freqs only
    QPushButton*    scanStartBtn_       = nullptr;
    QPushButton*    scanPauseBtn_      = nullptr;  // pause / resume (same btn)
    QPushButton*    scanStopBtn_        = nullptr;
    QLabel*         scanFreqLabel_      = nullptr;  // monoInfo current freq
    QLabel*         scanStateLabel_     = nullptr;  // idle/scanning/hit text
    // Bookmark table group "频率书签".
    QTableWidget*   bmTable_            = nullptr;
    QPushButton*    bmAddBtn_           = nullptr;
    QPushButton*    bmEditBtn_          = nullptr;
    QPushButton*    bmDelBtn_           = nullptr;
    float           lastSnr_       = 0.0f;
    QList<dsp::SatPass> passes_;
    QTimer*         tleTimer_    = nullptr;
    bool            tleFetchActive_ = false;
    // True while the table is showing the built-in offline TLE snapshot (no
    // network, no fresh cache).  The badge must say so honestly.
    bool            usingBuiltinTle_ = false;
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
    QLabel*         channelBadge_ = nullptr;   // 立体声/单声道, from real pilot
    QCheckBox*      forceMonoCheck_ = nullptr;  // WFM-only, forces mono
    double          currentBwHz_ = 12500.0;   // live RF bandwidth (Up/Down nudge)

    // Multi-VFO management panel (left rail).
    QListWidget*    vfoList_     = nullptr;
    QPushButton*    vfoAddBtn_   = nullptr;
    QPushButton*    vfoDelBtn_   = nullptr;
    QVector<mbdsdr::dsp::VfoMarker> vfoMarkers_;  // last snapshot from engine
    void refreshVfoUi();                          // rebuild list + push band boxes
    QSlider*        squelchSlider_ = nullptr;
    QCheckBox*      squelchCheck_ = nullptr;
    QLabel*         squelchValue_ = nullptr;
    QLabel*         levelLabel_   = nullptr;
    QLabel*         squelchState_ = nullptr;
    QCheckBox*      anrCheck_   = nullptr;
    QSlider*        anrSlider_  = nullptr;
    QLabel*         anrValue_   = nullptr;
    QPushButton*    recordBtn_   = nullptr;
    QCheckBox*      gatedCheck_   = nullptr;
    // Unattended signal-triggered watch recording.
    QCheckBox*      watchCheck_ = nullptr;
    QSlider*        watchThrSlider_ = nullptr;
    QLabel*         watchThrValue_ = nullptr;
    QLabel*         watchLevel_ = nullptr;
    QDoubleSpinBox* watchPrerollSpin_ = nullptr;
    QDoubleSpinBox* watchHangSpin_ = nullptr;
    QLabel*         watchStatus_ = nullptr;
    QWidget*        watchForm_ = nullptr;
    // User-configurable recording directory.
    QLineEdit*      recDirEdit_ = nullptr;
    QPushButton*    recDirBrowseBtn_ = nullptr;
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
    QLabel*         sbRds_  = nullptr;   // RDS PS/PTY/RadioText; empty until real data
    QLabel*         sbSdr_  = nullptr;
    QLabel*         sbGain_ = nullptr;
    QLabel*         sbWatch_ = nullptr;
    QLabel*         sbScan_  = nullptr;   // scan / hit status (permanent strip)
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
    void updateElevationPlotFor(const dsp::SatPass& p); // el-vs-time samples
    void updateClockBiasLabel();    // 1s: GNSS/system/local time + bias
    void onAdsbPrune();             // 1s: TTL-expire aircraft, refresh table/map
    void refreshAdsbTable();        // rebuild ADS-B table + map + empty state

    // Scan / bookmark panel.
    void refreshBmTable();          // refill bmTable_ from bookmarkManager_->list()
    void scanTimerTick();           // 50 ms tick into the FrequencyScanner state machine
    void updateScanStatus();        // refresh scan labels + sbScan_ + button enables

    // Debounced QSettings writer: high-frequency signals (zoom/pan, slider
    // drags, spinbox edits) call scheduleSave() which (re)arms this one-shot
    // timer; the actual disk write happens 500 ms after the last change.
    QTimer* saveTimer_ = nullptr;
};

} // namespace mbdsdr
