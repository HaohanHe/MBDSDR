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
// ui::TuneHistory is a value member (tuneHistory_); header-only value object.
#include "ui/tune_history.h"
// dsp::TleEntry is a value member (tleEntries_); include its header.
#include "dsp/tle_client.h"

class QLabel;
class QDoubleSpinBox;
class QComboBox;
class QSlider;
class QPushButton;
class QCheckBox;
class QFrame;
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

#include "dsp/doppler_control_surface.h"

namespace mbdsdr {

namespace ui   { class BookmarkManager; class ActivityLog; }
namespace dsp  { class SpectrumEngine; struct AircraftInfo; class TleClient; struct SatPass; struct TleEntry; struct VfoMarker; class FrequencyScanner; class SpyServerServer;
                 class DeviceLister; class IRtlDeviceEnumerator; class DevicePresenceNotifier;
                 class NetworkAudioSink; class ScanActivityLink; }
namespace ui   { class SpectrumWidget; class SkyView; class WorldView; class ConstellationView;
                 class ElevationPlot; struct AircraftPoint; class AircraftTracker;
                 class SMeterWidget;
                 class RssiTrendWidget;
                 class WeatherSatPanel; class TaskStepsView;
                 class PocsagPanel; class M17Panel; class VorPanel;
                 class DataTextPanel; }
namespace ai   { class Agent; class AiSessionStore; class TaskRunner; struct StepResult; struct TaskPlan; }
namespace gnss { class GnssReceiver; struct GnssFix; }
// Phase27: headless control layer + its loopback-only HTTP front-end. Both live
// on THIS (GUI) thread -- the thread that owns the SpectrumEngine -- so the hub
// dispatches engine calls directly (same invariant the rest of the UI uses).
namespace control { class ControlHub; class HttpControlServer; }

class MainWindow : public QMainWindow, public dsp::DopplerControlSurface {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override;

    // Phase55 block3: DopplerControlSurface implementation (live compensation
    // bridge to ControlHub / HTTP / Agent). The UI checkbox drives the real loop;
    // these are the programmatic mirrors. Precondition checks live in
    // onDopplerCompToggled, so a tool toggle refuses honestly without a station
    // / captured pass.
    void setDopplerCompensationEnabled(bool on) override;
    bool isDopplerCompensationEnabled() const override;
    bool isDopplerCompensationAvailable() const override;
    // Programmatic entry point (CLI / screenshot harness / embedding): the
    // engine behind the UI. GUI remains optional for every action.
    dsp::SpectrumEngine* engine() { return engine_; }
    // Test / harness accessors (read-only handles to the headless controller +
    // bookmark store). No radio is opened by these; they only expose what the UI
    // already owns so offscreen tests can drive scan/bookmark state deterministically.
    dsp::FrequencyScanner* scanner() { return scanner_; }
    // Phase62 orphan wiring: headless activity-scan bridge the UI now owns.
    dsp::ScanActivityLink* scanLink() { return scanLink_; }
    // Offscreen harness: run the raw-IQ export with an explicit duration seconds,
    // skipping the modal duration dialog. Returns the real engine result; the
    // honest success/failure line lands on the recording-library status label
    // (read via harnessRecLibStatus).
    bool harnessExportIq(double seconds) { return doRecLibExportIq(seconds); }
    QLabel* harnessRecLibStatus() const { return recLibPlayStatus_; }
    ui::BookmarkManager* bookmarkManager() { return bookmarkManager_; }
    // Phase27 harness accessors: the production loopback control-HTTP server. The
    // port is 0 when it failed to bind (honest -- the banner then explains why);
    // offscreen e2e tests read the real bound port and drive loopback clients at
    // it. No radio is opened by these; they only expose what the UI already owns.
    quint16 harnessControlHttpPort() const;
    bool     harnessControlHttpListening() const;
    QLabel*  harnessControlHttpBanner() const { return controlHttpBanner_; }
    // AI multi-session store (harness/screenshot drive it offscreen).
    ai::AiSessionStore* aiSessionStore() { return aiSessionStore_; }
    // Phase63: right-rail panel tabs are closable (SDR++ module show/hide,
    // lightweight equivalent). This re-shows every hidden panel; the tab-bar
    // context menu and the small "显示全部面板" button call the same entry.
    void showAllRightTabs();

    // ---- Phase32 block1: offscreen chat-lifecycle harness -------------------
    // CI has no LLM key, so the streaming signals (partialReady/responseReady)
    // can't be driven by a real network reply. These call the EXACT same handlers
    // the Agent signals are wired to, so the transient-replace / 固化去重 /
    // incomplete badge lifecycle is exercised end-to-end on the production
    // MainWindow without fabricating a reply. harnessAiBeginUserTurn does the
    // bookkeeping only (it does NOT invoke the worker), leaving the pending
    // "未完成" state observable.
    void harnessAiBeginUserTurn(const QString& text);   // append user line + mark pending
    void harnessAiSetPartial(const QString& acc);       // partialReady handler
    void harnessAiFinishResponse(const QString& text);  // responseReady handler (固化 once)
    QString harnessAiChatText() const;   // defined in .cpp (QPlainTextEdit complete there)
    // Offscreen: auto-confirm the destructive delete-session dialog so tests can
    // drive aiDeleteSessionBtn_ without a blocking QMessageBox. Production keeps
    // this false and always asks first.
    void harnessSetAutoConfirmSessionDelete(bool on) { aiAutoConfirmDelete_ = on; }
    // Harness/programmatic refresh (offscreen screenshots / embedding): re-sync
    // the scan button enables + state labels AND the bookmark table from the
    // current headless state. No radio is touched.
    void refreshScanBookmarksUi() { updateScanStatus(); refreshBmTable(); }
    // Harness/screenshot: rescan the recording library from engine_->recordingDir().
    void refreshRecordingLibrary() { refreshRecLib(); }

    // Offscreen shortcut-wiring harness accessors (read-only mirrors of the live
    // controls). The rewritten shortcuts test sends real QKeyEvents and asserts
    // these moved, instead of the previous QVERIFY(true) stub.
    int harnessStepIndex() const;
    int harnessGainDb()   const;

    // ---- Device-info / dynamic sample-rate combo harness accessors ---------
    // Read-only; no radio is touched. Lets offscreen tests + the screenshot
    // harness assert the real readback panel and the device-derived combo.
    QList<double> harnessSampleRateOptions() const;   // Hz values in srCombo_
    bool          harnessSampleRateEnabled() const;
    QString       harnessDeviceName() const;
    QString       harnessTunerRangeText() const;

    // ---- Phase 21 honest-empty-state / onboarding harness accessors --------
    // Read-only handles so the offscreen UI test can drive the source-type
    // combo, read the honest banner/status, and assert the data-dependent
    // controls + onboarding card without touching private members.
    QComboBox*  harnessSrcTypeCombo() const { return srcTypeCombo_; }
    QLabel*     harnessSourceBanner() const { return sourceBanner_; }
    QLabel*     harnessStatusLabel()  const { return statusLabel_; }
    QPushButton*harnessRecordBtn()    const { return recordBtn_; }
    QComboBox*  harnessDemodCombo()   const { return demodCombo_; }
    QComboBox*  harnessBwCombo()      const { return bwCombo_; }
    QLabel*     harnessSyntheticBanner() const { return syntheticBanner_; }
    // Receive-link state badge (Idle/Connecting/Running/Error). Read-only handle
    // so offscreen tests + the screenshot harness can assert the real four-state
    // text driven by the real engine signals.
    QLabel*     harnessConnStateBadge() const { return connStateBadge_; }
    // Test/screenshot seam: feed a real source signal into the SAME private slots
    // the engine's queued signals reach (production args originate in the engine,
    // never here). Drives the real state code path, not a fake widget state.
    void harnessSourceChanged(const QString& name, bool connected) { onSourceChanged(name, connected); }
    void harnessSourceError(const QString& message) { onSourceError(message); }
    void harnessSourceDropped() { onSourceDropped(); }
    // Display-only screenshot seam: hold the transient Connecting pill (the real
    // connect click sets it, then a blocking socket call runs on the UI thread,
    // so it can't be held in a live offscreen shot otherwise).
    void harnessShowConnecting() { setConnState(ConnState::Connecting); }
    bool        harnessGuideCardVisible() const;   // defined in .cpp (QFrame complete there)
    QPushButton*harnessGuideDismissBtn() const { return guideDismissBtn_; }

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

    // ---- Phase27: loopback control-HTTP production wiring -------------------
    // The headless ControlHub is the SINGLE command surface shared by the GUI,
    // the AI tool loop and this HTTP front-end; MainWindow owns it (parented to
    // this) and attaches the engine right after the engine is constructed. The
    // HttpControlServer is a thin loopback-only JSON front-end on top of it.
    // Both run on the GUI thread (the engine's home thread); there is NO worker
    // thread to join -- shutdown is an orderly stop() on this thread before the
    // engine is torn down. Bind failure is non-fatal: an honest amber banner
    // explains the port conflict and the rest of the app keeps working.
    mbdsdr::control::ControlHub*        controlHub_        = nullptr;
    mbdsdr::control::HttpControlServer*  httpControlServer_ = nullptr;
    QLabel*  controlHttpBanner_ = nullptr;   // top-bar chip (status / honest failure)
    void setupControlHttpServer();
    ui::SpectrumWidget* spectrum_ = nullptr;
    ui::SkyView*     skyView_   = nullptr;
    ui::WorldView*   worldView_ = nullptr;
    ui::ConstellationView* constellationView_ = nullptr;
    ui::ElevationPlot* elevationPlot_ = nullptr;
    ui::WeatherSatPanel* weatherPanel_ = nullptr;
    ui::PocsagPanel* pocsagPanel_ = nullptr;
    ui::M17Panel*    m17Panel_    = nullptr;
    ui::VorPanel*    vorPanel_    = nullptr;
    ui::DataTextPanel* dataTextPanel_ = nullptr;

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
    // LEO PNT geometry readout (prediction, not a fix) + S-meter widget.
    QLabel* geoLabel_ = nullptr;
    ui::SMeterWidget* sMeter_ = nullptr;
    ui::RssiTrendWidget* rssiTrend_ = nullptr;
    // TLE freshness panel (epoch/days/status/manual refresh).
    QLabel* tleFreshLabel_ = nullptr;
    QPushButton* refetchTleBtn_ = nullptr;
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
    // Receive-link connection state, rendered as a single compact badge next to
    // the connect button. Driven ONLY by the real engine signals / the real
    // connect click -- never a guess. Idle is the honest empty state; Running is
    // real hardware streaming; Error carries the REAL reason from sourceError.
    enum class ConnState { Idle, Connecting, Running, Error };
    void setConnState(ConnState s, const QString& detail = QString());
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
    // Phase62 orphan C: independent "活动扫描链" bridge (dwell->decode->record),
    // separate from the manual scanner_ above. Owns its 50 ms UI-thread ticker;
    // fed by the engine's REAL rssiDbfs(). The retune/dwell seams drive the real
    // engine centre + recorder; a quiet band arms nothing (honest).
    dsp::ScanActivityLink* scanLink_    = nullptr;
    QTimer*         scanLinkTimer_     = nullptr;
    QElapsedTimer*  scanLinkTickClock_ = nullptr;
    QCheckBox*      scanLinkChk_       = nullptr;
    QLabel*         scanLinkStateLabel_ = nullptr;
    bool            scanLinkRecording_ = false;   // link armed the recorder
    void onScanLinkToggled(bool on);
    void scanLinkTimerTick();
    void updateScanLinkStatus();
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

    // ---- 时空视图 tab (centerTabs_ 追加) ------------------------------------
    // 四格总览（设备/信号/解码/GNSS）+ 当前接收目标 + 时间源(gnss/system) +
    // 多普勒补偿状态。文案/颜色全部由 ui/spacetime_format.h 的纯函数产出；这里只
    // 持有 label 句柄 + 两个真实读数缓存。无硬件时一切走诚实空态（时间源=system、
    // GNSS=无 fix、多普勒=未补偿），绝不把 system 钟伪装成 GNSS 授时。
    QLabel* spDeviceTile_  = nullptr;
    QLabel* spSignalTile_   = nullptr;
    QLabel* spDecodeTile_   = nullptr;
    QLabel* spGnssTile_     = nullptr;
    QLabel* spTargetLine_   = nullptr;
    QLabel* spTimeLine_     = nullptr;
    QLabel* spDopplerLine_  = nullptr;
    // 真实源连接态缓存（来自 sourceChanged / sourceTelemetry）。云内无硬件 ->
    // false，驱动诚实空态。
    bool         lastSpConnected_   = false;
    QString      lastSpSourceName_;
    // 时空视图专用遥测读数缓存：NaN = 还没收到真实读数（驱动诚实 "--" 空态）。
    // 不复用 lastRssi_/lastSnr_：那俩默认 -200/0（供扫描门限 / S-meter 用），
    // 并非 NaN，直接接会在无硬件时把 "-200 dBFS" 渲染成一个假读数。
    float  lastSpRssi_ = std::numeric_limits<float>::quiet_NaN();
    float  lastSpSnr_  = std::numeric_limits<float>::quiet_NaN();
    // 实时多普勒补偿值（Hz）：仅当捕获过境 + 补偿开启 + 几何传播出 range-rate 时
    // 由 1Hz 循环写入真实 liveFd；新捕获 / 补偿开关 / 过境结束时复位 NaN（诚实空态）。
    double lastSpDopplerHz_ = std::numeric_limits<double>::quiet_NaN();
    void refreshSpacetimeView();   // pull real state -> pure formatters -> labels

    // Left panel controls
    QDoubleSpinBox* freqSpin_   = nullptr;
    QComboBox*      stepCombo_  = nullptr;
    QComboBox*      tuneHistCombo_ = nullptr;   // "最近" recent-frequency jump list
    int             currentStepHz_ = 10000;   // tuning nudge / spinbox step
    ui::TuneHistory tuneHistory_;             // real read-back centre-frequency history
    void            refreshTuneHistoryCombo();  // rebuild tuneHistCombo_ from tuneHistory_
    QComboBox*      srCombo_    = nullptr;
    QSlider*        gainSlider_ = nullptr;
    QComboBox*      gainCombo_  = nullptr;   // discrete step combo (real RTL gain table)
    QLabel*         gainValue_  = nullptr;
    QLabel*         sourceBanner_ = nullptr;
    QLabel*         statusLabel_ = nullptr;
    QPushButton*    connectBtn_ = nullptr;
    // Single four-state receive-link badge (see ConnState). Quiet pill; the long
    // reason text still lives in sourceBanner_.
    QLabel*         connStateBadge_ = nullptr;
    ConnState       connState_ = ConnState::Idle;
    QComboBox*      srcTypeCombo_ = nullptr;
    // Prominent "合成/调试" provenance pill. VISIBLE only while the engine feeds
    // the explicitly-opted-in synthetic TestSignalSource (engine_->isSynthetic()).
    // Honest labelling so a user never mistakes generated IQ for a live receiver.
    QLabel*         syntheticBanner_ = nullptr;
    QLineEdit*      tcpHostEdit_ = nullptr;
    QSpinBox*       tcpPortSpin_ = nullptr;
    QLabel*         rssiLabel_  = nullptr;

    // ---- First-run light onboarding card (Phase 21 honest empty state) ------
    // Shown only when QSettings has no "ui/onboardingDismissed" record (first ever
    // launch, no hardware yet). Steps: ①连接 RTL-SDR ②调谐频率 ③选解调模式. The
    // "去连接" entry drives the real connect button; the ✕ "不再提示" writes the
    // QSettings key permanently. Never blocking / never modal.
    QFrame*         guideCard_ = nullptr;
    QPushButton*    guideDismissBtn_ = nullptr;
    QPushButton*    guideConnectBtn_ = nullptr;
    void            buildGuideCard();
    void            dismissGuideCard();

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

    // ---- Hot-plug presence lister (enumeration diff -> UI notice) -----------
    // A 1 Hz UI timer polls the injectable enumerator; a device appearing /
    // disappearing flips a calm notice in sourceBanner_ / statusLabel_. Owns the
    // production lister + enumerator. The diff->text mapping lives in
    // dsp::DevicePresenceNotifier and is unit-tested offline; real physical
    // plug/unplug recovery (auto-open) is 「真机待验」.
    dsp::IRtlDeviceEnumerator* deviceEnumerator_   = nullptr;
    dsp::DeviceLister*         deviceLister_       = nullptr;
    dsp::DevicePresenceNotifier* presenceNotifier_ = nullptr;
    QTimer*                    devicePollTimer_    = nullptr;
    void showPresenceNotice(const QString& text, bool appeared);

    // ---- Discrete gain control model ----------------------------------------
    // Rebuild gainCombo_ / gainSlider_ visibility + enable from the engine's real
    // gain table (empty = honest continuous slider). Called on source swap + the
    // ~1 Hz telemetry tick.
    void refreshGainControl();
    QString gainTableKey_;   // change detector for the combo rows

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

    // Phase62 orphan B: demodulated 48k PCM -> UDP/TCP network tap. The UI owns
    // the lifecycle (start/stop on THIS thread per the sink contract) and hands
    // the sink to the engine as a PARALLEL write tap (setNetworkAudioSink). The
    // borrowed raw pointer is only for honest status read-back; ownership stays
    // with the engine tap (nullptr detaches + destroys it).
    QLineEdit*      netAudioHostEdit_    = nullptr;
    QSpinBox*       netAudioPortSpin_    = nullptr;
    QComboBox*      netAudioProtoCombo_  = nullptr;
    QPushButton*    netAudioStartBtn_    = nullptr;
    QPushButton*    netAudioStopBtn_    = nullptr;
    QLabel*         netAudioStatusLabel_ = nullptr;
    dsp::NetworkAudioSink* netAudioRaw_  = nullptr;
    QTimer*         netAudioTimer_       = nullptr;  // 1 s stats refresh while streaming
    void onNetAudioStart();
    void onNetAudioStop();
    void updateNetAudioStatus();

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
    QPushButton*    vfoArmBtn_    = nullptr;   // toggle parallel demod (armed)
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
    QPushButton*    recLibAnalyzeBtn_ = nullptr;   // 该行 -> 离线流式分析
    QPushButton*    recLibExportBtn_  = nullptr;   // 解码器输出 -> .txt 文件
    QPushButton*    recLibIqBtn_      = nullptr;   // Phase62: 实时基带 IQ 段 -> SigMF
    QVector<mbdsdr::ui::RecordingEntry> recLibEntries_;
    std::vector<float> recLibPcm_;                 // decoded 48k mono float buffer
    qint64          recLibPcmPos_   = 0;           // playback cursor (samples)
    QTimer*         recLibPlayTimer_ = nullptr;    // ~20 ms chunked writer
    bool            recLibPlaying_  = false;
    void refreshRecLib();                          // rescan engine_->recordingDir()
    void onRecLibPlayToggle();                     // load+play / stop
    void onRecLibDelete();                         // confirm -> delete real files
    void onRecLibCopyPath();                       // copy selected row's full path
    void onRecLibAnalyze();                        // 选中行 -> engine 离线分析
    void onRecLibExportDecode();                   // 解码器输出文本 -> .txt
    // Phase62 orphan A: one-shot raw-IQ segment dump (SigMF pair) -> recording dir.
    void onRecLibExportIq();                       // modal duration dialog -> doRecLibExportIq
    bool doRecLibExportIq(double seconds);         // the real export (harness-friendly)

    // ---- Offline file analysis (streaming through the real DSP chain) ------
    // Opens a captured file (WAV/SigMF/raw) as the engine source; the SAME
    // spectrum/waterfall/channelizer/demod chain processes the file in ~25 ms
    // chunks. Pause / seek move the REAL file offset; offscreen has no audio
    // device, so the demod output is shown honestly (no fake playback).
    QLabel*         offAnaInfo_  = nullptr;        // path + 格式/参数
    QPushButton*    offAnaOpenBtn_   = nullptr;
    QPushButton*    offAnaPauseBtn_  = nullptr;
    QSlider*        offAnaSeek_  = nullptr;       // 0..1000
    QLabel*         offAnaPos_    = nullptr;      // 0.0s / 12.3s
    QTimer*         offAnaTimer_  = nullptr;      // poll engine offlinePosition
    bool            offAnaPaused_ = false;
    void openOfflinePath(const QString& path);    // engine->openOfflineFile + UI


    // Right tabs
    QTabWidget*     rightTabs_   = nullptr;
    QSplitter*      mainSplitter_ = nullptr;
    // Phase63: flat restore button, shown only while >=1 right-rail panel tab
    // is hidden (the tab-bar context menu offers the same action).
    QPushButton*    rightTabRestoreBtn_ = nullptr;

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
    QLabel*         sbAudio_ = nullptr;   // sound-card link health (声卡正常/欠载/断开…)
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
    // Autonomous multi-step task: process-observable step list + async runner.
    ui::TaskStepsView* aiTaskSteps_ = nullptr;
    QLabel*         aiTaskHint_  = nullptr;
    QComboBox*      aiTemplateCombo_ = nullptr;  // which deterministic task to run
    QDoubleSpinBox* aiParamLowHz_ = nullptr;      // sweep band low (Hz)
    QDoubleSpinBox* aiParamHighHz_ = nullptr;     // sweep band high / target (Hz)
    QComboBox*      aiParamMode_ = nullptr;       // demod mode override
    QDoubleSpinBox* aiParamLat_  = nullptr;       // station latitude (satellite pass)
    QDoubleSpinBox* aiParamLon_  = nullptr;       // station longitude
    QLineEdit*      aiParamSatName_ = nullptr;     // satellite name (substring match)
    QPushButton*    aiRunTaskBtn_ = nullptr;
    QPushButton*    aiStopTaskBtn_ = nullptr;
    ai::TaskRunner* aiRunner_ = nullptr;
    QList<mbdsdr::ai::StepResult> aiLiveSteps_;   // accumulated live steps (UI thread)
    void startRunnerPlan(const mbdsdr::ai::TaskPlan& plan);  // hand a plan to the worker

    // AGC + automatic signal activity log (separate data from bookmarks).
    QCheckBox*      aiAgcCheck_ = nullptr;
    QPlainTextEdit* aiActivityView_ = nullptr;
    ui::ActivityLog* activityLog_ = nullptr;
    void refreshActivityView();

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
    void onRunAutoTask();              // run a deterministic autonomous task template

    // Shared send bookkeeping (the 发送 button AND the offscreen harness both use
    // it): append the user line (which auto-clears any prior 未完成), raise the
    // pending-stream incomplete flag, and show the single "思考中…" transient.
    // Does NOT invoke the worker -- the caller does that.
    void aiBeginUserTurn(const QString& text);
    // The two worker-observation handlers (connected to Agent::partialReady /
    // ::responseReady). Extracted so the offscreen harness drives the SAME code.
    void aiOnPartialReady(const QString& accumulated);
    void aiOnResponseReady(const QString& finalText);
    // Offscreen seam: skip the modal delete confirm (see the public setter).
    bool aiAutoConfirmDelete_ = false;

    // hw = a real hardware device (RTL-SDR / rtl_tcp) is live -> gate tuner /
    // sample-rate / gain / advanced front-end controls. hasData = ANY live IQ
    // producer (real HW, the explicitly-opted-in synthetic test source, or an
    // opened offline capture file) -> gate demod / record / decode controls.
    // The honest empty NullSource yields hasData()==false and everything that
    // needs a sample stream is disabled (never a fake-but-idle control).
    void setControlsEnabled(bool hw, bool hasData);
    void saveUiState();
    void restoreUiState();
    // Phase63: closable right-rail panel tabs. onRightTabCloseRequested hides
    // (never removes) the tab -- indexes stay stable so decoder/bookmark
    // wiring by index is untouched; the last visible panel cannot be closed.
    void onRightTabCloseRequested(int idx);
    void updateRightTabRestoreAffordance();   // restore button iff >=1 tab hidden
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
    // Visual row -> BookmarkManager store index. Section header rows carry -1
    // and the honest empty-state row carries -2 (never bookable); bookmark data
    // rows carry their list() index via Qt::UserRole, so the grouped layout does
    // not drift the store-indexed edit/delete/double-click wiring.
    int bmStoreIndexAtVisualRow(int visualRow) const;
    void scanTimerTick();           // 50 ms tick into the FrequencyScanner state machine
    void updateScanStatus();        // refresh scan labels + sbScan_ + button enables

    // Debounced QSettings writer: high-frequency signals (zoom/pan, slider
    // drags, spinbox edits) call scheduleSave() which (re)arms this one-shot
    // timer; the actual disk write happens 500 ms after the last change.
    QTimer* saveTimer_ = nullptr;
};

} // namespace mbdsdr
