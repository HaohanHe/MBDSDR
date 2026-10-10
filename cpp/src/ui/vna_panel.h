// SPDX-License-Identifier: MIT
// NanoVNA instrument panel (right-rail "VNA" tab).
//
// Honest data policy: curves and status are repopulated ONLY from
// engine->vnaClient() (the real or replay-backed client). With no device and
// no test seed the panel sits on the honest empty state ("未连接 NanoVNA") and
// never draws a fabricated trace. UI text/format helpers are free functions in
// vna_panel_format.cpp so they are unit-testable without a widget.
//
// Visual tokens come from core/tokens.h; geometry goes through scaled().
#pragma once

#include <QWidget>

#include <complex>
#include <vector>

class QLabel;
class QLineEdit;
class QPushButton;
class QSpinBox;
class QTimer;

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }
namespace ui {

// Minimal reusable sweep-vs-frequency curve (same self-painted idiom as
// RssiTrendWidget). Plots an arbitrary y[] array; empty -> honest caption.
class VnaTraceWidget : public QWidget {
    Q_OBJECT
public:
    explicit VnaTraceWidget(const QString& caption, QWidget* parent = nullptr);
    // y values to plot (one per sweep point). Empty -> honest blank.
    void setData(const std::vector<double>& y);
    void clear();
    QSize sizeHint() const override;

protected:
    void paintEvent(QPaintEvent*) override;

private:
    QString caption_;
    std::vector<double> y_;
};

class VnaPanel : public QWidget {
    Q_OBJECT
public:
    explicit VnaPanel(dsp::SpectrumEngine* engine, QWidget* parent = nullptr);
    ~VnaPanel() override;

    // ---- Offscreen-screenshot-only seed (do not drive production) ---------
    // Feeds self-made sweep results straight into the display so the 仪器 tab
    // can be screenshotted without real hardware. Off the production path.
    void seedSnapshotForTest(const QString& model, const QString& version,
                             const QStringList& cal,
                             const std::vector<long>& freqs,
                             const std::vector<std::complex<double>>& s11,
                             const std::vector<std::complex<double>>& s21);

private slots:
    void onConnectClicked();
    void onMeasureClicked();
    void poll();

private:
    void applyReadout(const std::vector<long>& freqs,
                      const std::vector<std::complex<double>>& s11,
                      const std::vector<std::complex<double>>& s21);
    void refreshStatus();

    dsp::SpectrumEngine* engine_;

    QLineEdit*   portEdit_    = nullptr;
    QPushButton* connectBtn_  = nullptr;
    QPushButton* measureBtn_  = nullptr;
    QLabel*      statusLabel_ = nullptr;
    QLabel*      readoutLabel_ = nullptr;
    QLabel*      analysisLabel_ = nullptr;
    QLabel*      hintLabel_   = nullptr;
    QSpinBox*    startSpin_   = nullptr;
    QSpinBox*    stopSpin_    = nullptr;
    QSpinBox*    pointsSpin_  = nullptr;

    VnaTraceWidget* s11Plot_ = nullptr;  // VSWR vs frequency
    VnaTraceWidget* s21Plot_ = nullptr;  // gain dB vs frequency
    QTimer*         timer_   = nullptr;
};

} // namespace ui
} // namespace mbdsdr
