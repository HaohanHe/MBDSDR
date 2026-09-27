// SPDX-License-Identifier: MIT
#include "settings_dialog.h"
#include "ai/ai_config.h"
#include "core/tokens.h"

#include <QFormLayout>
#include <QLineEdit>
#include <QDoubleSpinBox>
#include <QComboBox>
#include <QDialogButtonBox>
#include <QTabWidget>
#include <QVBoxLayout>
#include <limits>

namespace mbdsdr {
namespace ui {

SettingsDialog::SettingsDialog(QWidget* parent) : QDialog(parent) {
    setWindowTitle("设置");
    setMinimumWidth(420);

    auto* tabs = new QTabWidget(this);

    // Station tab
    auto* stationPage = new QWidget;
    auto* stationForm = new QFormLayout(stationPage);
    latSpin_ = new QDoubleSpinBox;
    latSpin_->setRange(-90, 90);
    latSpin_->setDecimals(4);
    lonSpin_ = new QDoubleSpinBox;
    lonSpin_->setRange(-180, 180);
    lonSpin_->setDecimals(4);
    stationForm->addRow("本站纬度", latSpin_);
    stationForm->addRow("本站经度", lonSpin_);
    tabs->addTab(stationPage, "本站");

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
    rxForm->addRow("采样率", srCombo_);
    tabs->addTab(rxPage, "接收");

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
    apiKeyEdit_->setText(cfg.apiKey);
    baseUrlEdit_->setText(cfg.baseUrl);
    modelEdit_->setText(cfg.model);
}

void SettingsDialog::saveToConfig(ai::AiConfig& cfg) {
    cfg.stationLat = latSpin_->value();
    cfg.stationLon = lonSpin_->value();
    cfg.stationSet = true;
    cfg.apiKey = apiKeyEdit_->text();
    cfg.baseUrl = baseUrlEdit_->text();
    cfg.model = modelEdit_->text();
}

} // namespace ui
} // namespace mbdsdr
