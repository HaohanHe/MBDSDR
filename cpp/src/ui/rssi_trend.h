// SPDX-License-Identifier: MIT
//
// Mini RSSI/dBfs history trend strip (status bar, beside the S-meter). It stores
// ONLY the REAL engine RSSI samples fed via pushDbfs() -- the same value the
// S-meter and the sbRssi_ readout consume -- and paints the recent-N-sample line
// so signal fading / flutter over time is visible. No device / no frame / a
// non-finite (NaN) sample means the link is down: the buffer is CLEARED to the
// honest empty state, never a fabricated or stale history. All metrics are
// tokens-driven.
#pragma once

#include <QWidget>
#include <vector>

namespace mbdsdr {
namespace ui {

class RssiTrendWidget : public QWidget {
    Q_OBJECT
public:
    explicit RssiTrendWidget(QWidget* parent = nullptr);

    // Feed a REAL engine RSSI sample (dBFS). A non-finite value (NaN/inf, i.e.
    // no frame / link down) CLEARS the buffer to the honest empty state rather
    // than drawing a stale line. Finite samples are appended and the rolling
    // buffer is trimmed to tokens::kRssiTrendMaxSamples (oldest dropped).
    void pushDbfs(double dbfs);
    // Empty the buffer explicitly (source dropped). Honest blank until the next
    // real sample arrives.
    void clear();

    // ---- Offscreen-test-only read-back (do not drive production) ----------
    int  countForTest() const { return static_cast<int>(hist_.size()); }
    bool emptyForTest() const { return hist_.empty(); }

    QSize sizeHint() const override;

protected:
    void paintEvent(QPaintEvent* e) override;

private:
    std::vector<float> hist_;   // recent real dBfs samples; back = newest
};

} // namespace ui
} // namespace mbdsdr
