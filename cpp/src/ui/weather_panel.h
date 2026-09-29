// SPDX-License-Identifier: MIT
//
// NOAA APT weather-satellite image panel.
//
// Honest data policy: the widget ONLY paints the grayscale image handed to it
// by the engine's AptDecoder over aptImageReady(). It never synthesizes a fake
// "live-looking" cloud photo. When rows==0 / no image has arrived it draws no
// picture at all and shows a centered empty-state hint. The status row turns
// from "搜索中" (kTextSecondary) to "已锁定" (kSuccess) only when the decoder
// genuinely acquired line sync.
//
// Visual tokens: all colors come from core/tokens.h; all geometry is scaled()
// at runtime. No raw hex, no hard-coded pixels in business code.
#pragma once

#include <QWidget>
#include <QImage>
#include <QString>

class QLabel;
class QPushButton;

namespace mbdsdr {
namespace ui {

class WeatherSatPanel : public QWidget {
    Q_OBJECT
public:
    explicit WeatherSatPanel(QWidget* parent = nullptr);

public slots:
    // Push a new frame from the engine (cross-thread queued connection).
    // `image` grows one 1818-px row at a time; a null/empty image with
    // rows==0 returns the panel to its honest empty state.
    void setImage(const QImage& image, bool locked, int rows, double syncCorr);

    // Clear the displayed image and return to the empty state. Also emits
    // clearRequested() so the engine resets its AptDecoder.
    void clear();

signals:
    // User pressed "清除图像": the engine should reset its decoder.
    void clearRequested();
    // One-tune preset: retune the receiver to `freqHz` (Hz) and switch to WFM.
    void tuneRequested(double freqHz);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    void buildHeader();
    void updateStatusRow();

    QImage       image_;          // last frame from engine (may be null)
    bool         locked_ = false;
    int          rows_   = 0;
    double       syncCorr_ = 0.0;

    QLabel*      statusLabel_ = nullptr;
    QPushButton* clearBtn_    = nullptr;

public:
    // Read-outs for QtTest.
    bool   isEmpty() const { return image_.isNull() || rows_ <= 0; }
    int    rows() const      { return rows_; }
    bool   locked() const    { return locked_; }
    double syncCorr() const  { return syncCorr_; }
    QImage image() const     { return image_; }
};

} // namespace ui
} // namespace mbdsdr
