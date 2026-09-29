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
#include <QDateTime>
#include <QTimer>
#include <QDialog>
#include <QFormLayout>
#include <QDir>
#include <QFileInfo>
#include <QUrl>
#include <QDesktopServices>
#include <QMediaDevices>
#include <QAudioDevice>
#include <QListWidget>
#include <QListWidgetItem>

#include "dsp/vfo_manager.h"
#include "ui/constellation_view.h"

#include <cmath>
#include <algorithm>

#include "core/tokens.h"
#include "core/spectrum_frame.h"
#include "dsp/spectrum_engine.h"
#include "dsp/adsb_decoder.h"
#include "dsp/tle_client.h"
#include "ai/agent.h"
#include "ai/ai_config.h"
#include "ui/sky_view.h"
#include "ui/bookmark_manager.h"
#include "ui/shortcuts_dialog.h"
#include <QListWidget>
#include <QLineEdit>
#include <QSpinBox>
#include <QApplication>
#include <QClipboard>
#include <QInputDialog>
#include "ui/world_view.h"
#include "ui/elevation_plot.h"
#include "gnss/gnss_receiver.h"
#include "gnss/gnss_types.h"
#include "ui/spectrum_widget.h"
#include "ui/settings_dialog.h"
#include "ui/about_dialog.h"
#include "ui/radio_panel.h"

namespace mbdsdr {

namespace {
// Tuning step combo (index -> Hz). Must stay in sync with the items added in
// the frequency group: 1 Hz / 10 Hz / 100 Hz / 1 kHz / 10 kHz / 100 kHz / 1 MHz.
constexpr int kStepValuesHz[] = {1, 10, 100, 1000, 10000, 100000, 1000000};
constexpr int kStepCount = sizeof(kStepValuesHz) / sizeof(kStepValuesHz[0]);

// Bandwidth presets indexed by bwCombo_ order: 8k / 12.5k / 200k / 2.4k / 500Hz.
// Up/Down keyboard nudge doubles/halves the *current* bandwidth and clamps to
// [1k, 200k]; we then snap bwCombo_ to the nearest preset.
constexpr double kBwMinHz = 1000.0;
constexpr double kBwMaxHz = 200000.0;
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

    auto* gFreq = new QGroupBox("频率", leftCard);
    auto* gFreqLay = new QFormLayout(gFreq);
    freqSpin_ = new QDoubleSpinBox(gFreq);
    freqSpin_->setObjectName("freqSpin");
    freqSpin_->setRange(tokens::kFreqMinHz / 1e6, tokens::kFreqMaxHz / 1e6);
    freqSpin_->setValue(98.5);
    freqSpin_->setDecimals(3);
    freqSpin_->setSuffix(" MHz");
    gFreqLay->addRow("中心频率", freqSpin_);
    stepCombo_ = new QComboBox(gFreq);
    stepCombo_->addItems({"1 Hz", "10 Hz", "100 Hz", "1 kHz",
                          "10 kHz", "100 kHz", "1 MHz"});
    stepCombo_->setCurrentIndex(4);   // 10 kHz default
    gFreqLay->addRow("步进", stepCombo_);
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
    demodCombo_->setObjectName("demodCombo");
    demodCombo_->addItems({"AM", "NFM", "WFM", "USB", "LSB", "CW", "BPSK", "QPSK"});
    gRxLay->addRow("解调", demodCombo_);
    bwCombo_ = new QComboBox(gRx);
    bwCombo_->addItems({"8 kHz", "12.5 kHz", "200 kHz", "2.4 kHz", "500 Hz"});
    gRxLay->addRow("带宽", bwCombo_);
    leftLay->addWidget(gRx);

    // ---- Multi-VFO panel ---------------------------------------------------
    auto* gVfo = new QGroupBox("多 VFO", leftCard);
    auto* gVfoLay = new QVBoxLayout(gVfo);
    vfoList_ = new QListWidget(gVfo);
    vfoList_->setObjectName("vfoList");
    vfoList_->setMaximumHeight(tokens::scaled(96));
    vfoList_->setSelectionMode(QAbstractItemView::SingleSelection);
    gVfoLay->addWidget(vfoList_);
    auto* vfoBtnRow = new QHBoxLayout;
    vfoAddBtn_ = new QPushButton("＋ 添加 VFO", gVfo);
    vfoDelBtn_ = new QPushButton("－ 删除", gVfo);
    vfoAddBtn_->setObjectName("vfoAddBtn");
    vfoDelBtn_->setObjectName("vfoDelBtn");
    vfoBtnRow->addWidget(vfoAddBtn_);
    vfoBtnRow->addWidget(vfoDelBtn_);
    gVfoLay->addLayout(vfoBtnRow);
    leftLay->addWidget(gVfo);

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

    // World tab: a compact GNSS/serial toolbar on top, the offline map below.
    // Real data only: the receiver point appears only after a valid fix; with
    // no fix the map keeps the hand-entered station and states "GNSS 无定位".
    adsbMap_ = new QMap<QString, ui::AircraftPoint>();
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
    centerLay->addWidget(centerTabs_);
    splitter->addWidget(centerCard);

    // ---- Right panel: tabs ----
    auto* rightCard = new QFrame;
    rightCard->setObjectName("panelCard");
    auto* rightLay = new QVBoxLayout(rightCard);
    rightTabs_ = new QTabWidget(rightCard);
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
    adsbEmpty_ = new QLabel("1090MHz 无飞机\n（FC0012 一般收不到 1090，留作支持）", adsbPage);
    adsbEmpty_->setObjectName("statusHint");
    adsbEmpty_->setAlignment(Qt::AlignCenter);
    adsbEmpty_->setWordWrap(true);
    adsbLay->addWidget(adsbEmpty_);
    adsbTable_ = new QTableWidget(0, 6, adsbPage);
    adsbTable_->setHorizontalHeaderLabels({"ICAO", "呼号", "高度(ft)", "速度(kt)", "航向(°)", "垂直(fpm)", "距离(km)", "时间"});
    adsbTable_->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    adsbLay->addWidget(adsbTable_);
    rightTabs_->addTab(adsbPage, "ADS-B");

    // Constellation tab: live BPSK/QPSK symbol scatter from the selected digital VFO.
    {
        auto* cstPage = new QWidget;
        auto* cstLay = new QVBoxLayout(cstPage);
        cstLay->setContentsMargins(0, 0, 0, 0);
        constellationView_ = new ui::ConstellationView(cstPage);
        cstLay->addWidget(constellationView_);
        rightTabs_->addTab(cstPage, "星座");
    }

    // Sky tab: polar view on top, TLE freshness badge, pass list below.
    auto* skyPage = new QWidget;
    auto* skyLay = new QVBoxLayout(skyPage);
    skyLay->setContentsMargins(0, 0, 0, 0);
    skyView_ = new ui::SkyView();
    skyLay->addWidget(skyView_, 2);
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
    passTable_ = new QTableWidget(0, 5, skyPage);
    passTable_->setHorizontalHeaderLabels({"卫星", "AOS", "最大仰角", "LOS", "距今"});
    passTable_->horizontalHeader()->setStretchLastSection(true);
    passTable_->horizontalHeader()->setSectionsClickable(true);
    passTable_->setSortingEnabled(true);
    passTable_->verticalHeader()->setVisible(false);
    passTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    passTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    passTable_->setSelectionMode(QAbstractItemView::SingleSelection);
    skyLay->addWidget(passTable_, 1);
    rightTabs_->addTab(skyPage, "天空");

    // ---- Bookmarks tab: user-saved frequencies, jump on click ----
    bookmarkManager_ = new ui::BookmarkManager();
    bookmarkManager_->load();
    auto* bmPage = new QWidget;
    auto* bmLay = new QVBoxLayout(bmPage);
    bmList_ = new QListWidget(bmPage);
    bmLay->addWidget(bmList_, 1);
    auto* bmRow = new QHBoxLayout;
    auto* bmSave = new QPushButton("存为书签", bmPage);
    auto* bmDel = new QPushButton("删除", bmPage);
    bmRow->addWidget(bmSave);
    bmRow->addWidget(bmDel);
    bmLay->addLayout(bmRow);
    auto refreshBm = [this]() {
        bmList_->clear();
        for (const auto& b : bookmarkManager_->list()) {
            const QString f = QString("%1 MHz").arg(b.freqHz / 1e6, 0, 'f', 3);
            bmList_->addItem(b.note.isEmpty() ? f : f + " — " + b.note);
        }
    };
    refreshBm();
    connect(bmSave, &QPushButton::clicked, this, [this, refreshBm]() {
        bool ok = false;
        const QString note = QInputDialog::getText(this, "存为书签",
            "备注（可留空）:", QLineEdit::Normal, "", &ok);
        if (!ok) return;
        bookmarkManager_->add(freqSpin_->value() * 1e6, note.trimmed());
        refreshBm();
    });
    connect(bmDel, &QPushButton::clicked, this, [this, refreshBm]() {
        int row = bmList_->currentRow();
        if (row >= 0) { bookmarkManager_->remove(row); refreshBm(); }
    });
    connect(bmList_, &QListWidget::itemDoubleClicked, this, [this](QListWidgetItem* it) {
        int row = bmList_->row(it);
        if (row >= 0 && row < bookmarkManager_->list().size())
            engine_->onSetCenterFreq(bookmarkManager_->list()[row].freqHz);
    });

    // ---- Band scanner: step frequencies on a QTimer, record RSSI peaks ----
    auto* scanBox = new QGroupBox("频段扫描", bmPage);
    auto* scanLay = new QVBoxLayout(scanBox);
    auto* scanForm = new QFormLayout;
    scanStartSpin_ = new QDoubleSpinBox(scanBox);
    scanStartSpin_->setRange(0.1, 2200); scanStartSpin_->setValue(88);
    scanStartSpin_->setSuffix(" MHz");
    scanStopSpin_ = new QDoubleSpinBox(scanBox);
    scanStopSpin_->setRange(0.1, 2200); scanStopSpin_->setValue(108);
    scanStopSpin_->setSuffix(" MHz");
    scanStepCombo_ = new QComboBox(scanBox);
    scanStepCombo_->addItems({"10 kHz", "100 kHz", "1 MHz"});
    scanForm->addRow("起", scanStartSpin_);
    scanForm->addRow("止", scanStopSpin_);
    scanForm->addRow("步进", scanStepCombo_);
    scanLay->addLayout(scanForm);
    auto* scanBtns = new QHBoxLayout;
    scanStartBtn_ = new QPushButton("开始扫描", scanBox);
    scanStopBtn_ = new QPushButton("停止", scanBox);
    scanStopBtn_->setEnabled(false);
    scanBtns->addWidget(scanStartBtn_);
    scanBtns->addWidget(scanStopBtn_);
    scanLay->addLayout(scanBtns);
    scanResultList_ = new QListWidget(scanBox);
    scanResultList_->setToolTip("双击直跳");
    scanLay->addWidget(scanResultList_, 1);
    bmLay->addWidget(scanBox, 1);

    scanTimer_ = new QTimer(this);
    scanTimer_->setInterval(200);
    connect(scanTimer_, &QTimer::timeout, this, [this]() {
        if (scanFreq_ > scanStopSpin_->value() * 1e6) {
            scanTimer_->stop();
            scanStartBtn_->setEnabled(true);
            scanStopBtn_->setEnabled(false);
            return;
        }
        engine_->onSetCenterFreq(scanFreq_);
        // RSSI is updated async; record the latest known level.
        scanResultList_->addItem(QString("%1 MHz — %2 dBFS")
            .arg(scanFreq_ / 1e6, 0, 'f', 3).arg(lastRssi_, 0, 'f', 1));
        const double step = scanStepCombo_->currentIndex() == 0 ? 10e3
                          : scanStepCombo_->currentIndex() == 1 ? 100e3 : 1e6;
        scanFreq_ += step;
    });
    connect(scanStartBtn_, &QPushButton::clicked, this, [this]() {
        scanResultList_->clear();
        scanFreq_ = scanStartSpin_->value() * 1e6;
        scanStartBtn_->setEnabled(false);
        scanStopBtn_->setEnabled(true);
        scanTimer_->start();
    });
    connect(scanStopBtn_, &QPushButton::clicked, this, [this]() {
        scanTimer_->stop();
        scanStartBtn_->setEnabled(true);
        scanStopBtn_->setEnabled(false);
    });
    connect(scanResultList_, &QListWidget::itemDoubleClicked, this, [this](QListWidgetItem* it) {
        // Parse the "NN.NNN MHz —" prefix back to Hz and jump.
        bool ok = false;
        const double mhz = it->text().section(' ', 0, 0).toDouble(&ok);
        if (ok) engine_->onSetCenterFreq(mhz * 1e6);
    });

    rightTabs_->addTab(bmPage, "书签");

    connect(passTable_, &QTableWidget::cellClicked,
            this, [this](int row, int) { onPassRowClicked(row); });

    auto* aiPage = new QWidget;
    auto* aiLay = new QVBoxLayout(aiPage);
    aiStatus_ = new QLabel("AI 助手将在这里接入（需在设置中配置 API Key）", aiPage);
    aiStatus_->setWordWrap(true);
    aiLay->addWidget(aiStatus_);
    aiChat_ = new QPlainTextEdit(aiPage);
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
    sbGain_ = new QLabel("--", this);
    sbSdr_  = new QLabel("Test Signal", this);
    sbRec_  = new QLabel("", this);
    sbRec_->setStyleSheet(QString("color:%1; font-weight:600;").arg(tokens::kDanger));
    for (QLabel* l : {sbMode_, sbSr_, sbVfo_, sbGain_, sbSdr_, sbRec_}) {
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
    connect(engine_, &dsp::SpectrumEngine::audioLevel,
            this, &MainWindow::onAudioLevel);
    connect(engine_, &dsp::SpectrumEngine::rssiLevel,
            this, &MainWindow::onRssiLevel);
    connect(engine_, &dsp::SpectrumEngine::snrLevel,
            this, &MainWindow::onSnrLevel);
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
                engine_->setDemodMode(demodCombo_->currentText());
                sbMode_->setText(demodCombo_->currentText());
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
                sbSr_->setText(srCombo_->currentText());
            });
    connect(bwCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                static const double kBws[] = {8000.0, 12500.0, 200000.0, 2400.0, 500.0};
                if (idx >= 0 && idx <= 4) {
                    currentBwHz_ = kBws[idx];
                    engine_->setBandwidth(kBws[idx]);
                    if (spectrum_) spectrum_->setBandwidthHz(kBws[idx]);
                }
            });
    // Drag a VFO band edge on the spectrum -> update bandwidth (snap to preset).
    connect(spectrum_, &ui::SpectrumWidget::bandwidthChanged,
            this, [this](double hz) {
                static const double kBws[] = {8000.0, 12500.0, 200000.0, 2400.0, 500.0};
                int best = 0; double bd = 1e18;
                for (int i=0;i<5;++i){ double d=std::abs(kBws[i]-hz); if(d<bd){bd=d;best=i;} }
                bwCombo_->setCurrentIndex(best);   // -> engine.setBandwidth via above
            });
    connect(gatedCheck_, &QCheckBox::stateChanged, this, [this](int st) {
        engine_->setGatedRecordingEnabled(st != Qt::Unchecked);
    });
    connect(recordBtn_, &QPushButton::clicked, this, &MainWindow::onRecordClicked);

    // ---- Multi-VFO wiring -------------------------------------------------
    connect(engine_, &dsp::SpectrumEngine::vfoListChanged,
            this, [this]() { refreshVfoUi(); scheduleSave(); },
            Qt::QueuedConnection);
    connect(vfoAddBtn_, &QPushButton::clicked, this, [this]() { engine_->vfoAdd(); });
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
    connect(agent_, &ai::Agent::responseReady, this, [this](const QString& t) {
        aiChat_->appendPlainText("AI: " + t);
    });
    connect(agent_, &ai::Agent::toolCalled, this, [this](const QString& tool, const QString& result) {
        aiChat_->appendPlainText(QString("[调用工具: %1 — %2]").arg(tool, result));
    });
    connect(sendBtn, &QPushButton::clicked, this, [this]() {
        QString t = aiInput_->text().trimmed();
        if (t.isEmpty()) return;
        aiChat_->appendPlainText("You: " + t);
        agent_->sendMessage(t);
        aiInput_->clear();
    });
    connect(aiInput_, &QLineEdit::returnPressed, sendBtn, &QPushButton::click);

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
        static const double kBws[] = {8000.0, 12500.0, 200000.0, 2400.0, 500.0};
        int best = 0; double bestDiff = 1e18;
        for (int i = 0; i < 5; ++i) {
            double d = std::abs(kBws[i] - newBw);
            if (d < bestDiff) { bestDiff = d; best = i; }
        }
        bwCombo_->blockSignals(true);
        bwCombo_->setCurrentIndex(best);
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
    delete adsbMap_; adsbMap_ = nullptr;
}

void MainWindow::refreshVfoUi() {
    vfoMarkers_ = engine_->vfoMarkers();
    spectrum_->setVfoMarkers(vfoMarkers_);

    // Rebuild the VFO list (color dot + name + freq + mode).
    vfoList_->blockSignals(true);
    vfoList_->clear();
    int selRow = -1;
    for (int i = 0; i < vfoMarkers_.size(); ++i) {
        const auto& m = vfoMarkers_[i];
        auto* it = new QListWidgetItem(
            QString("%1   %2 MHz   %3")
                .arg(m.name, -8, QChar(' '))
                .arg(m.freqHz / 1e6, 0, 'f', 3)
                .arg(m.mode));
        QColor c = m.color.isValid() ? m.color : QColor(QString::fromUtf8(tokens::kAccent));
        it->setForeground(c);
        it->setData(Qt::UserRole, m.id);
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

        static const double kBws[] = {8000.0, 12500.0, 200000.0, 2400.0, 500.0};
        int bwIdx = 1; double bd = 1e18;
        for (int i = 0; i < 5; ++i) { double d = std::abs(kBws[i] - sel->bandwidthHz); if (d < bd) { bd = d; bwIdx = i; } }
        bwCombo_->blockSignals(true);
        bwCombo_->setCurrentIndex(bwIdx);
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
    s.setValue("anr/enabled", anrCheck_->isChecked());
    s.setValue("anr/strength", anrSlider_->value());

    // ---- Layout / tabs / FFT ----
    s.setValue("ui/rightTabIndex", rightTabs_->currentIndex());
    s.setValue("ui/centerTabIndex", centerTabs_->currentIndex());
    if (mainSplitter_) s.setValue("ui/splitterSizes", mainSplitter_->saveState());
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

void MainWindow::restoreUiState() {
    QSettings s("MBDSDR", "MBDSDR");
    restoreGeometry(s.value("geometry").toByteArray());

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
    currentBwHz_ = kBws[bwIdx];
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
}

void MainWindow::onSnrLevel(float snrDb) {
    lastSnr_ = snrDb;
    if (rssiLabel_)
        rssiLabel_->setText(QString("RSSI: %1 dBFS · SNR: %2 dB")
                            .arg(lastRssi_, 0, 'f', 1).arg(snrDb, 0, 'f', 1));
}

void MainWindow::onSourceTelemetry(const QString& name, bool connected,
                                    double centerHz, double sampleRateHz, double gainDb) {
    // Hardware readback values (not the UI requests). Offline test source:
    // connected=false, values are honest synthetic readbacks tagged 非硬件.
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

void MainWindow::onSquelchState(bool open) {
    squelchState_->setText(open ? "状态: OPEN" : "状态: CLOSED");
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

void MainWindow::onAdsbAircraft(const dsp::AircraftInfo& info) {
    if (adsbEmpty_) adsbEmpty_->hide();
    int row = adsbRow_.value(info.icao, -1);
    if (row < 0) {
        row = adsbTable_->rowCount();
        adsbTable_->insertRow(row);
        adsbRow_[info.icao] = row;
    }
    const auto set = [&](int col, const QString& s) {
        auto* it = adsbTable_->item(row, col);
        if (!it) { adsbTable_->setItem(row, col, new QTableWidgetItem(s)); }
        else it->setText(s);
    };
    set(0, info.icao);
    set(1, info.callsign.isEmpty() ? "--" : info.callsign);
    set(2, info.altitudeFt > 0 ? QString::number(info.altitudeFt) : "--");
    set(3, info.hasVelocity ? QString::number(info.groundspeedKt, 'f', 0) : "--");
    set(4, info.hasVelocity ? QString::number(info.headingDeg, 'f', 0) : "--");
    set(5, info.hasVerticalRate ? QString::number(info.verticalRateFpm) : "--");
    set(6, "--");   // distance needs station + aircraft position
    set(7, QDateTime::currentDateTime().toString("HH:mm:ss"));
    if (worldView_) {
        // Upsert the rich aircraft point by ICAO, keeping a short tail streak.
        // Only fields actually carried by this ADS-B message are overwritten;
        // absent fields keep their last value (never a fabricated default).
        ui::AircraftPoint& ap = (*adsbMap_)[info.icao];
        ap.icao = info.icao;
        if (!info.callsign.isEmpty()) ap.callsign = info.callsign;
        if (info.altitudeFt > 0) ap.altitudeFt = info.altitudeFt;
        if (info.hasVelocity) ap.headingDeg = info.headingDeg;
        if (info.hasPosition) {
            ap.lat = info.lat;
            ap.lon = info.lon;
            ap.track.append({info.lat, info.lon});
            while (ap.track.size() > 12) ap.track.removeFirst();
        }
        QList<ui::AircraftPoint> list;
        list.reserve(adsbMap_->size());
        for (auto it = adsbMap_->cbegin(); it != adsbMap_->cend(); ++it)
            list.append(it.value());
        worldView_->setAircraft(list);
    }
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
        tleClient_->computeFromEntries(cache.entries, stationLat_, stationLon_);
        tleClient_->fetch(stationLat_, stationLon_);   // background refresh
    } else {
        tleFetchActive_ = true;
        skyEmptyLabel_->setText("正在拉取 TLE...");
        skyEmptyLabel_->show();
        passTable_->setRowCount(0);
        tleClient_->fetch(stationLat_, stationLon_);
    }
}

void MainWindow::onPassesReady(QList<dsp::SatPass> passes) {
    passes_ = std::move(passes);
    tleFetchActive_ = false;
    liveRow_ = -1;
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
        tleClient_->computeFromEntries(cache.entries, stationLat_, stationLon_);
        statusBar()->showMessage("TLE 已过期（缓存时间 " +
            cache.fetchedAt.toLocalTime().toString("MM-dd HH:mm") + "）：" + reason);
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

        auto* elItem = new QTableWidgetItem(
            QString::number(p.maxEl, 'f', 1) + QStringLiteral("°"));
        elItem->setData(Qt::UserRole, p.maxEl);
        passTable_->setItem(row, 2, elItem);

        auto* losItem = new QTableWidgetItem(p.los.toLocalTime().toString("MM-dd HH:mm"));
        losItem->setData(Qt::UserRole, p.los.toMSecsSinceEpoch());
        passTable_->setItem(row, 3, losItem);

        auto* cdItem = new QTableWidgetItem(countdownText(p, now));
        cdItem->setData(Qt::UserRole, now.secsTo(p.aos));
        passTable_->setItem(row, 4, cdItem);

        if (active) {
            QBrush hi(QColor(tokens::kSuccess));
            for (int c = 0; c < 5; ++c)
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
        passTable_->item(row, 4)->setText(countdownText(passes_[i], now));
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
    if (!cache.valid) { tleBadge_->clear(); return; }
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
    // Persist the user's choice.
    const QString name = passes_[idx].name;
    QSettings("MBDSDR", "MBDSDR").setValue("ui/selectedSatellite", name);
    // Drive both views + elevation plot from the TLE name (slots don't re-emit).
    worldView_->setSelectedSatellite(name);
    skyView_->setSelectedSatellite(name);
    updateElevationPlotFor(passes_[idx]);
    updateLiveSatellite();   // paint immediately rather than waiting 1s
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
    skyView_->setCurrentTime(now);
    updateClockBiasLabel();

    if (!stationSet_) {
        skyView_->clearLiveSatellites();
        return;
    }

    // If the selected pass just ended, advance to the next one in view.
    if (liveRow_ >= 0 && liveRow_ < passes_.size() && now > passes_[liveRow_].los) {
        liveRow_ = -1;
        skyView_->setSelectedSatellite("");
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
        liveTimer_->stop();
    }

    // Draw every satellite currently in view; mark the selected one.
    QList<ui::LiveSat> sats;
    for (int i = 0; i < passes_.size(); ++i) {
        const dsp::SatPass& p = passes_[i];
        if (now < p.aos || now > p.los) continue;   // past LOS / pre-AOS: skip
        dsp::Topocentric t = tleClient_->propagateAt(now, p.tle,
                                                     stationLat_, stationLon_);
        ui::LiveSat ls;
        ls.az = t.az;
        ls.el = t.el;
        ls.name = p.name;
        ls.selected = (i == liveRow_);
        sats.append(ls);

        if (ls.selected) {
            statusBar()->showMessage(QString("%1 方位=%2° 仰角=%3° 距离=%4")
                .arg(p.name).arg(t.az, 0, 'f', 0).arg(t.el, 0, 'f', 1)
                .arg(formatRange(t.range)));
        }
    }
    skyView_->setLiveSatellites(sats);

    // Drop the same satellites onto the world map as lat/lon sub-points, each
    // with a forward ground-track polyline (now -> LOS, ~2 min steps).
    QList<ui::SatellitePoint> wpts;
    for (int i = 0; i < passes_.size(); ++i) {
        const dsp::SatPass& p = passes_[i];
        if (now < p.aos || now > p.los) continue;
        auto geo = tleClient_->propagateLatLon(now, p.tle);
        ui::SatellitePoint sp;
        sp.name = p.name;
        sp.lat = geo.latDeg;
        sp.lon = geo.lonDeg;
        sp.selected = (i == liveRow_);
        // Future ground track: sample sub-points every ~2 minutes until LOS.
        const qint64 spanSec = now.secsTo(p.los);
        if (spanSec > 0) {
            const int steps = std::min<qint64>(30, std::max<qint64>(2, spanSec / 120));
            for (int k = 0; k <= steps; ++k) {
                const QDateTime t = now.addMSecs(
                    qint64(double(now.msecsTo(p.los)) * k / steps));
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

void MainWindow::updateClockBiasLabel() {
    if (!clockInfoLabel_) return;
    const QDateTime sysUtc = QDateTime::currentDateTimeUtc();
    const QDateTime local = QDateTime::currentDateTime();
    if (lastGnssFix_.hasUtc && gnssHasFix_) {
        // bias = system UTC - GNSS UTC (seconds, sub-second via msecs).
        const double biasSec = lastGnssFix_.utc.msecsTo(sysUtc) / 1000.0;
        clockBiasSec_ = biasSec;
        clockInfoLabel_->setText(
            QString("GNSS UTC %1   系统 UTC %2   本地 %3   偏差 %4 s（系统−GNSS，未改钟）")
                .arg(lastGnssFix_.utc.toString("HH:mm:ss.zzz"))
                .arg(sysUtc.toString("HH:mm:ss.zzz"))
                .arg(local.toString("HH:mm:ss"))
                .arg(biasSec, 0, 'f', 3));
    } else {
        clockBiasSec_ = 0.0;
        clockInfoLabel_->setText(
            QString("GNSS UTC --   系统 UTC %1   本地 %2   偏差 --（GNSS 无定位）")
                .arg(sysUtc.toString("HH:mm:ss"))
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
        engine_->setAdsbReferencePosition(fix.latitude, fix.longitude);
        refetchTle();   // cache-first; re-propagate passes for the real station
    }

    worldView_->setGnssFix(true, fix.latitude, fix.longitude,
                           fix.satellitesInUse, fix.hdop);

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
        updateClockBiasLabel();
        // Restore the hand-entered station (if any) as the reference.
        ai::AiConfig cfg; cfg.load();
        stationLat_ = cfg.stationLat; stationLon_ = cfg.stationLon;
        stationSet_ = cfg.stationSet;
        worldView_->setStation(stationLat_, stationLon_);
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

} // namespace mbdsdr
