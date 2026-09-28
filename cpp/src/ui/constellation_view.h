// SPDX-License-Identifier: MIT
//
// Real-time digital constellation panel (QPainter).
//
// Honest data policy: the widget ONLY paints points that were actually fed in
// via feedSymbols(). It never synthesizes a fake "live-looking" scatter. When
// no digital mode is selected or no symbols have arrived, it shows a centered
// empty-state hint and paints nothing else. When fed offline-synthesized
// symbols (isHardware == false) it shows a small "非硬件 NOT HARDWARE" tag so
// the operator can never mistake a test fixture for a live hardware capture.
//
// Visual tokens: all colors come from core/tokens.h; all geometry is scaled()
// at runtime. No raw hex, no hard-coded pixels in business code.
#pragma once

#include <QWidget>
#include <QPointF>
#include <QString>
#include <complex>
#include <deque>
#include <vector>

#include "dsp/digital_demod.h"

namespace mbdsdr {
namespace ui {

class ConstellationView : public QWidget {
    Q_OBJECT
public:
    explicit ConstellationView(QWidget* parent = nullptr);

    // Mode drives the ideal reference points and the EVM ideal radius.
    void setMode(dsp::DigMode m);
    dsp::DigMode mode() const { return mode_; }

public slots:
    // Feed recovered symbol samples at decision instants. `isHardware` marks
    // whether the samples came from real RF hardware (true) or an offline /
    // synthetic source (false) -- the panel labels itself accordingly.
    void feedSymbols(const std::vector<std::complex<float>>& symbols, bool isHardware);

    // Forget all buffered points and return to the empty state.
    void clear();

protected:
    void paintEvent(QPaintEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;

public:
    // ---- Geometry / mapping exposed for QtTest ----
    // Map normalized constellation coordinates (I in [-1,1], Q in [-1,1]) to
    // widget pixels. The plot is centered and scaled so that unit radius maps
    // to `radius()` px.
    int  xOfI(float i) const;
    int  yOfQ(float q) const;
    float iOfX(int x) const;
    float qOfY(int y) const;
    QPointF center() const { return center_; }
    double  radius() const { return radius_; }

    // Read-outs for tests.
    bool    isEmpty() const { return points_.empty(); }
    bool    nonHardwareTagVisible() const { return showNotHwTag_; }
    QString nonHardwareTagText() const { return QString::fromUtf8("非硬件 NOT HARDWARE"); }
    QString emptyHintText() const { return QString::fromUtf8("等待数字信号…"); }
    QString evmText() const;         // "EVM 12.3%" or "—" when empty
    float   evmPercent() const { return evmPct_; }

private:
    void recomputeLayout();
    void pushPoint(std::complex<float> p);
    float nearestIdealDist(std::complex<float> p) const;

    struct AgePoint {
        std::complex<float> value;
        int                 age = 0;   // frames since fed
    };

    dsp::DigMode mode_ = dsp::DigMode::BPSK;
    std::deque<AgePoint> points_;
    bool                 showNotHwTag_ = false;

    QPointF center_{0.f, 0.f};
    double  radius_ = 1.0;

    // Rolling RMS radius for auto-normalization to the unit circle.
    float rmsRadius_ = 1.0f;

    // EVM accumulator (vs ideal constellation).
    float evmPct_ = 0.0f;
    long  evmN_   = 0;

    // Cap on retained points (oldest pruned).
    static constexpr int kMaxPoints = 400;
};

} // namespace ui
} // namespace mbdsdr
