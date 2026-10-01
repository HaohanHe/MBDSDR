// SPDX-License-Identifier: MIT
#pragma once

#include <QMainWindow>
#include <QList>
#include <limits>

// GnssFix is a value member (lastGnssFix_), so its layout must be visible here.
#include "gnss/gnss_types.h"
// DopplerStepLimiter is a value member (small, header-only convergence throttle).
#include "core/sat_capture.h"
// RecordingEntry is a value member (recLibEntries_); the struct must be complete.
#include "ui/recording_library.h"
// dsp::TleEntry is a value member (tleEntries_); include its header.
#include "dsp/tle_client.h"

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
class QEvent;
class QPropertyAnimation;
class QListWidget;
class QListWidgetItem;
class QLineEdit;
class QSpinBox;

namespace mbdsdr {
namespace ui { class BookmarkManager; }
namespace dsp  { class SpectrumEngine; struct AircraftInfo; class TleClient; struct SatPass; struct TleEntry; struct VfoMarker; class FrequencyScanner; class SpyServerServer; }
namespace ui   { class SpectrumWidget; class SkyView; class WorldView; class ConstellationView;
                 class ElevationPlot; struct AircraftPoint; class AircraftTracker;
                 class WeatherSatPanel; }
namespace ai   { class Agent; class AiSessionStore; }
namespace gnss { class GnssReceiver; struct GnssFix; }

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override;
    // Programmatic entry point (CLI / screenshot harness / embedding): the
    // engine behind the UI. GUI remains optional for every action.
    dsp::SpectrumEngine* engine() { return engine_; }
    // Test / harness accessors (read-only handles to the headless controller +
    // bookmark store). No radio is opened by these; they only expose what the UI
    // already owns so offscreen tests can drive scan/bookmark state deterministically.
    dsp::FrequencyScanner* scanner() { return scanner_; }
    ui::BookmarkManager* bookmarkManager() { return bookmarkManager_; }
    // AI multi-session store (harness/screenshot drive it offscreen).
    ai::AiSessionStore* aiSessionStore() { return aiSessionStore_; }
    // Harness/programmatic refresh (offscreen screenshots / embedding): re-sync
    // the scan button enables + state labels AND the bookmark table from the
    // current headless state. No radio is touched.
    void refreshScanBookmarksUi() { updateScanStatus(); refreshBmTable(); }
    // Harness/screenshot: rescan the recording library from engine_->recordingDir().
    void refreshRecordingLibrary() { refreshRecLib(); }

    // ---- Device-info / dynamic sample-rate combo harness accessors ---------
    // Read-only; no radio is touched. Lets offscreen tests + the screenshot
    // harness assert the real readback panel and the device-derived combo.
    QList<double> harnessSampleRateOptions() const;   // Hz values in srCombo_
    bool          harnessSampleRateEnabled() const;
    QString       harnessDeviceName() const;
    QString       harnessTunerRangeText() const;

private slots:
    void onSourceChanged(const QString& name, bool connected);
    void onSourceDropped();
    void onSourceError(const QString& message);
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
    // ---- sky "now / preview" time scrubber ------------------------------
    void onSkySliderChanged(int offsetMin);   // throttled: coalesce -> refreshSkyAt(preview)
    void onSkySliderReleased();                // back to live, restore slider to center
    void recomputePreview();                   // the throttled propagation at previewUtc_

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
    // Timing-service three-state chip (已定位授时/授时无定位/无 GNSS 授时).
    QLabel*      timingStateLabel_ = nullptr;
    // Visible-navigation-satellites panel (TLE/SGP4 PREDICTION, not received).
    QTableWidget* navSatTable_ = nullptr;
    QList<dsp::TleEntry> tleEntries_;   // raw TLEs backing the nav-sat filter
    // ADS-B aircraft tracker: merges decoded frames keyed by ICAO, applies TTL
    // expiry, and emits only real-position points to the map. Lives on the UI
    // thread; fed by onAdsbAircraft(), pruned by adsbTimer_ every second.
    ui::AircraftTracker* adsbTracker_ = nullptr;
    QTimer*             adsbTimer_  = nullptr;
    gnss::GnssFix lastGnssFix_;
    bool   gnssHasFix_ = false;
    // Hotplug event flags: while set, the next sourceChanged(false) (which is
    // the engine's fallback report right after a drop / failed connect) must
    // not overwrite the drop / error banner. Cleared on any reconnect or by a
    // manual source change (which does not set the flags).
    bool   hotplugDropped_   = false;
    bool   connectErrorShown_ = false;
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
    QPushButton*    scanSaveBmBtn_      = nullptr;  // Hit-state one-shot: save hit as bookmark
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
    // Sky time scrubber: center = live wall-now; dragging offsets the displayed
    // UTC moment and re-propagates all visible satellites with the REAL SGP4
    // propagator (throttled to <= kSkyPreviewThrottleMs). Release returns to live.
    QSlider*        skyTimeSlider_ = nullptr;
    QTimer*         skyPreviewTimer_ = nullptr;  // drag-coalesce (throttle)
    bool            previewMode_ = false;        // scrubber active (not live)
    QDateTime       previewUtc_;                 // the moment being previewed
    int             pendingPreviewOffsetMin_ = 0; // latest scrubber offset, coalesced
    QTabWidget* centerTabs_ = nullptr;

    // ---- One-tap pass capture + Doppler auto-compensation (sky tab) ----
    // capturePassBtn_ retunes the active VFO to the selected pass' downlink;
    // dopplerCompChk_ (default off) then nudges that VFO every 1 s from the
    // real propagated range-rate.  capturedIdx_ pins the pass we have actually
    // captured (-1 = none); the limiter smooths the 1 Hz retune.
    QPushButton*    capturePassBtn_      = nullptr;
    QCheckBox*      dopplerCompChk_     = nullptr;
    QLabel*         captureStatusLabel_  = nullptr;
    int             capturedIdx_        = -1;  // passes_ index locked for capture/-comp
    core::DopplerStepLimiter dopplerLimiter_{};
    void onCapturePassClicked();          // retune active VFO to the selected pass
    void onDopplerCompToggled(bool on);  // arm/disarm live Doppler tracking
    void updateCaptureControls();         // enable/disable + status line from selection

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

    // ---- Device info panel (real readback; honest empty state) -----------
    // Device name / tuning range / sample-rate range come from
    // engine_->sourceCapabilities() (rtl_tcp RTL0 handshake or the honest
    // no-device state). Never fabricated; ranges show "--" when unknown.
    QLabel*         devNameLabel_ = nullptr;
    QLabel*         devTunerRangeLabel_ = nullptr;
    QLabel*         devSrRangeLabel_    = nullptr;
    QLabel*         devProvenanceLabel_ = nullptr;
    void            refreshDeviceCapabilities();     // re-read engine caps -> labels + dynamic srCombo_
    QString         capsKey_;                        // change detector (1 Hz telemetry must not rebuild the combo)

    // ---- SpyServer remote-IQ server (SDR++/Airspy wire protocol) ----------
    // Off by default; binding is attempted only when the box is checked. The
    // server streams the engine's REAL IQ (rtl_tcp source, or the honestly
    // labelled offline test signal) -- never fabricated frames.
    dsp::SpyServerServer* spyServer_ = nullptr;
    QCheckBox*      spyserverChk_       = nullptr;
    QSpinBox*       spyserverPortSpin_   = nullptr;
    QLabel*         spyserverStatusLabel_ = nullptr;  // 监听端口/客户端数
    void onSpyServerToggled(bool on);                // start/stop the listener
    void updateSpyServerStatus();                    // refresh the status line

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
    QPushButton*    vfoCopyBtn_   = nullptr;   // copy active/selected VFO's params
    QVector<mbdsdr::dsp::VfoMarker> vfoMarkers_;  // last snapshot from engine
    // User-assigned display names keyed by engine VFO id. Empty by default and
    // persisted as JSON under QSettings key "ui/vfoNames". Never pre-seeded.
    QMap<int,QString> vfoNames_;
    void refreshVfoUi();                          // rebuild list + push band boxes
    // Copy the source (active, else selected row) VFO's freq/mode/bw into a brand
    // new VFO via the real engine vfoAdd/vfoSet*/vfoSetBandwidth API.
    void vfoCopyUi();
    // Double-click a row: explicitly switch the active VFO to that row's id.
    // (Single-click row activation already exists; this supplements it.)
    void onVfoItemDoubleClicked(QListWidgetItem* it);
    // Inline rename committed (itemChanged): persist to vfoNames_ + QSettings.
    void onVfoItemEdited(QListWidgetItem* it);
    // Build the list-row display string from a real marker + the optional user name.
    QString vfoRowText(const mbdsdr::dsp::VfoMarker& m) const;
    void loadVfoNames();                          // read ui/vfoNames JSON (default empty)
    QSlider*        squelchSlider_ = nullptr;
    QCheckBox*      squelchCheck_ = nullptr;
    QPushButton*    squelchAutoBtn_ = nullptr;  // checkable: auto gate = audio NF + margin
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

    // ---- 录制库 panel (right tab): scans the REAL recording directory ----
    // Lists actual .wav captures (+ their sidecar .json proof). Empty directory
    // -> honest "暂无录音" empty state. Playback decodes real 16-bit PCM and
    // streams it into the existing AudioOutput; headless/offscreen degrades
    // gracefully (no device -> samples dropped, status reported honestly).
    QListWidget*    recLibList_     = nullptr;
    QLabel*         recLibEmpty_    = nullptr;   // "暂无录音" placeholder
    QLabel*         recLibWatchState_ = nullptr;  // 值守: 等待/监听中/录制中
    QLabel*         recLibWatchLevel_ = nullptr;  // 电平 X dBFS · 门限 Y
    QLabel*         recLibPlayStatus_ = nullptr;  // 已加载/播放中/不支持格式/错误
    QPushButton*    recLibRefreshBtn_ = nullptr;
    QPushButton*    recLibCopyBtn_   = nullptr;
    QPushButton*    recLibDelBtn_    = nullptr;
    QPushButton*    recLibPlayBtn_   = nullptr;   // load/play|stop toggle
    QVector<mbdsdr::ui::RecordingEntry> recLibEntries_;
    std::vector<float> recLibPcm_;                 // decoded 48k mono float buffer
    qint64          recLibPcmPos_   = 0;           // playback cursor (samples)
    QTimer*         recLibPlayTimer_ = nullptr;    // ~20 ms chunked writer
    bool            recLibPlaying_  = false;
    void refreshRecLib();                          // rescan engine_->recordingDir()
    void onRecLibPlayToggle();                     // load+play / stop
    void onRecLibDelete();                         // confirm -> delete real files
    void onRecLibCopyPath();                       // copy selected row's full path

    // Right tabs
    QTabWidget*     rightTabs_   = nullptr;
    QSplitter*      mainSplitter_ = nullptr;

    // Focus mode state: remembered natural widths of the two side rails (in
    // splitter pixels) captured when collapsing, so toggling back restores the
    // user's preferred proportions instead of a hard-coded guess.
    QPushButton*    focusBtn_    = nullptr;
    bool            focusMode_   = false;
    int             leftRailW_   = 0;
    int             rightRailW_  = 0;
    QPropertyAnimation* focusAnimL_ = nullptr;
    QPropertyAnimation* focusAnimR_ = nullptr;
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
    // B5: extra one-line readouts, ALL from real engine readback signals.
    // "--" until the first real value arrives; the GNSS field stays empty until
    // a genuine fix (never a fabricated position).
    QLabel*         sbRssi_  = nullptr;   // RSSI dBFS (rssiLevel)
    QLabel*         sbSnr_   = nullptr;   // SNR dB (snrLevel)
    QLabel*         sbSquelch_ = nullptr; // OPEN / CLOSED / OFF (squelchState + checkbox)
    QLabel*         sbGnss_  = nullptr;   // "GNSS 定位" once a real fix lands; else empty
    bool            squelchOn_ = false;    // mirrors 启用静噪 checkbox (for OFF state)
    // Cached real engine readback (from sourceTelemetry) so the SpyServer
    // handshake DEVICE_INFO reports the honest live sample rate / gain, not a
    // hard-coded guess.
    double          lastSampleRateHz_ = 2.4e6;
    double          lastGainDb_ = 0.0;

    // AI
    ai::Agent*      agent_       = nullptr;
    ai::AiSessionStore* aiSessionStore_ = nullptr;
    QCheckBox*      aiManualCheck_ = nullptr;   // manual mode: AI suggests, never writes
    QPlainTextEdit* aiChat_      = nullptr;
    QLineEdit*      aiInput_     = nullptr;
    QLabel*         aiStatus_   = nullptr;
    QComboBox*      aiSessionCombo_ = nullptr;  // session switcher
    QPushButton*    aiNewSessionBtn_ = nullptr;
    QPushButton*    aiRenameSessionBtn_ = nullptr;
    QPushButton*    aiDeleteSessionBtn_ = nullptr;
    QPushButton*    aiCompactCtxBtn_ = nullptr;

    // Chat rendering model: persisted session messages + a single transient
    // line. partialReady() overwrites the transient (never appends);
    // responseReady() clears it and appends the final assistant message.
    QString aiTransient_;            // current partial / "思考中…" text
    QStringList aiToolNotes_;        // tool-call annotations shown for this turn
    QString aiCurSessionId_;

    void aiRenderChat();              // rebuild aiChat_ from store + notes + transient
    void aiRefreshSessionCombo();
    void onAiNewSession();
    void onAiRenameSession();
    void onAiDeleteSession();
    void onAiSessionChanged(int idx);
    void onAiCompactContext();

    void setControlsEnabled(bool hardwareConnected);
    void saveUiState();
    void restoreUiState();
    // Focus mode (CarWith driving-mode analog): collapse the left scroll rail
    // and the right tab rail to width 0 so the spectrum takes the full width.
    // animate=false applies the state instantly (used at startup restore).
    void setFocusMode(bool on, bool animate);
    // Double-click on a splitter handle restores the 0.22/0.56/0.22 ratios.
    void resetSplitterRatios();
    bool eventFilter(QObject* obj, QEvent* event) override;
    void fillPassTable();
    void refreshCountdowns();       // 1s: update the "距今" column
    void updateTleBadge();          // freshness label above the table
    void refetchTle();              // re-fetch TLE for the current station
    void updateLiveSatellite();     // 1s timer: propagate selected pass live
    void updateElevationPlotFor(const dsp::SatPass& p); // el-vs-time samples
    void updateClockBiasLabel();    // 1s: GNSS/system/local time + bias
    void refreshNavSatellites();    // 1s: TLE-predicted visible GNSS sats (预测)
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
