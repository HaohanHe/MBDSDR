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
namespace dsp { struct TleEntry; }
namespace ai {

// One predicted pass, in the shape the read-only predict_passes LLM tool hands
// to the model. Field naming aligns with the Flutter side astro/passes.dart Pass
// (riseTime/riseAz/setTime/setAz/maxEl): rise_* = AOS, set_* = LOS.
struct SatPassEntry {
    QString name;
    int catalogNumber = 0;      // NORAD catalog number (0 = unknown, never invented)
    QDateTime aosUtc;           // AOS / rise
    double azAos = 0.0;         // azimuth at rise, deg
    QDateTime losUtc;           // LOS / set
    double azLos = 0.0;         // azimuth at set, deg
    double maxEl = 0.0;         // peak elevation, deg
};

// Honest result of a read-only pass prediction. ok==false always carries a
// human-readable reason; we never fabricate a pass when the cache is empty.
struct SatPassListResult {
    bool ok = false;
    QString error;              // honest reason when ok==false
    QString source;             // data-source label, e.g. "cached_tle"
    QList<SatPassEntry> passes;
};

// Read-only pass prediction for the LLM predict_passes tool. Data source is the
// FRESH on-disk TLE cache (dsp::TleClient::cachedTle) -- the stale offline
// builtinTle() is deliberately NOT used as real passes. Honest empty states:
//   * invalid station coordinates / empty satellite name -> ok=false
//   * no valid fresh cache                                -> ok=false ("无新鲜 TLE")
//   * satellite not in cache / no pass in window          -> ok=false
SatPassListResult predictSatellitePasses(const QString& satName,
                                         double stationLatDeg, double stationLonDeg,
                                         const QDateTime& nowUtc, int hoursAhead = 24);

// Pure, deterministic seam over an explicit TLE list (no disk / no network).
// Unit tests inject a fixed known TLE here; production goes through
// predictSatellitePasses() above. Never falls back to builtin data.
SatPassListResult predictPassesFromEntries(const QList<dsp::TleEntry>& entries,
                                           const QString& satName,
                                           double stationLatDeg, double stationLonDeg,
                                           const QDateTime& nowUtc, int hoursAhead);

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
