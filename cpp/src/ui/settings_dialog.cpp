// SPDX-License-Identifier: MIT
#include "settings_dialog.h"
#include "core/app_settings.h"
#include "ai/ai_config.h"
#include "core/tokens.h"
#include "dsp/audio_output.h"

#include <QFormLayout>
#include <QLineEdit>
#include <QDoubleSpinBox>
#include <QSpinBox>
#include <QComboBox>
#include <QLabel>
#include <QDialogButtonBox>
#include <QTabWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGridLayout>
#include <QSlider>
#include <QCheckBox>
#include <QGroupBox>
#include <QShowEvent>
#include <QPropertyAnimation>
#include <QGraphicsOpacityEffect>
#include <QSettings>
#include <limits>
#include <cmath>

namespace mbdsdr {
namespace ui {

SettingsDialog::SettingsDialog(QWidget* parent) : QDialog(parent) {
    setWindowTitle(tr("设置"));
    setMinimumWidth(tokens::scaled(tokens::kSettingsMinW));

    auto* tabs = new QTabWidget(this);

    // Station tab
    auto* stationPage = new QWidget;
    auto* stationForm = new QFormLayout(stationPage);
    latSpin_ = new QDoubleSpinBox;
    latSpin_->setRange(-90, 90);
    latSpin_->setDecimals(6);
    latSpin_->setSuffix(" °");
    lonSpin_ = new QDoubleSpinBox;
    lonSpin_->setRange(-180, 180);
    lonSpin_->setDecimals(6);
    lonSpin_->setSuffix(" °");
    altSpin_ = new QSpinBox;
    altSpin_->setRange(-500, 9000);
    altSpin_->setSuffix(" m");
    stationForm->addRow(tr("本站纬度"), latSpin_);
    stationForm->addRow(tr("本站经度"), lonSpin_);
    stationForm->addRow(tr("本站海拔"), altSpin_);
    tabs->addTab(stationPage, tr("本站"));

    // AI tab
    auto* aiPage = new QWidget;
    auto* aiForm = new QFormLayout(aiPage);
    apiKeyEdit_ = new QLineEdit;
    apiKeyEdit_->setEchoMode(QLineEdit::Password);
    baseUrlEdit_ = new QLineEdit;
    modelEdit_ = new QLineEdit;
    aiForm->addRow("API Key", apiKeyEdit_);
    aiForm->addRow("Base URL", baseUrlEdit_);
    aiForm->addRow("Model", modelEdit_);
    tabs->addTab(aiPage, "AI");

    // RX tab: preferred sample rate. ItemData carries the exact Hz value the
    // engine speaks; the label is display only. The live device combo in the
    // left rail is capability-driven; this is the *preference* that combo snaps
    // to (nearest supported) on connect, matching rx/sampleRate.
    auto* rxPage = new QWidget;
    auto* rxForm = new QFormLayout(rxPage);
    srCombo_ = new QComboBox;
    const struct { const char* label; double hz; } kRates[] = {
        {"1.024 MS/s", 1.024e6},
        {"2.048 MS/s", 2.048e6},
        {"2.4 MS/s",   2.4e6},
        {"3.2 MS/s",   3.2e6},
    };
    for (const auto& r : kRates)
        srCombo_->addItem(QString::fromUtf8(r.label), r.hz);
    auto* srHint = new QLabel(tr("连接设备后按设备能力取最近值；实际采样率见左栏「源与连接」"));
    srHint->setObjectName("dockHint");
    srHint->setWordWrap(true);
    rxForm->addRow(tr("首选采样率"), srCombo_);
    rxForm->addRow("", srHint);
    tabs->addTab(rxPage, tr("接收"));

    // Audio tab: pick the output device. Index 0 = system default.
    auto* audioPage = new QWidget;
    auto* audioForm = new QFormLayout(audioPage);
    audioDeviceCombo_ = new QComboBox;
    audioDeviceCombo_->addItem(tr("系统默认"));   // index 0 -> "default"
    const QStringList devs = dsp::AudioOutput::availableDevices();
    audioDeviceCombo_->addItems(devs);
    audioNoDevLabel_ = new QLabel(tr("无可用音频输出设备"));
    audioNoDevLabel_->setObjectName("dockHint");
    audioNoDevLabel_->setVisible(devs.isEmpty());
    // On headless boxes there is no hardware at all -- the combo still shows the
    // "system default" row but is disabled so the user cannot pick a phantom.
    audioDeviceCombo_->setEnabled(!devs.isEmpty());
    audioForm->addRow(tr("输出设备"), audioDeviceCombo_);
    audioForm->addRow("", audioNoDevLabel_);
    volumeSlider_ = new QSlider(Qt::Horizontal);
    volumeSlider_->setRange(0, 100);
    volumeSlider_->setValue(80);
    audioForm->addRow(tr("主音量"), volumeSlider_);
    auto* fixedSr = new QLabel(tr("音频输出采样率：48000 Hz（固定）"));
    fixedSr->setObjectName("dockHint");
    audioForm->addRow("", fixedSr);
    tabs->addTab(audioPage, tr("音频"));

    // Appearance tab: UI scale (live) + read-only theme.
    auto* appearancePage = new QWidget;
    auto* appearanceForm = new QFormLayout(appearancePage);
    scaleCombo_ = new QComboBox;
    scaleCombo_->addItems({"0.7x", "1.0x", "1.25x", "1.5x"});
    appearanceForm->addRow(tr("UI 缩放"), scaleCombo_);
    auto* themeLbl = new QLabel(tr("默认"));
    themeLbl->setObjectName("dockHint");
    appearanceForm->addRow(tr("主题"), themeLbl);
    // UI 语言（i18n）：语言自称不翻译；改动经 ui/language 落盘，
    // 加载器只在启动时装翻译器——诚实提示重启生效，不做假实时切换。
    languageCombo_ = new QComboBox;
    languageCombo_->addItem(QStringLiteral("简体中文"), QStringLiteral("zh_CN"));
    languageCombo_->addItem(QStringLiteral("English"), QStringLiteral("en"));
    languageCombo_->addItem(QStringLiteral("日本語"), QStringLiteral("ja"));
    appearanceForm->addRow(tr("语言"), languageCombo_);
    auto* langHint = new QLabel(tr("更改语言后重启应用生效"));
    langHint->setObjectName("dockHint");
    appearanceForm->addRow("", langHint);
    tabs->addTab(appearancePage, tr("外观"));

    // ---- 界面 tab (phase82): panel visibility + independent display params.
    // The checkbox list is built from the token arrays so the order is fixed
    // and a missing/renamed panel id can never produce a phantom row.
    {
        auto* uiPage = new QWidget;
        auto* uiLay = new QVBoxLayout(uiPage);
        uiLay->setSpacing(tokens::scaled(tokens::kSpacingM));

        // == A. 界面元素：右栏面板 ==
        auto* rightBox = new QGroupBox(tr("右栏面板"), uiPage);
        auto* rightLay = new QVBoxLayout(rightBox);
        rightLay->setSpacing(tokens::scaled(tokens::kSpacingS));
        showAllRightChk_ = new QCheckBox(tr("全选（显示全部右栏面板）"), rightBox);
        showAllRightChk_->setToolTip(tr("勾选=显示全部；取消勾选不会自动隐藏任何面板，只是清掉全选标记"));
        rightLay->addWidget(showAllRightChk_);
        auto* rightGrid = new QGridLayout;
        rightGrid->setHorizontalSpacing(tokens::scaled(tokens::kSpacingM));
        rightGrid->setVerticalSpacing(tokens::scaled(tokens::kSpacingS));
        for (int i = 0; i < tokens::kRightPanelCount; ++i) {
            const QString id = QString::fromUtf8(tokens::kRightPanelIds[i]);
            auto* chk = new QCheckBox(id, rightBox);
            chk->setChecked(true);   // default: everything visible
            chk->setToolTip(tr("在右栏显示「%1」面板").arg(id));
            rightPanelChecks_.insert(id, chk);
            rightGrid->addWidget(chk, i / 2, i % 2);
        }
        rightLay->addLayout(rightGrid);
        uiLay->addWidget(rightBox);

        // == B. 界面元素：左栏分组 ==
        auto* leftBox = new QGroupBox(tr("左栏分组"), uiPage);
        auto* leftLay = new QVBoxLayout(leftBox);
        leftLay->setSpacing(tokens::scaled(tokens::kSpacingS));
        showAllLeftChk_ = new QCheckBox(tr("全选（显示全部左栏分组）"), leftBox);
        showAllLeftChk_->setToolTip(tr("勾选=显示全部；取消勾选不会自动隐藏任何分组，只是清掉全选标记"));
        leftLay->addWidget(showAllLeftChk_);
        auto* leftGrid = new QGridLayout;
        leftGrid->setHorizontalSpacing(tokens::scaled(tokens::kSpacingM));
        leftGrid->setVerticalSpacing(tokens::scaled(tokens::kSpacingS));
        for (int i = 0; i < tokens::kLeftPanelCount; ++i) {
            const QString id = QString::fromUtf8(tokens::kLeftPanelIds[i]);
            auto* chk = new QCheckBox(id, leftBox);
            chk->setChecked(true);
            chk->setToolTip(tr("在左栏显示「%1」分组").arg(id));
            leftPanelChecks_.insert(id, chk);
            leftGrid->addWidget(chk, i / 2, i % 2);
        }
        leftLay->addLayout(leftGrid);
        uiLay->addWidget(leftBox);

        // == C. 显示参数：三条独立滑条 + 组合框 ==
        displayGroup_ = new QGroupBox(tr("显示参数"), uiPage);
        auto* dispForm = new QFormLayout(displayGroup_);
        dispForm->setSpacing(tokens::scaled(tokens::kSpacingS));

        // 接收增益 (dB) -- same domain as the left-rail gainSlider_.
        auto* gainRow = new QWidget(displayGroup_);
        auto* gainLay = new QHBoxLayout(gainRow);
        gainLay->setContentsMargins(0, 0, 0, 0);
        gainSlider_ = new QSlider(Qt::Horizontal, gainRow);
        gainSlider_->setRange(static_cast<int>(tokens::kGainMinDb),
                              static_cast<int>(tokens::kGainMaxDb));
        gainSlider_->setSingleStep(tokens::kGainSliderStep);
        gainValue_ = new QLabel(QStringLiteral("0 dB"), gainRow);
        gainValue_->setMinimumWidth(tokens::scaled(64));
        gainLay->addWidget(gainSlider_, 1);
        gainLay->addWidget(gainValue_);
        dispForm->addRow(tr("接收增益"), gainRow);

        // 显示下限 (dB) -- the floor of the spectrum dB axis.
        auto* dbMinRow = new QWidget(displayGroup_);
        auto* dbMinLay = new QHBoxLayout(dbMinRow);
        dbMinLay->setContentsMargins(0, 0, 0, 0);
        dbMinSlider_ = new QSlider(Qt::Horizontal, dbMinRow);
        dbMinSlider_->setRange(tokens::kDbSpinLowerMin, tokens::kDbSpinLowerMax);
        dbMinSlider_->setSingleStep(tokens::kDbSliderStep);
        dbMinValue_ = new QLabel(QStringLiteral("-45 dB"), dbMinRow);
        dbMinValue_->setMinimumWidth(tokens::scaled(64));
        dbMinLay->addWidget(dbMinSlider_, 1);
        dbMinLay->addWidget(dbMinValue_);
        dispForm->addRow(tr("显示下限"), dbMinRow);

        // 显示上限 (dB) -- the ceiling of the spectrum dB axis.
        auto* dbMaxRow = new QWidget(displayGroup_);
        auto* dbMaxLay = new QHBoxLayout(dbMaxRow);
        dbMaxLay->setContentsMargins(0, 0, 0, 0);
        dbMaxSlider_ = new QSlider(Qt::Horizontal, dbMaxRow);
        dbMaxSlider_->setRange(tokens::kDbSpinUpperMin, tokens::kDbSpinUpperMax);
        dbMaxSlider_->setSingleStep(tokens::kDbSliderStep);
        dbMaxValue_ = new QLabel(QStringLiteral("0 dB"), dbMaxRow);
        dbMaxValue_->setMinimumWidth(tokens::scaled(64));
        dbMaxLay->addWidget(dbMaxSlider_, 1);
        dbMaxLay->addWidget(dbMaxValue_);
        dispForm->addRow(tr("显示上限"), dbMaxRow);

        // 显示缩放 -- same domain as the canvas zoomFactor.
        auto* zoomRow = new QWidget(displayGroup_);
        auto* zoomLay = new QHBoxLayout(zoomRow);
        zoomLay->setContentsMargins(0, 0, 0, 0);
        zoomSlider_ = new QSlider(Qt::Horizontal, zoomRow);
        // QSlider is integer; map kZoomMin..kZoomMax to a 0..N tick range with
        // kZoomSliderStep=0.1 so 1.0..16.0 becomes 0..150 ticks.
        zoomSlider_->setRange(0, static_cast<int>((tokens::kZoomMax - tokens::kZoomMin) / tokens::kZoomSliderStep));
        zoomSlider_->setSingleStep(1);
        zoomValue_ = new QLabel(QStringLiteral("1.0x"), zoomRow);
        zoomValue_->setMinimumWidth(tokens::scaled(64));
        zoomLay->addWidget(zoomSlider_, 1);
        zoomLay->addWidget(zoomValue_);
        dispForm->addRow(tr("显示缩放"), zoomRow);

        // FFT 点数 -- mirrors the spectrum toolbar fftCombo_ (1024/2048/4096).
        fftCombo_ = new QComboBox(displayGroup_);
        fftCombo_->addItems({"1024", "2048", "4096"});
        fftCombo_->setCurrentIndex(1);   // 2048 default
        dispForm->addRow(tr("FFT 点数"), fftCombo_);

        // 余晖 -- mirrors the spectrum toolbar persistCombo_ (关/低/高).
        persistCombo_ = new QComboBox(displayGroup_);
        persistCombo_->addItems({tr("关"), tr("低"), tr("高")});
        persistCombo_->setCurrentIndex(0);
        dispForm->addRow(tr("余晖"), persistCombo_);

        // 瀑布深度 -- mirrors the spectrum toolbar depthCombo_ (rows).
        wfDepthCombo_ = new QComboBox(displayGroup_);
        for (int d : tokens::kWaterfallDepthChoices)
            wfDepthCombo_->addItem(QStringLiteral("%1 行").arg(d), d);
        wfDepthCombo_->setCurrentIndex(1);   // 256 default
        dispForm->addRow(tr("瀑布深度"), wfDepthCombo_);

        // 画布右缘滑条（缩放/上限/下限）-- 阶段 B1。只管"这条竖排控件在不在"，
        // 三个值本身仍是 displayGroup_ 里那三条滑条 / 工具栏 spinbox 的同一份。
        edgeRailChk_ = new QCheckBox(tr("画布右缘滑条（缩放 / 上限 / 下限）"),
                                     displayGroup_);
        edgeRailChk_->setObjectName("edgeRailChk");
        edgeRailChk_->setChecked(tokens::kEdgeRailDefaultVisible);
        edgeRailChk_->setToolTip(
            tr("频谱画布右缘竖排三滑条：缩放（广度）、显示上限、显示下限（高度）"));
        dispForm->addRow(tr("画布元素"), edgeRailChk_);

        uiLay->addWidget(displayGroup_);
        uiLay->addStretch(1);

        // ---- live read-outs on the three sliders (pure UI, no side effect) --
        auto fmtDb = [](int v) { return QStringLiteral("%1 dB").arg(v); };
        connect(gainSlider_, &QSlider::valueChanged,
                this, [this, fmtDb](int v) { gainValue_->setText(fmtDb(v)); });
        connect(dbMinSlider_, &QSlider::valueChanged,
                this, [this, fmtDb](int v) { dbMinValue_->setText(fmtDb(v)); });
        connect(dbMaxSlider_, &QSlider::valueChanged,
                this, [this, fmtDb](int v) { dbMaxValue_->setText(fmtDb(v)); });
        connect(zoomSlider_, &QSlider::valueChanged, this, [this](int tick) {
            const double z = tokens::kZoomMin + tick * tokens::kZoomSliderStep;
            zoomValue_->setText(QStringLiteral("%1x").arg(z, 0, 'f', 1));
        });
        // Guard: never let the two dB sliders cross. If the user drags 下限
        // above 上限 (or vice versa) the OTHER slider is nudged just enough to
        // keep a 10 dB minimum window -- the same rule the spectrum spinboxes
        // already enforce (SpectrumWidget::applyDb lambda).
        auto keepDbOrder = [this, fmtDb](bool minMoved) {
            int lo = dbMinSlider_->value();
            int hi = dbMaxSlider_->value();
            if (lo < hi) return;
            const int want = 10;
            if (minMoved) {
                hi = std::min(lo + want, tokens::kDbSpinUpperMax);
                if (hi <= lo) lo = hi - want;   // lo already at its ceiling
                dbMaxSlider_->blockSignals(true);
                dbMaxSlider_->setValue(hi);
                dbMaxSlider_->blockSignals(false);
                dbMaxValue_->setText(fmtDb(hi));
            } else {
                lo = std::max(hi - want, tokens::kDbSpinLowerMin);
                if (lo >= hi) hi = lo + want;
                dbMinSlider_->blockSignals(true);
                dbMinSlider_->setValue(lo);
                dbMinSlider_->blockSignals(false);
                dbMinValue_->setText(fmtDb(lo));
            }
        };
        connect(dbMinSlider_, &QSlider::valueChanged,
                this, [keepDbOrder](int) { keepDbOrder(true); });
        connect(dbMaxSlider_, &QSlider::valueChanged,
                this, [keepDbOrder](int) { keepDbOrder(false); });

        // "全选" convenience toggles. They ONLY tick/untick the boxes -- they
        // never push state to the live widgets (that happens on OK).
        connect(showAllRightChk_, &QCheckBox::toggled, this,
                [this](bool on) {
                    for (auto* c : rightPanelChecks_) {
                        c->blockSignals(true);
                        c->setChecked(on);
                        c->blockSignals(false);
                    }
                });
        connect(showAllLeftChk_, &QCheckBox::toggled, this,
                [this](bool on) {
                    for (auto* c : leftPanelChecks_) {
                        c->blockSignals(true);
                        c->setChecked(on);
                        c->blockSignals(false);
                    }
                });

        tabs->addTab(uiPage, tr("界面"));
    }

    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
    connect(buttons, &QDialogButtonBox::accepted, this, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);

    auto* lay = new QVBoxLayout(this);
    lay->addWidget(tabs);
    lay->addWidget(buttons);
}

void SettingsDialog::loadFromConfig(const ai::AiConfig& cfg) {
    if (!std::isnan(cfg.stationLat)) latSpin_->setValue(cfg.stationLat);
    if (!std::isnan(cfg.stationLon)) lonSpin_->setValue(cfg.stationLon);
    altSpin_->setValue(static_cast<int>(cfg.stationAlt));
    apiKeyEdit_->setText(cfg.apiKey);
    baseUrlEdit_->setText(cfg.baseUrl);
    modelEdit_->setText(cfg.model);
    // Audio device: "default" -> index 0, otherwise match the stored description.
    if (cfg.audioDevice.isEmpty() || cfg.audioDevice == QStringLiteral("default")) {
        audioDeviceCombo_->setCurrentIndex(0);
    } else {
        int idx = audioDeviceCombo_->findText(cfg.audioDevice);
        audioDeviceCombo_->setCurrentIndex(idx < 0 ? 0 : idx);
    }
    mbdsdr::core::AppSettings s;
    volumeSlider_->setValue(s.value("rx/volume", 80).toInt());
    // Preferred sample rate: snap the combo to the nearest stored preference
    // (Hz). A missing/garbage key falls back to the 2.4 MS/s project default.
    {
        const double hz = s.value("rx/sampleRate", 2.4e6).toDouble();
        int best = 1;   // 2.4 MS/s
        double bestDiff = -1.0;
        for (int i = 0; i < srCombo_->count(); ++i) {
            const double d = std::abs(srCombo_->itemData(i).toDouble() - hz);
            if (bestDiff < 0.0 || d < bestDiff) { bestDiff = d; best = i; }
        }
        srCombo_->setCurrentIndex(best);
    }
    const double sc = s.value("ui/scaleFactor", 1.0).toDouble();
    const int sidx = sc <= 0.7 ? 0 : sc <= 1.0 ? 1 : sc <= 1.25 ? 2 : 3;
    scaleCombo_->setCurrentIndex(sidx);
    const QString lang = s.value("ui/language", QStringLiteral("zh_CN")).toString();
    const int li = languageCombo_->findData(lang);
    languageCombo_->setCurrentIndex(li < 0 ? 0 : li);

    // ---- phase82: 界面 tab ----
    // Right-rail panels: the key holds the ids the user HID. A missing key
    // means "everything visible" (honest default). Unknown ids in the stored
    // list are ignored (a renamed panel must not leave a phantom box ticked).
    {
        const QStringList hidden = s.value(tokens::kSettingsKeyHiddenRightPanels)
                                      .toStringList();
        for (auto it = rightPanelChecks_.begin(); it != rightPanelChecks_.end(); ++it) {
            const bool visible = !hidden.contains(it.key());
            it.value()->blockSignals(true);
            it.value()->setChecked(visible);
            it.value()->blockSignals(false);
        }
        // Reflect the aggregate into the 全选 box without firing its toggle.
        bool allOn = true;
        for (auto* c : rightPanelChecks_) if (!c->isChecked()) { allOn = false; break; }
        showAllRightChk_->blockSignals(true);
        showAllRightChk_->setChecked(allOn);
        showAllRightChk_->blockSignals(false);
    }
    {
        const QStringList hidden = s.value(tokens::kSettingsKeyHiddenLeftPanels)
                                      .toStringList();
        for (auto it = leftPanelChecks_.begin(); it != leftPanelChecks_.end(); ++it) {
            const bool visible = !hidden.contains(it.key());
            it.value()->blockSignals(true);
            it.value()->setChecked(visible);
            it.value()->blockSignals(false);
        }
        bool allOn = true;
        for (auto* c : leftPanelChecks_) if (!c->isChecked()) { allOn = false; break; }
        showAllLeftChk_->blockSignals(true);
        showAllLeftChk_->setChecked(allOn);
        showAllLeftChk_->blockSignals(false);
    }

    // Display parameters: same keys the spectrum toolbar / saveUiState use.
    gainSlider_->setValue(static_cast<int>(std::round(
        s.value("rx/gain", 0.0).toDouble())));
    dbMinSlider_->setValue(static_cast<int>(std::round(
        s.value(tokens::kSettingsKeyDbMin,
                static_cast<float>(tokens::kDbLowerDefault)).toFloat())));
    dbMaxSlider_->setValue(static_cast<int>(std::round(
        s.value(tokens::kSettingsKeyDbMax,
                static_cast<float>(tokens::kDbUpperDefault)).toFloat())));
    {
        const double z = s.value(tokens::kSettingsKeyZoomFactor, 1.0).toDouble();
        const int tick = static_cast<int>(std::round(
            (z - tokens::kZoomMin) / tokens::kZoomSliderStep));
        zoomSlider_->setValue(std::clamp(tick, 0, zoomSlider_->maximum()));
        zoomValue_->setText(QStringLiteral("%1x")
                                .arg(tokens::kZoomMin +
                                     zoomSlider_->value() * tokens::kZoomSliderStep,
                                     0, 'f', 1));
    }
    {
        const int fft = s.value(tokens::kSettingsKeyFftSize, 2048).toInt();
        int idx = 1;
        if (fft == 1024) idx = 0;
        else if (fft == 4096) idx = 2;
        fftCombo_->setCurrentIndex(idx);
    }
    persistCombo_->setCurrentIndex(
        std::clamp(s.value(tokens::kSettingsKeyPersistMode, 0).toInt(), 0, 2));
    {
        const int depth = s.value(tokens::kSettingsKeyWfDepth,
                                  tokens::kWaterfallDepthDefault).toInt();
        int idx = 1;   // 256 default
        for (int i = 0; i < int(sizeof(tokens::kWaterfallDepthChoices) /
                                 sizeof(tokens::kWaterfallDepthChoices[0])); ++i) {
            if (tokens::kWaterfallDepthChoices[i] == depth) { idx = i; break; }
        }
        wfDepthCombo_->setCurrentIndex(idx);
    }
    // 阶段 B1: canvas right-edge rail visibility (same key the widget reads on
    // construction, so the dialog and the live widget are one state).
    if (edgeRailChk_) {
        edgeRailChk_->setChecked(
            s.value(tokens::kSettingsKeyEdgeRailVisible,
                    tokens::kEdgeRailDefaultVisible).toBool());
    }
}

void SettingsDialog::saveToConfig(ai::AiConfig& cfg) {
    cfg.stationLat = latSpin_->value();
    cfg.stationLon = lonSpin_->value();
    cfg.stationAlt = altSpin_->value();
    // Zero/empty coordinates mean "no station" -> honest empty sky state.
    cfg.stationSet = !(qFuzzyIsNull(cfg.stationLat) && qFuzzyIsNull(cfg.stationLon));
    cfg.apiKey = apiKeyEdit_->text();
    cfg.baseUrl = baseUrlEdit_->text();
    cfg.model = modelEdit_->text();
    const int idx = audioDeviceCombo_->currentIndex();
    cfg.audioDevice = (idx <= 0) ? QStringLiteral("default")
                                 : audioDeviceCombo_->currentText();
    mbdsdr::core::AppSettings s;
    s.setValue("rx/volume", volumeSlider_->value());
    s.setValue("rx/sampleRate", sampleRateHz());
    s.setValue("ui/scaleFactor", userScale());
    s.setValue("ui/language", languageCombo_->currentData().toString());

    // ---- phase82: 界面 tab state ----
    // Persist the panel-visibility selections as "hidden id" lists so the
    // absent-key default stays "everything visible". Unknown ids cannot leak
    // in (the maps are built from the token arrays in the ctor).
    {
        QStringList hidden;
        for (auto it = rightPanelChecks_.begin(); it != rightPanelChecks_.end(); ++it)
            if (!it.value()->isChecked()) hidden << it.key();
        s.setValue(tokens::kSettingsKeyHiddenRightPanels, hidden);
    }
    {
        QStringList hidden;
        for (auto it = leftPanelChecks_.begin(); it != leftPanelChecks_.end(); ++it)
            if (!it.value()->isChecked()) hidden << it.key();
        s.setValue(tokens::kSettingsKeyHiddenLeftPanels, hidden);
    }
    // Display parameters: same keys the spectrum toolbar / saveUiState use.
    s.setValue("rx/gain", static_cast<double>(gainSlider_->value()));
    s.setValue(tokens::kSettingsKeyDbMin, static_cast<float>(dbMinSlider_->value()));
    s.setValue(tokens::kSettingsKeyDbMax, static_cast<float>(dbMaxSlider_->value()));
    s.setValue(tokens::kSettingsKeyZoomFactor,
               tokens::kZoomMin + zoomSlider_->value() * tokens::kZoomSliderStep);
    s.setValue(tokens::kSettingsKeyFftSize, fftCombo_->currentText().toInt());
    s.setValue(tokens::kSettingsKeyPersistMode, persistCombo_->currentIndex());
    s.setValue(tokens::kSettingsKeyWfDepth, wfDepthCombo_->currentData().toInt());
    // 阶段 B1: the rail switch -- written here AND by SpectrumWidget so hiding
    // it from either place lands on the same key.
    if (edgeRailChk_)
        s.setValue(tokens::kSettingsKeyEdgeRailVisible, edgeRailChk_->isChecked());
}

QStringList SettingsDialog::hiddenRightPanels() const {
    QStringList hidden;
    for (auto it = rightPanelChecks_.begin(); it != rightPanelChecks_.end(); ++it)
        if (!it.value()->isChecked()) hidden << it.key();
    return hidden;
}

QStringList SettingsDialog::hiddenLeftPanels() const {
    QStringList hidden;
    for (auto it = leftPanelChecks_.begin(); it != leftPanelChecks_.end(); ++it)
        if (!it.value()->isChecked()) hidden << it.key();
    return hidden;
}

int SettingsDialog::displayGainDb() const {
    return gainSlider_ ? gainSlider_->value() : 0;
}

int SettingsDialog::displayDbMin() const {
    return dbMinSlider_ ? dbMinSlider_->value() : tokens::kDbLowerDefault;
}

int SettingsDialog::displayDbMax() const {
    return dbMaxSlider_ ? dbMaxSlider_->value() : tokens::kDbUpperDefault;
}

double SettingsDialog::displayZoom() const {
    if (!zoomSlider_) return 1.0;
    return tokens::kZoomMin + zoomSlider_->value() * tokens::kZoomSliderStep;
}

int SettingsDialog::displayFftSize() const {
    return fftCombo_ ? fftCombo_->currentText().toInt() : 2048;
}

int SettingsDialog::displayPersistMode() const {
    return persistCombo_ ? persistCombo_->currentIndex() : 0;
}

int SettingsDialog::displayWfDepth() const {
    return wfDepthCombo_ ? wfDepthCombo_->currentData().toInt()
                         : tokens::kWaterfallDepthDefault;
}

bool SettingsDialog::edgeRailVisible() const {
    return edgeRailChk_ ? edgeRailChk_->isChecked()
                        : tokens::kEdgeRailDefaultVisible;
}

int SettingsDialog::volume() const { return volumeSlider_->value(); }

double SettingsDialog::sampleRateHz() const {
    if (!srCombo_) return 2.4e6;
    const double hz = srCombo_->currentData().toDouble();
    return hz > 0.0 ? hz : 2.4e6;
}

double SettingsDialog::userScale() const {
    switch (scaleCombo_->currentIndex()) {
        case 0: return 0.7; case 2: return 1.25; case 3: return 1.5;
        default: return 1.0;
    }
}

void SettingsDialog::showEvent(QShowEvent* e) {
    QDialog::showEvent(e);
    setWindowOpacity(0.0);
    auto* a = new QPropertyAnimation(this, "windowOpacity", this);
    a->setDuration(tokens::kAnimMedium1);
    a->setEasingCurve(QEasingCurve::OutCubic);
    a->setStartValue(0.0); a->setEndValue(1.0);
    a->start(QAbstractAnimation::DeleteWhenStopped);
}

} // namespace ui
} // namespace mbdsdr
