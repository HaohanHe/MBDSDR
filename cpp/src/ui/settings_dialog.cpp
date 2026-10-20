// SPDX-License-Identifier: MIT
#include "settings_dialog.h"
#include "core/app_settings.h"
#include "ai/ai_config.h"
#include "core/tokens.h"
#include "dsp/audio_output.h"

#include <QFormLayout>
#include <QLineEdit>
#include <QDoubleSpinBox>
#include <QComboBox>
#include <QLabel>
#include <QDialogButtonBox>
#include <QTabWidget>
#include <QVBoxLayout>
#include <QSlider>
#include <QShowEvent>
#include <QPropertyAnimation>
#include <QGraphicsOpacityEffect>
#include <QSettings>
#include <limits>

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

    // RX tab
    auto* rxPage = new QWidget;
    auto* rxForm = new QFormLayout(rxPage);
    srCombo_ = new QComboBox;
    srCombo_->addItems({"1.024 MS/s", "2.048 MS/s", "2.4 MS/s", "3.2 MS/s"});
    rxForm->addRow(tr("采样率"), srCombo_);
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
    const double sc = s.value("ui/scaleFactor", 1.0).toDouble();
    const int sidx = sc <= 0.7 ? 0 : sc <= 1.0 ? 1 : sc <= 1.25 ? 2 : 3;
    scaleCombo_->setCurrentIndex(sidx);
    const QString lang = s.value("ui/language", QStringLiteral("zh_CN")).toString();
    const int li = languageCombo_->findData(lang);
    languageCombo_->setCurrentIndex(li < 0 ? 0 : li);
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
    s.setValue("ui/scaleFactor", userScale());
    s.setValue("ui/language", languageCombo_->currentData().toString());
}

int SettingsDialog::volume() const { return volumeSlider_->value(); }

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
