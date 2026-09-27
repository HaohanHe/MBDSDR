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
    recordBtn_ = new QPushButton("● 录制", gRec);
    gRecLay->addWidget(recordBtn_);
    gatedCheck_ = new QCheckBox("触发式录制", gRec);
    gRecLay->addWidget(gatedCheck_);
    recStatus_ = new QLabel("空闲", gRec);
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

    skyView_ = new ui::SkyView(adsbPage);
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
    connect(engine_, &dsp::SpectrumEngine::cwDecoded,
            this, &MainWindow::onCwDecoded);
    connect(engine_, &dsp::SpectrumEngine::adsbAircraft,
            this, &MainWindow::onAdsbAircraft);
    connect(spectrum_, &ui::SpectrumWidget::fftSizeRequested,
            engine_, &dsp::SpectrumEngine::setFftSize);

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
        engine_->onSetCenterFreq(freqSpin_->value() * 1e6 + 10000);
    });
    new QShortcut(QKeySequence(Qt::Key_Left), this, this, [this]() {
        engine_->onSetCenterFreq(freqSpin_->value() * 1e6 - 10000);
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

    setControlsEnabled(false);
    restoreUiState();
    engine_->start();
}

MainWindow::~MainWindow() {
    saveUiState();
    if (engine_) { engine_->shutdown(); engine_->wait(); }
}

void MainWindow::saveUiState() {
    QSettings s("MBDSDR", "MBDSDR");
    s.setValue("geometry", saveGeometry());
}

void MainWindow::restoreUiState() {
    QSettings s("MBDSDR", "MBDSDR");
    restoreGeometry(s.value("geometry").toByteArray());
}

void MainWindow::onSourceChanged(const QString& name, bool connected) {
    statusLabel_->setText(connected ? QString("● %1").arg(name) : QString("● %1 (test)").arg(name));
    if (connectBtn_) connectBtn_->setText(connected ? "断开" : "连接");
    if (sourceBanner_) sourceBanner_->setText(connected
        ? QString("%1 已连接（真实硬件）").arg(name)
        : QStringLiteral("RTL-SDR 未连接，使用测试信号"));
    setControlsEnabled(connected);
}

void MainWindow::onAudioLevel(float dbfs) {
    levelLabel_->setText(QString("电平: %1 dBFS").arg(dbfs, 0, 'f', 1));
    if (levelBar_) {
        int pct = qBound(0, static_cast<int>((dbfs + 60) / 60 * 100), 100);
        levelBar_->setStyleSheet(QString("background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 %1, stop:1 %2); border-radius: 4px;").arg(tokens::kCard1, tokens::kAccent));
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
    recStatus_->setText(recording ? "● REC: " + path : "空闲");
    recordBtn_->setText(recording ? "■ 停止" : "● 录制");
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
    gainSlider_->setEnabled(hw);
}

} // namespace mbdsdr
