// SPDX-License-Identifier: MIT
// Satellite-pass autonomous capture task: SGP4 the next pass over the station,
// auto-derive the capture frequency (downlink carrier + peak-elevation Doppler,
// via core::captureTargetHz) and the recommended demod mode, then emit a real
// TaskPlan that retunes the radio.
//
// Honest: if the satellite has no offline TLE, no pass in the window, or an
// unknown downlink carrier, ok=false with a reason -- we never invent a
// frequency or a pass.
#pragma once

#include "task_orchestrator.h"
#include <QString>
#include <QDateTime>

namespace mbdsdr {
namespace ai {

struct SatTaskResult {
    TaskPlan plan;
    bool ok = false;
    QString error;            // honest reason when ok==false
    QString satName;          // matched TLE name
    QDateTime aosUtc;         // next acquisition of signal
    double maxElDeg = 0.0;    // peak elevation of that pass
    double captureFreqHz = 0.0; // chosen retune frequency (downlink + peak fd)
    double dopplerAtPeakHz = 0.0;
    QString mode;             // recommended demod mode
};

// Build a capture plan for the named satellite (matched by case-insensitive
// substring against offline built-in TLEs), propagating with the SAME SGP4 the
// sky tab uses.  stationLatDeg/stationLonDeg are the operator's fixed station.
SatTaskResult planSatelliteCapture(const QString& satName,
                                   double stationLatDeg, double stationLonDeg,
                                   const QDateTime& nowUtc, int hoursAhead = 12);

} // namespace ai
} // namespace mbdsdr
