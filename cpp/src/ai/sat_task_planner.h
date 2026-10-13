// SPDX-License-Identifier: MIT
// Satellite-pass autonomous capture task: SGP4 the next pass over the station,
// auto-derive the capture frequency (downlink carrier + peak-elevation Doppler,
// via core::captureTargetHz) and the recommended demod mode, then emit a real
// TaskPlan that retunes the radio.
//
// Honest contract (read this before touching the freshness logic):
//   * We NEVER report a real upcoming pass from the stale offline builtinTle()
//     snapshot (epoch 2006). With no fresh on-disk TLE cache, planSatelliteCapture
//     returns ok=false, demo=true, and a human-readable warning explaining that
//     the reference result shown is offline demo data and MUST NOT be used for a
//     real capture. The SGP4 numbers are still computed so the UI has *something*
//     to display, but they are explicitly tagged as non-authoritative.
//   * A fresh cache means: cache.valid && entries non-empty && every matched
//     TLE's epoch is within kMaxTleAgeDays (30 days) of now. Beyond that, TLE
//     elements drift enough that a "real" AOS/frequency would be misleading.
//   * If the satellite has no TLE, no pass in the window, or an unknown downlink
//     carrier, ok=false with a reason -- we never invent a frequency or a pass.
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

// Max acceptable age (days) of a TLE epoch for it to be treated as a *real*
// upcoming pass. 30 days is the standard operational ceiling for amateur /
// weather-satellite SGP4: LEO elements drift ~1-2 km/day in position error, so
// beyond ~30 days the predicted AOS/LOS time and Doppler shift are no longer
// trustworthy enough to drive a real receiver retune. BuiltinTle() (2006 epoch)
// is orders of magnitude older and therefore always demo-only.
inline constexpr double kMaxTleAgeDays = 30.0;

// One satellite in the on-disk TLE cache, surfaced by list_satellites.
// Pure read of the cache; never falls back to builtinTle().
struct SatListEntry {
    QString name;
    int catalogNumber = 0;          // NORAD catalog number (0 = unknown/malformed)
    QDateTime epochUtc;             // TLE line-1 epoch (invalid when unparseable)
    double ageDays = 0.0;           // days since epoch; 0 when epoch invalid
    bool fresh = false;            // epoch within kMaxTleAgeDays of now
    double downlinkHz = 0.0;        // nominal downlink (0 = unknown, never invented)
};

// Honest result of a read-only TLE-cache listing. ok==false always carries a
// human-readable reason; we never fabricate a satellite when the cache is empty.
struct SatListResult {
    bool ok = false;
    QString error;                  // honest reason when ok==false
    QString source;                 // "cached_tle" when a cache exists
    QDateTime fetchedAt;            // cache fetch time (invalid when no cache)
    QList<SatListEntry> sats;
};

// Read-only catalog of satellites present in the FRESH on-disk TLE cache.
// This is the prerequisite for predict_passes / planSatelliteCapture: the model
// needs to know which satellite names are actually in the cache before it can
// predict a pass. Uses ONLY the on-disk cache (dsp::TleClient::cachedTle); the
// stale offline builtinTle() (epoch 2006) is deliberately NOT listed. Honest
// empty state when there is no cache (ok=false, source="none").
SatListResult listCachedSatellites(const QDateTime& nowUtc);

// Pure, deterministic seam over an explicit TLE list (no disk / no network).
// Unit tests inject a fixed known TLE here; production goes through
// listCachedSatellites() above. Never falls back to builtin data.
SatListResult listSatellitesFromEntries(const QList<dsp::TleEntry>& entries,
                                        const QDateTime& nowUtc);

struct SatTaskResult {
    TaskPlan plan;
    bool ok = false;            // true ONLY when the result comes from a fresh
                               // (within kMaxTleAgeDays) TLE cache and every
                               // downstream step succeeded.
    bool demo = false;         // true when the SGP4 numbers below were derived
                               // from the stale offline builtinTle() snapshot
                               // (epoch 2006). The UI must show a "demo only"
                               // banner and MUST NOT let this plan drive a real
                               // retune / record. When demo==true, ok==false.
    QString error;             // honest reason when ok==false
    QString warning;           // human-readable warning (e.g. demo-data notice)
                               // surfaced to the operator alongside the numbers.
    QString satName;           // matched TLE name
    QDateTime aosUtc;          // next acquisition of signal
    double maxElDeg = 0.0;     // peak elevation of that pass
    double captureFreqHz = 0.0; // chosen retune frequency (downlink + peak fd)
    double dopplerAtPeakHz = 0.0;
    QString mode;              // recommended demod mode
};

// Build a capture plan for the named satellite. Data-source selection (fresh
// cache vs offline builtin demo) is the ONLY thing this public function does;
// the actual SGP4 propagation lives in planCaptureFromEntries() below so the
// freshness / demo tagging can be unit-tested in isolation.
SatTaskResult planSatelliteCapture(const QString& satName,
                                   double stationLatDeg, double stationLonDeg,
                                   const QDateTime& nowUtc, int hoursAhead = 12);

// Pure, deterministic seam over an explicit TLE list (no disk / no network).
// Propagates the matched TLEs, picks the first visible pass, derives the
// downlink + Doppler target frequency, and returns a SatTaskResult with
// ok=true. The freshness / demo tagging is the CALLER's responsibility -- this
// function trusts its input. Mirrors predictPassesFromEntries() so the two
// seams read the same way.
SatTaskResult planCaptureFromEntries(const QList<dsp::TleEntry>& entries,
                                     const QString& satName,
                                     double stationLatDeg, double stationLonDeg,
                                     const QDateTime& nowUtc, int hoursAhead);

// True when every entry in `entries` has a parseable TLE line-1 epoch that is
// within kMaxTleAgeDays of `nowUtc`. Empty entries => false (no data is not
// fresh data). Malformed epochs => treated as stale (false). Pure.
bool entriesAreFresh(const QList<dsp::TleEntry>& entries, const QDateTime& nowUtc);

} // namespace ai
} // namespace mbdsdr
