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
#include <QPlainTextEdit>
#include <QTableWidget>
#include <QLineEdit>
#include <QStackedWidget>
#include <QSettings>
#include <QShortcut>
#include <QScrollArea>
#include <QDateTime>
#include <QTimer>
#include <QDialog>
#include <QFormLayout>
#include <QDir>
#include <QFileInfo>
#include <QUrl>
#include <QDesktopServices>

#include <cmath>
#include <algorithm>

#include "core/tokens.h"
#include "core/spectrum_frame.h"
#include "dsp/spectrum_engine.h"
#include "dsp/adsb_decoder.h"
#include "ai/agent.h"
#include "ai/ai_config.h"
#include "ui/sky_view.h"
#include "ui/world_view.h"
#include "ui/waterfall.h"
#include "ui/settings_dialog.h"
#include "ui/about_dialog.h"

namespace mbdsdr {

MainWindow::MainWindow(QWidget* parent)
    : QMainWindow(parent)
{
    setWindowTitle("MBDSDR");
    resize(tokens::scaled(1280), tokens::scaled(800));
    setStyleSheet(tokens::buildDarkQss());

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

    auto* aboutBtn = new QPushButton("关于", topBar);
    auto* settingsBtn = new QPushButton("⚙", topBar);
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

    auto* gFreq = new QGroupBox("频率", leftCard);
    auto* gFreqLay = new QFormLayout(gFreq);
    freqSpin_ = new QDoubleSpinBox(gFreq);
    freqSpin_->setRange(tokens::kFreqMinHz / 1e6, tokens::kFreqMaxHz / 1e6);
    freqSpin_->setValue(98.5);
    freqSpin_->setDecimals(3);
    freqSpin_->setSuffix(" MHz");
    gFreqLay->addRow("中心频率", freqSpin_);
    leftLay->addWidget(gFreq);

    auto* gRx = new QGroupBox("接收参数", leftCard);
    auto* gRxLay = new QFormLayout(gRx);
    srCombo_ = new QComboBox(gRx);
    srCombo_->addItems({"1.024 MS/s", "2.048 MS/s", "2.4 MS/s", "3.2 MS/s"});
    srCombo_->setCurrentIndex(2);
    gRxLay->addRow("采样率", srCombo_);
    gainSlider_ = new QSlider(Qt::Horizontal, gRx);
    gainSlider_->setRange(0, 50);
    gainValue_ = new QLabel("0 dB", gRx);
    auto* gainRow = new QHBoxLayout;
    gainRow->addWidget(gainSlider_);
    gainRow->addWidget(gainValue_);
    gRxLay->addRow("增益", gainRow);
    demodCombo_ = new QComboBox(gRx);
    demodCombo_->addItems({"AM", "NFM", "WFM", "USB", "LSB", "CW"});
    gRxLay->addRow("解调", demodCombo_);
    bwCombo_ = new QComboBox(gRx);
    bwCombo_->addItems({"8 kHz", "12.5 kHz", "200 kHz", "2.4 kHz", "500 Hz"});
    gRxLay->addRow("带宽", bwCombo_);
    leftLay->addWidget(gRx);

    auto* gSql = new QGroupBox("静噪", leftCard);
    auto* gSqlLay = new QVBoxLayout(gSql);
    squelchCheck_ = new QCheckBox("启用静噪", gSql);
    gSqlLay->addWidget(squelchCheck_);
    auto* sqlRow = new QHBoxLayout;
    squelchSlider_ = new QSlider(Qt::Horizontal, gSql);
    squelchSlider_->setRange(-100, -20);
    squelchSlider_->setValue(-50);
    squelchValue_ = new QLabel("-50 dB", gSql);
    sqlRow->addWidget(squelchSlider_);
    sqlRow->addWidget(squelchValue_);
    gSqlLay->addLayout(sqlRow);
    squelchState_ = new QLabel("状态: CLOSED", gSql);
    gSqlLay->addWidget(squelchState_);
    leftLay->addWidget(gSql);

    auto* gAud = new QGroupBox("音频", leftCard);
    auto* gAudLay = new QVBoxLayout(gAud);
    levelLabel_ = new QLabel("电平: -- dBFS", gAud);
    gAudLay->addWidget(levelLabel_);
    levelBar_ = new QLabel("", gAud);
    levelBar_->setFixedHeight(tokens::scaled(16));
    gAudLay->addWidget(levelBar_);
    leftLay->addWidget(gAud);

    auto* gRec = new QGroupBox("录制", leftCard);
    auto* gRecLay = new QVBoxLayout(gRec);
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
    auto* openRecDirBtn = new QPushButton("打开录制目录", gRec);
    openRecDirBtn->setToolTip("在系统文件管理器中打开 recordings/ 目录");
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
    worldView_ = new ui::WorldView(centerCard);
    waterfall_ = new ui::WaterfallWidget(centerCard);

    // Spectrum tab: line spectrum on top, scrolling waterfall below (SDR++ style).
    auto* specSplit = new QSplitter(Qt::Vertical, centerCard);
    specSplit->setChildrenCollapsible(false);
    specSplit->addWidget(spectrum_);
    specSplit->addWidget(waterfall_);
    specSplit->setStretchFactor(0, 3);
    specSplit->setStretchFactor(1, 2);

    centerTabs_->addTab(specSplit, "频谱");
    centerTabs_->addTab(worldView_, "世界");
    centerLay->addWidget(centerTabs_);
    splitter->addWidget(centerCard);

    // ---- Right panel: tabs ----
    auto* rightCard = new QFrame;
    rightCard->setObjectName("panelCard");
    auto* rightLay = new QVBoxLayout(rightCard);
    rightTabs_ = new QTabWidget(rightCard);

    auto* cwPage = new QWidget;
    auto* cwLay = new QVBoxLayout(cwPage);
    cwWpm_ = new QLabel("WPM: --", cwPage);
    cwLay->addWidget(cwWpm_);
    cwText_ = new QPlainTextEdit(cwPage);
    cwText_->setReadOnly(true);
    cwLay->addWidget(cwText_);
    rightTabs_->addTab(cwPage, "CW");

    auto* adsbPage = new QWidget;
    auto* adsbLay = new QVBoxLayout(adsbPage);
    adsbLay->addWidget(new QLabel("ADS-B 1090MHz — 需专用天线", adsbPage));
    adsbTable_ = new QTableWidget(0, 4, adsbPage);
    adsbTable_->setHorizontalHeaderLabels({"ICAO", "呼号", "高度", "时间"});
    adsbLay->addWidget(adsbTable_);
    rightTabs_->addTab(adsbPage, "ADS-B");

    skyView_ = new ui::SkyView();
    rightTabs_->addTab(skyView_, "天空");

    auto* aiPage = new QWidget;
    auto* aiLay = new QVBoxLayout(aiPage);
    aiStatus_ = new QLabel("未配置 API Key — 仅本地指令", aiPage);
    aiLay->addWidget(aiStatus_);
    aiChat_ = new QPlainTextEdit(aiPage);
    aiChat_->setReadOnly(true);
    aiLay->addWidget(aiChat_);
    aiInput_ = new QLineEdit(aiPage);
    aiInput_->setPlaceholderText("输入频率/模式/指令...");
    aiLay->addWidget(aiInput_);
    auto* sendBtn = new QPushButton("发送", aiPage);
    aiLay->addWidget(sendBtn);
    rightTabs_->addTab(aiPage, "AI 助手");

    rightLay->addWidget(rightTabs_);
    splitter->addWidget(rightCard);

    splitter->setStretchFactor(0, 1);
    splitter->setStretchFactor(1, 3);
    splitter->setStretchFactor(2, 1);
    splitter->setSizes({tokens::scaled(280), tokens::scaled(800), tokens::scaled(280)});

    centralLay->addWidget(splitter);
    setCentralWidget(central);
    statusBar()->showMessage("MBDSDR C++");

    // ---- Engine ----
    engine_ = new dsp::SpectrumEngine(this);
    connect(engine_, &dsp::SpectrumEngine::spectrumReady,
            spectrum_, &ui::SpectrumWidget::setSpectrum);
    connect(engine_, &dsp::SpectrumEngine::spectrumReady,
            waterfall_, &ui::WaterfallWidget::setSpectrum);
    connect(engine_, &dsp::SpectrumEngine::sourceChanged,
            this, &MainWindow::onSourceChanged);
    connect(engine_, &dsp::SpectrumEngine::audioLevel,
            this, &MainWindow::onAudioLevel);
    connect(engine_, &dsp::SpectrumEngine::rssiLevel,
            this, &MainWindow::onRssiLevel);
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
    connect(spectrum_, &ui::SpectrumWidget::fftSizeRequested,
            engine_, &dsp::SpectrumEngine::setFftSize);
    // Zoom/pan the spectrum and waterfall stay in lockstep.
    connect(spectrum_, &ui::SpectrumWidget::visibleRangeChanged,
            waterfall_, &ui::WaterfallWidget::setVisibleRange);

    connect(freqSpin_, QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, [this](double mhz) { engine_->onSetCenterFreq(mhz * 1e6); });
    connect(gainSlider_, &QSlider::valueChanged,
            this, [this](int v) {
                gainValue_->setText(QString("%1 dB").arg(v));
                engine_->onSetGain(v);
            });
    connect(demodCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int) {
                engine_->setDemodMode(demodCombo_->currentText());
                static const QMap<QString, int> bwIdx = {
                    {"AM", 0}, {"NFM", 1}, {"WFM", 2}, {"USB", 3}, {"LSB", 3}, {"CW", 4}
                };
                auto it = bwIdx.find(demodCombo_->currentText());
                if (it != bwIdx.end()) {
                    bwCombo_->blockSignals(true);
                    bwCombo_->setCurrentIndex(it.value());
                    bwCombo_->blockSignals(false);
                }
            });
    connect(squelchSlider_, &QSlider::valueChanged,
            this, [this](int v) {
                squelchValue_->setText(QString("%1 dB").arg(v));
                engine_->setSquelchThreshold(static_cast<float>(v));
            });
    connect(squelchCheck_, &QCheckBox::stateChanged, this, [this](int st) {
        bool en = (st != Qt::Unchecked);
        engine_->setSquelchEnabled(en);
        if (!en) squelchState_->setText("状态: CLOSED");
    });
    connect(srCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                static const double kRates[] = {1.024e6, 2.048e6, 2.4e6, 3.2e6};
                if (idx >= 0 && idx <= 3) engine_->onSetSampleRate(kRates[idx]);
            });
    connect(bwCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                static const double kBws[] = {8000.0, 12500.0, 200000.0, 2400.0, 500.0};
                if (idx >= 0 && idx <= 4) engine_->setBandwidth(kBws[idx]);
            });
    connect(gatedCheck_, &QCheckBox::stateChanged, this, [this](int st) {
        engine_->setGatedRecordingEnabled(st != Qt::Unchecked);
    });
    connect(recordBtn_, &QPushButton::clicked, this, &MainWindow::onRecordClicked);

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
    connect(openRecDirBtn, &QPushButton::clicked, this, []() {
        QDesktopServices::openUrl(
            QUrl::fromLocalFile(QDir::currentPath() + "/recordings"));
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
            bool ok = engine_->tryConnectRtl();
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
    connect(settingsBtn, &QPushButton::clicked, this, [this]() {
        ui::SettingsDialog dlg(this);
        ai::AiConfig cfg;
        cfg.load();
        dlg.loadFromConfig(cfg);
        if (dlg.exec() == QDialog::Accepted) {
            dlg.saveToConfig(cfg);
            cfg.save();
            worldView_->setStation(cfg.stationLat, cfg.stationLon);
        }
    });

    agent_ = new ai::Agent(this);
    connect(agent_, &ai::Agent::responseReady, this, [this](const QString& t) {
        aiChat_->appendPlainText("AI: " + t);
    });
    connect(sendBtn, &QPushButton::clicked, this, [this]() {
        QString t = aiInput_->text().trimmed();
        if (t.isEmpty()) return;
        aiChat_->appendPlainText("You: " + t);
        agent_->sendMessage(t);
        aiInput_->clear();
    });
    connect(aiInput_, &QLineEdit::returnPressed, sendBtn, &QPushButton::click);

    new QShortcut(QKeySequence(Qt::Key_Right), this, this, [this]() {
        engine_->onSetCenterFreq(freqSpin_->value() * 1e6 + tokens::kFreqFineStepHz);
    });
    new QShortcut(QKeySequence(Qt::Key_Left), this, this, [this]() {
        engine_->onSetCenterFreq(freqSpin_->value() * 1e6 - tokens::kFreqFineStepHz);
    });
    new QShortcut(QKeySequence(Qt::Key_Up), this, this, [this]() {
        engine_->onSetGain(gainSlider_->value() + 1);
    });
    new QShortcut(QKeySequence(Qt::Key_Down), this, this, [this]() {
        engine_->onSetGain(gainSlider_->value() - 1);
    });
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
        engine_->onSetCenterFreq(hz);
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
    connect(mainSplitter_, &QSplitter::splitterMoved, this, &MainWindow::scheduleSave);
    connect(spectrum_, &ui::SpectrumWidget::viewChanged, this, &MainWindow::scheduleSave);
    connect(spectrum_, &ui::SpectrumWidget::visibleRangeChanged,
            this, [this](double, double) { scheduleSave(); });

    setControlsEnabled(false);
    restoreUiState();
    engine_->start();
}

MainWindow::~MainWindow() {
    // Flush any pending debounced save so the last 500 ms of tweaks are not lost.
    if (saveTimer_ && saveTimer_->isActive()) {
        saveTimer_->stop();
        saveSettings();
    }
    saveUiState();
    if (engine_) { engine_->shutdown(); engine_->wait(); }
}

void MainWindow::saveUiState() {
    QSettings s("MBDSDR", "MBDSDR");
    s.setValue("geometry", saveGeometry());

    // ---- RX state (Hz / dB as stored) ----
    s.setValue("rx/centerFreq", freqSpin_->value() * 1e6);
    {
        static const double kRates[] = {1.024e6, 2.048e6, 2.4e6, 3.2e6};
        int idx = srCombo_->currentIndex();
        if (idx >= 0 && idx <= 3) s.setValue("rx/sampleRate", kRates[idx]);
    }
    s.setValue("rx/demodMode", demodCombo_->currentText());
    {
        static const double kBws[] = {8000.0, 12500.0, 200000.0, 2400.0, 500.0};
        int idx = bwCombo_->currentIndex();
        if (idx >= 0 && idx <= 4) s.setValue("rx/bandwidth", kBws[idx]);
    }
    s.setValue("rx/gain", static_cast<double>(gainSlider_->value()));
    s.setValue("rx/squelchEnabled", squelchCheck_->isChecked());
    s.setValue("rx/squelchThreshold", static_cast<float>(squelchSlider_->value()));
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

    // ---- Layout / tabs / FFT ----
    s.setValue("ui/rightTabIndex", rightTabs_->currentIndex());
    s.setValue("ui/centerTabIndex", centerTabs_->currentIndex());
    if (mainSplitter_) s.setValue("ui/splitterSizes", mainSplitter_->saveState());
    s.setValue("rx/fftSize", spectrum_->fftSizeValue());
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

void MainWindow::restoreUiState() {
    QSettings s("MBDSDR", "MBDSDR");
    restoreGeometry(s.value("geometry").toByteArray());

    // Block widget signals while we repopulate controls; we dispatch to the
    // engine explicitly below so each setting is applied exactly once.
    freqSpin_->blockSignals(true);
    srCombo_->blockSignals(true);
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

    static const double kRates[] = {1.024e6, 2.048e6, 2.4e6, 3.2e6};
    const double rateHz = s.value("rx/sampleRate", 2.4e6).toDouble();
    int srIdx = 2;
    for (int i = 0; i < 4; ++i) if (std::abs(kRates[i] - rateHz) < 1e3) srIdx = i;
    srCombo_->setCurrentIndex(srIdx);

    const QString demod = s.value("rx/demodMode", "NFM").toString();
    int dIdx = demodCombo_->findText(demod);
    if (dIdx < 0) dIdx = 1;  // NFM
    demodCombo_->setCurrentIndex(dIdx);

    static const double kBws[] = {8000.0, 12500.0, 200000.0, 2400.0, 500.0};
    const double bwHz = s.value("rx/bandwidth", 12500.0).toDouble();
    int bwIdx = 1;
    for (int i = 0; i < 5; ++i) if (std::abs(kBws[i] - bwHz) < 500.0) bwIdx = i;
    bwCombo_->setCurrentIndex(bwIdx);

    const double gainDb = s.value("rx/gain", 0.0).toDouble();
    gainSlider_->setValue(static_cast<int>(std::round(gainDb)));
    gainValue_->setText(QString("%1 dB").arg(gainSlider_->value()));

    const bool sqlEn = s.value("rx/squelchEnabled", false).toBool();
    squelchCheck_->setChecked(sqlEn);
    const float sqlThr = s.value("rx/squelchThreshold", -50.0f).toFloat();
    squelchSlider_->setValue(static_cast<int>(std::round(sqlThr)));
    squelchValue_->setText(QString("%1 dB").arg(squelchSlider_->value()));

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

    // ---- View ----
    const double zoom = s.value("view/zoomFactor", 1.0).toDouble();
    spectrum_->setZoomFactor(zoom);

    // ---- Recording options ----
    const int recTarget = s.value("rec/target", 0).toInt();
    recTargetCombo_->setCurrentIndex(std::clamp(recTarget, 0, 1));
    recTemplateEdit_->setText(s.value("rec/template", "{time}_{freq}_{mode}").toString());
    recStereoCheck_->setChecked(s.value("rec/stereo", false).toBool());
    recIgnoreSqlChk_->setChecked(s.value("rec/ignoreSquelch", false).toBool());
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

    // Unblock.
    freqSpin_->blockSignals(false);
    srCombo_->blockSignals(false);
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
    engine_->setBandwidth(kBws[bwIdx]);
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

    // Manual gain slider only matters in manual tuner-gain mode.
    gainSlider_->setEnabled(!tunerAgcChk_->isChecked());
    // Note: setZoomFactor() above already emitted visibleRangeChanged; the
    // first arriving spectrum frame will re-emit with viewCenterHz_ anchored
    // to the real f0, which is what syncs the waterfall.
}

void MainWindow::onSourceChanged(const QString& name, bool connected) {
    statusLabel_->setText(connected ? QString("● %1").arg(name) : QString("● %1 (test)").arg(name));
    if (connectBtn_) connectBtn_->setText(connected ? "断开" : "连接");
    if (sourceBanner_) sourceBanner_->setText(connected
        ? QString("%1 已连接（真实硬件）").arg(name)
        : QStringLiteral("RTL-SDR 未连接，使用测试信号"));
    // Recording needs a live data producer -- hardware OR the offline test
    // signal (which still synthesizes IQ/audio). The engine guards the rest.
    if (recordBtn_) recordBtn_->setEnabled(true);
    setControlsEnabled(connected);
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
    if (rssiLabel_) rssiLabel_->setText(QString("RSSI: %1 dBFS").arg(dbfs, 0, 'f', 1));
}

void MainWindow::onSquelchState(bool open) {
    squelchState_->setText(open ? "状态: OPEN" : "状态: CLOSED");
}

void MainWindow::onRecordingState(bool recording, const QString& path) {
    if (recording) {
        recStatus_->setText("● REC: " + QFileInfo(path).fileName());
    } else {
        recStatus_->setText("空闲");
    }
    recordBtn_->setText(recording ? "■ 停止" : "● 录制");
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
}

void MainWindow::onRecordClicked() {
    if (recordBtn_->text().contains("停止")) engine_->stopRecording();
    else engine_->startRecording();
}

void MainWindow::onCwDecoded(const QString& text, double wpm) {
    cwText_->appendPlainText(text);
    cwWpm_->setText(QString("WPM: %1").arg(wpm, 0, 'f', 1));
}

void MainWindow::onAdsbAircraft(const dsp::AircraftInfo& info) {
    int row = adsbTable_->rowCount();
    adsbTable_->insertRow(row);
    adsbTable_->setItem(row, 0, new QTableWidgetItem(info.icao));
    adsbTable_->setItem(row, 1, new QTableWidgetItem(info.callsign));
    adsbTable_->setItem(row, 2, new QTableWidgetItem(info.altitudeFt > 0 ? QString::number(info.altitudeFt) : "--"));
    adsbTable_->setItem(row, 3, new QTableWidgetItem(QDateTime::currentDateTime().toString("HH:mm:ss")));
}

void MainWindow::setControlsEnabled(bool hw) {
    freqSpin_->setEnabled(hw);
    srCombo_->setEnabled(hw);
    // Manual gain slider only when hardware connected AND tuner in manual mode.
    gainSlider_->setEnabled(hw && !(tunerAgcChk_ && tunerAgcChk_->isChecked()));
    if (advPanel_) advPanel_->setEnabled(hw);
}

} // namespace mbdsdr
