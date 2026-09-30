// SPDX-License-Identifier: MIT
#include "main_window.h"
#include "spectrum_widget.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QSplitter>
#include <QFrame>
#include <QLabel>
#include <QPushButton>
#include <QStatusBar>
#include <QDoubleSpinBox>
#include <QComboBox>
#include <QSlider>
#include <QCheckBox>
#include <QGroupBox>
#include <QTabWidget>
#include <QGraphicsOpacityEffect>
#include <QPropertyAnimation>
#include <QPlainTextEdit>
#include <QTableWidget>
#include <QHeaderView>
#include <QLineEdit>
#include <QStackedWidget>
#include <QSettings>
#include <QShortcut>
#include <QScrollArea>
#include <QEvent>
#include <QDateTime>
#include <QTimer>
#include <QDialog>
#include <QFontMetrics>
#include <QFormLayout>
#include <QDir>
#include <QFileInfo>
#include <QUrl>
#include <QDesktopServices>
#include <QMediaDevices>
#include <QAudioDevice>
#include <QListWidget>
#include <QListWidgetItem>
#include <QScroller>
#include <QMap>
#include <QFileDialog>
#include <QJsonDocument>
#include <QJsonObject>
#include <QElapsedTimer>
#include <QDialogButtonBox>
#include <QMessageBox>
#include <QGuiApplication>
#include <QClipboard>

#include "dsp/vfo_manager.h"
#include "ui/constellation_view.h"

#include <cmath>
#include <algorithm>

#include "core/tokens.h"
#include "core/spectrum_frame.h"
#include "core/bandwidth_preset.h"
#include "dsp/spectrum_engine.h"
#include "dsp/spyserver_server.h"
#include "dsp/adsb_decoder.h"
#include "dsp/tle_client.h"
#include "ai/agent.h"
#include "ai/ai_config.h"
#include "ai/ai_session_store.h"
#include "ai/ai_context.h"
#include "ui/sky_view.h"
#include "ui/bookmark_manager.h"
#include "dsp/frequency_scanner.h"
#include "ui/shortcuts_dialog.h"
#include <QListWidget>
#include <QLineEdit>
#include <QSpinBox>
#include <QApplication>
#include <QClipboard>
#include <QInputDialog>
#include "ui/world_view.h"
#include "ui/aircraft_tracker.h"
#include "ui/elevation_plot.h"
#include "ui/weather_panel.h"
#include "gnss/gnss_receiver.h"
#include "gnss/gnss_types.h"
#include "ui/spectrum_widget.h"
#include "ui/settings_dialog.h"
#include "ui/about_dialog.h"
#include "ui/radio_panel.h"

namespace mbdsdr {

// Recording-library core types live in the ui sub-namespace.
using ui::RecordingLibrary;
using ui::RecordingEntry;
using ui::WavProbe;

namespace {
// Tuning step combo (index -> Hz). Must stay in sync with the items added in
// the frequency group: 1 Hz / 10 Hz / 100 Hz / 1 kHz / 10 kHz / 100 kHz / 1 MHz.
constexpr int kStepValuesHz[] = {1, 10, 100, 1000, 10000, 100000, 1000000};
constexpr int kStepCount = sizeof(kStepValuesHz) / sizeof(kStepValuesHz[0]);

// Bandwidth presets shown in bwCombo_, in display order. EVERY mode default
// from core::defaultBandwidthHzForMode has a matching entry so the combo can
// honestly represent NFM/WFM/AM/USB/LSB/CW/BPSK/QPSK/ADS-B (including the wide
// 2 MHz ADS-B channel) instead of snapping a 2 MHz channel to the old 200 kHz
// entry. Single source for the combo handler, the canvas band-edge snap, the
// keyboard nudge and settings restore -- no scattered kBws arrays.
constexpr double kBwComboPresetsHz[] = {
    500.0, 2400.0, 9000.0, 12000.0, 12500.0, 200000.0, 2000000.0
};
constexpr int    kBwComboPresetCount =
    sizeof(kBwComboPresetsHz) / sizeof(kBwComboPresetsHz[0]);
// Bandwidth presets indexed by bwCombo_ order.
// Up/Down keyboard nudge doubles/halves the *current* bandwidth and clamps to
// [1k, 2M]; we then snap bwCombo_ to the nearest preset.
constexpr double kBwMinHz = 100.0;
constexpr double kBwMaxHz = 2000000.0;
// Index of the combo preset nearest to `hz` (used to keep the combo display
// consistent with the live bandwidth after a mode switch / nudge / VFO drag).
inline int nearestBwPresetIndex(double hz) {
    int best = 0; double bd = 1e18;
    for (int i = 0; i < kBwComboPresetCount; ++i) {
        double d = std::abs(kBwComboPresetsHz[i] - hz);
        if (d < bd) { bd = d; best = i; }
    }
    return best;
}
} // namespace

MainWindow::MainWindow(QWidget* parent)
    : QMainWindow(parent)
{
        setWindowTitle("MBDSDR");
    resize(tokens::scaled(1280), tokens::scaled(800));
    setStyleSheet(tokens::buildDarkQss());

    // Engine must exist before UI construction: many widgets connect their
    // signals directly to engine slots while the panels are being built.
    engine_ = new dsp::SpectrumEngine(this);

    // ---- Top bar (real elements only) ----
    auto* topBar = new QFrame;
    topBar->setObjectName("topBar");
    topBar->setFixedHeight(tokens::scaled(tokens::kTopbarH));
    auto* topLay = new QHBoxLayout(topBar);
    topLay->setContentsMargins(tokens::scaled(16), 0, tokens::scaled(16), 0);

    auto* titleLabel = new QLabel("MBDSDR", topBar);
    QFont tf = titleLabel->font();
    tf.setBold(true);
    titleLabel->setFont(tf);
    topLay->addWidget(titleLabel);

    statusLabel_ = new QLabel("● Test Signal", topBar);
    topLay->addWidget(statusLabel_);
    topLay->addStretch();

    auto* clockLabel = new QLabel(topBar);
    clockLabel->setObjectName("monoInfo");
    auto* clockTimer = new QTimer(this);
    connect(clockTimer, &QTimer::timeout, clockLabel, [clockLabel]() {
        clockLabel->setText(QDateTime::currentDateTimeUtc().toString("HH:mm:ss UTC"));
    });
    clockTimer->start(1000);
    clockLabel->setText(QDateTime::currentDateTimeUtc().toString("HH:mm:ss UTC"));
    topLay->addWidget(clockLabel);

    // Focus-mode toggle (CarWith driving-mode analog): collapses both side rails
    // so the spectrum fills the width. Generic QPushButton QSS applies; the
    // active state is painted accent-colored by setFocusMode() only on this btn.
    focusBtn_ = new QPushButton("◉ 专注", topBar);
    focusBtn_->setObjectName("focusBtn");
    focusBtn_->setCheckable(true);
    focusBtn_->setToolTip("专注模式：隐藏侧栏，频谱占满全宽");
    topLay->addWidget(focusBtn_);

    auto* helpBtn = new QPushButton("?", topBar);
    helpBtn->setToolTip("快捷键");
    auto* aboutBtn = new QPushButton("关于", topBar);
    auto* settingsBtn = new QPushButton("⚙", topBar);
    topLay->addWidget(helpBtn);
    topLay->addWidget(aboutBtn);
    topLay->addWidget(settingsBtn);

    // ---- Central splitter (3 columns) ----
    auto* central = new QWidget;
    auto* centralLay = new QVBoxLayout(central);
    centralLay->setContentsMargins(0, 0, 0, 0);
    centralLay->setSpacing(0);
    centralLay->addWidget(topBar);

    auto* splitter = new QSplitter(Qt::Horizontal);
    mainSplitter_ = splitter;
    splitter->setChildrenCollapsible(false);

    // ---- Left panel: scrollable controls ----
    auto* leftScroll = new QScrollArea;
    leftScroll->setWidgetResizable(true);
    leftScroll->setFrameShape(QFrame::NoFrame);
    leftScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    leftScroll->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
    // Touch: flick-to-scroll on the control rail (mouse wheel keeps working).
    QScroller::grabGesture(leftScroll->viewport(), QScroller::TouchGesture);
    auto* leftCard = new QFrame;
    leftCard->setObjectName("panelCard");
    auto* leftLay = new QVBoxLayout(leftCard);
    leftLay->setContentsMargins(tokens::scaled(12), tokens::scaled(12), tokens::scaled(12), tokens::scaled(12));
    leftLay->setSpacing(tokens::scaled(8));

    auto* gSrc = new QGroupBox("源与连接", leftCard);
    auto* gSrcLay = new QVBoxLayout(gSrc);
    sourceBanner_ = new QLabel("RTL-SDR 未连接，使用测试信号", gSrc);
    sourceBanner_->setObjectName("dockHint");
    sourceBanner_->setWordWrap(true);
    gSrcLay->addWidget(sourceBanner_);

    // Source type selector: local RTL-SDR vs rtl_tcp remote.
    srcTypeCombo_ = new QComboBox(gSrc);
    srcTypeCombo_->addItems({"本地 RTL-SDR", "rtl_tcp 远程"});
    gSrcLay->addWidget(srcTypeCombo_);
    tcpHostEdit_ = new QLineEdit("127.0.0.1", gSrc);
    tcpHostEdit_->setPlaceholderText("host");
    gSrcLay->addWidget(tcpHostEdit_);
    tcpPortSpin_ = new QSpinBox(gSrc);
    tcpPortSpin_->setRange(1, 65535);
    tcpPortSpin_->setValue(1234);
    gSrcLay->addWidget(tcpPortSpin_);
    // host/port only relevant for rtl_tcp mode.
    tcpHostEdit_->setVisible(false);
    tcpPortSpin_->setVisible(false);
    connect(srcTypeCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
        const bool tcp = (idx == 1);
        tcpHostEdit_->setVisible(tcp);
        tcpPortSpin_->setVisible(tcp);
    });

    connectBtn_ = new QPushButton("连接", gSrc);
    gSrcLay->addWidget(connectBtn_);
    rssiLabel_ = new QLabel("RSSI: -- dBFS", gSrc);
    gSrcLay->addWidget(rssiLabel_);

    // ---- Collapsible advanced RF front-end options (RTL-SDR only) ----
    advToggle_ = new QPushButton("高级 ▶", gSrc);
    gSrcLay->addWidget(advToggle_);
    advPanel_ = new QWidget(gSrc);
    auto* advLay = new QFormLayout(advPanel_);
    dsCombo_ = new QComboBox(advPanel_);
    dsCombo_->addItems({"关闭", "I 支路", "Q 支路"});
    advLay->addRow("直采", dsCombo_);
    offsetChk_ = new QCheckBox("偏移调谐", advPanel_);
    advLay->addRow(offsetChk_);
    rtlAgcChk_ = new QCheckBox("RTL AGC", advPanel_);
    advLay->addRow(rtlAgcChk_);
    tunerAgcChk_ = new QCheckBox("Tuner AGC", advPanel_);
    advLay->addRow(tunerAgcChk_);
    biasTeeChk_ = new QCheckBox("Bias-T", advPanel_);
    advLay->addRow(biasTeeChk_);
    ppmSpin_ = new QDoubleSpinBox(advPanel_);
    ppmSpin_->setRange(tokens::kPpmMin, tokens::kPpmMax);
    ppmSpin_->setSingleStep(tokens::kPpmStep);
    ppmSpin_->setSuffix(" ppm");
    advLay->addRow("PPM", ppmSpin_);
    advPanel_->setVisible(false);
    gSrcLay->addWidget(advPanel_);
    connect(advToggle_, &QPushButton::clicked, this, [this]() {
        const bool show = !advPanel_->isVisible();
        advPanel_->setVisible(show);
        advToggle_->setText(show ? "高级 ▼" : "高级 ▶");
    });
    leftLay->addWidget(gSrc);

    // ---- SpyServer 远程 IQ 服务 (SDR++/Airspy 协议) ----
    // A TCP listener that streams the engine's real IQ to a remote SDR++ /
    // SDRangel client. Default OFF so it never grabs :5555 by accident. The
    // port is persisted (net/spyPort); the enable state (net/spyEnabled) too,
    // but the server is only (re)started from the checkbox toggle.
    {
        auto* gSpy = new QGroupBox("SpyServer 远程", leftCard);
        auto* gSpyLay = new QVBoxLayout(gSpy);
        spyserverChk_ = new QCheckBox("开启 SpyServer", gSpy);
        spyserverChk_->setObjectName("spyserverChk");
        gSpyLay->addWidget(spyserverChk_);

        auto* portRow = new QHBoxLayout;
        auto* portLbl = new QLabel("端口", gSpy);
        spyserverPortSpin_ = new QSpinBox(gSpy);
        spyserverPortSpin_->setObjectName("spyserverPortSpin");
        spyserverPortSpin_->setRange(1024, 65535);
        spyserverPortSpin_->setValue(5555);   // SpyServer default (note §2)
        spyserverPortSpin_->setSuffix("");
        portRow->addWidget(portLbl);
        portRow->addWidget(spyserverPortSpin_);
        gSpyLay->addLayout(portRow);

        spyserverStatusLabel_ = new QLabel("未开启", gSpy);
        spyserverStatusLabel_->setObjectName("spyserverStatusLabel");
        gSpyLay->addWidget(spyserverStatusLabel_);

        leftLay->addWidget(gSpy);

        // Build the server once; it starts/stops on the checkbox.
        spyServer_ = new dsp::SpyServerServer(this);
        // Wire client-tunable parameters straight to the real engine slots.
        dsp::SpyServerTuner tuner;
        tuner.setCenterFreq = [this](double hz) {
            QMetaObject::invokeMethod(engine_, [this, hz]() {
                engine_->onSetCenterFreq(hz);
            }, Qt::QueuedConnection);
        };
        tuner.setGain = [this](double db) {
            QMetaObject::invokeMethod(engine_, [this, db]() {
                engine_->onSetGain(db);
            }, Qt::QueuedConnection);
        };
        tuner.queryInfo = [this](double& maxSr, double& minHz, double& maxHz,
                                 double& centerHz, double& gainDb) {
            // Honest live readback: sample rate / gain from the last engine
            // telemetry (cached in onSourceTelemetry), frequency from the engine.
            maxSr = lastSampleRateHz_;
            minHz = tokens::kFreqMinHz;
            maxHz = tokens::kFreqMaxHz;
            centerHz = engine_->centerFreq();
            gainDb = lastGainDb_;
        };
        spyServer_->setTuner(tuner);
        // Push the engine's real IQ blocks to the server only while streaming.
        connect(engine_, &dsp::SpectrumEngine::iqTapReady,
                spyServer_, &dsp::SpyServerServer::feedIQ);
        connect(spyServer_, &dsp::SpyServerServer::iqTapRequired,
                engine_, &dsp::SpectrumEngine::setSpyServerTapRequested);
        connect(spyServer_, &dsp::SpyServerServer::clientCountChanged,
                this, [this](int) { updateSpyServerStatus(); });
        connect(spyServer_, &dsp::SpyServerServer::listeningChanged,
                this, [this](bool, quint16) { updateSpyServerStatus(); });
        connect(spyserverChk_, &QCheckBox::toggled,
                this, &MainWindow::onSpyServerToggled);
        connect(spyserverPortSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
                this, [this](int v) {
            QSettings("MBDSDR", "MBDSDR").setValue("net/spyPort", v);
            scheduleSave();
        });
    }

    auto* gFreq = new QGroupBox("频率", leftCard);
    auto* gFreqLay = new QFormLayout(gFreq);
    freqSpin_ = new QDoubleSpinBox(gFreq);
    freqSpin_->setObjectName("freqSpin");
    freqSpin_->setRange(tokens::kFreqMinHz / 1e6, tokens::kFreqMaxHz / 1e6);
    freqSpin_->setValue(98.5);
    freqSpin_->setDecimals(3);
    freqSpin_->setSuffix(" MHz");
    freqSpin_->setMinimumWidth(tokens::scaled(140));
    gFreqLay->addRow("中心频率", freqSpin_);
    stepCombo_ = new QComboBox(gFreq);
    stepCombo_->addItems({"1 Hz", "10 Hz", "100 Hz", "1 kHz",
                          "10 kHz", "100 kHz", "1 MHz"});
    stepCombo_->setCurrentIndex(4);   // 10 kHz default
    stepCombo_->setMinimumWidth(tokens::scaled(120));
    gFreqLay->addRow("步进", stepCombo_);
    leftLay->addWidget(gFreq);

    auto* gRx = new QGroupBox("接收参数", leftCard);
    auto* gRxLay = new QFormLayout(gRx);
    srCombo_ = new QComboBox(gRx);
    srCombo_->addItems({"1.024 MS/s", "2.048 MS/s", "2.4 MS/s", "3.2 MS/s"});
    srCombo_->setCurrentIndex(2);
    srCombo_->setMinimumWidth(tokens::scaled(120));
    gRxLay->addRow("采样率", srCombo_);
    gainSlider_ = new QSlider(Qt::Horizontal, gRx);
    gainSlider_->setRange(0, 50);
    gainValue_ = new QLabel("0 dB", gRx);
    auto* gainRow = new QHBoxLayout;
    gainRow->addWidget(gainSlider_);
    gainRow->addWidget(gainValue_);
    gRxLay->addRow("增益", gainRow);
    demodCombo_ = new QComboBox(gRx);
    demodCombo_->setObjectName("demodCombo");
    demodCombo_->addItems({"AM", "NFM", "WFM", "USB", "LSB", "CW", "BPSK", "QPSK", "ADS-B"});
    demodCombo_->setMinimumWidth(tokens::scaled(120));
    gRxLay->addRow("解调", demodCombo_);
    bwCombo_ = new QComboBox(gRx);
    bwCombo_->setObjectName("bwCombo");
    // Build the item list from the shared preset table so the combo order and
    // the kBwComboPresetsHz array can never drift apart.
    for (int i = 0; i < kBwComboPresetCount; ++i) {
        const double hz = kBwComboPresetsHz[i];
        QString lbl;
        if (hz >= 1000000.0)      lbl = QString::number(hz / 1e6) + " MHz";
        else if (hz >= 1000.0)    lbl = QString::number(hz / 1000.0) + " kHz";
        else                       lbl = QString::number(hz, 'f', 0) + " Hz";
        bwCombo_->addItem(lbl);
    }
    bwCombo_->setMinimumWidth(tokens::scaled(120));
    gRxLay->addRow("带宽", bwCombo_);
    // Channel-status badge: "立体声" only when the engine's real 19 kHz pilot is
    // locked and the L/R matrix has engaged; otherwise honestly "单声道". Fully
    // driven by onStereoState() -- never fabricated.
    channelBadge_ = new QLabel("单声道", gRx);
    channelBadge_->setObjectName("channelBadge");
    channelBadge_->setAlignment(Qt::AlignCenter);
    channelBadge_->setStyleSheet(
        QString("QLabel#channelBadge { color: %1; }").arg(QString::fromUtf8(tokens::kTextSecondary)));
    gRxLay->addRow("声道", channelBadge_);
    // Force-mono: only meaningful / enabled on WFM. Toggling forwards to the
    // engine which pins the stereo blend to 0.
    forceMonoCheck_ = new QCheckBox("强制单声道", gRx);
    forceMonoCheck_->setObjectName("forceMonoCheck");
    forceMonoCheck_->setEnabled(false);
    gRxLay->addRow("", forceMonoCheck_);
    leftLay->addWidget(gRx);

    // ---- Multi-VFO panel ---------------------------------------------------
    auto* gVfo = new QGroupBox("多 VFO", leftCard);
    gVfo->setObjectName("vfoGroup");
    auto* gVfoLay = new QVBoxLayout(gVfo);
    vfoList_ = new QListWidget(gVfo);
    vfoList_->setObjectName("vfoList");
    vfoList_->setMinimumHeight(tokens::scaled(tokens::kVfoListMinH));
    vfoList_->setMaximumHeight(tokens::scaled(tokens::kVfoListMaxH));
    vfoList_->setSelectionMode(QAbstractItemView::SingleSelection);
    // Inline rename: each row is editable, but double-click ALSO activates.
    vfoList_->setEditTriggers(QAbstractItemView::DoubleClicked |
                              QAbstractItemView::EditKeyPressed);
    // Touch: flick-scrolling on the list itself (mouse wheel keeps working).
    vfoList_->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
    QScroller::grabGesture(vfoList_->viewport(), QScroller::TouchGesture);
    gVfoLay->addWidget(vfoList_);
    auto* vfoBtnRow = new QHBoxLayout;
    vfoAddBtn_ = new QPushButton("＋ 添加 VFO", gVfo);
    vfoCopyBtn_ = new QPushButton("复制", gVfo);
    vfoDelBtn_ = new QPushButton("－ 删除", gVfo);
    vfoAddBtn_->setObjectName("vfoAddBtn");
    vfoCopyBtn_->setObjectName("vfoCopyBtn");
    vfoDelBtn_->setObjectName("vfoDelBtn");
    // Text-only small buttons: enforce the named 44px touch minimum (scaled).
    for (QPushButton* b : {vfoAddBtn_, vfoCopyBtn_, vfoDelBtn_})
        b->setMinimumHeight(tokens::scaled(tokens::kTouchMin));
    vfoBtnRow->addWidget(vfoAddBtn_);
    vfoBtnRow->addWidget(vfoCopyBtn_);
    vfoBtnRow->addWidget(vfoDelBtn_);
    gVfoLay->addLayout(vfoBtnRow);
    leftLay->addWidget(gVfo);

    auto* gSql = new QGroupBox("静噪", leftCard);
    gSql->setObjectName("squelchGroup");
    auto* gSqlLay = new QVBoxLayout(gSql);
    squelchCheck_ = new QCheckBox("启用静噪", gSql);
    squelchCheck_->setObjectName("squelchCheck");
    gSqlLay->addWidget(squelchCheck_);
    auto* sqlRow = new QHBoxLayout;
    squelchSlider_ = new QSlider(Qt::Horizontal, gSql);
    squelchSlider_->setObjectName("squelchSlider");
    // Range / default come from the audio-RMS-domain squelch tokens (no bare
    // numbers): more-negative = more sensitive.
    squelchSlider_->setRange(tokens::kSquelchMinDb, tokens::kSquelchMaxDb);
    squelchSlider_->setValue(tokens::kSquelchDefaultDb);
    squelchValue_ = new QLabel(
        QString("%1 dB").arg(tokens::kSquelchDefaultDb), gSql);
    squelchValue_->setObjectName("squelchValue");
    sqlRow->addWidget(squelchSlider_);
    sqlRow->addWidget(squelchValue_);
    gSqlLay->addLayout(sqlRow);
    // "Auto gate": checkable. While on, the threshold follows the REAL tracked
    // audio-RMS noise floor + margin (tokens::kSquelchAutoMarginDb). The floor
    // comes from the engine in the SAME dBFS domain as the threshold -- never the
    // IQ total-power / per-bin canvas floor.
    squelchAutoBtn_ = new QPushButton("自动门限", gSql);
    squelchAutoBtn_->setObjectName("squelchAutoBtn");
    squelchAutoBtn_->setCheckable(true);
    squelchAutoBtn_->setToolTip(
        QString("门限 = 实测音频噪声底 + %1 dB（同域）").arg(tokens::kSquelchAutoMarginDb));
    gSqlLay->addWidget(squelchAutoBtn_);
    squelchState_ = new QLabel("状态: CLOSED", gSql);
    squelchState_->setObjectName("squelchState");
    gSqlLay->addWidget(squelchState_);
    leftLay->addWidget(gSql);

    // ---- ANR (audio noise reduction) group ----
    auto* gAnr = new QGroupBox("音频降噪 (ANR)", leftCard);
    auto* gAnrLay = new QVBoxLayout(gAnr);
    anrCheck_ = new QCheckBox("启用 ANR", gAnr);
    anrCheck_->setObjectName("anrCheck");
    gAnrLay->addWidget(anrCheck_);
    auto* anrRow = new QHBoxLayout;
    anrSlider_ = new QSlider(Qt::Horizontal, gAnr);
    anrSlider_->setRange(0, 100);
    anrSlider_->setValue(50);
    anrSlider_->setObjectName("anrSlider");
    anrValue_ = new QLabel("50%", gAnr);
    anrRow->addWidget(anrSlider_);
    anrRow->addWidget(anrValue_);
    gAnrLay->addLayout(anrRow);
    leftLay->addWidget(gAnr);

    auto* gAud = new QGroupBox("音频", leftCard);
    auto* gAudLay = new QVBoxLayout(gAud);
    auto* nbChk = new QCheckBox("噪声抑制 (Noise Blanker)", gAud);
    connect(nbChk, &QCheckBox::toggled,
            engine_, &dsp::SpectrumEngine::setNoiseBlanker);
    gAudLay->addWidget(nbChk);
    levelLabel_ = new QLabel("电平: -- dBFS", gAud);
    gAudLay->addWidget(levelLabel_);
    levelBar_ = new QLabel("", gAud);
    levelBar_->setFixedHeight(tokens::scaled(16));
    gAudLay->addWidget(levelBar_);
    leftLay->addWidget(gAud);

    auto* gRec = new QGroupBox("录制", leftCard);
    auto* gRecLay = new QVBoxLayout(gRec);

    // User-configurable output directory (default program dir "record").
    auto* recDirForm = new QFormLayout;
    auto* recDirRow = new QWidget(gRec);
    auto* recDirLay = new QHBoxLayout(recDirRow);
    recDirLay->setContentsMargins(0, 0, 0, 0);
    recDirEdit_ = new QLineEdit("record", recDirRow);
    recDirEdit_->setMinimumWidth(tokens::scaled(80));
    recDirBrowseBtn_ = new QPushButton("浏览…", recDirRow);
    recDirLay->addWidget(recDirEdit_);
    recDirLay->addWidget(recDirBrowseBtn_);
    recDirForm->addRow("录制目录", recDirRow);
    gRecLay->addLayout(recDirForm);

    auto* recForm = new QFormLayout;
    recTargetCombo_ = new QComboBox(gRec);
    recTargetCombo_->addItems({"基带 IQ (SigMF)", "解调音频 (WAV)"});
    recTargetCombo_->setMinimumWidth(tokens::scaled(tokens::kRecComboMinW));
    recForm->addRow("录制对象", recTargetCombo_);
    recTemplateEdit_ = new QLineEdit("{time}_{freq}_{mode}", gRec);
    recTemplateEdit_->setMinimumWidth(tokens::scaled(tokens::kRecTemplateMinW));
    recTemplateEdit_->setToolTip("文件名占位符: {time}=时间戳, {freq}=MHz, {mode}=解调模式");
    recForm->addRow("文件名模板", recTemplateEdit_);
    gRecLay->addLayout(recForm);
    recStereoCheck_ = new QCheckBox("立体声 (音频)", gRec);
    recStereoCheck_->setToolTip("仅对解调音频 (WAV) 录制有效");
    gRecLay->addWidget(recStereoCheck_);
    recIgnoreSqlChk_ = new QCheckBox("忽略静噪 (持续录制)", gRec);
    gRecLay->addWidget(recIgnoreSqlChk_);
    gatedCheck_ = new QCheckBox("触发式分段录制", gRec);
    gRecLay->addWidget(gatedCheck_);

    // ---- Unattended signal-triggered watch recording ----
    watchCheck_ = new QCheckBox("值守录制（信号触发）", gRec);
    watchCheck_->setToolTip(
        "持续监听 RSSI，仅当真实信号超过触发门限时才开始录制（带前滚）；"
        "信号消失并经过结束延时后自动停止并落盘，每段一个文件");
    gRecLay->addWidget(watchCheck_);

    watchForm_ = new QWidget(gRec);
    auto* watchLay = new QVBoxLayout(watchForm_);
    watchLay->setContentsMargins(0, 0, 0, 0);
    auto* watchFormLay = new QFormLayout;
    auto* thrRowW = new QWidget(watchForm_);
    auto* thrLay = new QHBoxLayout(thrRowW);
    thrLay->setContentsMargins(0, 0, 0, 0);
    watchThrSlider_ = new QSlider(Qt::Horizontal, thrRowW);
    watchThrSlider_->setRange(-100, -20);
    watchThrSlider_->setValue(-50);
    watchThrValue_ = new QLabel("-50 dB", thrRowW);
    thrLay->addWidget(watchThrSlider_);
    thrLay->addWidget(watchThrValue_);
    watchFormLay->addRow("触发门限", thrRowW);
    watchLevel_ = new QLabel("电平: -- dBFS", watchForm_);
    watchLevel_->setObjectName("dockHint");
    watchFormLay->addRow("", watchLevel_);
    watchPrerollSpin_ = new QDoubleSpinBox(watchForm_);
    watchPrerollSpin_->setRange(0.1, 2.0);
    watchPrerollSpin_->setSingleStep(0.05);
    watchPrerollSpin_->setDecimals(2);
    watchPrerollSpin_->setSuffix(" s");
    watchPrerollSpin_->setValue(0.4);
    watchFormLay->addRow("前滚", watchPrerollSpin_);
    watchHangSpin_ = new QDoubleSpinBox(watchForm_);
    watchHangSpin_->setRange(0.3, 10.0);
    watchHangSpin_->setSingleStep(0.1);
    watchHangSpin_->setDecimals(1);
    watchHangSpin_->setSuffix(" s");
    watchHangSpin_->setValue(1.5);
    watchFormLay->addRow("结束延时", watchHangSpin_);
    watchLay->addLayout(watchFormLay);
    watchStatus_ = new QLabel("值守: 关", watchForm_);
    watchStatus_->setWordWrap(true);
    watchLay->addWidget(watchStatus_);
    watchForm_->setEnabled(false);
    gRecLay->addWidget(watchForm_);

    auto* openRecDirBtn = new QPushButton("打开录制目录", gRec);
    openRecDirBtn->setToolTip("在系统文件管理器中打开当前录制目录");
    gRecLay->addWidget(openRecDirBtn);
    recordBtn_ = new QPushButton("● 录制", gRec);
    gRecLay->addWidget(recordBtn_);
    recStatus_ = new QLabel("空闲", gRec);
    recStatus_->setWordWrap(true);
    gRecLay->addWidget(recStatus_);
    leftLay->addWidget(gRec);

    leftLay->addStretch();
    leftScroll->setWidget(leftCard);
    splitter->addWidget(leftScroll);

    // ---- Center: stacked spectrum / world ----
    auto* centerCard = new QFrame;
    centerCard->setObjectName("panelCard");
    auto* centerLay = new QVBoxLayout(centerCard);
    centerLay->setContentsMargins(0, 0, 0, 0);
    centerTabs_ = new QTabWidget(centerCard);
    spectrum_ = new ui::SpectrumWidget(centerCard);

    // World tab: a compact GNSS/serial toolbar on top, the offline map below.
    // Real data only: the receiver point appears only after a valid fix; with
    // no fix the map keeps the hand-entered station and states "GNSS 无定位".
    adsbTracker_ = new ui::AircraftTracker();
    auto* worldPage = new QWidget(centerCard);
    auto* worldPageLay = new QVBoxLayout(worldPage);
    worldPageLay->setContentsMargins(0, 0, 0, 0);
    worldPageLay->setSpacing(0);

    auto* gnssBar = new QFrame(worldPage);
    gnssBar->setObjectName("panelCard");
    auto* gnssBarLay = new QHBoxLayout(gnssBar);
    gnssBarLay->setContentsMargins(tokens::scaled(8), tokens::scaled(3),
                                  tokens::scaled(8), tokens::scaled(3));
    gnssBarLay->setSpacing(tokens::scaled(6));
    gnssBarLay->addWidget(new QLabel("GNSS 串口", gnssBar));
    gnssDeviceEdit_ = new QLineEdit(gnssBar);
    gnssDeviceEdit_->setPlaceholderText("/dev/ttyUSB0");
    gnssDeviceEdit_->setToolTip("NMEA 0183 串口设备路径（如 /dev/ttyUSB0）");
    gnssBarLay->addWidget(gnssDeviceEdit_, 1);
    gnssBaudCombo_ = new QComboBox(gnssBar);
    gnssBaudCombo_->addItems({"9600", "38400", "115200"});
    gnssBarLay->addWidget(gnssBaudCombo_);
    gnssConnectBtn_ = new QPushButton("连接", gnssBar);
    gnssBarLay->addWidget(gnssConnectBtn_);
    gnssStatusLabel_ = new QLabel("未连接", gnssBar);
    gnssBarLay->addWidget(gnssStatusLabel_);
    gnssBarLay->addSpacing(tokens::scaled(8));
    layerGnssChk_ = new QCheckBox("GNSS", gnssBar);  layerGnssChk_->setChecked(true);
    layerAdsbChk_ = new QCheckBox("ADS-B", gnssBar); layerAdsbChk_->setChecked(true);
    layerSatChk_  = new QCheckBox("卫星", gnssBar);  layerSatChk_->setChecked(true);
    gnssBarLay->addWidget(layerGnssChk_);
    gnssBarLay->addWidget(layerAdsbChk_);
    gnssBarLay->addWidget(layerSatChk_);
    gnssBarLay->addStretch();
    gnssFixLabel_ = new QLabel("GNSS 无定位", gnssBar);
    gnssFixLabel_->setObjectName("monoInfo");
    gnssBarLay->addWidget(gnssFixLabel_);
    worldPageLay->addWidget(gnssBar);

    worldView_ = new ui::WorldView(worldPage);
    worldPageLay->addWidget(worldView_, 1);

    // Spectrum tab: one unified canvas draws the line spectrum, the shared
    // frequency strip and the scrolling waterfall with a single geometry, so
    // the frequency axes align by construction. The scroll-speed / palette
    // controls live in the container's tool strip.
    centerTabs_->addTab(spectrum_, "频谱");
    centerTabs_->addTab(worldPage, "世界");

    // Weather-satellite (NOAA APT) tab: the decoded image is wide (1818 px),
    // so it lives in the center stack alongside the spectrum / map rather than
    // the narrow right rail. Fed by engine aptImageReady (queued).
    {
        auto* weatherPage = new QWidget;
        auto* weatherLay = new QVBoxLayout(weatherPage);
        weatherLay->setContentsMargins(0, 0, 0, 0);
        weatherPanel_ = new ui::WeatherSatPanel(weatherPage);
        weatherLay->addWidget(weatherPanel_);
        centerTabs_->addTab(weatherPage, "气象");
    }

    centerLay->addWidget(centerTabs_);
    splitter->addWidget(centerCard);

    // ---- Right panel: tabs ----
    auto* rightCard = new QFrame;
    rightCard->setObjectName("panelCard");
    auto* rightLay = new QVBoxLayout(rightCard);
    rightTabs_ = new QTabWidget(rightCard);
    rightTabs_->setObjectName("rightTabs");
    rightTabs_->setUsesScrollButtons(true);
    rightTabs_->setElideMode(Qt::ElideRight);

    auto* cwPage = new QWidget;
    auto* cwLay = new QVBoxLayout(cwPage);
    auto* cwTop = new QHBoxLayout;
    cwWpm_ = new QLabel("WPM: --", cwPage);
    cwTop->addWidget(cwWpm_);
    cwTop->addStretch();
    auto* cwClear = new QPushButton("清空", cwPage);
    connect(cwClear, &QPushButton::clicked, this, [this]() {
        cwText_->clear();
    });
    cwTop->addWidget(cwClear);
    cwLay->addLayout(cwTop);
    cwEmpty_ = new QLabel("切换到 CW 模式开始解码", cwPage);
    cwEmpty_->setObjectName("statusHint");
    cwEmpty_->setAlignment(Qt::AlignCenter);
    cwLay->addWidget(cwEmpty_);
    cwText_ = new QPlainTextEdit(cwPage);
    cwText_->setReadOnly(true);
    cwLay->addWidget(cwText_);
    rightTabs_->addTab(cwPage, "CW");

    auto* adsbPage = new QWidget;
    auto* adsbLay = new QVBoxLayout(adsbPage);
    adsbEmpty_ = new QLabel("1090MHz 暂无飞机\n请在解调选择 ADS-B，真实收到 1090MHz 帧后显示", adsbPage);
    adsbEmpty_->setObjectName("statusHint");
    adsbEmpty_->setAlignment(Qt::AlignCenter);
    adsbEmpty_->setWordWrap(true);
    adsbLay->addWidget(adsbEmpty_);
    adsbTable_ = new QTableWidget(0, 8, adsbPage);
    adsbTable_->setHorizontalHeaderLabels({"ICAO", "呼号", "高度(ft)", "速度(kt)", "航向(°)", "垂直(fpm)", "距离(km)", "时间"});
    adsbTable_->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    adsbLay->addWidget(adsbTable_);
    rightTabs_->addTab(adsbPage, "ADS-B");

    // 1 s TTL-prune tick: expires silent aircraft and refreshes the table/map.
    adsbTimer_ = new QTimer(this);
    adsbTimer_->setInterval(1000);
    connect(adsbTimer_, &QTimer::timeout, this, &MainWindow::onAdsbPrune);
    adsbTimer_->start();

    // Constellation tab: live BPSK/QPSK symbol scatter from the selected digital VFO.
    {
        auto* cstPage = new QWidget;
        auto* cstLay = new QVBoxLayout(cstPage);
        cstLay->setContentsMargins(0, 0, 0, 0);
        cstLay->setSpacing(0);
        constellationView_ = new ui::ConstellationView(cstPage);
        cstLay->addWidget(constellationView_, 1);

        // Toolbar: display-only view controls over the REAL symbol scatter.
        // Small text-only buttons get a 44px touch hit-area on top of the
        // dense 26px visual height (tokens: kControlH visual / kTouchMinDim hit).
        // Laid out in two compact rows so it survives the narrow right rail
        // (~150 px) without clipping or overlapping labels.
        auto* cstBar = new QVBoxLayout;
        cstBar->setContentsMargins(tokens::scaled(tokens::kSpacingM), 0,
                                   tokens::scaled(tokens::kSpacingM),
                                   tokens::scaled(tokens::kSpacingS));
        cstBar->setSpacing(tokens::scaled(tokens::kSpacingS));
        auto* zoomRow = new QHBoxLayout;
        zoomRow->setSpacing(tokens::scaled(tokens::kSpacingS));
        auto* zoomOutBtn = new QPushButton(QStringLiteral("−"), cstPage);
        zoomOutBtn->setObjectName("cstZoomOutBtn");
        zoomOutBtn->setMinimumWidth(tokens::scaled(tokens::kTouchMinDim));
        zoomOutBtn->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
        zoomOutBtn->setToolTip(QStringLiteral("缩小星座图"));
        auto* zoomResetBtn = new QPushButton(QStringLiteral("1:1"), cstPage);
        zoomResetBtn->setObjectName("cstZoomResetBtn");
        zoomResetBtn->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
        zoomResetBtn->setToolTip(QStringLiteral("复位缩放"));
        auto* zoomInBtn = new QPushButton(QStringLiteral("+"), cstPage);
        zoomInBtn->setObjectName("cstZoomInBtn");
        zoomInBtn->setMinimumWidth(tokens::scaled(tokens::kTouchMinDim));
        zoomInBtn->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
        zoomInBtn->setToolTip(QStringLiteral("放大星座图"));
        zoomRow->addWidget(zoomOutBtn);
        zoomRow->addWidget(zoomResetBtn, 1);
        zoomRow->addWidget(zoomInBtn);
        cstBar->addLayout(zoomRow);

        auto* histBtn = new QPushButton(QStringLiteral("I 直方图"), cstPage);
        histBtn->setObjectName("cstHistBtn");
        histBtn->setCheckable(true);
        histBtn->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
        histBtn->setToolTip(QStringLiteral("叠加真实 I 统计直方图"));
        cstBar->addWidget(histBtn);
        cstLay->addLayout(cstBar);

        connect(zoomOutBtn, &QPushButton::clicked,
                constellationView_, &ui::ConstellationView::zoomOut);
        connect(zoomResetBtn, &QPushButton::clicked,
                constellationView_, &ui::ConstellationView::resetZoom);
        connect(zoomInBtn, &QPushButton::clicked,
                constellationView_, &ui::ConstellationView::zoomIn);
        connect(histBtn, &QPushButton::toggled,
                constellationView_, &ui::ConstellationView::setHistogramVisible);

        rightTabs_->addTab(cstPage, "星座");
    }

    // Sky tab: polar view on top, TLE freshness badge, pass list below.
    auto* skyPage = new QWidget;
    auto* skyLay = new QVBoxLayout(skyPage);
    skyLay->setContentsMargins(0, 0, 0, 0);
    skyView_ = new ui::SkyView();
    skyLay->addWidget(skyView_, 2);

    // --- Time scrubber: "现在 / 预览" dual state -------------------------
    // Center = live wall-now (实时). Dragging offsets the displayed UTC moment
    // (±kSkyPreviewRangeMin minutes); the whole sky is re-propagated with the
    // real SGP4 propagator, throttled to <=10 Hz. Release returns to live.
    {
        auto* timeBar = new QHBoxLayout;
        timeBar->setContentsMargins(tokens::scaled(6), 0, tokens::scaled(6), 0);
        QLabel* liveTag = new QLabel(QStringLiteral("实时"), skyPage);
        liveTag->setObjectName("dockHint");
        timeBar->addWidget(liveTag);
        QLabel* backTag = new QLabel(QStringLiteral("-30分"), skyPage);
        backTag->setObjectName("dockHint");
        backTag->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
        timeBar->addWidget(backTag);
        skyTimeSlider_ = new QSlider(Qt::Horizontal, skyPage);
        skyTimeSlider_->setObjectName("skyTimeSlider");
        skyTimeSlider_->setRange(-tokens::kSkyPreviewRangeMin, tokens::kSkyPreviewRangeMin);
        skyTimeSlider_->setValue(0);
        // Touch: a wide grab strip, not a thin 26px line (tokens, scaled()).
        skyTimeSlider_->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
        skyTimeSlider_->setToolTip(
            QStringLiteral("拖拽预览过去/未来时刻（±%1 分钟）：\n"
                           "用真实 SGP4 重新传播全部可见卫星（拖拽中 ≤10Hz）。\n"
                           "松开回到「现在」实时模式。")
                .arg(tokens::kSkyPreviewRangeMin));
        timeBar->addWidget(skyTimeSlider_, 1);
        QLabel* fwdTag = new QLabel(QStringLiteral("+30分"), skyPage);
        fwdTag->setObjectName("dockHint");
        timeBar->addWidget(fwdTag);
        skyLay->addLayout(timeBar);

        // Drag-coalesce timer: valueChanged fires far faster than SGP4 needs;
        // restart this short timer on every change and only propagate when it
        // finally fires (=> <= 10 Hz while dragging).
        skyPreviewTimer_ = new QTimer(this);
        skyPreviewTimer_->setSingleShot(true);
        skyPreviewTimer_->setInterval(tokens::kSkyPreviewThrottleMs);
        connect(skyPreviewTimer_, &QTimer::timeout,
                this, &MainWindow::recomputePreview);
    }
    // Elevation-vs-time curve for the selected pass (AOS..LOS on the x axis).
    elevationPlot_ = new ui::ElevationPlot(skyPage);
    elevationPlot_->setMinimumHeight(tokens::scaled(120));
    elevationPlot_->setMaximumHeight(tokens::scaled(160));
    skyLay->addWidget(elevationPlot_);
    // Clock-bias readout: GNSS UTC vs system UTC vs local, plus a copy button.
    // We only DISPLAY the bias -- the app never sets the system clock.
    {
        auto* clockBar = new QHBoxLayout;
        clockBar->setContentsMargins(tokens::scaled(6), 0, tokens::scaled(6), 0);
        clockInfoLabel_ = new QLabel(skyPage);
        clockInfoLabel_->setObjectName("monoInfo");
        clockBar->addWidget(clockInfoLabel_, 1);
        copyClockBtn_ = new QPushButton("复制时钟偏差", skyPage);
        copyClockBtn_->setToolTip("把（系统 UTC − GNSS UTC）偏差秒数复制到剪贴板；\n"
                                  "本程序不修改系统时钟，真正校时需 root / CAP_SYS_TIME 特权");
        clockBar->addWidget(copyClockBtn_);
        skyLay->addLayout(clockBar);
    }
    // Empty-state caption lives in a layout row BELOW the polar plot (not
    // painted over the compass), so it never collides with N/E/S/W labels.
    skyEmptyLabel_ = new QLabel(skyPage);
    skyEmptyLabel_->setObjectName("statusHint");
    skyEmptyLabel_->setAlignment(Qt::AlignCenter);
    skyEmptyLabel_->setWordWrap(true);
    skyLay->addWidget(skyEmptyLabel_);
    tleBadge_ = new QLabel(skyPage);
    tleBadge_->setObjectName("dockHint");
    skyLay->addWidget(tleBadge_);

    // ---- One-tap capture + Doppler auto-compensation control bar ----------
    // 捕获: retune the active VFO to the selected pass' downlink carrier
    // (+ predicted peak Doppler) and apply the frequency-domain mode/bandwidth.
    // Disabled until a row with a known downlink is selected.
    // 多普勒自动补偿: while checked AND the captured pass is AOS..LOS, nudge the
    // active VFO every 1 s from the real propagated range-rate (bounded steps).
    {
        auto* capBar = new QHBoxLayout;
        capBar->setContentsMargins(tokens::scaled(6), 0, tokens::scaled(6), 0);
        capBar->setSpacing(tokens::kSpacingM);

        capturePassBtn_ = new QPushButton(QStringLiteral("捕获"), skyPage);
        capturePassBtn_->setObjectName("capturePassBtn");
        capturePassBtn_->setEnabled(false);   // no row selected yet
        capturePassBtn_->setToolTip(
            QStringLiteral("把活动 VFO 调到所选过境的下行频率（含峰值多普勒建议值），\n"
                           "并按频段应用默认解调模式/带宽。\n"
                           "未知下行频率的卫星无法捕获。"));
        capBar->addWidget(capturePassBtn_);

        dopplerCompChk_ = new QCheckBox(QStringLiteral("多普勒自动补偿"), skyPage);
        dopplerCompChk_->setObjectName("dopplerCompChk");
        dopplerCompChk_->setChecked(false);   // default off
        dopplerCompChk_->setToolTip(
            QStringLiteral("过境进行中时，每秒用真实轨道传播的距离变化率\n"
                           "实时微调活动 VFO 频率（f0+fd），限幅步进防抖动。\n"
                           "需先设置本站位置并捕获一个过境。"));
        capBar->addWidget(dopplerCompChk_);

        captureStatusLabel_ = new QLabel(skyPage);
        captureStatusLabel_->setObjectName("captureStatusLabel");
        captureStatusLabel_->setText(QStringLiteral("未锁定"));
        capBar->addWidget(captureStatusLabel_, 1);

        skyLay->addLayout(capBar);
    }

    passTable_ = new QTableWidget(0, 6, skyPage);
    passTable_->setHorizontalHeaderLabels({"卫星", "AOS", "LOS", "最大仰角", "预测多普勒", "距今"});
    passTable_->horizontalHeader()->setStretchLastSection(true);
    passTable_->horizontalHeader()->setSectionsClickable(true);
    passTable_->setSortingEnabled(true);
    passTable_->verticalHeader()->setVisible(false);
    passTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    passTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    passTable_->setSelectionMode(QAbstractItemView::SingleSelection);
    skyLay->addWidget(passTable_, 1);
    rightTabs_->addTab(skyPage, "天空");

    // ---- Bookmarks / Scanner tab (SDR++-style "扫描/书签" panel) ----
    bookmarkManager_ = new ui::BookmarkManager();
    bookmarkManager_->load();
    scanner_ = new dsp::FrequencyScanner();
    auto* bmPage = new QWidget;
    auto* bmLay = new QVBoxLayout(bmPage);
    bmLay->setContentsMargins(tokens::kSpacingM, tokens::kSpacingM,
                              tokens::kSpacingM, tokens::kSpacingM);
    bmLay->setSpacing(tokens::kSpacingM);

    // ===== A. 频率扫描 control group =====
    auto* scanBox = new QGroupBox("频率扫描", bmPage);
    scanBox->setObjectName("scanGroup");
    auto* scanBoxLay = new QVBoxLayout(scanBox);
    scanBoxLay->setSpacing(tokens::kSpacingS);
    auto* scanForm = new QFormLayout;
    scanForm->setLabelAlignment(Qt::AlignRight);

    scanStartSpin_ = new QDoubleSpinBox(scanBox);
    scanStartSpin_->setRange(0.1, 2200);
    scanStartSpin_->setDecimals(3);
    scanStartSpin_->setValue(88);
    scanStartSpin_->setSuffix(" MHz");
    scanForm->addRow("起始", scanStartSpin_);

    scanStopSpin_ = new QDoubleSpinBox(scanBox);
    scanStopSpin_->setRange(0.1, 2200);
    scanStopSpin_->setDecimals(3);
    scanStopSpin_->setValue(108);
    scanStopSpin_->setSuffix(" MHz");
    scanForm->addRow("终止", scanStopSpin_);

    scanStepCombo_ = new QComboBox(scanBox);
    scanStepCombo_->addItems({"10 kHz", "12.5 kHz", "100 kHz", "1 MHz"});
    scanStepCombo_->setCurrentIndex(2);   // 默认 100 kHz
    scanForm->addRow("步进", scanStepCombo_);

    scanDwellSpin_ = new QSpinBox(scanBox);
    scanDwellSpin_->setRange(100, 2000);
    scanDwellSpin_->setSingleStep(50);
    scanDwellSpin_->setValue(300);        // 默认驻留 300 ms
    scanDwellSpin_->setSuffix(" ms");
    scanForm->addRow("驻留", scanDwellSpin_);

    scanThrSpin_ = new QDoubleSpinBox(scanBox);
    scanThrSpin_->setObjectName("scanThrSpin");
    scanThrSpin_->setRange(-120, 0);
    scanThrSpin_->setSingleStep(1);
    scanThrSpin_->setValue(-50);         // 默认门限 -50 dBFS
    scanThrSpin_->setSuffix(" dBFS");
    scanForm->addRow("门限", scanThrSpin_);

    scanDirCombo_ = new QComboBox(scanBox);
    scanDirCombo_->addItems({"向上", "向下", "来回"});
    scanForm->addRow("方向", scanDirCombo_);

    scanHoldCombo_ = new QComboBox(scanBox);
    scanHoldCombo_->addItems({"直到信号消失", "固定时长"});
    scanForm->addRow("命中停留", scanHoldCombo_);

    scanLingerSpin_ = new QSpinBox(scanBox);
    scanLingerSpin_->setRange(0, 60000);
    scanLingerSpin_->setValue(1000);     // lingerMs，直到信号消失时
    scanLingerSpin_->setSuffix(" ms");
    scanForm->addRow("消失延时", scanLingerSpin_);

    scanHoldMsSpin_ = new QSpinBox(scanBox);
    scanHoldMsSpin_->setRange(0, 60000);
    scanHoldMsSpin_->setValue(2000);     // holdMs，固定时长时
    scanHoldMsSpin_->setSuffix(" ms");
    scanForm->addRow("固定停留", scanHoldMsSpin_);

    scanBmOnlyChk_ = new QCheckBox("只扫书签", scanBox);
    scanForm->addRow("", scanBmOnlyChk_);

    scanBoxLay->addLayout(scanForm);

    // linger vs hold spinbox enabledness follows the hold-mode combo.
    auto syncHoldDependent = [this]() {
        const bool fixed = scanHoldCombo_->currentIndex() == 1;
        scanLingerSpin_->setEnabled(!fixed);
        scanHoldMsSpin_->setEnabled(fixed);
    };
    connect(scanHoldCombo_, &QComboBox::currentIndexChanged,
            this, [syncHoldDependent]() { syncHoldDependent(); });
    syncHoldDependent();

    auto* scanBtnRow = new QHBoxLayout;
    scanStartBtn_ = new QPushButton("开始", scanBox);
    scanStartBtn_->setObjectName("scanStartBtn");
    scanPauseBtn_ = new QPushButton("暂停", scanBox);
    scanPauseBtn_->setEnabled(false);
    scanStopBtn_  = new QPushButton("停止", scanBox);
    scanStopBtn_->setEnabled(false);
    // One-shot "save the current hit as a bookmark". Enabled ONLY while the
    // scanner is in ScanState::Hit (see updateScanStatus); carries the real
    // hit frequency + live mode/bandwidth into the bookmark dialog.
    scanSaveBmBtn_ = new QPushButton("存入书签", scanBox);
    scanSaveBmBtn_->setObjectName("scanSaveBmBtn");
    scanSaveBmBtn_->setEnabled(false);
    scanSaveBmBtn_->setToolTip("把当前命中频率存为书签（仅命中时可用）");
    scanBtnRow->addWidget(scanStartBtn_);
    scanBtnRow->addWidget(scanPauseBtn_);
    scanBtnRow->addWidget(scanStopBtn_);
    scanBtnRow->addWidget(scanSaveBmBtn_);
    scanBoxLay->addLayout(scanBtnRow);

    scanFreqLabel_ = new QLabel("当前 --.-- MHz", scanBox);
    scanFreqLabel_->setObjectName("monoInfo");
    scanBoxLay->addWidget(scanFreqLabel_);
    scanStateLabel_ = new QLabel("空闲", scanBox);
    scanStateLabel_->setObjectName("dockHint");
    scanBoxLay->addWidget(scanStateLabel_);
    bmLay->addWidget(scanBox);

    // ===== B. 频率书签 table group =====
    auto* bmBox = new QGroupBox("频率书签", bmPage);
    bmBox->setObjectName("bmGroup");
    auto* bmBoxLay = new QVBoxLayout(bmBox);
    bmBoxLay->setSpacing(tokens::kSpacingS);
    bmTable_ = new QTableWidget(0, 5, bmBox);
    bmTable_->setObjectName("bmTable");
    bmTable_->setHorizontalHeaderLabels({"名称", "频率(MHz)", "模式", "带宽(kHz)", "分组"});
    bmTable_->verticalHeader()->setVisible(false);
    bmTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    bmTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    bmTable_->setSelectionMode(QAbstractItemView::SingleSelection);
    bmTable_->horizontalHeader()->setStretchLastSection(true);
    bmBoxLay->addWidget(bmTable_, 1);
    auto* bmBtnRow = new QHBoxLayout;
    bmAddBtn_  = new QPushButton("添加", bmBox);
    bmEditBtn_ = new QPushButton("编辑", bmBox);
    bmDelBtn_  = new QPushButton("删除", bmBox);
    bmBtnRow->addWidget(bmAddBtn_);
    bmBtnRow->addWidget(bmEditBtn_);
    bmBtnRow->addWidget(bmDelBtn_);
    bmBoxLay->addLayout(bmBtnRow);
    bmLay->addWidget(bmBox, 1);

    // Modal add/edit dialog.  prefill.frequencyHz<=0 means "add": the dialog
    // seeds frequency/mode/bandwidth from the live engine.  Returns true on OK.
    auto bmDialog = [this](const ui::Bookmark& prefill, ui::Bookmark& outBm) -> bool {
        QDialog dlg(this);
        dlg.setWindowTitle(prefill.frequencyHz > 0.0 ? "编辑书签" : "添加书签");
        auto* form = new QFormLayout(&dlg);
        QLineEdit* nameEdit = new QLineEdit(prefill.name, &dlg);
        // Empty name: hint with the (prefilled or live) frequency so the user has
        // a sensible default to type over -- never a fabricated station name.
        const double hintHz = prefill.frequencyHz > 0.0 ? prefill.frequencyHz
                                                        : freqSpin_->value() * 1e6;
        nameEdit->setPlaceholderText(QString("%1 MHz").arg(hintHz / 1e6, 0, 'f', 3));
        QDoubleSpinBox* freqMhz = new QDoubleSpinBox(&dlg);
        freqMhz->setRange(0.1, 2200);
        freqMhz->setDecimals(3);
        freqMhz->setSuffix(" MHz");
        freqMhz->setValue(prefill.frequencyHz > 0.0
                          ? prefill.frequencyHz / 1e6 : freqSpin_->value());
        QComboBox* modeCombo = new QComboBox(&dlg);
        modeCombo->addItems({"NFM", "WFM", "AM", "LSB", "USB"});
        modeCombo->setCurrentText(prefill.mode.isEmpty() ? demodCombo_->currentText()
                                                         : prefill.mode);
        QDoubleSpinBox* bwKhz = new QDoubleSpinBox(&dlg);
        bwKhz->setRange(0, 10000);
        bwKhz->setDecimals(1);
        bwKhz->setSingleStep(1);
        bwKhz->setSuffix(" kHz");
        const double bwHz = prefill.bandwidthHz > 0.0 ? prefill.bandwidthHz : currentBwHz_;
        bwKhz->setValue(bwHz / 1e3);
        QLineEdit* groupEdit = new QLineEdit(prefill.group, &dlg);
        form->addRow("名称", nameEdit);
        form->addRow("频率", freqMhz);
        form->addRow("模式", modeCombo);
        form->addRow("带宽", bwKhz);
        form->addRow("分组", groupEdit);
        auto* btns = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dlg);
        form->addRow(btns);
        connect(btns, &QDialogButtonBox::accepted, &dlg, &QDialog::accept);
        connect(btns, &QDialogButtonBox::rejected, &dlg, &QDialog::reject);
        if (dlg.exec() != QDialog::Accepted) return false;
        outBm.name = nameEdit->text().trimmed();
        outBm.frequencyHz = freqMhz->value() * 1e6;
        outBm.mode = modeCombo->currentText();
        outBm.bandwidthHz = bwKhz->value() * 1e3;
        outBm.group = groupEdit->text().trimmed();
        return true;
    };

    // After any bookmark mutation, resync the scanner's bookmark-frequency list
    // while "只扫书签" is configured.
    auto resyncScanBookmarks = [this]() {
        if (scanBmOnlyChk_->isChecked())
            scanner_->setBookmarkFrequencies(bookmarkManager_->frequencies());
    };

    connect(bmAddBtn_, &QPushButton::clicked, this, [this, bmDialog, resyncScanBookmarks]() {
        ui::Bookmark bm;   // empty -> dialog seeds from live engine freq/mode/bw
        if (bmDialog(bm, bm)) {
            bookmarkManager_->add(bm);
            refreshBmTable();
            resyncScanBookmarks();
        }
    });
    connect(bmEditBtn_, &QPushButton::clicked, this, [this, bmDialog, resyncScanBookmarks]() {
        int row = bmTable_->currentRow();
        if (row < 0 || row >= bookmarkManager_->list().size()) return;
        ui::Bookmark bm = bookmarkManager_->list().at(row);
        if (bmDialog(bm, bm)) {
            bookmarkManager_->update(row, bm);
            refreshBmTable();
            resyncScanBookmarks();
        }
    });
    connect(bmDelBtn_, &QPushButton::clicked, this, [this, resyncScanBookmarks]() {
        int row = bmTable_->currentRow();
        if (row < 0) return;
        bookmarkManager_->removeAt(row);
        refreshBmTable();
        resyncScanBookmarks();
    });
    // Scan-hit one-shot: capture the REAL hit frequency into the bookmark dialog,
    // pre-filled with the live demod mode + bandwidth; the name stays empty so
    // the user can label it. Button is only enabled in ScanState::Hit.
    connect(scanSaveBmBtn_, &QPushButton::clicked, this,
            [this, bmDialog, resyncScanBookmarks]() {
        if (!scanner_ || scanner_->state() != dsp::ScanState::Hit) return;
        ui::Bookmark bm;
        bm.frequencyHz = scanner_->hitFrequency();
        bm.mode = demodCombo_->currentText();
        bm.bandwidthHz = currentBwHz_;
        bm.name.clear();   // let the user name it; placeholder shows the freq
        if (bmDialog(bm, bm)) {
            bookmarkManager_->add(bm);
            refreshBmTable();
            resyncScanBookmarks();
        }
    });
    // Double-click row = jump directly (no edit dialog).
    connect(bmTable_, &QTableWidget::cellDoubleClicked, this, [this](int row, int) {
        if (row < 0 || row >= bookmarkManager_->list().size()) return;
        const ui::Bookmark& b = bookmarkManager_->list().at(row);
        engine_->onSetCenterFreq(b.frequencyHz);
        if (!b.mode.isEmpty()) engine_->setDemodMode(b.mode);
        if (b.bandwidthHz > 0) engine_->setBandwidth(b.bandwidthHz);
    });
    refreshBmTable();

    // ===== C. Drive the headless scanner state machine on a 50 ms timer =====
    scanTimer_ = new QTimer(this);
    scanTimer_->setInterval(50);
    scanTickClock_ = new QElapsedTimer();
    connect(scanTimer_, &QTimer::timeout, this, &MainWindow::scanTimerTick);

    connect(scanStartBtn_, &QPushButton::clicked, this, [this]() {
        dsp::ScanConfig cfg;
        cfg.startHz = scanStartSpin_->value() * 1e6;
        cfg.stopHz  = scanStopSpin_->value() * 1e6;
        switch (scanStepCombo_->currentIndex()) {
            case 0:  cfg.stepHz = 10e3;   break;
            case 1:  cfg.stepHz = 12.5e3; break;
            case 3:  cfg.stepHz = 1e6;    break;
            default: cfg.stepHz = 100e3;  break;
        }
        cfg.dwellMs = scanDwellSpin_->value();
        cfg.thresholdDb = static_cast<float>(scanThrSpin_->value());
        cfg.direction = scanDirCombo_->currentIndex() == 0 ? dsp::ScanDirection::Up
                      : scanDirCombo_->currentIndex() == 1 ? dsp::ScanDirection::Down
                                                           : dsp::ScanDirection::PingPong;
        cfg.holdMode = scanHoldCombo_->currentIndex() == 1
                       ? dsp::HitHoldMode::FixedMs : dsp::HitHoldMode::UntilSignalGone;
        cfg.lingerMs = scanLingerSpin_->value();
        cfg.holdMs   = scanHoldMsSpin_->value();
        if (scanBmOnlyChk_->isChecked()) {
            cfg.source = dsp::ScanSource::Bookmarks;
            scanner_->setBookmarkFrequencies(bookmarkManager_->frequencies());
        } else {
            cfg.source = dsp::ScanSource::Range;
        }
        scanner_->setConfig(cfg);
        scanTickClock_->start();
        scanner_->start();
        scanTimer_->start();
        updateScanStatus();
    });
    connect(scanPauseBtn_, &QPushButton::clicked, this, [this]() {
        const dsp::ScanState st = scanner_->state();
        if (st == dsp::ScanState::Scanning || st == dsp::ScanState::Hit)
            scanner_->pause();
        else if (st == dsp::ScanState::Paused)
            scanner_->resume();
        updateScanStatus();
    });
    connect(scanStopBtn_, &QPushButton::clicked, this, [this]() {
        scanner_->stop();
        updateScanStatus();
    });
    updateScanStatus();

    rightTabs_->addTab(bmPage, "扫描/书签");

    // ===== 录制库 tab: scan the REAL recording directory (rec/dir) =====
    // Lists actual .wav captures + their sidecar .json proof. The directory is
    // whatever the engine actually writes to (engine_->recordingDir(), default
    // "record"). An empty / missing dir honestly shows "暂无录音".
    {
        auto* recPage = new QWidget;
        auto* recLay = new QVBoxLayout(recPage);
        recLay->setContentsMargins(tokens::kSpacingM, tokens::kSpacingM,
                                   tokens::kSpacingM, tokens::kSpacingM);
        recLay->setSpacing(tokens::kSpacingM);

        // -- watch recorder status (fed by the real watchStateChanged/RSSI) --
        auto* wBox = new QGroupBox("值守录制状态", recPage);
        auto* wBoxLay = new QVBoxLayout(wBox);
        wBoxLay->setSpacing(tokens::kSpacingS);
        recLibWatchState_ = new QLabel("值守: 未启用", wBox);
        recLibWatchState_->setObjectName("monoInfo");
        recLibWatchLevel_ = new QLabel("电平: -- dBFS · 门限 --", wBox);
        recLibWatchLevel_->setObjectName("dockHint");
        wBoxLay->addWidget(recLibWatchState_);
        wBoxLay->addWidget(recLibWatchLevel_);
        recLay->addWidget(wBox);

        // -- recordings list --
        auto* lBox = new QGroupBox("录制文件", recPage);
        auto* lBoxLay = new QVBoxLayout(lBox);
        lBoxLay->setSpacing(tokens::kSpacingS);
        recLibList_ = new QListWidget(lBox);
        recLibList_->setSelectionMode(QAbstractItemView::SingleSelection);
        recLibList_->setEditTriggers(QAbstractItemView::NoEditTriggers);
        lBoxLay->addWidget(recLibList_, 1);
        recLibEmpty_ = new QLabel("暂无录音", lBox);
        recLibEmpty_->setAlignment(Qt::AlignCenter);
        recLibEmpty_->setObjectName("dockHint");
        lBoxLay->addWidget(recLibEmpty_);

        auto* rBtnRow = new QHBoxLayout;
        recLibRefreshBtn_ = new QPushButton("刷新", lBox);
        recLibCopyBtn_    = new QPushButton("复制路径", lBox);
        recLibDelBtn_     = new QPushButton("删除", lBox);
        recLibPlayBtn_    = new QPushButton("播放", lBox);
        // Touch: every row action is a >=44px tap target (tokens, scaled()).
        for (auto* b : {recLibRefreshBtn_, recLibCopyBtn_, recLibDelBtn_, recLibPlayBtn_})
            b->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
        rBtnRow->addWidget(recLibRefreshBtn_);
        rBtnRow->addWidget(recLibCopyBtn_);
        rBtnRow->addWidget(recLibDelBtn_);
        rBtnRow->addWidget(recLibPlayBtn_);
        lBoxLay->addLayout(rBtnRow);
        recLibPlayStatus_ = new QLabel("未加载", lBox);
        recLibPlayStatus_->setObjectName("monoInfo");
        lBoxLay->addWidget(recLibPlayStatus_);
        recLay->addWidget(lBox, 1);

        rightTabs_->addTab(recPage, "录制库");

        connect(recLibRefreshBtn_, &QPushButton::clicked,
                this, [this]{ refreshRecLib(); });
        connect(recLibCopyBtn_, &QPushButton::clicked,
                this, &MainWindow::onRecLibCopyPath);
        connect(recLibDelBtn_, &QPushButton::clicked,
                this, &MainWindow::onRecLibDelete);
        connect(recLibPlayBtn_, &QPushButton::clicked,
                this, &MainWindow::onRecLibPlayToggle);

        // Chunked playback: push decoded 48 kHz mono float ~20 ms at a time into
        // the EXISTING AudioOutput write channel. Offscreen/headless has no
        // device, so writes are dropped (QtAudioSink::isAvailable()==false) --
        // the status label reports this honestly, never fakes audio out.
        recLibPlayTimer_ = new QTimer(this);
        recLibPlayTimer_->setInterval(20);
        connect(recLibPlayTimer_, &QTimer::timeout, this, [this]() {
            if (!engine_ || !engine_->audioOutput()) { recLibPlayTimer_->stop(); return; }
            const qint64 chunk = 960;   // 20 ms @ 48 kHz
            const qint64 total = static_cast<qint64>(recLibPcm_.size());
            if (recLibPcmPos_ >= total) { onRecLibPlayToggle(); return; }  // reached end
            const qint64 n = qMin(chunk, total - recLibPcmPos_);
            std::vector<float> blk(recLibPcm_.begin() + recLibPcmPos_,
                                   recLibPcm_.begin() + recLibPcmPos_ + n);
            recLibPcmPos_ += n;
            engine_->audioOutput()->write(blk);
        });

        refreshRecLib();
    }

    connect(passTable_, &QTableWidget::cellClicked,
            this, [this](int row, int) { onPassRowClicked(row); });
    connect(capturePassBtn_, &QPushButton::clicked,
            this, &MainWindow::onCapturePassClicked);
    connect(dopplerCompChk_, &QCheckBox::toggled,
            this, &MainWindow::onDopplerCompToggled);

    auto* aiPage = new QWidget;
    auto* aiLay = new QVBoxLayout(aiPage);
    aiStatus_ = new QLabel("AI 助手将在这里接入（需在设置中配置 API Key）", aiPage);
    aiStatus_->setWordWrap(true);
    aiLay->addWidget(aiStatus_);

    // ---- Multi-session switcher ----------------------------------------
    // The store persists to AppDataLocation/ai_sessions (overridable by the
    // MBDSDR_AI_SESSIONS_DIR env for tests/screenshots). First run creates
    // exactly ONE empty session -- no seeded conversation.
    aiSessionStore_ = new mbdsdr::ai::AiSessionStore(QString(), aiPage);
    aiCurSessionId_ = aiSessionStore_->currentId();

    auto* sessRow = new QHBoxLayout;
    aiSessionCombo_ = new QComboBox(aiPage);
    aiSessionCombo_->setObjectName("aiSessionCombo");
    aiSessionCombo_->setMinimumWidth(120);
    // Touch: the session switcher and its row buttons are >=44px tap targets.
    aiSessionCombo_->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
    sessRow->addWidget(aiSessionCombo_, /*stretch=*/1);
    aiNewSessionBtn_ = new QPushButton("新会话", aiPage);
    aiNewSessionBtn_->setObjectName("aiNewSessionBtn");
    aiRenameSessionBtn_ = new QPushButton("重命名", aiPage);
    aiRenameSessionBtn_->setObjectName("aiRenameSessionBtn");
    aiDeleteSessionBtn_ = new QPushButton("删除", aiPage);
    aiDeleteSessionBtn_->setObjectName("aiDeleteSessionBtn");
    for (auto* b : {aiNewSessionBtn_, aiRenameSessionBtn_, aiDeleteSessionBtn_})
        b->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
    sessRow->addWidget(aiNewSessionBtn_);
    sessRow->addWidget(aiRenameSessionBtn_);
    sessRow->addWidget(aiDeleteSessionBtn_);
    aiLay->addLayout(sessRow);

    aiCompactCtxBtn_ = new QPushButton("压缩上下文", aiPage);
    aiCompactCtxBtn_->setObjectName("aiCompactCtxBtn");
    aiCompactCtxBtn_->setMinimumHeight(tokens::scaled(tokens::kTouchMinDim));
    aiCompactCtxBtn_->setToolTip("早期轮次超出上下文预算时会自动折叠为「已摘要」；也可手动触发。");
    aiLay->addWidget(aiCompactCtxBtn_);

    // Manual-mode toggle: when checked, AI may only SUGGEST -- write tools
    // (tune/mode/bandwidth/record/scan) are gated by the backend and never
    // touch the radio. Initial state is wired after agent_ is constructed below
    // (agent_->manualMode() reflects the persisted QSettings value).
    aiManualCheck_ = new QCheckBox("手动模式（AI 只建议、不执行写操作）", aiPage);
    aiManualCheck_->setObjectName("aiManualModeCheck");
    aiManualCheck_->setToolTip("勾选后 AI 不会真正调谐/改模式/录制，只返回被拦截的建议。");
    aiLay->addWidget(aiManualCheck_);
    aiChat_ = new QPlainTextEdit(aiPage);
    aiChat_->setObjectName("aiChat");
    aiChat_->setReadOnly(true);
    aiLay->addWidget(aiChat_);
    aiInput_ = new QLineEdit(aiPage);
    aiInput_->setPlaceholderText("输入频率/模式/指令...");
    aiLay->addWidget(aiInput_);
    auto* sendBtn = new QPushButton("发送", aiPage);
    aiLay->addWidget(sendBtn);
    // No fake replies: input only enabled when a key is configured.
    {
        mbdsdr::ai::AiConfig cfg;
        cfg.load();
        const bool hasKey = cfg.isConfigured();
        aiInput_->setEnabled(hasKey);
        sendBtn->setEnabled(hasKey);
        aiStatus_->setText(hasKey
            ? "已配置 API Key — AI 功能接入中"
            : "AI 助手将在这里接入（需在设置中配置 API Key）");
    }
    rightTabs_->addTab(aiPage, "AI 助手");

    // Radio / transmit panel: serial CAT, CW, AX.25/KISS, SoapySDR TX.
    rightTabs_->addTab(new ui::RadioPanel(rightCard), "电台");

    rightLay->addWidget(rightTabs_);
    splitter->addWidget(rightCard);

    splitter->setStretchFactor(0, 1);
    splitter->setStretchFactor(1, 3);
    splitter->setStretchFactor(2, 1);
    splitter->setSizes({tokens::scaled(280), tokens::scaled(800), tokens::scaled(280)});

    centralLay->addWidget(splitter);
    setCentralWidget(central);
    statusBar()->showMessage("MBDSDR C++");
    // Permanent status strip: mode | sample rate | VFO | gain | source.
    // sr/vfo/gain are refreshed by the engine's ~1 Hz sourceTelemetry push with
    // the ACTUAL hardware readback values (gain is rounded by the driver), not
    // the UI spinbox requests.
    sbMode_ = new QLabel("--", this);
    sbSr_   = new QLabel("--", this);
    sbVfo_  = new QLabel("--", this);
    sbRds_  = new QLabel("", this);
    sbGain_ = new QLabel("--", this);
    sbSdr_  = new QLabel("Test Signal", this);
    sbWatch_ = new QLabel("", this);
    sbWatch_->setStyleSheet(QString("color:%1; font-weight:600;").arg(tokens::kAccent));
    sbScan_ = new QLabel("", this);
    sbScan_->setStyleSheet(QString("color:%1; font-weight:600;").arg(tokens::kAccent));
    sbRec_  = new QLabel("", this);
    sbRec_->setStyleSheet(QString("color:%1; font-weight:600;").arg(tokens::kDanger));
    // B5: extra one-line readouts. "--" until the first real engine readback;
    // the GNSS field stays EMPTY until a genuine fix (never a fabricated one).
    sbRssi_  = new QLabel("--", this);
    sbSnr_   = new QLabel("--", this);
    sbSquelch_ = new QLabel("静噪 OFF", this);
    sbGnss_  = new QLabel("", this);
    for (QLabel* l : {sbMode_, sbSr_, sbVfo_, sbRds_, sbGain_, sbSdr_, sbWatch_,
                      sbScan_, sbRec_, sbRssi_, sbSnr_, sbSquelch_, sbGnss_}) {
        l->setObjectName("dockHint");
        statusBar()->addPermanentWidget(l);
    }
    sbMode_->setText(demodCombo_->currentText());

    // ---- Engine wiring (engine_ created before UI construction) ----
    {
        QSettings s("MBDSDR", "MBDSDR");
        if (engine_->audioOutput())
            engine_->audioOutput()->setVolume(s.value("rx/volume", 80).toInt() / 100.0);
    }
    connect(engine_, &dsp::SpectrumEngine::spectrumReady,
            spectrum_, &ui::SpectrumWidget::setSpectrum);
    connect(engine_, &dsp::SpectrumEngine::sourceChanged,
            this, &MainWindow::onSourceChanged);
    connect(engine_, &dsp::SpectrumEngine::sourceDropped,
            this, &MainWindow::onSourceDropped);
    connect(engine_, &dsp::SpectrumEngine::sourceError,
            this, &MainWindow::onSourceError);
    connect(engine_, &dsp::SpectrumEngine::audioLevel,
            this, &MainWindow::onAudioLevel);
    connect(engine_, &dsp::SpectrumEngine::rssiLevel,
            this, &MainWindow::onRssiLevel);
    connect(engine_, &dsp::SpectrumEngine::snrLevel,
            this, &MainWindow::onSnrLevel);
    // Real noise floor -> the spectrum canvas baseline. The engine tracks the
    // floor in the total-power (RSSI) domain; the canvas dB axis is per-bin
    // dBFS, so undo the Parseval scaling exactly (median_bin = total −
    // 10·log10(N)) -- the baseline then sits on the visible noise band.
    connect(engine_, &dsp::SpectrumEngine::noiseFloorLevel,
            this, [this](float totalDb) {
        const int n = engine_ ? engine_->fftSize() : 2048;
        const float perBinDb = totalDb
            - 10.0f * std::log10(static_cast<float>(n));
        if (spectrum_) spectrum_->setNoiseFloorDb(perBinDb);
    });
    connect(engine_, &dsp::SpectrumEngine::sourceTelemetry,
            this, &MainWindow::onSourceTelemetry);
    connect(engine_, &dsp::SpectrumEngine::squelchState,
            this, &MainWindow::onSquelchState);
    connect(engine_, &dsp::SpectrumEngine::recordingStateChanged,
            this, &MainWindow::onRecordingState);
    connect(engine_, &dsp::SpectrumEngine::recordingProgress,
            this, &MainWindow::onRecordingProgress);
    connect(engine_, &dsp::SpectrumEngine::cwDecoded,
            this, &MainWindow::onCwDecoded);
    connect(engine_, &dsp::SpectrumEngine::adsbAircraft,
            this, &MainWindow::onAdsbAircraft);
    connect(engine_, &dsp::SpectrumEngine::rdsUpdated,
            this, &MainWindow::onRdsUpdated);
    connect(engine_, &dsp::SpectrumEngine::stereoState,
            this, &MainWindow::onStereoState);
    connect(forceMonoCheck_, &QCheckBox::toggled,
            this, [this](bool on) { engine_->setForceMono(on); });
    connect(spectrum_, &ui::SpectrumWidget::fftSizeRequested,
            engine_, &dsp::SpectrumEngine::setFftSize);
    connect(spectrum_, &ui::SpectrumWidget::windowTypeRequested,
            engine_, &dsp::SpectrumEngine::setWindowType);
    connect(spectrum_, &ui::SpectrumWidget::averageModeRequested,
            engine_, &dsp::SpectrumEngine::setAverageMode);
    // Zoom/pan lockstep between the trace and the waterfall is now intrinsic:
    // both are drawn by the same canvas from one visible window.

    connect(freqSpin_, QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, [this](double mhz) {
                engine_->onSetCenterFreq(mhz * 1e6);
                sbVfo_->setText(QString("%1 MHz").arg(mhz, 0, 'f', 3));
            });
    // Step combo: set currentStepHz_ and make the spinbox up/down arrows walk
    // by the same step (spinbox unit is MHz).
    auto applyStep = [this](int idx) {
        if (idx < 0 || idx >= kStepCount) return;
        currentStepHz_ = kStepValuesHz[idx];
        freqSpin_->setSingleStep(static_cast<double>(currentStepHz_) / 1e6);
        if (spectrum_) spectrum_->setStepHz(currentStepHz_);
    };
    applyStep(stepCombo_->currentIndex());
    connect(stepCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this, applyStep](int idx) {
        applyStep(idx);
        scheduleSave();
    });
    connect(gainSlider_, &QSlider::valueChanged,
            this, [this](int v) {
                gainValue_->setText(QString("%1 dB").arg(v));
                engine_->onSetGain(v);
            });
    connect(demodCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int) {
                // Capture the PREVIOUS mode before setDemodMode flips the engine
                // cache -- the coverage rule needs it to tell a natural mode walk
                // from a user-tweaked bandwidth.
                const QString oldMode = engine_->demodMode();
                const QString newMode = demodCombo_->currentText();
                engine_->setDemodMode(newMode);
                sbMode_->setText(newMode);
                // Force-mono only applies to WFM stereo; leave the badge honestly
                // on "单声道" until the next real pilot-driven stereoState arrives.
                const bool wfm = (newMode == "WFM");
                forceMonoCheck_->setEnabled(wfm);
                if (!wfm) onStereoState(false, 0.0f, 0.0f);
                // ADS-B (1090 MHz Mode S): walk the receiver to the band. These
                // are reversible -- the user may retune/change rate afterwards.
                if (newMode == "ADS-B") {
                    // Park the tuner on 1090 MHz and mirror it into the spinbox
                    // without re-entering the freqChanged signal loop.
                    engine_->onSetCenterFreq(1090.0e6);
                    freqSpin_->blockSignals(true);
                    freqSpin_->setValue(1090.0);
                    freqSpin_->blockSignals(false);
                    // 1.024 MS/s is too narrow for 1090 MHz; step up to 2.4 MS/s
                    // if we're on the slowest rate. >=2.048 MS/s is fine.
                    if (srCombo_->currentIndex() == 0)
                        srCombo_->setCurrentIndex(2);   // handler sets 2.4e6
                }
                // --- Per-mode default bandwidth preset (B4) -------------------
                // setDemodMode() already resets the selected VFO's bandwidth to
                // the new mode default internally; here we own the UI<->engine
                // contract: land on the value the pure coverage rule picks
                // (adopt new-mode default unless the user manually tuned away
                // from the old-mode default), push it through the REAL
                // engine setBandwidth API, then mirror it into currentBwHz_ and
                // the spectrum band-edge display + combo.
                const double wantBw =
                    core::bandwidthOnModeSwitch(oldMode, newMode, currentBwHz_);
                currentBwHz_ = wantBw;
                engine_->setBandwidth(wantBw);
                if (spectrum_) spectrum_->setBandwidthHz(wantBw);
                bwCombo_->blockSignals(true);
                bwCombo_->setCurrentIndex(nearestBwPresetIndex(wantBw));
                bwCombo_->blockSignals(false);
                statusBar()->showMessage(
                    QStringLiteral("带宽预设: %1 -> %2 Hz").arg(oldMode, newMode));
            });
    connect(squelchSlider_, &QSlider::valueChanged,
            this, [this](int v) {
                squelchValue_->setText(QString("%1 dB").arg(v));
                engine_->setSquelchThreshold(static_cast<float>(v));
            });
    connect(squelchCheck_, &QCheckBox::stateChanged, this, [this](int st) {
        bool en = (st != Qt::Unchecked);
        squelchOn_ = en;
        engine_->setSquelchEnabled(en);
        if (!en) squelchState_->setText("状态: CLOSED");
        // B5: immediately reflect OFF when the user disables the gate.
        if (sbSquelch_ && !en) sbSquelch_->setText(QStringLiteral("静噪 OFF"));
    });
    // Auto gate: threshold = tracked audio-RMS noise floor + margin (same dBFS
    // domain). applyAutoThreshold is read-back only; the slider valueChanged
    // handler above pushes the result into the engine.
    auto applyAutoThreshold = [this]() {
        if (!engine_ || !squelchSlider_) return;
        const double floor = engine_->audioNoiseFloorDbfs();
        const double target = std::clamp(
            floor + tokens::kSquelchAutoMarginDb,
            double(tokens::kSquelchMinDb), double(tokens::kSquelchMaxDb));
        squelchSlider_->setValue(static_cast<int>(std::round(target)));
    };
    connect(engine_, &dsp::SpectrumEngine::audioRmsNoiseFloor, this,
            [this, applyAutoThreshold](float) {
        if (squelchAutoBtn_ && squelchAutoBtn_->isChecked()) applyAutoThreshold();
    });
    connect(squelchAutoBtn_, &QPushButton::toggled, this,
            [this, applyAutoThreshold](bool on) {
        if (on) applyAutoThreshold();   // sample the current floor immediately
        scheduleSave();
    });
    // Grabbing the slider = the user wants manual control: drop auto-follow so
    // the engine floor can't fight their drag.
    connect(squelchSlider_, &QSlider::sliderPressed, this, [this]() {
        if (squelchAutoBtn_ && squelchAutoBtn_->isChecked())
            squelchAutoBtn_->setChecked(false);
    });
    connect(srCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                static const double kRates[] = {1.024e6, 2.048e6, 2.4e6, 3.2e6};
                if (idx >= 0 && idx <= 3) engine_->onSetSampleRate(kRates[idx]);
                sbSr_->setText(srCombo_->currentText());
            });
    connect(bwCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                if (idx >= 0 && idx < kBwComboPresetCount) {
                    currentBwHz_ = kBwComboPresetsHz[idx];
                    engine_->setBandwidth(kBwComboPresetsHz[idx]);
                    if (spectrum_) spectrum_->setBandwidthHz(kBwComboPresetsHz[idx]);
                }
            });
    // Drag a VFO band edge on the spectrum -> update bandwidth (snap to preset).
    connect(spectrum_, &ui::SpectrumWidget::bandwidthChanged,
            this, [this](double hz) {
                bwCombo_->setCurrentIndex(nearestBwPresetIndex(hz));
            });
    connect(gatedCheck_, &QCheckBox::stateChanged, this, [this](int st) {
        engine_->setGatedRecordingEnabled(st != Qt::Unchecked);
    });

    // ---- Watch mode controls ----
    connect(watchCheck_, &QCheckBox::stateChanged, this, [this](int st) {
        const bool on = (st != Qt::Unchecked);
        engine_->setWatchEnabled(on);
        watchForm_->setEnabled(on);
        scheduleSave();
    });
    connect(watchThrSlider_, &QSlider::valueChanged, this, [this](int v) {
        watchThrValue_->setText(QString("%1 dB").arg(v));
        engine_->setWatchThresholdDb(static_cast<float>(v));
        scheduleSave();
    });
    connect(watchPrerollSpin_,
            QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, [this](double v) {
                engine_->setWatchPrerollMs(v * 1000.0);
                scheduleSave();
            });
    connect(watchHangSpin_,
            QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, [this](double v) {
                engine_->setWatchHangMs(v * 1000.0);
                scheduleSave();
            });
    // Live watch status (cross-thread, queued).
    connect(engine_, &dsp::SpectrumEngine::watchStateChanged,
            this, [this](bool enabled, bool recording, int segments) {
                if (!enabled) {
                    if (watchStatus_) watchStatus_->setText("值守: 关");
                    if (sbWatch_) sbWatch_->setText("");
                    if (recLibWatchState_) recLibWatchState_->setText("值守: 未启用");
                    return;
                }
                const QString tail = QString("已录 %1 段").arg(segments);
                if (watchStatus_)
                    watchStatus_->setText(recording
                        ? QString("值守: ● 录制中 · %1").arg(tail)
                        : QString("值守: 监听中 · %1").arg(tail));
                if (sbWatch_)
                    sbWatch_->setText(recording
                        ? QString("● 值守录制 · %1").arg(tail)
                        : QString("值守监听 · %1").arg(tail));
                // Recording-library panel mirror (same real state, restrained).
                if (recLibWatchState_)
                    recLibWatchState_->setText(recording
                        ? QString("● 录制中 · %1").arg(tail)
                        : QString("监听中（等待触发）· %1").arg(tail));
            }, Qt::QueuedConnection);

    // ---- Recording directory ----
    connect(recDirBrowseBtn_, &QPushButton::clicked, this, [this]() {
        const QString start = recDirEdit_->text().isEmpty()
                                  ? QStringLiteral("record") : recDirEdit_->text();
        const QString dir = QFileDialog::getExistingDirectory(
            this, "选择录制目录", start);
        if (!dir.isEmpty()) {
            recDirEdit_->setText(dir);
            engine_->setRecordingDir(dir);
            scheduleSave();
        }
    });
    connect(recDirEdit_, &QLineEdit::editingFinished, this, [this]() {
        engine_->setRecordingDir(recDirEdit_->text());
        scheduleSave();
    });

    connect(recordBtn_, &QPushButton::clicked, this, &MainWindow::onRecordClicked);

    // ---- Multi-VFO wiring -------------------------------------------------
    connect(engine_, &dsp::SpectrumEngine::vfoListChanged,
            this, [this]() { refreshVfoUi(); scheduleSave(); },
            Qt::QueuedConnection);
    connect(vfoAddBtn_, &QPushButton::clicked, this, [this]() { engine_->vfoAdd(); });
    connect(vfoCopyBtn_, &QPushButton::clicked, this, &MainWindow::vfoCopyUi);
    connect(vfoDelBtn_, &QPushButton::clicked, this, [this]() {
        const int sel = engine_->selectedVfoId();
        if (sel > 0) engine_->vfoRemove(sel);
    });
    connect(vfoList_, &QListWidget::currentItemChanged,
            this, [this](QListWidgetItem* cur, QListWidgetItem*) {
        if (!cur) return;
        bool ok = false;
        const int id = cur->data(Qt::UserRole).toInt(&ok);
        if (ok && id > 0) engine_->vfoSelect(id);
    });
    // Double-click a row: explicitly switch the active VFO. Single-click row
    // activation above is unchanged; this supplements the touch/keyboard gesture.
    connect(vfoList_, &QListWidget::itemDoubleClicked,
            this, &MainWindow::onVfoItemDoubleClicked);
    // Inline rename commit: persist the user name to vfoNames_ + QSettings.
    connect(vfoList_, &QListWidget::itemChanged,
            this, &MainWindow::onVfoItemEdited);
    // Spectrum band-box interaction <-> engine.
    connect(spectrum_, &ui::SpectrumWidget::vfoMarkerSelected,
            this, [this](int id) { engine_->vfoSelect(id); });
    connect(spectrum_, &ui::SpectrumWidget::vfoMarkerCenterTuned,
            this, [this](int id, double hz) {
        // Unified offset path: every point/drag/wheel tune goes through the
        // engine's offset tuner. The engine decides whether the VFO merely
        // slides its channelizer offset inside the current capture, or (only
        // when it hits the capture edge) retunes the source local oscillator.
        // No UI branch re-tunes the LO per selected VFO any more.
        engine_->vfoSetOffset(id, hz);
    });
    connect(spectrum_, &ui::SpectrumWidget::vfoMarkerBandwidthChanged,
            this, [this](int id, double hz) { engine_->vfoSetBandwidth(id, hz); });

    // ---- Constellation panel (cross-thread: queued) ----------------------
    connect(engine_, &dsp::SpectrumEngine::constellationSymbols,
            this, [this](const std::vector<std::complex<float>>& syms, bool hw) {
        if (constellationView_) constellationView_->feedSymbols(syms, hw);
    }, Qt::QueuedConnection);
    connect(engine_, &dsp::SpectrumEngine::constellationCleared,
            this, [this]() { if (constellationView_) constellationView_->clear(); },
            Qt::QueuedConnection);

    // ---- NOAA APT weather panel (cross-thread: queued) -------------------
    // The engine emits aptImageReady only while the selected VFO is WFM and a
    // row grew or the lock state changed; never a per-sample flood.
    connect(engine_, &dsp::SpectrumEngine::aptImageReady,
            this, [this](const QImage& img, bool locked, int rows, double corr) {
        if (weatherPanel_) weatherPanel_->setImage(img, locked, rows, corr);
    }, Qt::QueuedConnection);
    // Panel "清除图像" -> engine resets its decoder (next loop starts fresh).
    if (weatherPanel_) {
        connect(weatherPanel_, &ui::WeatherSatPanel::clearRequested,
                this, [this]() { engine_->resetAptDecoder(); });
        // One-tune NOAA presets: retune and force WFM so the APT decoder feeds.
        connect(weatherPanel_, &ui::WeatherSatPanel::tuneRequested,
                this, [this](double hz) {
            engine_->onSetCenterFreq(hz);
            freqSpin_->blockSignals(true);
            freqSpin_->setValue(hz / 1e6);
            freqSpin_->blockSignals(false);
            sbVfo_->setText(QString("%1 MHz").arg(hz / 1e6, 0, 'f', 3));
            if (demodCombo_->currentText() != "WFM") {
                const int idx = demodCombo_->findText("WFM");
                if (idx >= 0) demodCombo_->setCurrentIndex(idx);  // -> setDemodMode
            }
        });
    }

    // ---- ANR controls ----
    connect(anrCheck_, &QCheckBox::toggled, this, [this](bool on) {
        engine_->setAnrEnabled(on);
        scheduleSave();
    });
    connect(anrSlider_, &QSlider::valueChanged, this, [this](int v) {
        anrValue_->setText(QString("%1%").arg(v));
        engine_->setAnrStrength(v / 100.0f);
        scheduleSave();
    });

    // ---- Recording options (SDR++-aligned) ----
    connect(recTargetCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
        engine_->setRecTarget(idx == 1 ? dsp::RecTarget::DemodAudio
                                       : dsp::RecTarget::BasebandIQ);
        // Stereo only applies to audio WAV recording.
        recStereoCheck_->setEnabled(idx == 1);
    });
    connect(recTemplateEdit_, &QLineEdit::editingFinished, this, [this]() {
        engine_->setRecFilenameTemplate(recTemplateEdit_->text());
    });
    connect(recStereoCheck_, &QCheckBox::toggled,
            this, [this](bool on) { engine_->setRecStereo(on); });
    connect(recIgnoreSqlChk_, &QCheckBox::toggled,
            this, [this](bool on) { engine_->setRecIgnoreSquelch(on); });

    // Open the on-disk recordings folder in the system file manager.
    connect(openRecDirBtn, &QPushButton::clicked, this, [this]() {
        QString dir = engine_->recordingDir();
        QDir().mkpath(dir);
        if (QDir(dir).isRelative())
            dir = QDir::currentPath() + QLatin1Char('/') + dir;
        QDesktopServices::openUrl(QUrl::fromLocalFile(dir));
    });

    // ---- Advanced RTL-SDR front-end options (forwarded to the source) ----
    connect(dsCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) { engine_->setDirectSampling(idx); });
    connect(offsetChk_, &QCheckBox::toggled,
            this, [this](bool on) { engine_->setOffsetTuning(on); });
    connect(rtlAgcChk_, &QCheckBox::toggled,
            this, [this](bool on) { engine_->setRtlAgc(on); });
    connect(tunerAgcChk_, &QCheckBox::toggled, this, [this](bool on) {
        engine_->setTunerAgc(on);
        // Manual gain slider only matters in manual tuner-gain mode.
        gainSlider_->setEnabled(!on);
    });
    connect(biasTeeChk_, &QCheckBox::toggled,
            this, [this](bool on) { engine_->setBiasTee(on); });
    connect(ppmSpin_, QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, [this](double v) { engine_->setPpm(v); });

    connect(connectBtn_, &QPushButton::clicked, this, [this]() {
        if (connectBtn_->text() == "连接") {
            bool ok;
            if (srcTypeCombo_->currentIndex() == 1) {
                ok = engine_->connectRtlTcp(tcpHostEdit_->text().trimmed(),
                                            static_cast<quint16>(tcpPortSpin_->value()));
                statusBar()->showMessage(ok
                    ? QString("已连接 rtl_tcp %1:%2").arg(tcpHostEdit_->text()).arg(tcpPortSpin_->value())
                    : QString("rtl_tcp 连接失败：%1:%2（使用测试信号）")
                        .arg(tcpHostEdit_->text()).arg(tcpPortSpin_->value()));
            } else {
                ok = engine_->tryConnectRtl();
            }
            connectBtn_->setText(ok ? "断开" : "连接");
        } else {
            engine_->disconnectSource();
            connectBtn_->setText("连接");
        }
    });

    connect(aboutBtn, &QPushButton::clicked, this, [this]() {
        ui::AboutDialog dlg(this);
        dlg.exec();
    });
    connect(helpBtn, &QPushButton::clicked, this, [this]() {
        ui::ShortcutsDialog dlg(this);
        dlg.exec();
    });
    connect(settingsBtn, &QPushButton::clicked, this, [this]() {
        ui::SettingsDialog dlg(this);
        ai::AiConfig cfg;
        cfg.load();
        const QString oldAudioDev = cfg.audioDevice;
        dlg.loadFromConfig(cfg);
        if (dlg.exec() == QDialog::Accepted) {
            dlg.saveToConfig(cfg);
            cfg.save();
            worldView_->setStation(cfg.stationLat, cfg.stationLon);
            if (adsbTracker_) adsbTracker_->setStation(cfg.stationLat, cfg.stationLon);
            // Hot-update satellite forecast: re-fetch + re-propagate without
            // restarting, whenever the station location changed.
            refetchTle();
            // Hot-restart audio on the newly-selected output device, if changed.
            if (cfg.audioDevice != oldAudioDev && engine_->audioOutput()) {
                if (cfg.audioDevice == QStringLiteral("default")) {
                    engine_->audioOutput()->setDevice(QAudioDevice());  // null = default
                } else {
                    const auto devs = QMediaDevices::audioOutputs();
                    for (const auto& d : devs) {
                        if (d.description() == cfg.audioDevice) {
                            engine_->audioOutput()->setDevice(d);
                            break;
                        }
                    }
                }
            }
            // Apply volume live (0..100 -> 0..1).
            if (engine_->audioOutput())
                engine_->audioOutput()->setVolume(dlg.volume() / 100.0);
            // Apply UI scale live: re-tokenize + restyle immediately.
            tokens::setUserScale(dlg.userScale());
            static_cast<QApplication*>(qApp)->setStyleSheet(tokens::buildDarkQss());
            // Re-evaluate AI tab now that the key may have changed.
            const bool hasKey = cfg.isConfigured();
            if (aiInput_) aiInput_->setEnabled(hasKey);
            if (aiStatus_) aiStatus_->setText(hasKey
                ? "已配置 API Key — AI 功能接入中"
                : "AI 助手将在这里接入（需在设置中配置 API Key）");
            if (agent_) agent_->configureFromConfig();
        }
    });

    agent_ = new ai::Agent(this);
    agent_->setEngine(engine_);
    agent_->configureFromConfig();
    // Manual-mode toggle: initial state from the persisted backend value, then
    // two-way wiring. Toggling pushes straight into agent_->setManualMode() which
    // persists and forwards to the worker; no separate QSettings needed here.
    if (aiManualCheck_) {
        QSignalBlocker blk(aiManualCheck_);
        aiManualCheck_->setChecked(agent_->manualMode());
    }
    connect(aiManualCheck_, &QCheckBox::toggled, this,
            [this](bool on) { agent_->setManualMode(on); });
    // ---- Chat rendering: session messages + a SINGLE transient line --------
    // partialReady() replaces the transient (never appends); responseReady()
    // clears the transient + tool notes, appends the final assistant message to
    // the persisted session, and re-renders -- so the final content shows up
    // exactly once, even across many partial chunks.
    connect(agent_, &ai::Agent::responseReady, this, [this](const QString& t) {
        aiToolNotes_.clear();
        aiTransient_.clear();
        aiSessionStore_->appendMessage(aiCurSessionId_,
            mbdsdr::ai::SessionMessage{"assistant", t});
        aiRenderChat();
    });
    connect(agent_, &ai::Agent::partialReady, this, [this](const QString& acc) {
        aiTransient_ = acc;          // replaces the previous transient, no dup
        aiRenderChat();
    });
    connect(agent_, &ai::Agent::contextCompacted, this, [this](const QString& note) {
        aiSessionStore_->appendMessage(aiCurSessionId_,
            mbdsdr::ai::SessionMessage{"summary", note});
        aiRenderChat();
    });
    connect(agent_, &ai::Agent::toolCalled, this, [this](const QString& tool, const QString& result) {
        // A gated (manual-mode) write tool comes back as
        // {"ok":false,"gated":true,"error":"手动模式：未执行 <tool>"}.
        // Annotate it RESTRAINED (a quiet inline note, not a loud sticker);
        // an executed tool keeps the original "调用工具" wording. These notes
        // are transient for the current turn and cleared on the final reply.
        bool gated = false;
        const QJsonDocument doc = QJsonDocument::fromJson(result.toUtf8());
        if (doc.isObject() && doc.object().value("gated").toBool()) gated = true;
        if (gated) {
            aiToolNotes_.append(QString("[已拦截·手动模式: %1]").arg(tool));
        } else {
            aiToolNotes_.append(QString("[调用工具: %1 — %2]").arg(tool, result));
        }
        aiRenderChat();
    });
    connect(sendBtn, &QPushButton::clicked, this, [this]() {
        QString t = aiInput_->text().trimmed();
        if (t.isEmpty()) return;
        aiSessionStore_->appendMessage(aiCurSessionId_,
            mbdsdr::ai::SessionMessage{"user", t});
        aiToolNotes_.clear();
        aiTransient_ = QString::fromUtf8("思考中…");
        aiRenderChat();
        agent_->sendMessage(t);
        aiInput_->clear();
    });
    connect(aiInput_, &QLineEdit::returnPressed, sendBtn, &QPushButton::click);

    // ---- Session management wiring ---------------------------------------
    aiRefreshSessionCombo();
    aiRenderChat();
    connect(aiSessionCombo_, &QComboBox::currentIndexChanged,
            this, &MainWindow::onAiSessionChanged);
    connect(aiNewSessionBtn_, &QPushButton::clicked, this, &MainWindow::onAiNewSession);
    connect(aiRenameSessionBtn_, &QPushButton::clicked, this, &MainWindow::onAiRenameSession);
    connect(aiDeleteSessionBtn_, &QPushButton::clicked, this, &MainWindow::onAiDeleteSession);
    connect(aiCompactCtxBtn_, &QPushButton::clicked, this, &MainWindow::onAiCompactContext);
    connect(aiSessionStore_, &mbdsdr::ai::AiSessionStore::sessionsChanged,
            this, [this]() { aiRefreshSessionCombo(); });
    connect(aiSessionStore_, &mbdsdr::ai::AiSessionStore::currentSessionChanged,
            this, [this]() { aiRenderChat(); });

    // ---- Keyboard tuning ----
    // Left/Right: nudge center frequency by currentStepHz_ (set in the freq
    // group). Shift+Left/Right: fine tune at currentStepHz_/10.
    new QShortcut(QKeySequence(Qt::Key_Right), this, this, [this]() {
        engine_->onSetCenterFreq(freqSpin_->value() * 1e6 + currentStepHz_);
    });
    new QShortcut(QKeySequence(Qt::Key_Left), this, this, [this]() {
        engine_->onSetCenterFreq(freqSpin_->value() * 1e6 - currentStepHz_);
    });
    new QShortcut(QKeySequence(Qt::SHIFT | Qt::Key_Right), this, this, [this]() {
        engine_->onSetCenterFreq(freqSpin_->value() * 1e6 + currentStepHz_ / 10);
    });
    new QShortcut(QKeySequence(Qt::SHIFT | Qt::Key_Left), this, this, [this]() {
        engine_->onSetCenterFreq(freqSpin_->value() * 1e6 - currentStepHz_ / 10);
    });
    // Up/Down: widen/narrow the IF bandwidth (×2 / ÷2, clamped to [1k, 200k]).
    // The bwCombo_ is then snapped to the nearest preset for display.
    auto nudgeBandwidth = [this](double factor) {
        double newBw = currentBwHz_ * factor;
        newBw = std::clamp(newBw, kBwMinHz, kBwMaxHz);
        currentBwHz_ = newBw;
        engine_->setBandwidth(newBw);
        bwCombo_->blockSignals(true);
        bwCombo_->setCurrentIndex(nearestBwPresetIndex(newBw));
        bwCombo_->blockSignals(false);
        statusBar()->showMessage(QString("带宽: %1 Hz").arg(newBw, 0, 'f', 0));
    };
    new QShortcut(QKeySequence(Qt::Key_Up), this, this, [nudgeBandwidth]() { nudgeBandwidth(2.0); });
    new QShortcut(QKeySequence(Qt::Key_Down), this, this, [nudgeBandwidth]() { nudgeBandwidth(0.5); });
    new QShortcut(QKeySequence("Ctrl+R"), this, this, [this]() { recordBtn_->click(); });
    static bool muted = false;
    new QShortcut(QKeySequence(Qt::Key_Space), this, this, [this]() {
        muted = !muted;
        engine_->setMuted(muted);
        statusBar()->showMessage(muted ? "已静音" : "");
    });

    connect(spectrum_, &ui::SpectrumWidget::frequencyChanged, this, [this](double hz) {
        freqSpin_->blockSignals(true);
        freqSpin_->setValue(hz / 1e6);
        freqSpin_->blockSignals(false);
        // The canvas drag-tunes the SELECTED VFO through the same offset path
        // (in-band slide; LO retune only on capture edge, decided by engine).
        const int sel = engine_->selectedVfoId();
        if (sel >= 0) engine_->vfoSetOffset(sel, hz);
    });

    // ---- Debounced persistence: high-frequency signals (zoom/pan every frame,
    // slider/spinbox drags) arm a 500 ms one-shot timer instead of hitting the
    // disk on every event. The timer flushes the real QSettings write. ----
    saveTimer_ = new QTimer(this);
    saveTimer_->setSingleShot(true);
    saveTimer_->setInterval(500);
    connect(saveTimer_, &QTimer::timeout, this, &MainWindow::saveSettings);

    connect(freqSpin_, QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, &MainWindow::scheduleSave);
    connect(stepCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &MainWindow::scheduleSave);
    connect(srCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &MainWindow::scheduleSave);
    connect(demodCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &MainWindow::scheduleSave);
    connect(bwCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &MainWindow::scheduleSave);
    connect(gainSlider_, &QSlider::valueChanged, this, &MainWindow::scheduleSave);
    connect(squelchSlider_, &QSlider::valueChanged, this, &MainWindow::scheduleSave);
    connect(squelchCheck_, &QCheckBox::stateChanged, this, &MainWindow::scheduleSave);
    connect(ppmSpin_, QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, &MainWindow::scheduleSave);
    connect(dsCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &MainWindow::scheduleSave);
    connect(offsetChk_, &QCheckBox::toggled, this, &MainWindow::scheduleSave);
    connect(rtlAgcChk_, &QCheckBox::toggled, this, &MainWindow::scheduleSave);
    connect(tunerAgcChk_, &QCheckBox::toggled, this, &MainWindow::scheduleSave);
    connect(biasTeeChk_, &QCheckBox::toggled, this, &MainWindow::scheduleSave);
    connect(recTargetCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &MainWindow::scheduleSave);
    connect(recTemplateEdit_, &QLineEdit::textEdited, this, &MainWindow::scheduleSave);
    connect(recStereoCheck_, &QCheckBox::toggled, this, &MainWindow::scheduleSave);
    connect(recIgnoreSqlChk_, &QCheckBox::toggled, this, &MainWindow::scheduleSave);
    connect(rightTabs_, &QTabWidget::currentChanged, this, &MainWindow::scheduleSave);
    connect(centerTabs_, &QTabWidget::currentChanged, this, &MainWindow::scheduleSave);

    // Gentle fade-in when switching tabs (150ms). Real-time spectrum/waterfall
    // are untouched; this only dresses the tab content swap.
    auto fadeIn = [](QWidget* w) {
        if (!w) return;
        auto* eff = new QGraphicsOpacityEffect(w);
        eff->setOpacity(0.0);
        w->setGraphicsEffect(eff);
        auto* anim = new QPropertyAnimation(eff, "opacity", eff);
        anim->setDuration(tokens::kAnimMedium1);
        anim->setEasingCurve(QEasingCurve::OutCubic);
        anim->setStartValue(0.0);
        anim->setEndValue(1.0);
        anim->start(QAbstractAnimation::DeleteWhenStopped);
    };
    connect(centerTabs_, &QTabWidget::currentChanged, this,
            [=](int) { fadeIn(centerTabs_->currentWidget()); });
    connect(rightTabs_, &QTabWidget::currentChanged, this,
            [=](int) { fadeIn(rightTabs_->currentWidget()); });
    connect(mainSplitter_, &QSplitter::splitterMoved, this, &MainWindow::scheduleSave);
    connect(spectrum_, &ui::SpectrumWidget::viewChanged, this, &MainWindow::scheduleSave);
    connect(spectrum_, &ui::SpectrumWidget::visibleRangeChanged,
            this, [this](double, double) { scheduleSave(); });

    // Focus-mode toggle: collapse/restore the side rails with a 220ms OutCubic.
    connect(focusBtn_, &QPushButton::toggled, this,
            [this](bool on) { setFocusMode(on, true); });

    // Double-click a splitter handle to reset the 0.22/0.56/0.22 proportions
    // (CarWith mini-map-card "drag to resize, double-click to reset"). Handles
    // exist now that all three widgets have been added.
    if (mainSplitter_) {
        for (int i = 1; i < mainSplitter_->count(); ++i)
            mainSplitter_->handle(i)->installEventFilter(this);
    }

    setControlsEnabled(false);
    restoreUiState();
    engine_->start();
    refreshVfoUi();   // populate the VFO list + band boxes from the engine

    // Startup: honour the saved audio output device (if the user picked a
    // non-default one). On headless boxes the device list is empty, so this is
    // a no-op and the audioOut_ stays disabled.
    {
        ai::AiConfig cfg;
        cfg.load();
        if (cfg.audioDevice != QStringLiteral("default") && engine_->audioOutput()) {
            const auto devs = QMediaDevices::audioOutputs();
            for (const auto& d : devs) {
                if (d.description() == cfg.audioDevice) {
                    engine_->audioOutput()->setDevice(d);
                    break;
                }
            }
        }
    }

    // ---- Satellite pass forecast (real TLE from celestrak.org) ----
    tleClient_ = new dsp::TleClient(this);
    connect(tleClient_, &dsp::TleClient::passesReady,
            this, &MainWindow::onPassesReady);
    connect(tleClient_, &dsp::TleClient::fetchFailed,
            this, &MainWindow::onTleFetchFailed);

    {
        ai::AiConfig cfg;
        cfg.load();
        stationLat_ = cfg.stationLat;
        stationLon_ = cfg.stationLon;
        stationSet_ = cfg.stationSet;
        // Draw the hand-entered station on the map (NaN => no station marker).
        worldView_->setStation(stationLat_, stationLon_);
        if (adsbTracker_) adsbTracker_->setStation(stationLat_, stationLon_);
        refetchTle();
    }

    // ---- GNSS serial receiver (real NMEA device only) ---------------------
    gnssRx_ = new gnss::GnssReceiver(this);
    connect(gnssRx_, &gnss::GnssReceiver::newFix,
            this, &MainWindow::onNewFix);
    connect(gnssRx_, &gnss::GnssReceiver::connectionChanged,
            this, &MainWindow::onGnssConnectionChanged);
    connect(gnssConnectBtn_, &QPushButton::clicked,
            this, &MainWindow::onGnssConnectClicked);
    connect(copyClockBtn_, &QPushButton::clicked,
            this, &MainWindow::copyClockBias);
    // Layer visibility toggles -> WorldView, persisted (debounced).
    connect(layerGnssChk_, &QCheckBox::toggled, this, [this](bool on) {
        worldView_->setLayerVisible(ui::MapLayer::Gnss, on); scheduleSave(); });
    connect(layerAdsbChk_, &QCheckBox::toggled, this, [this](bool on) {
        worldView_->setLayerVisible(ui::MapLayer::Aircraft, on); scheduleSave(); });
    connect(layerSatChk_, &QCheckBox::toggled, this, [this](bool on) {
        worldView_->setLayerVisible(ui::MapLayer::Satellite, on); scheduleSave(); });
    // Bidirectional selection sync: a click on either view drives the other,
    // the elevation plot, and the highlighted pass-table row. The widget slots
    // setSelectedSatellite() do NOT re-emit satelliteSelected, so no loop.
    connect(worldView_, &ui::WorldView::satelliteSelected,
            this, &MainWindow::selectSatelliteByName);
    connect(skyView_, &ui::SkyView::satelliteSelected,
            this, &MainWindow::selectSatelliteByName);

    // Local expiry filter: every 60s drop passes whose LOS already passed.
    tleTimer_ = new QTimer(this);
    tleTimer_->setInterval(60000);
    connect(tleTimer_, &QTimer::timeout, this, [this]() {
        if (passes_.isEmpty()) return;
        QDateTime now = QDateTime::currentDateTimeUtc();
        QList<dsp::SatPass> keep;
        for (const auto& p : passes_)
            if (p.los > now) keep.append(p);
        if (keep.size() != passes_.size()) {
            passes_ = keep;
            fillPassTable();
        }
    });
    tleTimer_->start();

    // Periodic background refresh: re-pull fresh TLE every 30 minutes without
    // disturbing the currently-displayed passes.
    QTimer* refreshTimer = new QTimer(this);
    refreshTimer->setInterval(30 * 60 * 1000);
    connect(refreshTimer, &QTimer::timeout, this, [this]() {
        if (stationSet_) tleClient_->fetch(stationLat_, stationLon_);
    });
    refreshTimer->start();

    // Live satellite position: re-propagate the selected pass every second
    // while it is actually visible.
    liveTimer_ = new QTimer(this);
    liveTimer_->setInterval(1000);
    connect(liveTimer_, &QTimer::timeout, this, &MainWindow::updateLiveSatellite);
    // Always tick: this also drives the sky clock readout + setCurrentTime,
    // independent of whether a satellite pass is currently selected.
    liveTimer_->start();

    // Sky time scrubber: valueChanged -> throttled preview propagation; release
    // -> back to live. During preview the 1 Hz live tick re-propagates the
    // FROZEN preview moment instead of wall-now (see updateLiveSatellite).
    connect(skyTimeSlider_, &QSlider::valueChanged,
            this, &MainWindow::onSkySliderChanged);
    connect(skyTimeSlider_, &QSlider::sliderReleased,
            this, &MainWindow::onSkySliderReleased);
}

MainWindow::~MainWindow() {
    // Stop the GNSS receiver thread first so no late newFix lands mid-teardown.
    if (gnssRx_) { gnssRx_->stop(); gnssRx_->wait(2000); }
    // Flush any pending debounced save so the last 500 ms of tweaks are not lost.
    if (saveTimer_ && saveTimer_->isActive()) {
        saveTimer_->stop();
        saveSettings();
    }
    saveUiState();
    if (engine_) { engine_->shutdown(); engine_->wait(); }
    if (adsbTimer_) adsbTimer_->stop();
    delete adsbTracker_; adsbTracker_ = nullptr;
    // Headless scanner + its elapsed-time clock are plain (non-QObject) objects.
    if (scanTimer_) scanTimer_->stop();
    delete scanner_; scanner_ = nullptr;
    delete scanTickClock_; scanTickClock_ = nullptr;
}

void MainWindow::refreshBmTable() {
    if (!bmTable_) return;
    bmTable_->setRowCount(0);
    const auto& items = bookmarkManager_->list();
    for (const auto& b : items) {
        const int row = bmTable_->rowCount();
        bmTable_->insertRow(row);
        auto set = [&](int col, const QString& text) {
            bmTable_->setItem(row, col, new QTableWidgetItem(text));
        };
        set(0, b.name);
        set(1, QString::number(b.frequencyHz / 1e6, 'f', 3));
        set(2, b.mode);
        set(3, b.bandwidthHz > 0.0 ? QString::number(b.bandwidthHz / 1e3, 'f', 1)
                                   : QString(""));
        // Group column: empty storage renders as "默认" in the table only.
        set(4, b.group.isEmpty() ? QString("默认") : b.group);
    }
}

void MainWindow::scanTimerTick() {
    if (!scanner_) return;
    // First tick after start(): assume one full period; thereafter measure the
    // real elapsed time since the previous tick.
    int elapsed = 50;
    if (scanTickClock_->isValid())
        elapsed = static_cast<int>(scanTickClock_->restart());
    else
        scanTickClock_->start();
    bool needTune = false;
    // lastRssi_ is the REAL engine-measured RSSI (onRssiLevel); no fabricated
    // hits here.
    const double target = scanner_->tick(elapsed, lastRssi_, &needTune);
    if (needTune && engine_)
        engine_->onSetCenterFreq(target);
    updateScanStatus();
}

void MainWindow::updateScanStatus() {
    if (!scanner_) return;
    const dsp::ScanState st = scanner_->state();
    if (scanFreqLabel_)
        scanFreqLabel_->setText(QString("当前 %1 MHz")
                                .arg(scanner_->currentFrequency() / 1e6, 0, 'f', 3));
    QString stateText;
    if (st == dsp::ScanState::Scanning)      stateText = "扫描中";
    else if (st == dsp::ScanState::Paused)   stateText = "已暂停";
    else if (st == dsp::ScanState::Hit)
        stateText = QString("命中 %1 MHz · %2 dBFS")
                    .arg(scanner_->hitFrequency() / 1e6, 0, 'f', 3)
                    .arg(scanner_->lastLevelDb(), 0, 'f', 1);
    else                                     stateText = "空闲";
    if (scanStateLabel_) scanStateLabel_->setText(stateText);

    // Permanent status strip label.
    if (sbScan_) {
        if (st == dsp::ScanState::Scanning) {
            sbScan_->setText("扫描中…");
            sbScan_->setStyleSheet(QString("color:%1; font-weight:600;")
                                   .arg(tokens::kAccent));
        } else if (st == dsp::ScanState::Hit) {
            sbScan_->setText(QString("● 命中 %1 MHz")
                             .arg(scanner_->hitFrequency() / 1e6, 0, 'f', 3));
            sbScan_->setStyleSheet(QString("color:%1; font-weight:600;")
                                   .arg(tokens::kSuccess));
        } else if (st == dsp::ScanState::Paused) {
            sbScan_->setText("已暂停");
            sbScan_->setStyleSheet(QString("color:%1; font-weight:600;")
                                   .arg(tokens::kAccent));
        } else {
            sbScan_->setText("");
        }
    }

    // Button enables / pause-resume label follow the live scanner state.
    if (scanStartBtn_)  scanStartBtn_->setEnabled(st == dsp::ScanState::Idle);
    if (scanStopBtn_)   scanStopBtn_->setEnabled(st != dsp::ScanState::Idle);
    if (scanPauseBtn_) {
        scanPauseBtn_->setEnabled(st == dsp::ScanState::Scanning ||
                                  st == dsp::ScanState::Hit ||
                                  st == dsp::ScanState::Paused);
        scanPauseBtn_->setText(st == dsp::ScanState::Paused ? "继续" : "暂停");
    }
    // "只扫书签" only selectable while Idle.
    if (scanBmOnlyChk_) scanBmOnlyChk_->setEnabled(st == dsp::ScanState::Idle);
    // "存入书签" only meaningful while a real hit is held.
    if (scanSaveBmBtn_) scanSaveBmBtn_->setEnabled(st == dsp::ScanState::Hit);
}

// ===================== 录制库 panel =====================================
void MainWindow::refreshRecLib() {
    if (!recLibList_) return;
    const QString dir = engine_ ? engine_->recordingDir() : QString();
    recLibEntries_ = RecordingLibrary::scan(dir);
    recLibList_->clear();
    for (int i = 0; i < recLibEntries_.size(); ++i) {
        const RecordingEntry& e = recLibEntries_[i];
        const QString t = e.meta.time.isEmpty() ? "--" : e.meta.time;
        const QString f = e.meta.frequencyHz > 0.0
                ? QString("%1 MHz").arg(e.meta.frequencyHz / 1e6, 0, 'f', 3) : "--";
        const QString m = e.meta.mode.isEmpty() ? "--" : e.meta.mode;
        // Two-line row: summary on top, the honest full path underneath.
        auto* item = new QListWidgetItem(
            QString("%1  %2  %3\n%4").arg(t, f, m, QDir::toNativeSeparators(e.wavPath)));
        item->setData(Qt::UserRole, i);
        recLibList_->addItem(item);
    }
    // Honest empty state vs populated list.
    recLibList_->setVisible(!recLibEntries_.isEmpty());
    recLibEmpty_->setVisible(recLibEntries_.isEmpty());
    // Loading a new directory cancels any playback.
    if (recLibPlayTimer_) recLibPlayTimer_->stop();
    recLibPlaying_ = false;
    recLibPcm_.clear();
    recLibPcmPos_ = 0;
    if (recLibPlayBtn_) recLibPlayBtn_->setText("播放");
    if (recLibPlayStatus_) {
        recLibPlayStatus_->setText(recLibEntries_.isEmpty()
            ? QStringLiteral("未加载")
            : QStringLiteral("%1 个文件").arg(recLibEntries_.size()));
    }
}

void MainWindow::onRecLibCopyPath() {
    const int row = recLibList_->currentRow();
    if (row < 0 || row >= recLibEntries_.size()) return;
    QGuiApplication::clipboard()->setText(recLibEntries_[row].wavPath);
    if (recLibPlayStatus_)
        recLibPlayStatus_->setText(QString("已复制: %1").arg(recLibEntries_[row].wavPath));
}

void MainWindow::onRecLibDelete() {
    const int row = recLibList_->currentRow();
    if (row < 0 || row >= recLibEntries_.size()) return;
    const RecordingEntry e = recLibEntries_[row];
    // Confirm before deleting real files (.wav + sidecar .json together).
    const auto ans = QMessageBox::question(
        this, "删除录音",
        QString("删除此录音及其旁证文件？\n\n%1").arg(QFileInfo(e.wavPath).fileName()),
        QMessageBox::Yes | QMessageBox::No, QMessageBox::No);
    if (ans != QMessageBox::Yes) return;
    RecordingLibrary::removeEntry(e);
    refreshRecLib();
}

void MainWindow::onRecLibPlayToggle() {
    // ---- stop / end of file ----
    if (recLibPlaying_) {
        recLibPlayTimer_->stop();
        recLibPlaying_ = false;
        recLibPcmPos_ = 0;
        recLibPcm_.clear();
        if (recLibPlayBtn_) recLibPlayBtn_->setText("播放");
        if (recLibPlayStatus_) recLibPlayStatus_->setText("已停止");
        return;
    }
    // ---- load the selected WAV and start streaming ----
    const int row = recLibList_->currentRow();
    if (row < 0 || row >= recLibEntries_.size()) return;
    const RecordingEntry& e = recLibEntries_[row];
    const WavProbe probe = RecordingLibrary::probeWav(e.wavPath);
    if (!probe.ok) {
        // Honest: non-PCM / truncated / not a WAV -> no fake playback.
        recLibPcm_.clear();
        recLibPcmPos_ = 0;
        if (recLibPlayStatus_)
            recLibPlayStatus_->setText(QString("不支持: %1").arg(probe.error));
        return;
    }
    std::vector<float> pcm;
    if (!RecordingLibrary::decodePcmMonoToFloat(probe, e.wavPath, pcm) || pcm.empty()) {
        if (recLibPlayStatus_)
            recLibPlayStatus_->setText("解码失败");
        return;
    }
    recLibPcm_ = std::move(pcm);
    recLibPcmPos_ = 0;
    recLibPlaying_ = true;
    if (recLibPlayBtn_) recLibPlayBtn_->setText("停止");
    if (recLibPlayStatus_) {
        const double secs = probe.sampleRate > 0
                ? static_cast<double>(recLibPcm_.size()) / probe.sampleRate : 0.0;
        recLibPlayStatus_->setText(
            QString("已加载 · %1 Hz · %2 s · 播放中")
                .arg(probe.sampleRate).arg(secs, 0, 'f', 1));
    }
    recLibPlayTimer_->start();
}

void MainWindow::refreshVfoUi() {
    vfoMarkers_ = engine_->vfoMarkers();
    spectrum_->setVfoMarkers(vfoMarkers_);

    // Rebuild the VFO list: active dot + [user name] (id) + freq MHz + mode + bw.
    vfoList_->blockSignals(true);
    vfoList_->clear();
    vfoList_->setUniformItemSizes(false);
    const int rowH = tokens::scaled(tokens::kVfoRowH);
    vfoList_->setIconSize(QSize(0, 0));
    int selRow = -1;
    for (int i = 0; i < vfoMarkers_.size(); ++i) {
        const auto& m = vfoMarkers_[i];
        auto* it = new QListWidgetItem;
        QColor c = m.color.isValid() ? m.color : QColor(QString::fromUtf8(tokens::kAccent));
        it->setForeground(c);
        it->setData(Qt::UserRole, m.id);
        // Touch-sized rows (>=44 logical px tap target).
        it->setSizeHint(QSize(0, rowH));
        // Inline rename. The editor shows ONLY the raw user name (empty if
        // unnamed), never the formatted display string; on commit
        // onVfoItemEdited() maps it back to the engine id. Set DisplayRole LAST
        // so the formatted text is never clobbered by the EditRole assignment.
        it->setData(Qt::EditRole, vfoNames_.value(m.id, QString()));
        it->setData(Qt::DisplayRole, vfoRowText(m));
        it->setFlags(Qt::ItemIsSelectable | Qt::ItemIsEnabled | Qt::ItemIsEditable);
        vfoList_->addItem(it);
        if (m.selected) selRow = i;
    }
    if (selRow >= 0) vfoList_->setCurrentRow(selRow);
    vfoList_->blockSignals(false);

    // Backfill the single-channel controls from the selected VFO.
    const dsp::VfoMarker* sel = nullptr;
    for (const auto& m : vfoMarkers_) if (m.selected) { sel = &m; break; }
    if (sel) {
        freqSpin_->blockSignals(true);
        freqSpin_->setValue(sel->freqHz / 1e6);
        freqSpin_->blockSignals(false);

        demodCombo_->blockSignals(true);
        int dIdx = demodCombo_->findText(sel->mode);
        if (dIdx >= 0) demodCombo_->setCurrentIndex(dIdx);
        demodCombo_->blockSignals(false);

        bwCombo_->blockSignals(true);
        bwCombo_->setCurrentIndex(nearestBwPresetIndex(sel->bandwidthHz));
        bwCombo_->blockSignals(false);

        currentBwHz_ = sel->bandwidthHz;
        sbMode_->setText(sel->mode);
        sbVfo_->setText(QString("%1  %2 MHz").arg(sel->name).arg(sel->freqHz / 1e6, 0, 'f', 3));

        // Constellation panel: follow the selected VFO's digital mode.
        if (constellationView_) {
            if (sel->mode == "QPSK") {
                constellationView_->setMode(dsp::DigMode::QPSK);
                rightTabs_->setCurrentWidget(constellationView_->parentWidget());
            } else if (sel->mode == "BPSK") {
                constellationView_->setMode(dsp::DigMode::BPSK);
                rightTabs_->setCurrentWidget(constellationView_->parentWidget());
            }
        }
    }
}

QString MainWindow::vfoRowText(const dsp::VfoMarker& m) const {
    // Active marker (restrained): a filled dot for the listened-to VFO, two
    // spaces otherwise -- never loud. Identity: "名字 (id)" when the user gave
    // a name, else just "#id". Then real freq (MHz), mode and IF bandwidth.
    const QString dot = m.selected ? QString::fromUtf8("●") : QString::fromUtf8(" ");
    const QString nm = vfoNames_.value(m.id).trimmed();
    const QString idPart = nm.isEmpty()
        ? QString("#%1").arg(m.id)
        : QString("%1 (%2)").arg(nm, QString::number(m.id));
    // Bandwidth in human units.
    QString bw;
    if (m.bandwidthHz >= 1e6)      bw = QString("%1 MHz").arg(m.bandwidthHz / 1e6, 0, 'f', 2);
    else if (m.bandwidthHz >= 1e3) bw = QString("%1 kHz").arg(m.bandwidthHz / 1e3, 0, 'f', 1);
    else                            bw = QString("%1 Hz").arg(int(m.bandwidthHz));
    return QString("%1 %2  %3 MHz  %4  %5")
        .arg(dot, idPart, QString::number(m.freqHz / 1e6, 'f', 3), m.mode, bw);
}

void MainWindow::vfoCopyUi() {
    if (!engine_) return;
    // Read a FRESH snapshot from the engine (not the throttled UI cache) so the
    // copy always sees the active VFO's true freq/mode/bw.
    const auto markers = engine_->vfoMarkers();
    // Source = the active (selected) VFO; fall back to the currently highlighted
    // row if the active marker isn't in the snapshot for any reason.
    const dsp::VfoMarker* src = nullptr;
    for (const auto& m : markers) if (m.selected) { src = &m; break; }
    if (!src && vfoList_->currentItem()) {
        bool ok = false;
        const int id = vfoList_->currentItem()->data(Qt::UserRole).toInt(&ok);
        if (ok) for (const auto& m : markers) if (m.id == id) { src = &m; break; }
    }
    if (!src) return;
    const double f = src->freqHz, bw = src->bandwidthHz;
    const QString mode = src->mode;
    // Real engine API: add at center, then stamp the source's params onto it.
    // setMode resets bandwidth to the mode default, so set bandwidth AFTER mode.
    engine_->vfoAdd();
    const int newId = engine_->selectedVfoId();   // vfoAdd selects the new VFO
    if (newId > 0) {
        engine_->vfoSetFreq(newId, f);
        engine_->vfoSetMode(newId, mode);
        engine_->vfoSetBandwidth(newId, bw);
    }
    // vfoListChanged arrives asynchronously and refreshes the list.
}

void MainWindow::onVfoItemDoubleClicked(QListWidgetItem* it) {
    if (!it || !engine_) return;
    bool ok = false;
    const int id = it->data(Qt::UserRole).toInt(&ok);
    if (ok && id > 0) engine_->vfoSelect(id);
}

void MainWindow::onVfoItemEdited(QListWidgetItem* it) {
    if (!it) return;
    bool ok = false;
    const int id = it->data(Qt::UserRole).toInt(&ok);
    if (!ok || id <= 0) return;
    const QString name = it->data(Qt::EditRole).toString().trimmed();
    if (name.isEmpty()) vfoNames_.remove(id);
    else                vfoNames_[id] = name;
    saveUiState();     // persist ui/vfoNames immediately
    // Reformat ONLY this row's display text in place. We must NOT clear/rebuild
    // the list here: setData(EditRole) fires this slot synchronously while the
    // delegate still owns the item, and a full refreshVfoUi() would delete `it`
    // out from under the in-flight edit.
    const auto markers = engine_->vfoMarkers();
    for (const auto& m : markers) {
        if (m.id == id) {
            // Temporarily block signals so reformatting doesn't recurse.
            vfoList_->blockSignals(true);
            // Set EditRole FIRST, DisplayRole LAST: assigning EditRole otherwise
            // makes the view fall back to / clobber the displayed text.
            it->setData(Qt::EditRole, name);
            it->setData(Qt::DisplayRole, vfoRowText(m));
            vfoList_->blockSignals(false);
            break;
        }
    }
}

void MainWindow::loadVfoNames() {
    QSettings s("MBDSDR", "MBDSDR");
    vfoNames_.clear();
    const QByteArray raw = s.value("ui/vfoNames").toByteArray();
    if (raw.isEmpty()) return;   // default: empty map, no preset names
    const QJsonDocument doc = QJsonDocument::fromJson(raw);
    if (!doc.isObject()) return;
    const QJsonObject obj = doc.object();
    for (auto it = obj.begin(); it != obj.end(); ++it)
        vfoNames_[it.key().toInt()] = it.value().toString();
}

void MainWindow::saveUiState() {
    QSettings s("MBDSDR", "MBDSDR");
    s.setValue("geometry", saveGeometry());

    // ---- RX state (Hz / dB as stored) ----
    s.setValue("rx/centerFreq", freqSpin_->value() * 1e6);
    s.setValue("rx/tuningStep", currentStepHz_);
    {
        static const double kRates[] = {1.024e6, 2.048e6, 2.4e6, 3.2e6};
        int idx = srCombo_->currentIndex();
        if (idx >= 0 && idx <= 3) s.setValue("rx/sampleRate", kRates[idx]);
    }
    s.setValue("rx/demodMode", demodCombo_->currentText());
    // Persist the live bandwidth (a keyboard nudge may have set a non-preset
    // value); restore snaps the combo to the nearest preset for display.
    s.setValue("rx/bandwidth", currentBwHz_);
    s.setValue("rx/gain", static_cast<double>(gainSlider_->value()));
    s.setValue("rx/squelchEnabled", squelchCheck_->isChecked());
    s.setValue("rx/squelchThreshold", static_cast<float>(squelchSlider_->value()));
    s.setValue("rx/squelchAuto", squelchAutoBtn_->isChecked());
    s.setValue("rx/dbMin", static_cast<float>(spectrum_->dbMinValue()));
    s.setValue("rx/dbMax", static_cast<float>(spectrum_->dbMaxValue()));

    // ---- RTL front-end ----
    s.setValue("rtl/ppm", ppmSpin_->value());
    s.setValue("rtl/directSampling", dsCombo_->currentIndex());
    s.setValue("rtl/rtlAgc", rtlAgcChk_->isChecked());
    s.setValue("rtl/tunerAgc", tunerAgcChk_->isChecked());
    s.setValue("rtl/offsetTuning", offsetChk_->isChecked());
    s.setValue("rtl/biasTee", biasTeeChk_->isChecked());   // default false

    // ---- View ----
    s.setValue("view/zoomFactor", spectrum_->zoomFactor());

    // ---- Recording options ----
    s.setValue("rec/target", recTargetCombo_->currentIndex());
    s.setValue("rec/template", recTemplateEdit_->text());
    s.setValue("rec/stereo", recStereoCheck_->isChecked());
    s.setValue("rec/ignoreSquelch", recIgnoreSqlChk_->isChecked());
    s.setValue("rec/dir", recDirEdit_->text());
    s.setValue("watch/enabled", watchCheck_->isChecked());
    s.setValue("watch/threshold", static_cast<float>(watchThrSlider_->value()));
    s.setValue("watch/prerollMs", watchPrerollSpin_->value() * 1000.0);
    s.setValue("watch/hangMs", watchHangSpin_->value() * 1000.0);
    s.setValue("anr/enabled", anrCheck_->isChecked());
    s.setValue("anr/strength", anrSlider_->value());

    // ---- Layout / tabs / FFT ----
    s.setValue("ui/rightTabIndex", rightTabs_->currentIndex());
    s.setValue("ui/centerTabIndex", centerTabs_->currentIndex());
    if (mainSplitter_) s.setValue("ui/splitterSizes", mainSplitter_->saveState());
    s.setValue(tokens::kSettingsKeyFocusMode, focusMode_);
    s.setValue("rx/fftSize", spectrum_->fftSizeValue());

    // ---- Multi-VFO set (count + per-channel params + selection) ----
    s.setValue("vfo/count", vfoMarkers_.size());
    for (int i = 0; i < vfoMarkers_.size(); ++i) {
        const auto& m = vfoMarkers_[i];
        s.setValue(QString("vfo/%1/freq").arg(i), m.freqHz);
        s.setValue(QString("vfo/%1/mode").arg(i), m.mode);
        s.setValue(QString("vfo/%1/bw").arg(i), m.bandwidthHz);
        s.setValue(QString("vfo/%1/color").arg(i), m.color.name());
        s.setValue(QString("vfo/%1/selected").arg(i), m.selected);
    }

    // ---- User VFO display names (JSON map: "vfo id" -> name). Empty by default,
    // never pre-seeded. Dropped ids simply never appear in the map. ----
    {
        QJsonObject obj;
        for (auto it = vfoNames_.begin(); it != vfoNames_.end(); ++it)
            obj[QString::number(it.key())] = it.value();
        s.setValue("ui/vfoNames", QJsonDocument(obj).toJson(QJsonDocument::Compact));
    }

    // ---- GNSS serial device + world-map layers / view state ----
    if (gnssDeviceEdit_) s.setValue("gnss/device", gnssDeviceEdit_->text());
    if (gnssBaudCombo_)  s.setValue("gnss/baud", gnssBaudCombo_->currentText());
    if (layerGnssChk_)  s.setValue("map/layerGnss", layerGnssChk_->isChecked());
    if (layerAdsbChk_)  s.setValue("map/layerAdsb", layerAdsbChk_->isChecked());
    if (layerSatChk_)   s.setValue("map/layerSat", layerSatChk_->isChecked());
    if (worldView_) {
        const auto vs = worldView_->viewState();
        s.setValue("map/viewLat", vs.lat);
        s.setValue("map/viewLon", vs.lon);
        s.setValue("map/viewZoom", vs.zoom);
    }
    s.sync();
}

void MainWindow::saveSettings() {
    // Thin alias: immediate persistence on every control change.
    saveUiState();
}

void MainWindow::scheduleSave() {
    // Re-arm the single-shot timer; repeated events within 500 ms collapse into
    // a single disk write when it finally fires.
    if (saveTimer_) saveTimer_->start();
}

void MainWindow::setFocusMode(bool on, bool animate) {
    if (!mainSplitter_) return;
    QWidget* left  = mainSplitter_->widget(0);   // leftScroll
    QWidget* right = mainSplitter_->widget(2);   // rightCard
    if (!left || !right) return;
    focusMode_ = on;

    // Stop any in-flight animation so rapid toggling never races.
    if (focusAnimL_) focusAnimL_->stop();
    if (focusAnimR_) focusAnimR_->stop();

    // Paint the active state only on this button (accent text + border),
    // leaving the shared QSS for every other button untouched.
    if (focusBtn_) {
        QSignalBlocker blk(focusBtn_);
        focusBtn_->setChecked(on);
        focusBtn_->setStyleSheet(on
            ? QString("QPushButton { color: %1; border-color: %1; }").arg(tokens::kAccent)
            : QString());
    }

    if (on) {
        // Remember the rails' natural width before collapsing (only when they
        // are currently visible / non-zero, i.e. a real user-driven collapse).
        if (left->isVisibleTo(mainSplitter_)  && left->width()  > 0) leftRailW_  = left->width();
        if (right->isVisibleTo(mainSplitter_) && right->width() > 0) rightRailW_ = right->width();

        if (!animate) {
            left->setMaximumWidth(0);
            right->setMaximumWidth(0);
            left->setVisible(false);
            right->setVisible(false);
        } else {
            auto collapse = [this](QWidget* w, QPropertyAnimation*& anim, int from) {
                if (!anim) {
                    anim = new QPropertyAnimation(w, "maximumWidth", this);
                    anim->setDuration(tokens::kAnimMedium1);
                    anim->setEasingCurve(QEasingCurve::OutCubic);
                }
                anim->setTargetObject(w);
                anim->setStartValue(from);
                anim->setEndValue(0);
                anim->start();
            };
            collapse(left,  focusAnimL_,  left->maximumWidth());
            collapse(right, focusAnimR_, right->maximumWidth());
            // Once the width hits 0, drop the widgets entirely so the center
            // spectrum truly takes the full width.
            QTimer::singleShot(tokens::kAnimMedium1, this, [this, left, right]() {
                if (!focusMode_) return;   // user toggled back mid-animation
                left->setVisible(false);
                right->setVisible(false);
            });
        }
    } else {
        // Bring the rails back. Target the remembered widths; fall back to the
        // frozen 0.22/0.22 ratios if we never recorded them (e.g. restored
        // focus=on at first launch, then the user exits).
        const int total = mainSplitter_->width();
        int lw = leftRailW_  > 0 ? leftRailW_  : int(total * tokens::kRatioLeft);
        int rw = rightRailW_ > 0 ? rightRailW_ : int(total * tokens::kRatioRight);

        left->setVisible(true);
        right->setVisible(true);
        if (!animate) {
            left->setMaximumWidth(lw);
            right->setMaximumWidth(rw);
        } else {
            auto expand = [this](QWidget* w, QPropertyAnimation*& anim, int to) {
                if (!anim) {
                    anim = new QPropertyAnimation(w, "maximumWidth", this);
                    anim->setDuration(tokens::kAnimMedium1);
                    anim->setEasingCurve(QEasingCurve::OutCubic);
                }
                anim->setTargetObject(w);
                anim->setStartValue(0);
                anim->setEndValue(to);
                anim->start();
            };
            expand(left,  focusAnimL_,  lw);
            expand(right, focusAnimR_, rw);
        }
    }
    scheduleSave();
}

void MainWindow::resetSplitterRatios() {
    if (!mainSplitter_) return;
    const int total = mainSplitter_->width();
    if (total <= 0) return;
    const int l = int(total * tokens::kRatioLeft);
    const int r = int(total * tokens::kRatioRight);
    const int c = total - l - r;
    mainSplitter_->setSizes({l, c, r});
}

bool MainWindow::eventFilter(QObject* obj, QEvent* event) {
    // Double-click on a splitter handle resets the three-column proportions.
    // If focus mode is on, the first double-click exits focus (the rails are
    // hidden and setSizes cannot resurrect them until they are visible again).
    if (event->type() == QEvent::MouseButtonDblClick && mainSplitter_
            && (obj == mainSplitter_->handle(1)
                || (mainSplitter_->count() > 2 && obj == mainSplitter_->handle(2)))) {
        if (focusMode_) setFocusMode(false, true);
        else resetSplitterRatios();
        return true;
    }
    return QMainWindow::eventFilter(obj, event);
}

void MainWindow::restoreUiState() {
    QSettings s("MBDSDR", "MBDSDR");
    restoreGeometry(s.value("geometry").toByteArray());

    // User VFO display names (JSON by engine id); default empty, no presets.
    loadVfoNames();

    // Block widget signals while we repopulate controls; we dispatch to the
    // engine explicitly below so each setting is applied exactly once.
    freqSpin_->blockSignals(true);
    srCombo_->blockSignals(true);
    stepCombo_->blockSignals(true);
    demodCombo_->blockSignals(true);
    bwCombo_->blockSignals(true);
    gainSlider_->blockSignals(true);
    squelchSlider_->blockSignals(true);
    squelchCheck_->blockSignals(true);
    dsCombo_->blockSignals(true);
    offsetChk_->blockSignals(true);
    rtlAgcChk_->blockSignals(true);
    tunerAgcChk_->blockSignals(true);
    biasTeeChk_->blockSignals(true);
    ppmSpin_->blockSignals(true);
    recTargetCombo_->blockSignals(true);
    recTemplateEdit_->blockSignals(true);
    recStereoCheck_->blockSignals(true);
    recIgnoreSqlChk_->blockSignals(true);

    // ---- RX ----
    const double centerHz = s.value("rx/centerFreq", 98.5e6).toDouble();
    freqSpin_->setValue(centerHz / 1e6);

    const int savedStep = s.value("rx/tuningStep", 10000).toInt();
    int stepIdx = 4;  // 10 kHz default
    for (int i = 0; i < kStepCount; ++i) {
        if (kStepValuesHz[i] == savedStep) { stepIdx = i; break; }
    }
    stepCombo_->setCurrentIndex(stepIdx);
    currentStepHz_ = kStepValuesHz[stepIdx];
    freqSpin_->setSingleStep(static_cast<double>(currentStepHz_) / 1e6);

    static const double kRates[] = {1.024e6, 2.048e6, 2.4e6, 3.2e6};
    const double rateHz = s.value("rx/sampleRate", 2.4e6).toDouble();
    int srIdx = 2;
    for (int i = 0; i < 4; ++i) if (std::abs(kRates[i] - rateHz) < 1e3) srIdx = i;
    srCombo_->setCurrentIndex(srIdx);

    const QString demod = s.value("rx/demodMode", "NFM").toString();
    int dIdx = demodCombo_->findText(demod);
    if (dIdx < 0) dIdx = 1;  // NFM
    demodCombo_->setCurrentIndex(dIdx);

    const double bwHz = s.value("rx/bandwidth", 12500.0).toDouble();
    bwCombo_->setCurrentIndex(nearestBwPresetIndex(bwHz));

    const double gainDb = s.value("rx/gain", 0.0).toDouble();
    gainSlider_->setValue(static_cast<int>(std::round(gainDb)));
    gainValue_->setText(QString("%1 dB").arg(gainSlider_->value()));

    const bool sqlEn = s.value("rx/squelchEnabled", false).toBool();
    squelchCheck_->setChecked(sqlEn);
    const float sqlThr = s.value("rx/squelchThreshold",
                                static_cast<float>(tokens::kSquelchDefaultDb)).toFloat();
    squelchSlider_->setValue(static_cast<int>(std::round(sqlThr)));
    squelchValue_->setText(QString("%1 dB").arg(squelchSlider_->value()));
    // Restore auto-gate arming. Checking it re-applies floor+margin via the
    // toggled handler (once audio blocks have flowed the real floor is ready).
    squelchAutoBtn_->setChecked(s.value("rx/squelchAuto", false).toBool());

    const float dbMin = s.value("rx/dbMin", static_cast<float>(tokens::kDbLowerDefault)).toFloat();
    const float dbMax = s.value("rx/dbMax", static_cast<float>(tokens::kDbUpperDefault)).toFloat();
    spectrum_->setDbSpinValues(static_cast<int>(std::round(dbMin)),
                               static_cast<int>(std::round(dbMax)));

    // ---- RTL ----
    const double ppm = s.value("rtl/ppm", 0.0).toDouble();
    ppmSpin_->setValue(ppm);
    const int ds = s.value("rtl/directSampling", 0).toInt();
    dsCombo_->setCurrentIndex(std::clamp(ds, 0, 2));
    rtlAgcChk_->setChecked(s.value("rtl/rtlAgc", false).toBool());
    tunerAgcChk_->setChecked(s.value("rtl/tunerAgc", false).toBool());
    offsetChk_->setChecked(s.value("rtl/offsetTuning", false).toBool());
    biasTeeChk_->setChecked(s.value("rtl/biasTee", false).toBool());   // default false

    // ---- SpyServer (default OFF; only binds when the box is checked) ----
    spyserverPortSpin_->blockSignals(true);
    spyserverChk_->blockSignals(true);
    spyserverPortSpin_->setValue(s.value("net/spyPort", 5555).toInt());
    spyserverChk_->setChecked(s.value("net/spyEnabled", false).toBool());
    spyserverPortSpin_->blockSignals(false);
    spyserverChk_->blockSignals(false);
    // If it was left on, actually (re)start the listener now that the port is set.
    if (spyserverChk_->isChecked()) onSpyServerToggled(true);
    else updateSpyServerStatus();

    // ---- View ----
    const double zoom = s.value("view/zoomFactor", 1.0).toDouble();
    spectrum_->setZoomFactor(zoom);

    // ---- Recording options ----
    const int recTarget = s.value("rec/target", 0).toInt();
    recTargetCombo_->setCurrentIndex(std::clamp(recTarget, 0, 1));
    recTemplateEdit_->setText(s.value("rec/template", "{time}_{freq}_{mode}").toString());
    recStereoCheck_->setChecked(s.value("rec/stereo", false).toBool());
    recIgnoreSqlChk_->setChecked(s.value("rec/ignoreSquelch", false).toBool());

    // ---- Watch + directory restore (params before arming) ----
    recDirEdit_->setText(s.value("rec/dir", QStringLiteral("record")).toString());
    engine_->setRecordingDir(recDirEdit_->text());
    watchThrSlider_->setValue(static_cast<int>(std::round(
        s.value("watch/threshold", -50.0f).toFloat())));
    watchThrValue_->setText(QString("%1 dB").arg(watchThrSlider_->value()));
    watchPrerollSpin_->setValue(
        s.value("watch/prerollMs", 400.0).toDouble() / 1000.0);
    watchHangSpin_->setValue(
        s.value("watch/hangMs", 1500.0).toDouble() / 1000.0);
    watchCheck_->setChecked(s.value("watch/enabled", false).toBool());

    anrCheck_->setChecked(s.value("anr/enabled", false).toBool());
    anrSlider_->setValue(s.value("anr/strength", 50).toInt());
    engine_->setAnrEnabled(anrCheck_->isChecked());
    engine_->setAnrStrength(anrSlider_->value() / 100.0f);
    // Stereo only applies in audio mode.
    recStereoCheck_->setEnabled(recTargetCombo_->currentIndex() == 1);

    // ---- Layout / tabs / FFT ----
    rightTabs_->setCurrentIndex(std::clamp(s.value("ui/rightTabIndex", 0).toInt(),
                                           0, rightTabs_->count() - 1));
    centerTabs_->setCurrentIndex(std::clamp(s.value("ui/centerTabIndex", 0).toInt(),
                                            0, centerTabs_->count() - 1));
    const QByteArray splitterState = s.value("ui/splitterSizes").toByteArray();
    if (mainSplitter_ && !splitterState.isEmpty()) mainSplitter_->restoreState(splitterState);
    spectrum_->setFftSizeValue(s.value("rx/fftSize", 2048).toInt());

    // Restore focus mode last (no animation): collapse the rails instantly.
    // Block the button's toggled signal so we don't double-toggle.
    {
        const bool focus = s.value(tokens::kSettingsKeyFocusMode, false).toBool();
        if (focusBtn_) focusBtn_->blockSignals(true);
        setFocusMode(focus, /*animate=*/false);
        if (focusBtn_) focusBtn_->blockSignals(false);
    }

    // Unblock.
    freqSpin_->blockSignals(false);
    srCombo_->blockSignals(false);
    stepCombo_->blockSignals(false);
    demodCombo_->blockSignals(false);
    bwCombo_->blockSignals(false);
    gainSlider_->blockSignals(false);
    squelchSlider_->blockSignals(false);
    squelchCheck_->blockSignals(false);
    dsCombo_->blockSignals(false);
    offsetChk_->blockSignals(false);
    rtlAgcChk_->blockSignals(false);
    tunerAgcChk_->blockSignals(false);
    biasTeeChk_->blockSignals(false);
    ppmSpin_->blockSignals(false);
    recTargetCombo_->blockSignals(false);
    recTemplateEdit_->blockSignals(false);
    recStereoCheck_->blockSignals(false);
    recIgnoreSqlChk_->blockSignals(false);

    // ---- Dispatch restored values to the engine (UI controls already show
    // the right state; push the same values into the running pipeline). ----
    engine_->onSetCenterFreq(centerHz);
    engine_->onSetSampleRate(kRates[srIdx]);
    engine_->setDemodMode(demodCombo_->currentText());
    currentBwHz_ = bwHz;
    engine_->setBandwidth(bwHz);
    engine_->onSetGain(static_cast<double>(gainSlider_->value()));
    engine_->setSquelchEnabled(sqlEn);
    engine_->setSquelchThreshold(static_cast<float>(squelchSlider_->value()));

    engine_->setDirectSampling(dsCombo_->currentIndex());
    engine_->setOffsetTuning(offsetChk_->isChecked());
    engine_->setRtlAgc(rtlAgcChk_->isChecked());
    engine_->setTunerAgc(tunerAgcChk_->isChecked());
    engine_->setBiasTee(biasTeeChk_->isChecked());
    engine_->setPpm(ppmSpin_->value());

    // Recording options
    engine_->setRecTarget(recTargetCombo_->currentIndex() == 1
                          ? dsp::RecTarget::DemodAudio : dsp::RecTarget::BasebandIQ);
    engine_->setRecFilenameTemplate(recTemplateEdit_->text());
    engine_->setRecStereo(recStereoCheck_->isChecked());
    engine_->setRecIgnoreSquelch(recIgnoreSqlChk_->isChecked());
    engine_->setFftSize(spectrum_->fftSizeValue());

    // ---- Multi-VFO set restore (engine starts with one default VFO) -------
    {
        const int vfoCount = s.value("vfo/count", 1).toInt();
        if (vfoCount >= 1) {
            QVector<int> ids;
            ids.push_back(engine_->selectedVfoId());   // existing default VFO
            for (int i = 1; i < vfoCount; ++i) {
                engine_->vfoAdd();                    // auto-selects the new one
                ids.push_back(engine_->selectedVfoId());
            }
            int selId = ids.value(0, 0);
            for (int i = 0; i < vfoCount && i < ids.size(); ++i) {
                const int id = ids[i];
                const double freq = s.value(QString("vfo/%1/freq").arg(i), 98.5e6).toDouble();
                const QString mode = s.value(QString("vfo/%1/mode").arg(i), "NFM").toString();
                const double bw = s.value(QString("vfo/%1/bw").arg(i), 12500.0).toDouble();
                const QString colName = s.value(QString("vfo/%1/color").arg(i), "#7CC4FF").toString();
                engine_->vfoSetMode(id, mode);
                engine_->vfoSetBandwidth(id, bw);
                engine_->vfoSetColor(id, QColor(colName));
                engine_->vfoSetFreq(id, freq);
                if (s.value(QString("vfo/%1/selected").arg(i), false).toBool()) selId = id;
            }
            engine_->vfoSelect(selId);
        }
    }

    // ---- GNSS serial device + world-map layers / view state restore -------
    if (gnssDeviceEdit_)
        gnssDeviceEdit_->setText(s.value("gnss/device", "").toString());
    if (gnssBaudCombo_) {
        const QString baud = s.value("gnss/baud", "9600").toString();
        const int bi = gnssBaudCombo_->findText(baud);
        if (bi >= 0) gnssBaudCombo_->setCurrentIndex(bi);
    }
    if (layerGnssChk_) layerGnssChk_->setChecked(s.value("map/layerGnss", true).toBool());
    if (layerAdsbChk_) layerAdsbChk_->setChecked(s.value("map/layerAdsb", true).toBool());
    if (layerSatChk_)  layerSatChk_->setChecked(s.value("map/layerSat", true).toBool());
    if (worldView_) {
        worldView_->setLayerVisible(ui::MapLayer::Gnss, layerGnssChk_->isChecked());
        worldView_->setLayerVisible(ui::MapLayer::Aircraft, layerAdsbChk_->isChecked());
        worldView_->setLayerVisible(ui::MapLayer::Satellite, layerSatChk_->isChecked());
        ui::MapProjection::ViewState vs;
        vs.lat  = s.value("map/viewLat", 0.0).toDouble();
        vs.lon  = s.value("map/viewLon", 0.0).toDouble();
        vs.zoom = s.value("map/viewZoom", 1.0).toDouble();
        worldView_->setViewState(vs);
    }

    // Manual gain slider only matters in manual tuner-gain mode.
    gainSlider_->setEnabled(!tunerAgcChk_->isChecked());
    // Note: setZoomFactor() above already emitted visibleRangeChanged; the
    // first arriving spectrum frame will re-emit with viewCenterHz_ anchored
    // to the real f0, which is what syncs the waterfall.
}

void MainWindow::onSourceChanged(const QString& name, bool connected) {
    if (connected) {
        hotplugDropped_ = false;
        connectErrorShown_ = false;
    } else if (hotplugDropped_ || connectErrorShown_) {
        // The engine follows a drop / failed connect with the fallback
        // sourceChanged(false); keep the drop / error banner visible instead
        // of showing a plain "test signal" state.
        return;
    }
    statusLabel_->setText(connected ? QString("● %1").arg(name) : QString("● %1 (test)").arg(name));
    if (connectBtn_) connectBtn_->setText(connected ? "断开" : "连接");
    if (sourceBanner_) sourceBanner_->setText(connected
        ? QString("%1 已连接（真实硬件）").arg(name)
        : QStringLiteral("RTL-SDR 未连接，使用测试信号"));
    // Recording needs a live data producer -- hardware OR the offline test
    // signal (which still synthesizes IQ/audio). The engine guards the rest.
    if (recordBtn_) recordBtn_->setEnabled(true);
    sbSdr_->setText(name + (connected ? "" : " (test)"));
    setControlsEnabled(connected);
}

// Hotplug event channel (separate from the 1 Hz telemetry poll): the engine
// detected that a previously connected real device stopped delivering IQ and
// already fell back to the offline test source. Tell the user immediately --
// quiet, neutral, no alarm styling (状态是安静信息).
void MainWindow::onSourceDropped() {
    hotplugDropped_ = true;
    statusLabel_->setText(QStringLiteral("● 设备断开，等待重插"));
    if (connectBtn_) connectBtn_->setText(QStringLiteral("连接"));
    if (sourceBanner_)
        sourceBanner_->setText(QStringLiteral("设备断开，正在等待重新连接…（真实数据流中断）"));
    // Controls stay enabled: the offline test source is honest data (合成测试
    // 信号), and the auto-reconnect may bring the device back at any moment.
}

// A connect attempt failed with a REAL socket reason (refused / timeout /
// host lookup / ...). Never a fabricated cause.
void MainWindow::onSourceError(const QString& message) {
    if (hotplugDropped_) {
        // The device is absent and a reconnect attempt just failed: keep the
        // drop banner, append the real reason as quiet context.
        if (sourceBanner_)
            sourceBanner_->setText(
                QStringLiteral("设备断开，正在等待重新连接…（%1）").arg(message));
        return;
    }
    connectErrorShown_ = true;
    statusLabel_->setText(QStringLiteral("● rtl_tcp 连接失败"));
    if (sourceBanner_)
        sourceBanner_->setText(QStringLiteral("连接失败：%1").arg(message));
}

void MainWindow::onAudioLevel(float dbfs) {
    levelLabel_->setText(QString("电平: %1 dBFS").arg(dbfs, 0, 'f', 1));
    if (levelBar_) {
        int pct = qBound(0, static_cast<int>((dbfs + 60) / 60 * 100), 100);
        levelBar_->setStyleSheet(QString("background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 %1, stop:1 %2); border-radius: %3px;")
            .arg(tokens::kCard1, tokens::kAccent)
            .arg(tokens::kRadiusSmall));
        levelBar_->setText(QString("%1%").arg(pct));
    }
}

void MainWindow::onRssiLevel(float dbfs) {
    lastRssi_ = dbfs;
    if (rssiLabel_)
        rssiLabel_->setText(QString("RSSI: %1 dBFS · SNR: %2 dB")
                            .arg(dbfs, 0, 'f', 1).arg(lastSnr_, 0, 'f', 1));
    // Watch threshold meter: live RSSI against the configured threshold.
    if (watchLevel_)
        watchLevel_->setText(QString("电平 %1 dBFS · 门限 %2")
                             .arg(dbfs, 0, 'f', 1)
                             .arg(watchThrSlider_ ? watchThrSlider_->value() : -50));
    // B5: one-line RSSI readout (real engine value, not a guess).
    if (sbRssi_)
        sbRssi_->setText(QString("RSSI %1").arg(dbfs, 0, 'f', 1));
    // Recording-library panel watch meter (same real RSSI + threshold).
    if (recLibWatchLevel_)
        recLibWatchLevel_->setText(QString("电平 %1 dBFS · 门限 %2")
                             .arg(dbfs, 0, 'f', 1)
                             .arg(watchThrSlider_ ? watchThrSlider_->value() : -50));
}

void MainWindow::onSnrLevel(float snrDb) {
    lastSnr_ = snrDb;
    if (rssiLabel_)
        rssiLabel_->setText(QString("RSSI: %1 dBFS · SNR: %2 dB")
                            .arg(lastRssi_, 0, 'f', 1).arg(snrDb, 0, 'f', 1));
    // B5: one-line SNR readout (real measured SNR).
    if (sbSnr_)
        sbSnr_->setText(QString("SNR %1").arg(snrDb, 0, 'f', 1));
}

void MainWindow::onSourceTelemetry(const QString& name, bool connected,
                                    double centerHz, double sampleRateHz, double gainDb) {
    // Hardware readback values (not the UI requests). Offline test source:
    // connected=false, values are honest synthetic readbacks tagged 非硬件.
    if (sampleRateHz > 0.0) lastSampleRateHz_ = sampleRateHz;
    lastGainDb_ = gainDb;
    if (sbSr_)
        sbSr_->setText(sampleRateHz > 0.0
            ? QString("%1 MS/s").arg(sampleRateHz / 1e6, 0, 'f', 3) : QString("--"));
    if (sbVfo_)
        sbVfo_->setText(centerHz > 0.0
            ? QString("%1 MHz").arg(centerHz / 1e6, 0, 'f', 3) : QString("--"));
    if (sbGain_)
        sbGain_->setText(gainDb > 0.0
            ? QString("增益 %1 dB").arg(gainDb, 0, 'f', 1) : QString("--"));
    if (sbSdr_)
        sbSdr_->setText(name + (connected ? QString() : QStringLiteral("（非硬件）")));
}

void MainWindow::onSpyServerToggled(bool on) {
    if (!spyServer_) return;
    if (on) {
        const quint16 port = static_cast<quint16>(spyserverPortSpin_->value());
        if (!spyServer_->start(port)) {
            // Bind failed (port taken / permission): reflect honestly and
            // uncheck so the UI state matches reality -- never claim listening.
            spyserverChk_->blockSignals(true);
            spyserverChk_->setChecked(false);
            spyserverChk_->blockSignals(false);
            spyserverStatusLabel_->setText(QStringLiteral("监听失败（端口被占?）"));
            return;
        }
    } else {
        spyServer_->stop();
    }
    QSettings("MBDSDR", "MBDSDR").setValue("net/spyEnabled", on);
    scheduleSave();
    updateSpyServerStatus();
}

void MainWindow::updateSpyServerStatus() {
    if (!spyserverStatusLabel_) return;
    if (!spyServer_ || !spyServer_->isListening()) {
        spyserverStatusLabel_->setText(QStringLiteral("未开启"));
        return;
    }
    spyserverStatusLabel_->setText(
        QStringLiteral("监听 %1 · %2 客户端")
            .arg(spyServer_->port()).arg(spyServer_->clientCount()));
}

void MainWindow::onSquelchState(bool open) {
    squelchState_->setText(open ? "状态: OPEN" : "状态: CLOSED");
    // B5: gate strip readout. The engine reports gate-open when squelch is
    // disabled too, so we OR it with the real checkbox enabled state to show
    // OFF honestly rather than a misleading OPEN.
    if (sbSquelch_)
        sbSquelch_->setText(!squelchOn_ ? QStringLiteral("静噪 OFF")
                             : (open ? QStringLiteral("静噪 OPEN")
                                     : QStringLiteral("静噪 CLOSED")));
}

void MainWindow::onRecordingState(bool recording, const QString& path) {
    if (recording) {
        recStatus_->setText("● REC: " + QFileInfo(path).fileName());
    } else {
        recStatus_->setText("空闲");
        if (sbRec_) sbRec_->setText("");
    }
    recordBtn_->setText(recording ? "■ 停止" : "● 录制");
    recordBtn_->setProperty("recording", recording);
    recordBtn_->style()->unpolish(recordBtn_);
    recordBtn_->style()->polish(recordBtn_);
    // Lock the recording target / stereo switch while a file is open --
    // changing them mid-capture would corrupt the in-progress file. The
    // stereo checkbox is re-enabled only for the audio target on stop.
    recTargetCombo_->setEnabled(!recording);
    recStereoCheck_->setEnabled(!recording && (recTargetCombo_->currentIndex() == 1));
}

void MainWindow::onRecordingProgress(const QString& path, int seconds, qint64 bytes) {
    if (!recStatus_) return;
    const int mm = seconds / 60;
    const int ss = seconds % 60;
    const double kb = bytes / 1024.0;
    recStatus_->setText(
        QString("● REC: %1 (%2:%3, %4 KB)")
            .arg(QFileInfo(path).fileName())
            .arg(mm, 2, 10, QLatin1Char('0'))
            .arg(ss, 2, 10, QLatin1Char('0'))
            .arg(kb, 0, 'f', 1));
    if (sbRec_) sbRec_->setText(QString("● REC %1:%2").arg(mm,2,10,QLatin1Char('0')).arg(ss,2,10,QLatin1Char('0')));
}

void MainWindow::onRecordClicked() {
    if (recordBtn_->text().contains("停止")) engine_->stopRecording();
    else engine_->startRecording();
}

void MainWindow::onCwDecoded(const QString& text, double wpm) {
    if (cwEmpty_) cwEmpty_->hide();
    cwText_->appendPlainText(text);
    cwWpm_->setText(QString("WPM: %1").arg(wpm, 0, 'f', 1));
}

void MainWindow::onRdsUpdated(const QString& ps, int pty,
                              const QString& rt, bool locked) {
    if (!sbRds_) return;
    // Honest-data rule: no CRC-verified groups -> label stays EMPTY. We never
    // invent a station name, and switching to a non-WFM mode simply stops
    // emitting (the last real RDS readout stays sticky, like a real car radio).
    if (!locked) {
        sbRds_->setText("");
        sbRds_->setToolTip("");
        return;
    }
    const QString name   = ps.trimmed();
    const QString rtText = rt.trimmed();
    if (name.isEmpty() && rtText.isEmpty()) {
        sbRds_->setText("");
        sbRds_->setToolTip("");
        return;
    }
    QString line = QString("RDS: %1 · PTY %2").arg(name).arg(pty);
    if (!rtText.isEmpty()) line += QString(" · %1").arg(rtText);
    // Long RadioText is elided in the strip; the full text goes to the tooltip.
    const QFontMetrics fm(sbRds_->font());
    sbRds_->setText(fm.elidedText(line, Qt::ElideRight, tokens::scaled(260)));
    sbRds_->setToolTip(rtText.isEmpty() ? name : rtText);
}

void MainWindow::onStereoState(bool stereo, float blend, float pilotQuality) {
    if (!channelBadge_) return;
    // Honest-pilot rule: the engine only reports stereo=true when the real 19 kHz
    // pilot is locked and the matrix blend has actually come up. Everything else
    // (weak/missing pilot, non-WFM mode, forced mono) shows "单声道".
    const char* color = stereo ? tokens::kSuccess : tokens::kTextSecondary;
    channelBadge_->setText(stereo ? QStringLiteral("立体声") : QStringLiteral("单声道"));
    channelBadge_->setStyleSheet(
        QString("QLabel#channelBadge { color: %1; }").arg(QString::fromUtf8(color)));
    if (stereo)
        channelBadge_->setToolTip(
            QStringLiteral("导频锁定 · 混合 %.0f%% · 导频质量 %.2f")
                .arg(blend * 100.0f, 0, 'f', 0).arg(pilotQuality));
    else
        channelBadge_->setToolTip(QStringLiteral("单声道（无锁定导频）"));
}

void MainWindow::onAdsbAircraft(const dsp::AircraftInfo& info) {
    if (!adsbTracker_) return;
    // Merge into the tracker (keyed by ICAO, field-honest, bounded tail).
    adsbTracker_->upsert(info, QDateTime::currentDateTime());
    refreshAdsbTable();
}

void MainWindow::refreshAdsbTable() {
    if (!adsbTracker_ || !adsbTable_) return;
    // Rebuild the table rows from the tracker's merged snapshots (fix or not).
    auto ac = adsbTracker_->aircraft();
    adsbTable_->setRowCount(0);
    adsbRow_.clear();
    for (const auto& a : ac) {
        int row = adsbTable_->rowCount();
        adsbTable_->insertRow(row);
        adsbRow_[a.icao] = row;
        const auto set = [&](int col, const QString& s) {
            adsbTable_->setItem(row, col, new QTableWidgetItem(s));
        };
        set(0, a.icao);
        set(1, a.callsign.isEmpty() ? "--" : a.callsign);
        set(2, a.altitudeFt > 0 ? QString::number(a.altitudeFt) : "--");
        set(3, a.hasVelocity ? QString::number(a.groundspeedKt, 'f', 0) : "--");
        set(4, a.hasVelocity ? QString::number(a.headingDeg, 'f', 0) : "--");
        set(5, a.hasVerticalRate ? QString::number(a.verticalRateFpm) : "--");
        // Distance column: finite haversine when station + aircraft position both
        // known; otherwise an honest "--" (never a fabricated number).
        double d = adsbTracker_->distanceKm(a.icao);
        set(6, std::isfinite(d) ? QString::number(d, 'f', 1) : "--");
        set(7, a.lastSeen.toString("HH:mm:ss"));
    }
    // Map: points() only contains aircraft with a real decoded position --
    // callsign-only rows never produce a fake map dot.
    if (worldView_) worldView_->setAircraft(adsbTracker_->points());
    // Honest empty state.
    if (adsbTracker_->isEmpty()) {
        if (adsbEmpty_) adsbEmpty_->show();
        if (worldView_) worldView_->setAircraft({});
    } else {
        if (adsbEmpty_) adsbEmpty_->hide();
    }
}

void MainWindow::onAdsbPrune() {
    if (!adsbTracker_) return;
    QDateTime now = QDateTime::currentDateTime();
    QStringList removed = adsbTracker_->prune(now);
    // Rebuild only when something actually expired, or the list drained to empty
    // (so the empty-state label reappears). Steady state stays untouched.
    if (!removed.isEmpty() || adsbTracker_->isEmpty())
        refreshAdsbTable();
}

void MainWindow::setControlsEnabled(bool hw) {
    freqSpin_->setEnabled(hw);
    srCombo_->setEnabled(hw);
    // Manual gain slider only when hardware connected AND tuner in manual mode.
    gainSlider_->setEnabled(hw && !(tunerAgcChk_ && tunerAgcChk_->isChecked()));
    if (advPanel_) advPanel_->setEnabled(hw);
}

void MainWindow::refetchTle() {
    // Pull fresh TLE + recompute passes for the currently-configured station.
    // No station -> honest empty state, never a network call.
    liveRow_ = -1;
    capturedIdx_ = -1;
    dopplerLimiter_.disarm();
    if (dopplerCompChk_ && dopplerCompChk_->isChecked())
        dopplerCompChk_->setChecked(false);
    skyView_->clearLiveSatellites();
    if (!stationSet_ || !std::isfinite(stationLat_) || !std::isfinite(stationLon_)) {
        passes_.clear();
        passTable_->setRowCount(0);
        skyView_->setPasses({});
        skyEmptyLabel_->setText("无过境数据——请在设置中填写本站位置");
        skyEmptyLabel_->show();
        return;
    }
    // Feed station position to ADS-B decoder for CPR local decode (single-frame fix).
    engine_->setAdsbReferencePosition(stationLat_, stationLon_);
    // Cache-first: if we have a recent (<48h) TLE cache, show it immediately
    // and refresh in the background rather than blocking on the network.
    dsp::TleCache cache = tleClient_->cachedTle();
    const qint64 ageSec = cache.valid
        ? cache.fetchedAt.secsTo(QDateTime::currentDateTimeUtc()) : -1;
    if (cache.valid && ageSec >= 0 && ageSec < 48 * 3600) {
        usingBuiltinTle_ = false;
        tleClient_->computeFromEntries(cache.entries, stationLat_, stationLon_);
        tleClient_->fetch(stationLat_, stationLon_);   // background refresh
    } else {
        // No fresh cache: show the built-in offline SGP4 verification snapshot
        // immediately so the sky is never empty, then try to refresh from the
        // network in the background.  We never present the 2006-era offline
        // elements as fresh data -- the badge below says so explicitly.
        usingBuiltinTle_ = true;
        tleClient_->computeFromEntries(dsp::TleClient::builtinTle(),
                                       stationLat_, stationLon_);
        tleFetchActive_ = true;
        tleClient_->fetch(stationLat_, stationLon_);
    }
}

void MainWindow::onPassesReady(QList<dsp::SatPass> passes) {
    passes_ = std::move(passes);
    tleFetchActive_ = false;
    liveRow_ = -1;
    // The whole pass list was recomputed: any prior capture points at stale
    // indices, so release it and let fillPassTable re-pick + re-enable.
    capturedIdx_ = -1;
    dopplerLimiter_.disarm();
    if (dopplerCompChk_ && dopplerCompChk_->isChecked())
        dopplerCompChk_->setChecked(false);
    skyView_->clearLiveSatellites();
    if (passes_.isEmpty()) {
        skyEmptyLabel_->setText("未来 24h 无过境");
        skyEmptyLabel_->show();
    } else {
        skyEmptyLabel_->hide();
    }
    fillPassTable();
}

void MainWindow::onTleFetchFailed(const QString& reason) {
    tleFetchActive_ = false;
    // Network failed: fall back to whatever cache we have (even if stale),
    // rather than dropping to an empty sky.
    dsp::TleCache cache = tleClient_->cachedTle();
    if (cache.valid && !cache.entries.isEmpty()) {
        usingBuiltinTle_ = false;   // we have real (cached) elements now
        tleClient_->computeFromEntries(cache.entries, stationLat_, stationLon_);
        statusBar()->showMessage("TLE 已过期（缓存时间 " +
            cache.fetchedAt.toLocalTime().toString("MM-dd HH:mm") + "）：" + reason);
        return;
    }
    // No cache at all.  If we already populated the table from the built-in
    // offline snapshot, keep it and report the network failure honestly instead
    // of wiping the sky to an empty state.
    if (usingBuiltinTle_ && !passes_.isEmpty()) {
        updateTleBadge();
        statusBar()->showMessage("在线 TLE 拉取失败，当前为内置离线星历（可能已过期）：" + reason);
        return;
    }
    passes_.clear();
    liveRow_ = -1;
    skyView_->clearLiveSatellites();
    passTable_->setRowCount(0);
    skyView_->setPasses({});
    skyEmptyLabel_->setText("无过境数据——TLE 拉取失败");
    skyEmptyLabel_->show();
    statusBar()->showMessage("TLE 拉取失败：" + reason);
}

static QString countdownText(const dsp::SatPass& p, const QDateTime& now) {
    if (now >= p.aos && now <= p.los) return QStringLiteral("进行中");
    if (now < p.aos) {
        qint64 secs = now.secsTo(p.aos);
        if (secs <= 120) return QStringLiteral("即将过顶");
        if (secs < 3600) return QStringLiteral("%1 分后").arg(secs / 60);
        return QStringLiteral("%1 小时后").arg(secs / 3600.0, 0, 'f', 1);
    }
    return QStringLiteral("已结束");
}

void MainWindow::fillPassTable() {
    // Sorting would shuffle rows mid-population; build unsorted then enable.
    passTable_->setSortingEnabled(false);
    passTable_->setRowCount(0);
    QList<ui::PassArc> arcs;
    QDateTime now = QDateTime::currentDateTimeUtc();

    QSettings s;
    QString wanted = s.value("ui/selectedSatellite").toString();
    int wantedRow = -1, firstActive = -1;

    for (int i = 0; i < passes_.size(); ++i) {
        const auto& p = passes_[i];
        int row = passTable_->rowCount();
        passTable_->insertRow(row);
        const bool active = now >= p.aos && now <= p.los;
        if (active && firstActive < 0) firstActive = row;

        auto* nameItem = new QTableWidgetItem(
            (active ? QStringLiteral("● ") : QString()) + p.name);
        nameItem->setData(Qt::UserRole, i);   // visual row -> passes_ index
        passTable_->setItem(row, 0, nameItem);

        auto* aosItem = new QTableWidgetItem(p.aos.toLocalTime().toString("MM-dd HH:mm"));
        aosItem->setData(Qt::UserRole, p.aos.toMSecsSinceEpoch());
        passTable_->setItem(row, 1, aosItem);

        auto* losItem = new QTableWidgetItem(p.los.toLocalTime().toString("MM-dd HH:mm"));
        losItem->setData(Qt::UserRole, p.los.toMSecsSinceEpoch());
        passTable_->setItem(row, 2, losItem);

        auto* elItem = new QTableWidgetItem(
            QString::number(p.maxEl, 'f', 1) + QStringLiteral("°"));
        elItem->setData(Qt::UserRole, p.maxEl);
        passTable_->setItem(row, 3, elItem);

        // Predicted receive Doppler at peak elevation, for the nominal downlink.
        // Unknown carrier (f0==0) => honest em-dash, never a guessed number.
        QString dopText = QStringLiteral("—");
        if (p.f0DownlinkHz > 0.0) {
            double kHz = p.dopplerAtPeakHz / 1000.0;
            dopText = (kHz >= 0.0 ? QStringLiteral("+") : QStringLiteral("-"))
                    + QString::number(std::fabs(kHz), 'f', 1)
                    + QStringLiteral(" kHz");
        }
        auto* dopItem = new QTableWidgetItem(dopText);
        dopItem->setData(Qt::TextAlignmentRole, Qt::AlignCenter);
        dopItem->setData(Qt::UserRole, p.dopplerAtPeakHz);
        passTable_->setItem(row, 4, dopItem);

        auto* cdItem = new QTableWidgetItem(countdownText(p, now));
        cdItem->setData(Qt::UserRole, now.secsTo(p.aos));
        passTable_->setItem(row, 5, cdItem);

        if (active) {
            QBrush hi(QColor(tokens::kSuccess));
            for (int c = 0; c < 6; ++c)
                passTable_->item(row, c)->setBackground(hi);
        }
        if (!wanted.isEmpty() && p.name == wanted && wantedRow < 0) wantedRow = row;

        ui::PassArc arc;
        arc.name = p.name;
        arc.track = p.track;
        arc.aosUtc = p.aos;   // honest AOS/LOS/max-elevation carried into the sky view
        arc.losUtc = p.los;
        arc.maxEl  = p.maxEl;
        arcs.append(arc);
    }
    skyView_->setPasses(arcs);

    // Default sort: AOS ascending.
    passTable_->setSortingEnabled(true);
    passTable_->sortByColumn(1, Qt::AscendingOrder);

    updateTleBadge();

    // Prefer the remembered satellite, else the first pass currently in view.
    int pick = (wantedRow >= 0) ? wantedRow : firstActive;
    if (pick >= 0) {
        passTable_->selectRow(pick);
        onPassRowClicked(pick);
    }
}

void MainWindow::refreshCountdowns() {
    if (passTable_->rowCount() == 0) return;
    QDateTime now = QDateTime::currentDateTimeUtc();
    for (int row = 0; row < passTable_->rowCount(); ++row) {
        QTableWidgetItem* idxItem = passTable_->item(row, 0);
        if (!idxItem) continue;
        int i = idxItem->data(Qt::UserRole).toInt();
        if (i < 0 || i >= passes_.size()) continue;
        passTable_->item(row, 5)->setText(countdownText(passes_[i], now));
    }
    // Quiet pre-AOS reminder for the selected satellite: within 2 minutes,
    // no system notification, just the status bar.
    if (liveRow_ >= 0 && liveRow_ < passes_.size()) {
        const dsp::SatPass& p = passes_[liveRow_];
        qint64 secs = now.secsTo(p.aos);
        if (secs > 0 && secs <= 120) {
            statusBar()->showMessage(
                QStringLiteral("%1 即将过顶（%2 秒）").arg(p.name).arg(secs));
        }
    }
}

void MainWindow::updateTleBadge() {
    if (!tleBadge_) return;
    dsp::TleCache cache = tleClient_->cachedTle();
    if (!cache.valid) {
        // Nothing fresh on disk.  If the table is showing the built-in offline
        // snapshot, label it honestly; otherwise stay blank.
        if (usingBuiltinTle_ && !passes_.isEmpty()) {
            tleBadge_->setText(QStringLiteral("内置离线 TLE（公开星历快照，可能已过期）"));
            tleBadge_->setStyleSheet(QString("color: %1;").arg(tokens::kWarning));
        } else {
            tleBadge_->clear();
        }
        return;
    }
    qint64 ageH = cache.fetchedAt.secsTo(QDateTime::currentDateTimeUtc()) / 3600;
    bool stale = ageH >= 48;
    tleBadge_->setText(QString("TLE 更新于 %1（%2）")
        .arg(cache.fetchedAt.toLocalTime().toString("MM-dd HH:mm"),
             stale ? QStringLiteral("过期") : QStringLiteral("新鲜")));
    tleBadge_->setStyleSheet(QString("color: %1;")
        .arg(stale ? tokens::kWarning : tokens::textRgba(tokens::kTextAlphaTertiary)));
}

void MainWindow::onPassRowClicked(int row) {
    if (row < 0 || row >= passTable_->rowCount()) return;
    QTableWidgetItem* idxItem = passTable_->item(row, 0);
    if (!idxItem) return;
    int idx = idxItem->data(Qt::UserRole).toInt();
    if (idx < 0 || idx >= passes_.size()) return;
    liveRow_ = idx;
    liveTimer_->start();
    // A new selection releases any prior capture / Doppler lock (the lock is
    // bound to exactly the row the user captured).
    capturedIdx_ = -1;
    dopplerLimiter_.disarm();
    if (dopplerCompChk_ && dopplerCompChk_->isChecked())
        dopplerCompChk_->setChecked(false);
    // Persist the user's choice.
    const QString name = passes_[idx].name;
    QSettings("MBDSDR", "MBDSDR").setValue("ui/selectedSatellite", name);
    // Drive both views + elevation plot from the TLE name (slots don't re-emit).
    worldView_->setSelectedSatellite(name);
    skyView_->setSelectedSatellite(name);
    updateElevationPlotFor(passes_[idx]);
    updateCaptureControls();
    updateLiveSatellite();   // paint immediately rather than waiting 1s
}

void MainWindow::onCapturePassClicked() {
    if (liveRow_ < 0 || liveRow_ >= passes_.size()) return;
    const dsp::SatPass& p = passes_[liveRow_];
    // Honest refusal: no nominal downlink carrier, nothing to tune to.
    if (p.f0DownlinkHz <= 0.0) {
        captureStatusLabel_->setText(QStringLiteral("无下行频率数据，无法捕获"));
        return;
    }
    // Retune target = nominal downlink + predicted peak-Doppler suggestion.
    const double target = core::captureTargetHz(p.f0DownlinkHz, p.dopplerAtPeakHz);
    const core::SatChannelMode ch = core::recommendSatelliteMode(p.f0DownlinkHz);

    // Apply to the ACTIVE VFO through the real engine API (SDR++-style offset
    // tuner: in-band slide, genuine LO retune only when the target crosses the
    // capture edge). Mode/bandwidth from the frequency-domain recommendation.
    const int selVfo = engine_->selectedVfoId();
    if (selVfo >= 0) {
        engine_->vfoSetOffset(selVfo, target);
        engine_->vfoSetMode(selVfo, ch.mode);
        engine_->vfoSetBandwidth(selVfo, ch.bandwidthHz);
    }

    capturedIdx_ = liveRow_;
    dopplerLimiter_.reset(target);   // first live step continues from here

    // Keep the left-panel readouts honest (mirrors the weather one-tune presets).
    if (freqSpin_) {
        freqSpin_->blockSignals(true);
        freqSpin_->setValue(target / 1.0e6);
        freqSpin_->blockSignals(false);
    }
    if (demodCombo_) {
        demodCombo_->blockSignals(true);
        const int dIdx = demodCombo_->findText(ch.mode);
        if (dIdx >= 0) demodCombo_->setCurrentIndex(dIdx);
        demodCombo_->blockSignals(false);
    }
    currentBwHz_ = ch.bandwidthHz;
    if (sbVfo_) sbVfo_->setText(QString("%1 MHz").arg(target / 1.0e6, 0, 'f', 4));

    captureStatusLabel_->setText(QStringLiteral("已捕获 · 调谐 %1 MHz · %2")
        .arg(target / 1.0e6, 0, 'f', 4).arg(ch.mode));
    updateCaptureControls();
}

void MainWindow::onDopplerCompToggled(bool on) {
    if (on) {
        // Honest preconditions: a station (for live propagation) and a capture.
        if (!stationSet_) {
            const QSignalBlocker block(dopplerCompChk_);
            dopplerCompChk_->setChecked(false);
            captureStatusLabel_->setText(QStringLiteral("需先设置本站位置"));
            return;
        }
        if (capturedIdx_ < 0) {
            const QSignalBlocker block(dopplerCompChk_);
            dopplerCompChk_->setChecked(false);
            captureStatusLabel_->setText(QStringLiteral("请先捕获一个过境"));
            return;
        }
        // The 1 Hz loop (updateLiveSatellite) now drives dopplerLimiter_.advance()
        // and retunes the active VFO; the label flips to "锁定中·多普勒补偿…".
    } else {
        dopplerLimiter_.disarm();
        // Back to the captured (but not auto-tracking) state, or un-locked.
        if (capturedIdx_ >= 0 && capturedIdx_ < passes_.size()) {
            const dsp::SatPass& p = passes_[capturedIdx_];
            const core::SatChannelMode ch = core::recommendSatelliteMode(p.f0DownlinkHz);
            const double target = core::captureTargetHz(p.f0DownlinkHz, p.dopplerAtPeakHz);
            captureStatusLabel_->setText(QStringLiteral("已捕获 · 调谐 %1 MHz · %2")
                .arg(target / 1.0e6, 0, 'f', 4).arg(ch.mode));
        } else {
            captureStatusLabel_->setText(QStringLiteral("未锁定"));
        }
    }
    updateCaptureControls();
}

void MainWindow::updateCaptureControls() {
    if (!capturePassBtn_) return;
    const bool hasSel = liveRow_ >= 0 && liveRow_ < passes_.size();
    const double f0 = hasSel ? passes_[liveRow_].f0DownlinkHz : 0.0;
    // Capture button: enabled only with a selected row that has a known carrier.
    capturePassBtn_->setEnabled(hasSel && f0 > 0.0);
    // The compensation checkbox needs a station AND a captured pass.
    dopplerCompChk_->setEnabled(stationSet_ && capturedIdx_ >= 0);
    if (!stationSet_) {
        dopplerCompChk_->setToolTip(QStringLiteral("多普勒自动补偿需先在设置中填写本站位置"));
    } else if (capturedIdx_ < 0) {
        dopplerCompChk_->setToolTip(QStringLiteral("请先在过境列表中选中并「捕获」一个过境"));
    } else {
        dopplerCompChk_->setToolTip(QStringLiteral(
            "过境进行中时，每秒用真实轨道传播的距离变化率\n"
            "实时微调活动 VFO 频率（f0+fd），限幅步进防抖动。"));
    }
    // Status line for the not-yet-captured states (the 1 Hz loop owns the
    // "已捕获…" / "锁定中…" strings while a lock is active).
    if (capturedIdx_ < 0) {
        if (!hasSel) captureStatusLabel_->setText(QStringLiteral("未锁定"));
        else if (f0 <= 0.0) captureStatusLabel_->setText(QStringLiteral("无下行频率数据"));
        else captureStatusLabel_->setText(QStringLiteral("未锁定"));
    }
}

static QString formatRange(double km) {
    if (km >= 10000.0)
        return QStringLiteral("%1 万 km").arg(km / 10000.0, 0, 'f', 1);
    return QStringLiteral("%1 km").arg(km, 0, 'f', 0);
}

void MainWindow::updateLiveSatellite() {
    refreshCountdowns();
    // 1 Hz wall clock: drive the polar plot's UTC marker and the clock-bias
    // readout regardless of whether a station / pass exists.
    const QDateTime now = QDateTime::currentDateTimeUtc();
    // Preview scrubber: when dragging, the sky shows the frozen preview moment
    // instead of wall-now; every position below is propagated at viewT (real
    // SGP4). Wall-`now` still drives pass-end housekeeping + Doppler only when
    // live.
    const QDateTime viewT = previewMode_ ? previewUtc_ : now;
    skyView_->setCurrentTime(viewT);
    updateClockBiasLabel();

    if (!stationSet_) {
        skyView_->clearLiveSatellites();
        skyView_->setSelectedTrajectory({});
        // Without a station there is no live propagation: compensation cannot run.
        capturedIdx_ = -1;
        dopplerLimiter_.disarm();
        if (dopplerCompChk_ && dopplerCompChk_->isChecked())
            dopplerCompChk_->setChecked(false);
        updateCaptureControls();
        return;
    }

    // If the selected pass just ended, advance to the next one in view.
    // Skipped while previewing (scrubbing past the end must not jump selection).
    if (!previewMode_ && liveRow_ >= 0 && liveRow_ < passes_.size() && now > passes_[liveRow_].los) {
        liveRow_ = -1;
        skyView_->setSelectedSatellite("");
        // The captured pass is gone: stop Doppler compensation and release.
        capturedIdx_ = -1;
        dopplerLimiter_.disarm();
        if (dopplerCompChk_ && dopplerCompChk_->isChecked())
            dopplerCompChk_->setChecked(false);
        for (int row = 0; row < passTable_->rowCount(); ++row) {
            QTableWidgetItem* it = passTable_->item(row, 0);
            if (!it) continue;
            int i = it->data(Qt::UserRole).toInt();
            if (i >= 0 && i < passes_.size() &&
                passes_[i].aos <= now && now <= passes_[i].los) {
                passTable_->selectRow(row);
                onPassRowClicked(row);   // recurses once via updateLiveSatellite
                return;
            }
        }
        updateCaptureControls();
        liveTimer_->stop();
    }

    // Draw every satellite visible at the displayed moment (viewT); mark the
    // selected one. In preview mode viewT is the scrubbed time, not wall-now.
    QList<ui::LiveSat> sats;
    for (int i = 0; i < passes_.size(); ++i) {
        const dsp::SatPass& p = passes_[i];
        if (viewT < p.aos || viewT > p.los) continue;   // past LOS / pre-AOS: skip
        dsp::Topocentric t = tleClient_->propagateAt(viewT, p.tle,
                                                     stationLat_, stationLon_);
        ui::LiveSat ls;
        ls.az = t.az;
        ls.el = t.el;
        ls.name = p.name;
        ls.selected = (i == liveRow_);
        sats.append(ls);

        // Status-bar readout + live Doppler retune are LIVE only: scrubbing a
        // past/future moment must not retune the tuner.
        if (ls.selected && !previewMode_) {
            QString msg = QString("%1 方位=%2° 仰角=%3° 距离=%4")
                .arg(p.name).arg(t.az, 0, 'f', 0).arg(t.el, 0, 'f', 1)
                .arg(formatRange(t.range));
            // Live Doppler from the current range-rate, and the resulting
            // suggested tuning frequency (only when a nominal downlink is known).
            double liveFd = 0.0;
            if (p.f0DownlinkHz > 0.0) {
                liveFd = dsp::dopplerHz(p.f0DownlinkHz, t.rangeRateKmS);
                const double tune = p.f0DownlinkHz + liveFd;
                msg += QString(" · 预测多普勒 %1 kHz · 建议调谐 %2 MHz")
                    .arg(liveFd / 1000.0, 0, 'f', 1)
                    .arg(tune / 1.0e6, 0, 'f', 3);
            }
            statusBar()->showMessage(msg);

            // ---- Live Doppler auto-compensation (real propagated range-rate) --
            // Only while checked AND this exact pass is the one we captured AND
            // it is within AOS..LOS.  The limiter bounds each 1 Hz retune so the
            // tuner converges on f0+fd without dithering.
            const bool compOn = dopplerCompChk_ && dopplerCompChk_->isChecked();
            if (compOn && capturedIdx_ == i && p.f0DownlinkHz > 0.0) {
                const double target = p.f0DownlinkHz + liveFd;
                const double stepped = dopplerLimiter_.advance(target);
                const int selVfo = engine_->selectedVfoId();
                if (selVfo >= 0) engine_->vfoSetOffset(selVfo, stepped);
                if (sbVfo_) sbVfo_->setText(
                    QString("%1 MHz").arg(stepped / 1.0e6, 0, 'f', 4));
                captureStatusLabel_->setText(
                    QStringLiteral("锁定中·多普勒补偿 %1%2 Hz")
                        .arg(liveFd >= 0.0 ? QStringLiteral("+")
                                           : QStringLiteral("−"))
                        .arg(std::llround(std::fabs(liveFd))));
            }
        }
    }
    skyView_->setLiveSatellites(sats);

    // --- Selected-satellite real trajectory overlay ----------------------
    // Propagate the SELECTED pass at viewT ± kSkyTrajectoryWindowMin (uniform
    // samples), real SGP4 -> az/el. Drawn dashed on the polar chart, coexisting
    // with the predicted pass arcs. Cleared when nothing is selected.
    if (liveRow_ >= 0 && liveRow_ < passes_.size()) {
        const dsp::SatPass& sel = passes_[liveRow_];
        QList<QPair<double,double>> traj;
        const qint64 spanMs = qint64(tokens::kSkyTrajectoryWindowMin) * 60 * 1000;
        for (int k = 0; k < tokens::kSkyTrajectorySamples; ++k) {
            const QDateTime t = viewT.addMSecs(qint64(double(2 * spanMs) * k /
                                                      (tokens::kSkyTrajectorySamples - 1)) - spanMs);
            dsp::Topocentric tp = tleClient_->propagateAt(t, sel.tle, stationLat_, stationLon_);
            traj.append({tp.az, tp.el});
        }
        skyView_->setSelectedTrajectory(traj);
    } else {
        skyView_->setSelectedTrajectory({});
    }

    // Drop the same satellites onto the world map as lat/lon sub-points, each
    // with a forward ground-track polyline (viewT -> LOS, ~2 min steps).
    QList<ui::SatellitePoint> wpts;
    for (int i = 0; i < passes_.size(); ++i) {
        const dsp::SatPass& p = passes_[i];
        if (viewT < p.aos || viewT > p.los) continue;
        auto geo = tleClient_->propagateLatLon(viewT, p.tle);
        ui::SatellitePoint sp;
        sp.name = p.name;
        sp.lat = geo.latDeg;
        sp.lon = geo.lonDeg;
        sp.selected = (i == liveRow_);
        // Future ground track: sample sub-points every ~2 minutes until LOS.
        const qint64 spanSec = viewT.secsTo(p.los);
        if (spanSec > 0) {
            const int steps = std::min<qint64>(30, std::max<qint64>(2, spanSec / 120));
            for (int k = 0; k <= steps; ++k) {
                const QDateTime t = viewT.addMSecs(
                    qint64(double(viewT.msecsTo(p.los)) * k / steps));
                auto g = tleClient_->propagateLatLon(t, p.tle);
                sp.track.append({g.latDeg, g.lonDeg});
            }
        }
        wpts.append(sp);
    }
    worldView_->setSatellites(wpts);
}

void MainWindow::updateElevationPlotFor(const dsp::SatPass& p) {
    // Build (UTC, elevation) samples across the pass. SatPass.track carries
    // sampled (az, el) but no per-sample timestamps, so we distribute them
    // linearly across AOS..LOS -- the elevation SHAPE is real, the time axis
    // is a uniform resampling of the already-propagated track.
    QList<QPair<QDateTime, double>> samples;
    const int n = p.track.size();
    if (n > 0) {
        const qint64 totalMs = p.aos.msecsTo(p.los);
        for (int k = 0; k < n; ++k) {
            const QDateTime t = p.aos.addMSecs(n > 1 ? qint64(double(totalMs) * k / (n - 1)) : 0);
            samples.append({t, p.track[k].second});
        }
    }
    elevationPlot_->setPass(p.name, samples);
}

void MainWindow::onSkySliderChanged(int offsetMin) {
    // valueChanged fires on every drag tick. We do NOT propagate per tick:
    // record the latest offset and (re)start the single-shot throttle timer;
    // recomputePreview() runs once it settles => <= 10 Hz while dragging.
    pendingPreviewOffsetMin_ = offsetMin;
    skyPreviewTimer_->start();   // restart = coalesce
}

void MainWindow::recomputePreview() {
    // Throttle fired: adopt the preview moment and re-propagate the whole sky
    // with the REAL SGP4 propagator. updateLiveSatellite() uses viewT = previewUtc_.
    previewUtc_ = QDateTime::currentDateTimeUtc().addSecs(pendingPreviewOffsetMin_ * 60);
    previewMode_ = true;
    updateLiveSatellite();
}

void MainWindow::onSkySliderReleased() {
    // Release (or the value settling) returns to live wall-now. Reset the slider
    // to center without re-entering preview (block signals so valueChanged(0)
    // does not immediately arm another preview).
    previewMode_ = false;
    if (skyTimeSlider_) {
        const QSignalBlocker block(skyTimeSlider_);
        skyTimeSlider_->setValue(0);
    }
    pendingPreviewOffsetMin_ = 0;
    updateLiveSatellite();   // back to live now
}

void MainWindow::updateClockBiasLabel() {
    if (!clockInfoLabel_) return;
    // Clock-domain readout: GNSS 授时时间 (real NMEA GGA/RMC/ZDA time) vs the
    // host system clock, with Δt = GNSS − system in ms. We only DISPLAY the
    // offset -- the app never sets the system clock.
    const QDateTime sysUtc = QDateTime::currentDateTimeUtc();
    const QDateTime local = QDateTime::currentDateTime();
    // Time source is the parsed NMEA fix clock (hasUtc is set once GGA/RMC/ZDA
    // carries a timestamp; ZDA is optional). Gate the offset on the time itself,
    // not on a position fix: RMC/GGA may carry time even without a 2/3D fix.
    if (lastGnssFix_.hasUtc && lastGnssFix_.utc.isValid()) {
        const qint64 dtMs = gnss::clockOffsetMs(lastGnssFix_.utc, sysUtc); // GNSS − sys
        clockBiasSec_ = dtMs / 1000.0;
        const QChar sign = (dtMs >= 0) ? QChar('+') : QChar(0x2212); // −
        clockInfoLabel_->setText(
            QString("时钟域  GNSS 授时 %1 UTC  授时源 NMEA(GGA/RMC)   系统 %2   本地 %3   "
                    "Δt %4%5 ms（GNSS−系统，未改钟）")
                .arg(lastGnssFix_.utc.toUTC().toString("HH:mm:ss.zzz"))
                .arg(sysUtc.toUTC().toString("HH:mm:ss.zzz"))
                .arg(local.toString("HH:mm:ss"))
                .arg(sign).arg(std::llround(std::fabs(double(dtMs)))));
    } else {
        // Honest empty state: no NMEA clock -> no fabricated offset.
        clockBiasSec_ = 0.0;
        clockInfoLabel_->setText(
            QString("时钟域  GNSS 授时 --（无 GNSS 授时）   系统 %1   本地 %2   Δt --")
                .arg(sysUtc.toUTC().toString("HH:mm:ss"))
                .arg(local.toString("HH:mm:ss")));
    }
}

void MainWindow::onNewFix(gnss::GnssFix fix) {
    lastGnssFix_ = fix;
    if (fix.hasUtc) updateClockBiasLabel();

    if (!fix.isValid()) {
        // No position: never paint a fake receiver point. Keep the manual
        // station and the honest "GNSS 无定位" state.
        return;
    }

    gnssHasFix_ = true;
    // B5: honest strip readout -- only shows once a real position fix lands.
    if (sbGnss_)
        sbGnss_->setText(QStringLiteral("GNSS 定位"));
    // Only treat the station as moved when the fix shifts by more than ~100 m,
    // so a stationary receiver does not thrash the TLE fetch every sentence.
    const bool stationMoved =
        !std::isfinite(lastGnssAppliedLat_) || !std::isfinite(lastGnssAppliedLon_) ||
        std::fabs(fix.latitude - lastGnssAppliedLat_) > 1e-3 ||
        std::fabs(fix.longitude - lastGnssAppliedLon_) > 1e-3;
    if (stationMoved) {
        lastGnssAppliedLat_ = fix.latitude;
        lastGnssAppliedLon_ = fix.longitude;
        stationLat_ = fix.latitude;
        stationLon_ = fix.longitude;
        stationSet_ = true;
        worldView_->setStation(fix.latitude, fix.longitude);
        if (adsbTracker_) adsbTracker_->setStation(fix.latitude, fix.longitude);
        engine_->setAdsbReferencePosition(fix.latitude, fix.longitude);
        refetchTle();   // cache-first; re-propagate passes for the real station
    }

    worldView_->setGnssFix(true, fix.latitude, fix.longitude,
                           fix.satellitesInUse, fix.hdop,
                           fix.hasUtc ? fix.utc : QDateTime());

    // Fix status: quality / satellites in use / HDOP.
    gnssFixLabel_->setText(
        QString("%1 %2  星%3  HDOP %4")
            .arg(gnss::fixQualityToString(fix.fixQuality))
            .arg(QString::number(fix.latitude, 'f', 5) + "," +
                 QString::number(fix.longitude, 'f', 5))
            .arg(fix.satellitesInUse)
            .arg(fix.hdop, 0, 'f', 1));

    // Bridge GSV visible satellites onto the sky polar view as diamonds.
    QList<ui::GnssSkySat> gs;
    gs.reserve(fix.visibleSatellites.size());
    for (const auto& s : fix.visibleSatellites) {
        ui::GnssSkySat g;
        g.prn = "G" + QString::number(s.prn);
        g.az = s.azimuth;
        g.el = s.elevation;
        g.snr = s.snr;
        g.used = s.used;
        gs.append(g);
    }
    skyView_->setGnssSatellites(gs);
}

void MainWindow::onGnssConnectionChanged(bool connected, QString description) {
    gnssConnectBtn_->setText(connected ? "断开" : "连接");
    gnssStatusLabel_->setText(connected ? "已连接" : "未连接");
    if (!connected) {
        // Connection dropped / EOF: clear the fix and fall back to the manual
        // AiConfig station. We never persist or show a stale fake position.
        gnssHasFix_ = false;
        lastGnssFix_ = gnss::GnssFix();
        worldView_->setGnssFix(false, 0, 0, 0, 0);
        skyView_->clearGnssSatellites();
        gnssFixLabel_->setText("GNSS 无定位");
        if (sbGnss_) sbGnss_->setText("");   // no receiver -> omit, never a stale fix
        updateClockBiasLabel();
        // Restore the hand-entered station (if any) as the reference.
        ai::AiConfig cfg; cfg.load();
        stationLat_ = cfg.stationLat; stationLon_ = cfg.stationLon;
        stationSet_ = cfg.stationSet;
        worldView_->setStation(stationLat_, stationLon_);
        if (adsbTracker_) adsbTracker_->setStation(stationLat_, stationLon_);
        refetchTle();
    }
    if (!description.isEmpty()) statusBar()->showMessage("GNSS: " + description);
}

void MainWindow::onGnssConnectClicked() {
    if (gnssRx_->isRunning()) {
        gnssRx_->stop();   // connectionChanged(false) updates the UI
        return;
    }
    const QString path = gnssDeviceEdit_->text().trimmed();
    if (path.isEmpty()) {
        statusBar()->showMessage("GNSS: 请先填写串口设备路径");
        return;
    }
    bool baudOk = false;
    const int baud = gnssBaudCombo_->currentText().toInt(&baudOk);
    QSettings("MBDSDR", "MBDSDR").setValue("gnss/device", path);
    QSettings("MBDSDR", "MBDSDR").setValue("gnss/baud", gnssBaudCombo_->currentText());
    gnssConnectBtn_->setText("连接中…");
    gnssRx_->setDevice(path, baudOk ? baud : 9600);
    gnssRx_->start();
}

void MainWindow::selectSatelliteByName(const QString& name) {
    // Single source of truth for "which TLE satellite is selected". Drive both
    // views from the name; their setSelectedSatellite() slots do not re-emit.
    worldView_->setSelectedSatellite(name);
    skyView_->setSelectedSatellite(name);
    QSettings("MBDSDR", "MBDSDR").setValue("ui/selectedSatellite", name);

    int idx = -1;
    for (int i = 0; i < passes_.size(); ++i)
        if (passes_[i].name == name) { idx = i; break; }

    if (idx >= 0) {
        liveRow_ = idx;
        liveTimer_->start();
        updateElevationPlotFor(passes_[idx]);
        // Highlight the matching table row without re-entering onPassRowClicked.
        for (int row = 0; row < passTable_->rowCount(); ++row) {
            QTableWidgetItem* it = passTable_->item(row, 0);
            if (it && it->data(Qt::UserRole).toInt() == idx) {
                if (passTable_->currentRow() != row) passTable_->selectRow(row);
                break;
            }
        }
        updateLiveSatellite();
    } else {
        elevationPlot_->clear();
    }
}

void MainWindow::copyClockBias() {
    if (!lastGnssFix_.hasUtc || !gnssHasFix_) {
        statusBar()->showMessage("无 GNSS 定位，无法复制时钟偏差");
        return;
    }
    QApplication::clipboard()->setText(
        QString::number(clockBiasSec_, 'f', 3) + " s");
    statusBar()->showMessage(
        "时钟偏差已复制（系统 UTC − GNSS UTC = " +
        QString::number(clockBiasSec_, 'f', 3) +
        " s）。本程序不修改系统时钟；真正校时需 root / CAP_SYS_TIME 特权。");
}

// ---- AI multi-session + context-compaction UI helpers -------------------

void MainWindow::aiRenderChat() {
    if (!aiChat_ || !aiSessionStore_) return;
    aiChat_->clear();
    const auto msgs = aiSessionStore_->messages(aiCurSessionId_);
    for (const auto& m : msgs) {
        if (m.role == QLatin1String("user")) {
            aiChat_->appendPlainText("You: " + m.content);
        } else if (m.role == QLatin1String("assistant")) {
            aiChat_->appendPlainText("AI: " + m.content);
        } else if (m.role == QLatin1String("summary")) {
            // Restrained small annotation, not a loud sticker.
            aiChat_->appendHtml(
                QString("<div style='color:%1; font-size:9pt;'>〔已摘要〕 %2</div>")
                    .arg(tokens::textRgba(tokens::kTextAlphaTertiary),
                         m.content.toHtmlEscaped()));
        }
    }
    for (const QString& note : aiToolNotes_)
        aiChat_->appendPlainText(note);
    if (!aiTransient_.isEmpty())
        aiChat_->appendPlainText(QString("AI: %1 ▌").arg(aiTransient_));
}

void MainWindow::aiRefreshSessionCombo() {
    if (!aiSessionCombo_ || !aiSessionStore_) return;
    QSignalBlocker blk(aiSessionCombo_);
    aiSessionCombo_->clear();
    int sel = 0;
    const auto list = aiSessionStore_->sessions();
    for (int i = 0; i < list.size(); ++i) {
        aiSessionCombo_->addItem(list[i].title, list[i].id);
        if (list[i].id == aiCurSessionId_) sel = i;
    }
    aiSessionCombo_->setCurrentIndex(sel);
}

void MainWindow::onAiSessionChanged(int idx) {
    if (!aiSessionCombo_ || idx < 0) return;
    const QString id = aiSessionCombo_->itemData(idx).toString();
    if (id.isEmpty() || id == aiCurSessionId_) return;
    aiCurSessionId_ = id;
    aiSessionStore_->setCurrent(id);
    aiToolNotes_.clear();
    aiTransient_.clear();
    aiRenderChat();
}

void MainWindow::onAiNewSession() {
    if (!aiSessionStore_) return;
    aiCurSessionId_ = aiSessionStore_->createSession();
    aiToolNotes_.clear();
    aiTransient_.clear();
    aiRefreshSessionCombo();
    aiRenderChat();
}

void MainWindow::onAiRenameSession() {
    if (!aiSessionStore_ || aiCurSessionId_.isEmpty()) return;
    bool ok = false;
    QString name = QInputDialog::getText(this,
        QString::fromUtf8("重命名会话"), QString::fromUtf8("会话名称："),
        QLineEdit::Normal,
        aiSessionCombo_->currentText(), &ok);
    if (!ok || name.trimmed().isEmpty()) return;
    aiSessionStore_->renameSession(aiCurSessionId_, name.trimmed());
    aiRefreshSessionCombo();
}

void MainWindow::onAiDeleteSession() {
    if (!aiSessionStore_ || aiCurSessionId_.isEmpty()) return;
    aiSessionStore_->deleteSession(aiCurSessionId_);
    aiCurSessionId_ = aiSessionStore_->currentId();
    aiToolNotes_.clear();
    aiTransient_.clear();
    aiRefreshSessionCombo();
    aiRenderChat();
}

void MainWindow::onAiCompactContext() {
    // Manual compaction: run the pure compaction over the current session's
    // stored history (system prompt + recent rounds kept verbatim) and write
    // the compacted list back, then render. Uses the rule-based summary when no
    // LLM summary callback is wired from the UI thread (the worker thread does
    // the real LLM summary on the next send).
    if (!aiSessionStore_ || aiCurSessionId_.isEmpty()) return;
    const QString systemPrompt =
        QString::fromUtf8("你是 SDR 接收控制助手。可以调谐频率、切换解调模式、控制录制、扫描频段。回答简洁。");
    QList<mbdsdr::ai::ChatMessage> hist;
    for (const auto& m : aiSessionStore_->messages(aiCurSessionId_))
        hist.append(mbdsdr::ai::ChatMessage{m.role, m.content});
    auto out = mbdsdr::ai::compactContext(hist, systemPrompt);
    // Strip the leading system message before persisting (the store never
    // records a system entry; it only holds user/assistant/summary).
    QList<mbdsdr::ai::SessionMessage> stored;
    for (const auto& m : out.messages) {
        if (m.role == QLatin1String("system")) continue;
        stored.append(mbdsdr::ai::SessionMessage{m.role, m.content});
    }
    aiSessionStore_->setMessages(aiCurSessionId_, stored);
    aiRenderChat();
    if (out.didCompact)
        aiStatus_->setText(QString::fromUtf8("已压缩上下文：折叠 %1 轮早期对话为「已摘要」")
                               .arg(out.compressedRounds));
}

} // namespace mbdsdr