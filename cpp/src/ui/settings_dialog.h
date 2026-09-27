// SPDX-License-Identifier: MIT
#pragma once

#include <QDialog>

class QDoubleSpinBox;
class QLineEdit;
class QComboBox;

namespace mbdsdr {
namespace ai { struct AiConfig; }

namespace ui {

class SettingsDialog : public QDialog {
    Q_OBJECT
public:
    explicit SettingsDialog(QWidget* parent = nullptr);
    void loadFromConfig(const ai::AiConfig& cfg);
    void saveToConfig(ai::AiConfig& cfg);

private:
    QDoubleSpinBox* latSpin_ = nullptr;
    QDoubleSpinBox* lonSpin_ = nullptr;
    QLineEdit* apiKeyEdit_   = nullptr;
    QLineEdit* baseUrlEdit_  = nullptr;
    QLineEdit* modelEdit_    = nullptr;
    QComboBox* srCombo_      = nullptr;
};

} // namespace ui
} // namespace mbdsdr
