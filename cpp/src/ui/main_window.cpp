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
#include "ai/agent.h"
#include "ui/sky_view.h"
#include "ui/world_view.h"
#include "ui/settings_dialog.h"
#include "ui/about_dialog.h"
#include "ai/ai_config.h"

#include "core/tokens.h"
#include "dsp/spectrum_engine.h"

namespace mbdsdr {

static QFrame* makePanelCard(const QString& title, QWidget* parent) {
    auto* card = new QFrame(parent);
    card->setObjectName("panelCard");
    auto* lay = new QVBoxLayout(card);
    lay->setContentsMargins(tokens::kPanelPadLeft, tokens::kPanelPadTop,
                            tokens::kPanelPadLeft, tokens::kPanelPadTop);
    lay->setSpacing(12);
    auto* t = new QLabel(title, card);
    t->setObjectName("panelTitle");
    lay->addWidget(t);
    return card;
}

MainWindow::MainWindow(QWidget* parent) : QMainWindow(parent) {
    setWindowTitle("MBDSDR C++");
    resize(1280, 800);

    auto* central = new QWidget(this);
    setCentralWidget(central);
    auto* vbox = new QVBoxLayout(central);
    vbox->setContentsMargins(0, 0, 0, 0);
    vbox->setSpacing(0);

    // ---- Top bar ----
    auto* topBar = new QWidget();
    topBar->setObjectName("topBar");
    topBar->setFixedHeight(tokens::kTopbarH);
    auto* topLay = new QHBoxLayout(topBar);
    topLay->setContentsMargins(tokens::kTopbarPadLeft, tokens::kTopbarPadTop,
                               tokens::kTopbarPadRight, tokens::kTopbarPadBottom);
    topLay->setSpacing(20);
    auto* clock = new QLabel("12:30", topBar);
    clock->setObjectName("clockLabel");
    topLay->addWidget(clock);
    auto* mileBox = new QVBoxLayout();
    auto* mile = new QLabel("468km", topBar); mile->setObjectName("mileageLabel");
    auto* mileSub = new QLabel("预估", topBar); mileSub->setObjectName("mileageSub");
    mileBox->addWidget(mile); mileBox->addWidget(mileSub);
    topLay->addLayout(mileBox);
    auto* batteryTrack = new QFrame(topBar);
    batteryTrack->setFixedSize(tokens::kBatteryW, tokens::kBatteryH);
    batteryTrack->setStyleSheet(
        QString("background: %1; border-radius: %2px;")
            .arg(QString::fromUtf8(tokens::kBatteryTrack))
            .arg(tokens::kRadiusBattery));
    auto* batteryFill = new QFrame(batteryTrack);
    batteryFill->setGeometry(0, 0, 92, tokens::kBatteryH);
    batteryFill->setStyleSheet(
        QString("background: %1; border-radius: %2px;")
            .arg(QString::fromUtf8(tokens::kBatteryFill))
            .arg(tokens::kRadiusBattery));
    topLay->addWidget(batteryTrack);
    topLay->addSpacing(67);
    topLay->addWidget(new QLabel("P R N D", topBar));
    topLay->addStretch();
    auto* aboutBtn = new QPushButton("关于", topBar);
    auto* settingsBtn = new QPushButton("⚙", topBar);
    topLay->addWidget(aboutBtn);
    topLay->addWidget(settingsBtn);
    statusLabel_ = new QLabel("● --", topBar);
    statusLabel_->setObjectName("statusBanner");
    topLay->addWidget(statusLabel_);
    vbox->addWidget(topBar);

    // ---- Splitter ----
    auto* splitter = new QSplitter(Qt::Horizontal, central);

    // Left panel: real controls
    auto* leftCard = makePanelCard("控制面板", splitter);
    auto* leftLay = qobject_cast<QVBoxLayout*>(leftCard->layout());

    sourceBanner_ = new QLabel("RTL-SDR 未连接，使用测试信号", leftCard);
    sourceBanner_->setObjectName("dockHint");
    sourceBanner_->setWordWrap(true);
    leftLay->addWidget(sourceBanner_);

    // Connect button
    connectBtn_ = new QPushButton("连接", leftCard);
    leftLay->addWidget(connectBtn_);

    // RSSI
    rssiLabel_ = new QLabel("RSSI: -- dBFS", leftCard);
    leftLay->addWidget(rssiLabel_);

    // Frequency
    leftLay->addWidget(new QLabel("中心频率", leftCard));
    freqSpin_ = new QDoubleSpinBox(leftCard);
    freqSpin_->setRange(tokens::kFreqMinHz / 1e6, tokens::kFreqMaxHz / 1e6);
    freqSpin_->setSingleStep(tokens::kFreqStepHz / 1e6);
    freqSpin_->setDecimals(3);
    freqSpin_->setSuffix(" MHz");
    freqSpin_->setValue(98.5);
    leftLay->addWidget(freqSpin_);

    // Sample rate
    leftLay->addWidget(new QLabel("采样率", leftCard));
    srCombo_ = new QComboBox(leftCard);
    for (double sr : tokens::kSampleRatesHz) srCombo_->addItem(QString("%1 MS/s").arg(sr/1e6, 0, 'f', 2));
    srCombo_->setCurrentIndex(2); // 2.4 MS/s
    leftLay->addWidget(srCombo_);

    // Gain
    leftLay->addWidget(new QLabel("增益", leftCard));
    auto* gainRow = new QHBoxLayout();
    gainSlider_ = new QSlider(Qt::Horizontal, leftCard);
    gainSlider_->setRange(static_cast<int>(tokens::kGainMinDb * 10),
                          static_cast<int>(tokens::kGainMaxDb * 10));
    gainSlider_->setValue(200); // 20 dB
    gainValue_ = new QLabel("20.0 dB", leftCard);
    gainValue_->setObjectName("monoInfo");
    gainRow->addWidget(gainSlider_, 1);
    gainRow->addWidget(gainValue_);
    leftLay->addLayout(gainRow);

    // Demod mode
    leftLay->addWidget(new QLabel("解调模式", leftCard));
    demodCombo_ = new QComboBox(leftCard);
    demodCombo_->addItems({"NFM", "WFM", "AM", "USB", "LSB", "CW", "ADS-B"});
    leftLay->addWidget(demodCombo_);

    // Bandwidth
    leftLay->addWidget(new QLabel("带宽", leftCard));
    bwCombo_ = new QComboBox(leftCard);
    bwCombo_->addItems({"8 kHz", "12.5 kHz", "200 kHz", "2.4 kHz"});
    bwCombo_->setCurrentIndex(1); // NFM default
    leftLay->addWidget(bwCombo_);

    // Squelch
    leftLay->addWidget(new QLabel("静噪门限", leftCard));
    auto* sqRow = new QHBoxLayout();
    squelchSlider_ = new QSlider(Qt::Horizontal, leftCard);
    squelchSlider_->setRange(-100, -20);
    squelchSlider_->setValue(-50);
    squelchValue_ = new QLabel("-50 dB", leftCard);
    squelchValue_->setObjectName("monoInfo");
    sqRow->addWidget(squelchSlider_, 1);
    sqRow->addWidget(squelchValue_);
    leftLay->addLayout(sqRow);

    // Squelch state + audio level
    squelchState_ = new QLabel("静噪: --", leftCard);
    squelchState_->setObjectName("monoInfo");
    leftLay->addWidget(squelchState_);
    levelLabel_ = new QLabel("电平: -- dBFS", leftCard);
    levelLabel_->setObjectName("monoInfo");
    leftLay->addWidget(levelLabel_);

    // Recording
    recordBtn_ = new QPushButton("● REC", leftCard);
    leftLay->addWidget(recordBtn_);
    gatedCheck_ = new QCheckBox("触发录制（按通话分段）", leftCard);
    leftLay->addWidget(gatedCheck_);
    recStatus_ = new QLabel("录制: 空闲", leftCard);
    recStatus_->setObjectName("monoInfo");
    leftLay->addWidget(recStatus_);

    // Level bar
    leftLay->addWidget(new QLabel("音频电平", leftCard));
    levelBar_ = new QLabel("---", leftCard);
    levelBar_->setFixedHeight(20);
    levelBar_->setStyleSheet(QString("background: %1; border-radius: 4px;").arg(tokens::kCard1));
    leftLay->addWidget(levelBar_);

    // Sky view
    skyView_ = new ui::SkyView(leftCard);
    leftLay->addWidget(skyView_);

    leftLay->addStretch();
    splitter->addWidget(leftCard);

    // Center: stacked spectrum + world view
    auto* centerCard = new QFrame(splitter);
    centerCard->setObjectName("panelCard");
    auto* centerLay = new QVBoxLayout(centerCard);
    centerLay->setContentsMargins(0,0,0,0);

    centerStack_ = new QStackedWidget(centerCard);
    spectrum_ = new ui::SpectrumWidget(centerCard);
    centerStack_->addWidget(spectrum_);
    worldView_ = new ui::WorldView(centerCard);
    centerStack_->addWidget(worldView_);
    centerLay->addWidget(centerStack_);

    // Toggle buttons
    auto* toggleRow = new QHBoxLayout();
    auto* specBtn = new QPushButton("频谱", centerCard);
    auto* worldBtn = new QPushButton("世界", centerCard);
    toggleRow->addWidget(specBtn);
    toggleRow->addWidget(worldBtn);
    centerLay->addLayout(toggleRow);
    connect(specBtn, &QPushButton::clicked, this, [this]() { centerStack_->setCurrentWidget(spectrum_); });
    connect(worldBtn, &QPushButton::clicked, this, [this]() { centerStack_->setCurrentWidget(worldView_); });

    splitter->addWidget(centerCard);

    // Right panel: tabs
    auto* rightCard = makePanelCard("面板", splitter);
    rightTabs_ = new QTabWidget(rightCard);
    rightTabs_->setTabPosition(QTabWidget::North);

    auto* taskWidget = new QWidget();
    auto* taskLay = new QVBoxLayout(taskWidget);
    taskLay->addWidget(new QLabel("AI Agent / 任务列表\n（Phase 6 接入）", taskWidget));
    taskLay->addStretch();
    rightTabs_->addTab(taskWidget, "任务");

    auto* cwWidget = new QWidget();
    auto* cwLay = new QVBoxLayout(cwWidget);
    cwLay->addWidget(new QLabel("CW 摩尔斯解码（800Hz BFO）", cwWidget));
    cwWpm_ = new QLabel("WPM: --", cwWidget);
    cwLay->addWidget(cwWpm_);
    cwText_ = new QPlainTextEdit(cwWidget);
    cwText_->setReadOnly(true);
    cwText_->setPlaceholderText("无 CW 信号时此处为空");
    cwLay->addWidget(cwText_);
    rightTabs_->addTab(cwWidget, "CW");

    auto* adsbWidget = new QWidget();
    auto* adsbLay = new QVBoxLayout(adsbWidget);
    adsbLay->addWidget(new QLabel("ADS-B 1090MHz\n需 1090MHz 专用天线，FC0012 可能不支持", adsbWidget));
    adsbTable_ = new QTableWidget(0, 4, adsbWidget);
    adsbTable_->setHorizontalHeaderLabels({"ICAO", "呼号", "高度(ft)", "最后出现"});
    adsbTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    adsbLay->addWidget(adsbTable_);
    rightTabs_->addTab(adsbWidget, "ADS-B");

    // AI tab
    auto* aiWidget = new QWidget();
    auto* aiLay = new QVBoxLayout(aiWidget);
    aiStatus_ = new QLabel("未配置 API Key — 仅本地指令", aiWidget);
    aiLay->addWidget(aiStatus_);
    aiChat_ = new QPlainTextEdit(aiWidget);
    aiChat_->setReadOnly(true);
    aiLay->addWidget(aiChat_);
    auto* aiRow = new QHBoxLayout();
    aiInput_ = new QLineEdit(aiWidget);
    aiInput_->setPlaceholderText("输入指令，如 98.5 或 am 或 record");
    aiRow->addWidget(aiInput_, 1);
    auto* sendBtn = new QPushButton("发送", aiWidget);
    aiRow->addWidget(sendBtn);
    aiLay->addLayout(aiRow);
    rightTabs_->addTab(aiWidget, "AI 助手");

    qobject_cast<QVBoxLayout*>(rightCard->layout())->addWidget(rightTabs_);
    splitter->addWidget(rightCard);

    splitter->setStretchFactor(0, 1);
    splitter->setStretchFactor(1, 4);
    splitter->setStretchFactor(2, 1);
    splitter->setSizes({280, 720, 280});
    vbox->addWidget(splitter, 1);

    // ---- Bottom dock ----
    auto* dock = new QFrame();
    dock->setObjectName("bottomDock");
    dock->setFixedHeight(tokens::kDockH);
    auto* dockLay = new QHBoxLayout(dock);
    dockLay->setContentsMargins(tokens::kDockPadX, tokens::kDockPadY,
                                tokens::kDockPadX, tokens::kDockPadY);
    dockLay->setSpacing(tokens::kDockIconGap);
    auto* home = new QPushButton("⌂", dock); home->setObjectName("homeBtn");
    dockLay->addWidget(home);
    auto* tempBox = new QHBoxLayout(); tempBox->setSpacing(8);
    auto* la = new QLabel("‹", dock); la->setObjectName("tempArrow");
    auto* tv = new QLabel("23.5°", dock); tv->setObjectName("tempLabel");
    auto* ra = new QLabel("›", dock); ra->setObjectName("tempArrow");
    tempBox->addWidget(la); tempBox->addWidget(tv); tempBox->addWidget(ra);
    dockLay->addLayout(tempBox);
    for (int i = 0; i < 5; ++i) {
        auto* ic = new QPushButton(dock);
        ic->setObjectName(i == 0 ? "dockIconSelected" : "dockIcon");
        dockLay->addWidget(ic);
    }
    auto* np = new QFrame(dock);
    np->setFixedWidth(tokens::kNowPlayingW);
    np->setStyleSheet("background: transparent;");
    auto* npLay = new QHBoxLayout(np);
    npLay->setContentsMargins(0,0,0,0); npLay->setSpacing(10);
    auto* cover = new QFrame(np);
    cover->setFixedSize(tokens::kNowPlayingCover, tokens::kNowPlayingCover);
    cover->setStyleSheet(
        QString("background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 %1, stop:1 %2); border-radius: %3px;")
            .arg(QString::fromUtf8(tokens::kNowPlayingCoverFrom),
                 QString::fromUtf8(tokens::kNowPlayingCoverTo),
                 QString::number(tokens::kRadiusAlbumSm)));
    npLay->addWidget(cover);
    auto* npMid = new QVBoxLayout();
    auto* song = new QLabel("Starboy", np); song->setObjectName("songTitle");
    auto* prog = new QFrame(np);
    prog->setFixedHeight(4);
    prog->setStyleSheet(
        QString("background: %1; border-radius: %2px;")
            .arg(QString::fromUtf8(tokens::kProgressBg))
            .arg(tokens::kRadiusProgress));
    npMid->addWidget(song); npMid->addWidget(prog);
    npLay->addLayout(npMid, 1);
    auto* play = new QPushButton("▶", np); play->setObjectName("playBtn");
    auto* next = new QPushButton("⏭", np); next->setObjectName("nextBtn");
    npLay->addWidget(play); npLay->addWidget(next);
    dockLay->addWidget(np);
    auto* tempBox2 = new QHBoxLayout(); tempBox2->setSpacing(8);
    auto* l2 = new QLabel("‹", dock); l2->setObjectName("tempArrow");
    auto* t2 = new QLabel("23.5°", dock); t2->setObjectName("tempLabel");
    auto* r2 = new QLabel("›", dock); r2->setObjectName("tempArrow");
    tempBox2->addWidget(l2); tempBox2->addWidget(t2); tempBox2->addWidget(r2);
    dockLay->addLayout(tempBox2);
    auto* vol = new QPushButton("♪", dock); vol->setObjectName("volBtn");
    dockLay->addWidget(vol);
    vbox->addWidget(dock);

    statusBar()->showMessage("MBDSDR C++ Phase 2 -- 统一信号源 + RTL-SDR 接入层");

    // ---- Wire engine ----
    engine_ = new dsp::SpectrumEngine(this);
    connect(engine_, &dsp::SpectrumEngine::spectrumReady,
            spectrum_, &ui::SpectrumWidget::setSpectrum, Qt::QueuedConnection);
    connect(spectrum_, &ui::SpectrumWidget::fftSizeRequested,
            engine_, &dsp::SpectrumEngine::setFftSize);
    connect(engine_, &dsp::SpectrumEngine::sourceChanged,
            this, &MainWindow::onSourceChanged);
    connect(engine_, &dsp::SpectrumEngine::audioLevel,
            this, &MainWindow::onAudioLevel);
    connect(engine_, &dsp::SpectrumEngine::rssiLevel,
            this, &MainWindow::onRssiLevel);
    connect(engine_, &dsp::SpectrumEngine::squelchState,
            this, &MainWindow::onSquelchState);

    // Control signals -> engine
    connect(demodCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int) {
                engine_->setDemodMode(demodCombo_->currentText());
                // Auto-set default bandwidth per mode
                static const QMap<QString, int> bwIdx = {
                    {"AM", 0}, {"NFM", 1}, {"WFM", 2}, {"USB", 3}, {"LSB", 3}, {"CW", 1}
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

    connect(bwCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                static const double bw[] = {8000, 12500, 200000, 2400};
                engine_->setBandwidth(bw[idx]);
            });
    connect(recordBtn_, &QPushButton::clicked, this, &MainWindow::onRecordClicked);
    connect(gatedCheck_, &QCheckBox::toggled,
            this, [this](bool e) { engine_->setGatedRecordingEnabled(e); });
    connect(engine_, &dsp::SpectrumEngine::recordingStateChanged,
            this, &MainWindow::onRecordingState);

    // Control signals -> engine
    connect(freqSpin_, QOverload<double>::of(&QDoubleSpinBox::valueChanged),
            this, [this](double mhz) { engine_->onSetCenterFreq(mhz * 1e6); });
    connect(srCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                double sr = tokens::kSampleRatesHz.begin()[idx];
                engine_->onSetSampleRate(sr);
            });
    connect(gainSlider_, &QSlider::valueChanged,
            this, [this](int v) {
                double db = v / 10.0;
                gainValue_->setText(QString("%1 dB").arg(db, 0, 'f', 1));
                engine_->onSetGain(db);
            });

    connect(engine_, &dsp::SpectrumEngine::cwDecoded,
            this, &MainWindow::onCwDecoded);
    connect(engine_, &dsp::SpectrumEngine::adsbAircraft,
            this, &MainWindow::onAdsbAircraft);

    // AI agent
    agent_ = new ai::Agent(this);
    agent_->setEngine(engine_);
    agent_->configureFromConfig();
    connect(agent_, &ai::Agent::responseReady, this, [this](const QString& r) {
        aiChat_->appendPlainText("AI: " + r);
    });
    connect(agent_, &ai::Agent::toolCalled, this, [this](const QString& t, const QString& r) {
        aiChat_->appendPlainText(QString("[工具] %1 → %2").arg(t, r));
    });
    connect(agent_, &ai::Agent::statusChanged, this, [this](const QString& s) {
        aiStatus_->setText(s);
    });
    connect(aiInput_, &QLineEdit::returnPressed, this, [this]() {
        QString text = aiInput_->text();
        if (text.isEmpty()) return;
        aiChat_->appendPlainText("你: " + text);
        aiInput_->clear();
        agent_->sendMessage(text);
    });
    connect(sendBtn, &QPushButton::clicked, this, [this]() {
        aiInput_->returnPressed();
    });

    // Connect/disconnect button
    connect(connectBtn_, &QPushButton::clicked, this, [this]() {
        if (connectBtn_->text() == "连接") {
            bool ok = engine_->tryConnectRtl();
            connectBtn_->setText(ok ? "断开" : "连接");
        } else {
            engine_->disconnectSource();
            connectBtn_->setText("连接");
        }
    });

    // Keyboard shortcuts
    new QShortcut(QKeySequence(Qt::Key_Right), this, this, [this]() {
        engine_->onSetCenterFreq(freqSpin_->value() * 1e6 + 10000);  // +10kHz
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
    new QShortcut(QKeySequence("Ctrl+R"), this, this, [this]() {
        recordBtn_->click();
    });
    static bool muted = false;
    new QShortcut(QKeySequence(Qt::Key_Space), this, this, [this]() {
        muted = !muted;
        engine_->setMuted(muted);
        statusLabel_->setText(muted ? "已静音" : "");
    });

    // Spectrum drag tuning
    connect(spectrum_, &ui::SpectrumWidget::frequencyChanged, this, [this](double hz) {
        freqSpin_->blockSignals(true);
        freqSpin_->setValue(hz / 1e6);
        freqSpin_->blockSignals(false);
        engine_->onSetCenterFreq(hz);
    });

    // About / settings buttons
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

    engine_->start();
}

MainWindow::~MainWindow() {
    saveUiState();
    if (engine_) { engine_->shutdown(); engine_->wait(); }
}

void MainWindow::saveUiState() {
    QSettings s("MBDSDR", "MBDSDR");
    s.setValue("geometry", saveGeometry());
    s.setValue("windowState", saveState());
}

void MainWindow::restoreUiState() {
    QSettings s("MBDSDR", "MBDSDR");
    restoreGeometry(s.value("geometry").toByteArray());
    restoreState(s.value("windowState").toByteArray());
}

void MainWindow::onSourceChanged(const QString& name, bool connected) {
    statusLabel_->setText(connected ? QString("● %1").arg(name)
                                    : QString("● %1 (test)").arg(name));
    if (connectBtn_) connectBtn_->setText(connected ? "断开" : "连接");
    setControlsEnabled(connected);
}

void MainWindow::setControlsEnabled(bool hw) {
    freqSpin_->setEnabled(hw);
    srCombo_->setEnabled(hw);
    gainSlider_->setEnabled(hw);
    sourceBanner_->setText(hw ? QString() :
        QStringLiteral("RTL-SDR 未连接，使用测试信号（频率/增益不生效）"));
    sourceBanner_->setVisible(!hw);
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
    squelchState_->setText(open ? "静噪: OPEN" : "静噪: CLOSED");
}

void MainWindow::onRecordClicked() {
    if (!engine_) return;
    if (recordBtn_->text().contains("REC")) {
        engine_->startRecording();
    } else {
        engine_->stopRecording();
    }
}

void MainWindow::onRecordingState(bool recording, const QString& path) {
    recordBtn_->setText(recording ? "■ STOP" : "● REC");
    recStatus_->setText(recording ? QString("录制: %1").arg(path) : "录制: 空闲");
}

void MainWindow::onCwDecoded(const QString& text, double wpm) {
    cwText_->appendPlainText(text);
    cwWpm_->setText(QString("WPM: %1").arg(wpm, 0, 'f', 1));
}

void MainWindow::onAdsbAircraft(const mbdsdr::dsp::AircraftInfo& info) {
    int row = adsbTable_->rowCount();
    for (int i = 0; i < row; ++i) {
        if (adsbTable_->item(i, 0)->text() == info.icao) {
            adsbTable_->item(i, 1)->setText(info.callsign);
            adsbTable_->item(i, 2)->setText(info.altitudeFt > 0 ?
                QString::number(info.altitudeFt) : "--");
            adsbTable_->item(i, 3)->setText(info.lastSeen.toString("HH:mm:ss"));
            return;
        }
    }
    adsbTable_->insertRow(row);
    adsbTable_->setItem(row, 0, new QTableWidgetItem(info.icao));
    adsbTable_->setItem(row, 1, new QTableWidgetItem(info.callsign));
    adsbTable_->setItem(row, 2, new QTableWidgetItem(info.altitudeFt > 0 ?
        QString::number(info.altitudeFt) : "--"));
    adsbTable_->setItem(row, 3, new QTableWidgetItem(info.lastSeen.toString("HH:mm:ss")));
}

} // namespace mbdsdr
