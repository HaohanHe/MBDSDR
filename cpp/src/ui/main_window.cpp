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
#include <QGroupBox>

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
    demodCombo_->addItems({"NFM", "WFM", "AM", "USB", "LSB"});
    leftLay->addWidget(demodCombo_);

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

    leftLay->addStretch();
    splitter->addWidget(leftCard);

    // Center: spectrum
    auto* centerCard = new QFrame(splitter);
    centerCard->setObjectName("panelCard");
    auto* centerLay = new QVBoxLayout(centerCard);
    centerLay->setContentsMargins(0,0,0,0);
    spectrum_ = new ui::SpectrumWidget(centerCard);
    centerLay->addWidget(spectrum_);
    splitter->addWidget(centerCard);

    // Right panel
    auto* rightCard = makePanelCard("当前任务", splitter);
    qobject_cast<QVBoxLayout*>(rightCard->layout())->addWidget(
        new QLabel("占位：AI Agent / 任务列表\n（Phase 6 接入）", rightCard));
    qobject_cast<QVBoxLayout*>(rightCard->layout())->addStretch();
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
    connect(engine_, &dsp::SpectrumEngine::squelchState,
            this, &MainWindow::onSquelchState);

    // Control signals -> engine
    connect(demodCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int) { engine_->setDemodMode(demodCombo_->currentText()); });
    connect(squelchSlider_, &QSlider::valueChanged,
            this, [this](int v) {
                squelchValue_->setText(QString("%1 dB").arg(v));
                engine_->setSquelchThreshold(static_cast<float>(v));
            });

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

    engine_->start();
}

MainWindow::~MainWindow() {
    if (engine_) { engine_->shutdown(); engine_->wait(); }
}

void MainWindow::onSourceChanged(const QString& name, bool connected) {
    statusLabel_->setText(connected ? QString("● %1").arg(name)
                                    : QString("● %1 (test)").arg(name));
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
}

void MainWindow::onSquelchState(bool open) {
    squelchState_->setText(open ? "静噪: OPEN" : "静噪: CLOSED");
}

} // namespace mbdsdr
