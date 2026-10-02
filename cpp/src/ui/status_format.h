// SPDX-License-Identifier: MIT
//
// Permanent status-strip formatting: the ~1 Hz hardware-readback tick used to
// inline these QString `.arg(...)` chains inside MainWindow's slot bodies, so
// the labels could only be exercised through the full offscreen MainWindow.
// They are now pure header-only formatters with no Qt-widget state: every
// honest empty-state ("--") and the connected/disconnected tagging lives here
// and is unit-tested directly.
#pragma once

#include <QtCore/QString>

#include <cmath>

namespace mbdsdr {
namespace ui {

// Sample-rate read-out. hz <= 0 (no readback yet / offline) => honest "--".
inline QString fmtStripSampleRate(double hz) {
    return hz > 0.0 ? QString("%1 MS/s").arg(hz / 1e6, 0, 'f', 3)
                    : QString("--");
}

// VFO / centre-frequency read-out.
inline QString fmtStripVfoFreq(double hz) {
    return hz > 0.0 ? QString("%1 MHz").arg(hz / 1e6, 0, 'f', 3)
                    : QString("--");
}

// Tuner-gain read-out. Driver readback <= 0 (AGC / unknown) => "--".
inline QString fmtStripGain(double db) {
    return db > 0.0 ? QString("增益 %1 dB").arg(db, 0, 'f', 1)
                    : QString("--");
}

// Source name tag. Offline / dropped sources keep their name but are honestly
// tagged （非硬件） so a test-signal frame is never mistaken for live RF.
inline QString fmtStripSource(const QString& name, bool connected) {
    return connected ? name : name + QStringLiteral("（非硬件）");
}

// Squelch gate read-out. The engine reports gate-open even when the squelch is
// disabled, so `enabled` comes from the real checkbox state -- OFF is shown
// honestly instead of a misleading OPEN.
inline QString fmtStripSquelch(bool enabled, bool open) {
    if (!enabled) return QStringLiteral("静噪 OFF");
    return open ? QStringLiteral("静噪 OPEN") : QStringLiteral("静噪 CLOSED");
}

inline QString fmtStripRssi(float dbfs) {
    return std::isnan(dbfs) ? QString("--")
                            : QString("RSSI %1").arg(dbfs, 0, 'f', 1);
}

inline QString fmtStripSnr(float db) {
    return std::isnan(db) ? QString("--")
                          : QString("SNR %1").arg(db, 0, 'f', 1);
}

} // namespace ui
} // namespace mbdsdr
