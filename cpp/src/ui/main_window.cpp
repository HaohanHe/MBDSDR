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
#include <QBoxLayout>

#include "core/tokens.h"
#include "dsp/spectrum_engine.h"

namespace mbdsdr {

// Helper: build a standard panel card with title
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

MainWindow::MainWindow(QWidget* parent)
    : QMainWindow(parent)
{
    setWindowTitle("MBDSDR C++");
    resize(1280, 800);

    auto* central = new QWidget(this);
    setCentralWidget(central);
    auto* vbox = new QVBoxLayout(central);
    vbox->setContentsMargins(0, 0, 0, 0);
    vbox->setSpacing(0);

    // ======================= TOP BAR (pure black) =======================
    auto* topBar = new QWidget();
    topBar->setObjectName("topBar");
    topBar->setFixedHeight(tokens::kTopbarH);
    auto* topLay = new QHBoxLayout(topBar);
    topLay->setContentsMargins(tokens::kTopbarPadLeft, tokens::kTopbarPadTop,
                               tokens::kTopbarPadRight, tokens::kTopbarPadBottom);
    topLay->setSpacing(20);

    // Left: clock
    auto* clock = new QLabel("12:30", topBar);
    clock->setObjectName("clockLabel");
    topLay->addWidget(clock);

    // Mileage group
    auto* mileBox = new QVBoxLayout();
    auto* mile = new QLabel("468km", topBar);
    mile->setObjectName("mileageLabel");
    auto* mileSub = new QLabel("预估", topBar);
    mileSub->setObjectName("mileageSub");
    mileBox->addWidget(mile);
    mileBox->addWidget(mileSub);
    topLay->addLayout(mileBox);

    // Battery bar (placeholder: background track + fill)
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

    topLay->addSpacing(67); // PRND gap
    topLay->addWidget(new QLabel("P R N D", topBar));

    topLay->addStretch();

    // Right: system icon area (placeholder)
    auto* sysArea = new QWidget(topBar);
    sysArea->setFixedWidth(tokens::kSystemIconAreaW);
    auto* sysLay = new QHBoxLayout(sysArea);
    sysLay->setContentsMargins(0, 0, 0, 0);
    sysLay->setSpacing(12);
    for (int i = 0; i < 3; ++i) {
        auto* ic = new QPushButton(sysArea);
        ic->setFixedSize(tokens::kSystemIconSize, tokens::kSystemIconSize);
        ic->setStyleSheet("background: transparent; border: none;");
        sysLay->addWidget(ic);
    }
    topLay->addWidget(sysArea);

    vbox->addWidget(topBar);

    // ======================= THREE-COLUMN SPLITTER =======================
    auto* splitter = new QSplitter(Qt::Horizontal, central);

    // Left panel: sky track placeholder
    auto* leftCard = makePanelCard("天空轨道", splitter);
    qobject_cast<QVBoxLayout*>(leftCard->layout())->addWidget(
        new QLabel("占位：卫星 / 射频天空视图\n（Phase 5 接入）", leftCard));
    qobject_cast<QVBoxLayout*>(leftCard->layout())->addStretch();
    splitter->addWidget(leftCard);

    // Center: spectrum widget embedded in a panel card
    auto* centerCard = new QFrame(splitter);
    centerCard->setObjectName("panelCard");
    auto* centerLay = new QVBoxLayout(centerCard);
    centerLay->setContentsMargins(0, 0, 0, 0);
    centerLay->setSpacing(0);
    spectrum_ = new ui::SpectrumWidget(centerCard);
    centerLay->addWidget(spectrum_);
    splitter->addWidget(centerCard);

    // Right panel: current task
    auto* rightCard = makePanelCard("当前任务", splitter);
    qobject_cast<QVBoxLayout*>(rightCard->layout())->addWidget(
        new QLabel("占位：AI Agent / 任务列表\n（Phase 6 接入）", rightCard));
    qobject_cast<QVBoxLayout*>(rightCard->layout())->addStretch();
    splitter->addWidget(rightCard);

    splitter->setStretchFactor(0, 1);
    splitter->setStretchFactor(1, 4);
    splitter->setStretchFactor(2, 1);
    splitter->setSizes({240, 780, 240});
    vbox->addWidget(splitter, 1);

    // ======================= BOTTOM DOCK (pure black) =======================
    auto* dock = new QFrame();
    dock->setObjectName("bottomDock");
    dock->setFixedHeight(tokens::kDockH);
    auto* dockLay = new QHBoxLayout(dock);
    dockLay->setContentsMargins(tokens::kDockPadX, tokens::kDockPadY,
                                tokens::kDockPadX, tokens::kDockPadY);
    dockLay->setSpacing(tokens::kDockIconGap);

    // 1. Home button
    auto* home = new QPushButton("⌂", dock);
    home->setObjectName("homeBtn");
    dockLay->addWidget(home);

    // 2. Temp control group
    auto* tempBox = new QHBoxLayout();
    tempBox->setSpacing(8);
    auto* leftArrow = new QLabel("‹", dock);
    leftArrow->setObjectName("tempArrow");
    auto* tempVal = new QLabel("23.5°", dock);
    tempVal->setObjectName("tempLabel");
    auto* rightArrow = new QLabel("›", dock);
    rightArrow->setObjectName("tempArrow");
    tempBox->addWidget(leftArrow);
    tempBox->addWidget(tempVal);
    tempBox->addWidget(rightArrow);
    dockLay->addLayout(tempBox);

    // 3. App dock: 5 icons
    for (int i = 0; i < 5; ++i) {
        auto* ic = new QPushButton(dock);
        ic->setObjectName(i == 0 ? "dockIconSelected" : "dockIcon");
        dockLay->addWidget(ic);
    }

    // 4. NowPlaying bar
    auto* np = new QFrame(dock);
    np->setFixedWidth(tokens::kNowPlayingW);
    np->setStyleSheet("background: transparent;");
    auto* npLay = new QHBoxLayout(np);
    npLay->setContentsMargins(0, 0, 0, 0);
    npLay->setSpacing(10);
    auto* cover = new QFrame(np);
    cover->setFixedSize(tokens::kNowPlayingCover, tokens::kNowPlayingCover);
    cover->setStyleSheet(
        QString("background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 %1, stop:1 %2);"
                "border-radius: %3px;")
            .arg(QString::fromUtf8(tokens::kNowPlayingCoverFrom),
                 QString::fromUtf8(tokens::kNowPlayingCoverTo),
                 QString::number(tokens::kRadiusAlbumSm)));
    npLay->addWidget(cover);
    auto* npMid = new QVBoxLayout();
    auto* song = new QLabel("Starboy", np);
    song->setObjectName("songTitle");
    auto* prog = new QFrame(np);
    prog->setFixedHeight(4);
    prog->setStyleSheet(
        QString("background: %1; border-radius: %2px;")
            .arg(QString::fromUtf8(tokens::kProgressBg))
            .arg(tokens::kRadiusProgress));
    npMid->addWidget(song);
    npMid->addWidget(prog);
    npLay->addLayout(npMid, 1);
    auto* play = new QPushButton("▶", np);
    play->setObjectName("playBtn");
    auto* next = new QPushButton("⏭", np);
    next->setObjectName("nextBtn");
    npLay->addWidget(play);
    npLay->addWidget(next);
    dockLay->addWidget(np);

    // 5. Second temp control group (mirror)
    auto* tempBox2 = new QHBoxLayout();
    tempBox2->setSpacing(8);
    auto* l2 = new QLabel("‹", dock); l2->setObjectName("tempArrow");
    auto* t2 = new QLabel("23.5°", dock); t2->setObjectName("tempLabel");
    auto* r2 = new QLabel("›", dock); r2->setObjectName("tempArrow");
    tempBox2->addWidget(l2); tempBox2->addWidget(t2); tempBox2->addWidget(r2);
    dockLay->addLayout(tempBox2);

    // 6. Volume button
    auto* vol = new QPushButton("♪", dock);
    vol->setObjectName("volBtn");
    dockLay->addWidget(vol);

    vbox->addWidget(dock);

    statusBar()->showMessage("MBDSDR C++ Phase 1 -- 统一架构骨架 + FFT 频谱热路径（测试数据）");

    // ---- Wire engine (same process, queued cross-thread connection) ----
    engine_ = new dsp::SpectrumEngine(this);
    connect(engine_, &dsp::SpectrumEngine::spectrumReady,
            spectrum_, &ui::SpectrumWidget::setSpectrum,
            Qt::QueuedConnection);
    connect(spectrum_, &ui::SpectrumWidget::fftSizeRequested,
            engine_, &dsp::SpectrumEngine::setFftSize);
    engine_->start();
}

MainWindow::~MainWindow() {
    if (engine_) {
        engine_->shutdown();
        engine_->wait();
    }
}

} // namespace mbdsdr
