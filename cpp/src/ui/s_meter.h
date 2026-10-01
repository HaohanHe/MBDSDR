// SPDX-License-Identifier: MIT
//
// Compact SDR++-style S-meter. The signal level comes from the REAL engine
// RSSI (onRssiLevel, same value as the status-strip RSSI readout -- no second
// data path). Units are the standard S0..S9 scale: 1 S-unit = 6 dB above the
// tracked noise floor. Peak-hold refreshes on a stronger signal and decays
// slowly back toward the real level. No device / no frame -> honest empty
// state. All metrics are tokens-driven.
#pragma once

#include <QWidget>

namespace mbdsdr {
namespace ui {

class SMeterWidget : public QWidget {
    Q_OBJECT
public:
    explicit SMeterWidget(QWidget* parent = nullptr);

    // Real engine level (dBFS). qQNaN => no device / empty state.
    void setSignalDbfs(double dbfs);
    // Tracked noise floor (dBFS); needed to compute S units.
    void setNoiseFloorDbfs(double dbfs);
    // Slow peak-hold decay, call ~1 Hz with seconds elapsed.
    void tickDecay(double dtSec);

    // Pure: S units above the tracked noise floor, clamped to 0..S9.
    // Returns -1 when either level is invalid (empty state, not a fake 0).
    static int sUnitsAboveNoise(double signalDbfs, double noiseFloorDbfs);

    QSize sizeHint() const override;

private:
    void paintEvent(QPaintEvent* e) override;

    double signalDbfs_ = qQNaN();
    double noiseDbfs_   = qQNaN();
    double peakDbfs_    = qQNaN();   // peak-hold (slow decay)
    int    shownUnits_  = -1;        // -1 = empty
};

} // namespace ui
} // namespace mbdsdr
