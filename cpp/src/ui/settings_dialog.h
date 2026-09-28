// SPDX-License-Identifier: MIT
#pragma once

#include <QDialog>

class QDoubleSpinBox;
class QSpinBox;
class QLineEdit;
class QComboBox;
class QLabel;
class QSlider;

namespace mbdsdr {
namespace ai { struct AiConfig; }

namespace ui {

class SettingsDialog : public QDialog {
    Q_OBJECT
public:
    explicit SettingsDialog(QWidget* parent = nullptr);
    void loadFromConfig(const ai::AiConfig& cfg);
    void saveToConfig(ai::AiConfig& cfg);
    int volume() const;
    double userScale() const;

protected:
    void showEvent(QShowEvent* e) override;

private:
    QDoubleSpinBox* latSpin_ = nullptr;
    QDoubleSpinBox* lonSpin_ = nullptr;
    QSpinBox*       altSpin_  = nullptr;
    QLineEdit* apiKeyEdit_   = nullptr;
    QLineEdit* baseUrlEdit_  = nullptr;
    QLineEdit* modelEdit_    = nullptr;
    QComboBox* srCombo_      = nullptr;
    QComboBox* audioDeviceCombo_ = nullptr;
    QLabel*    audioNoDevLabel_ = nullptr;
    QSlider*   volumeSlider_ = nullptr;
    QComboBox* scaleCombo_   = nullptr;
};

} // namespace ui
} // namespace mbdsdr
