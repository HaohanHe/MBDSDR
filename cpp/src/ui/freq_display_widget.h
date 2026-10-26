// SPDX-License-Identifier: MIT
//
// Big frequency display with per-digit editing (SDR++/gqrx "radio dial" feel).
// Replaces the read-only QLabel: each of the 9 digits is a hot zone --
//   * left-click upper half: +1 at that place value
//   * left-click lower half: -1 at that place value
//   * wheel over a digit:    +/-1 at that place value
//   * right-click:           zero this digit and all lower places
// Leading zeros are dimmed so the significant digit count reads at a glance
// (SDR++ frequency_select.cpp:117-123 convention).
// The widget is pure UI + arithmetic; it never touches the engine directly --
// it emits frequencyEdited(hz) and the owner applies the tune.
#pragma once

#include <QColor>
#include <QFont>
#include <QWidget>

namespace mbdsdr {
namespace ui {

class FreqDisplayWidget : public QWidget {
    Q_OBJECT
public:
    explicit FreqDisplayWidget(QWidget* parent = nullptr);

    // Push the live dial frequency (Hz) from the owner's 500 ms poll.
    void setFrequency(qint64 hz);
    qint64 frequency() const { return freqHz_; }

    // The 9-digit grid is "DDD.DDD.DDD" = 9 digits + 2 separator dots.
    static constexpr int kDigitCount = 9;

    // Place value in Hz of digit index 0..8 (leftmost = 100 MHz = 1e8 Hz).
    static qint64 placeValueHz(int digitIndex) {
        qint64 v = 100000000;  // 1e8
        for (int i = 0; i < digitIndex; ++i) v /= 10;
        return v;
    }

    // Pure arithmetic (unit-testable, no QWidget state).
    // dir = +1 / -1; clamps to [minHz, maxHz].
    static qint64 nudgeDigit(qint64 hz, int digitIndex, int dir,
                             qint64 minHz, qint64 maxHz) {
        const qint64 step = placeValueHz(digitIndex);
        qint64 v = hz + (dir >= 0 ? step : -step);
        if (v < minHz) v = minHz;
        if (v > maxHz) v = maxHz;
        return v;
    }
    // Zero this digit and every digit to its right (right-click).
    static qint64 truncateBelow(qint64 hz, int digitIndex) {
        const qint64 step = placeValueHz(digitIndex);
        return (hz / step) * step;
    }

signals:
    void frequencyEdited(qint64 hz);

protected:
    void paintEvent(QPaintEvent*) override;
    void mousePressEvent(QMouseEvent*) override;
    void wheelEvent(QWheelEvent*) override;

private:
    // Character-cell hit test: x -> character index in "DDD.DDD.DDD" (-1 = miss).
    int digitAtX(int x) const;
    void applyDigitDelta(int digitIndex, int dir);

    qint64 freqHz_ = 0;
    qint64 minHz_ = 0;
    qint64 maxHz_ = 2000000000;
    QFont digitFont_;
};

}  // namespace ui
}  // namespace mbdsdr
