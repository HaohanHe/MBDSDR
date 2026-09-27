// SPDX-License-Identifier: MIT
#include "main_window.h"
#include "spectrum_widget.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QSplitter>
#include <QFrame>
#include <QLabel>
#include <QStatusBar>

#include "core/tokens.h"
#include "dsp/spectrum_engine.h"

namespace mbdsdr {

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

    // ---- Top bar (height from tokens) ----
    auto* topBar = new QWidget();
    topBar->setObjectName("topBar");
    topBar->setFixedHeight(tokens::kTopbarH);
    auto* topLayout = new QHBoxLayout(topBar);
    topLayout->setContentsMargins(20, 0, 20, 0);
    auto* title = new QLabel("MBDSDR C++", topBar);
    title->setObjectName("windowTitle");
    topLayout->addWidget(title);
    topLayout->addStretch();
    statusLabel_ = new QLabel("TEST SIGNAL ENGINE -- 非硬件 / NOT HARDWARE", topBar);
    statusLabel_->setObjectName("statusBanner");
    topLayout->addWidget(statusLabel_);
    vbox->addWidget(topBar);

    // ---- Horizontal splitter: left | center | right ----
    auto* splitter = new QSplitter(Qt::Horizontal, central);

    auto* leftCard = new QFrame();
    leftCard->setObjectName("card");
    auto* leftLayout = new QVBoxLayout(leftCard);
    leftLayout->setContentsMargins(20, 20, 20, 20);
    auto* leftTitle = new QLabel("控制面板", leftCard);
    leftTitle->setObjectName("sectionTitle");
    leftLayout->addWidget(leftTitle);
    leftLayout->addWidget(new QLabel("占位面板\n（Phase 2 接入增益/解调/带宽控件）", leftCard));
    leftLayout->addStretch();
    splitter->addWidget(leftCard);

    spectrum_ = new ui::SpectrumWidget(splitter);
    splitter->addWidget(spectrum_);

    auto* rightCard = new QFrame();
    rightCard->setObjectName("card");
    auto* rightLayout = new QVBoxLayout(rightCard);
    rightLayout->setContentsMargins(20, 20, 20, 20);
    auto* rightTitle = new QLabel("任务 / AI", rightCard);
    rightTitle->setObjectName("sectionTitle");
    rightLayout->addWidget(rightTitle);
    rightLayout->addWidget(new QLabel("占位面板\n（Phase 6 接入 AI Agent / MCP）", rightCard));
    rightLayout->addStretch();
    splitter->addWidget(rightCard);

    // Proportions from tokens: 0.19 : 0.62 : 0.19
    splitter->setStretchFactor(0, 1);
    splitter->setStretchFactor(1, 4);
    splitter->setStretchFactor(2, 1);
    splitter->setSizes({240, 780, 240});
    vbox->addWidget(splitter, 1);

    // ---- Bottom dock (height from tokens) ----
    auto* dock = new QFrame();
    dock->setObjectName("bottomDock");
    dock->setFixedHeight(tokens::kDockH);
    auto* dockLayout = new QHBoxLayout(dock);
    dockLayout->setContentsMargins(20, 0, 20, 0);
    dockLayout->addWidget(new QLabel("Dock 占位：录制 / 回放 / VFO 选择", dock));
    dockLayout->addStretch();
    auto* hint = new QLabel("TEST SIGNAL -- 无硬件连接", dock);
    hint->setObjectName("dockHint");
    dockLayout->addWidget(hint);
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
