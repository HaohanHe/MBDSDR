// SPDX-License-Identifier: MIT
#pragma once

#include <QDialog>
#include <QMap>
#include <QStringList>

class QDoubleSpinBox;
class QSpinBox;
class QLineEdit;
class QComboBox;
class QLabel;
class QSlider;
class QCheckBox;
class QGroupBox;

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

    // ---- phase82: 界面元素 / 显示参数 ----
    // Panel visibility: which right-rail tabs / left-rail groups the user has
    // HIDDEN. Callers apply these to the live widgets after exec()==Accepted.
    QStringList hiddenRightPanels() const;
    QStringList hiddenLeftPanels() const;
    // Independent display parameters (three sliders + combos). Ranges match
    // the spectrum toolbar spinboxes / gain slider exactly, so applying them
    // can never push the value outside the legal domain.
    int    displayGainDb() const;   // 接收增益 (dB, kGainMinDb..kGainMaxDb)
    int    displayDbMin() const;    // 显示下限 (dB, kDbSpinLowerMin..kDbSpinLowerMax)
    int    displayDbMax() const;    // 显示上限 (dB, kDbSpinUpperMin..kDbSpinUpperMax)
    double displayZoom() const;     // 显示缩放 (kZoomMin..kZoomMax)
    int    displayFftSize() const;  // FFT 点数 (1024/2048/4096)
    int    displayPersistMode() const;  // 余晖 0=关 / 1=低 / 2=高
    int    displayWfDepth() const;      // 瀑布深度 (rows, kWaterfallDepthChoices)

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
    QComboBox* languageCombo_ = nullptr;
    QComboBox* audioDeviceCombo_ = nullptr;
    QLabel*    audioNoDevLabel_ = nullptr;
    QSlider*   volumeSlider_ = nullptr;
    QComboBox* scaleCombo_   = nullptr;

    // ---- phase82: 界面 tab ----
    // One checkbox per right-rail panel (keyed by the panel id string) and one
    // per left-rail group. Checked = visible. The maps are populated in the
    // ctor from tokens::kRightPanelIds / kLeftPanelIds so the order is fixed.
    QMap<QString, QCheckBox*> rightPanelChecks_;
    QMap<QString, QCheckBox*> leftPanelChecks_;
    QCheckBox* showAllRightChk_ = nullptr;   // convenience: tick every right box
    QCheckBox* showAllLeftChk_  = nullptr;   // convenience: tick every left box

    // Display-parameter controls (three independent sliders + combos).
    QSlider* gainSlider_  = nullptr;
    QLabel*  gainValue_   = nullptr;
    QSlider* dbMinSlider_ = nullptr;
    QLabel*  dbMinValue_  = nullptr;
    QSlider* dbMaxSlider_ = nullptr;
    QLabel*  dbMaxValue_  = nullptr;
    QSlider* zoomSlider_  = nullptr;
    QLabel*  zoomValue_   = nullptr;
    QComboBox* fftCombo_  = nullptr;
    QComboBox* persistCombo_ = nullptr;
    QComboBox* wfDepthCombo_ = nullptr;
    QGroupBox* displayGroup_ = nullptr;

    void buildUiTab();   // ctor helper: fills the 界面 tab
};

} // namespace ui
} // namespace mbdsdr
