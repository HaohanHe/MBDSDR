// SPDX-License-Identifier: MIT
//
// Scan-session persistence (data + JSON codec only, no QWidget, no radio).
//
// A "scan session" is a complete snapshot of one frequency-scanning run:
//   * every parameter the user set on the 频率扫描 group (start/stop/step/
//     dwell/threshold/direction/hold-mode/linger/hold-ms/bookmark-only), and
//   * the demod mode + channel bandwidth that were active while scanning,
//   * the list of historical hits the scanner gate actually recorded since its
//     last start() (frequency + measured level + the mode/bw stamped at save).
//
// This blob owns NO radio, opens NO device, and fabricates no frequency or
// level. The hits it carries are whatever the caller passed in (the real
// FrequencyScanner::hits()); an empty band simply yields an empty hits array,
// which serialises honestly as "0 hits" and never invents a station.
//
// On-disk format: a single JSON object, extension ".mbdscan". Example:
// {
//   "app":"MBDSDR","kind":"scan-session","version":1,
//   "startMHz":88.0,"stopMHz":108.0,"stepIndex":2,"dwellMs":300,
//   "thresholdDb":-50.0,"dirIndex":0,"holdIndex":0,
//   "lingerMs":1000,"holdMs":2000,"bmOnly":false,
//   "mode":"NFM","bwHz":12500.0,
//   "hits":[ {"freqHz":118000000.0,"levelDb":-38.5,"mode":"AM","bwHz":8000.0} ]
// }
//
// Honesty: scanSessionFromJson() REJECTS (never silently heals) a document that
// is not an object, whose required fields are missing or of the wrong JSON
// type, or whose "hits" entry is not an array of well-formed objects. The caller
// surfaces the returned human-readable error verbatim to the user.
#pragma once

#include <QList>
#include <QString>

namespace mbdsdr {
namespace ui {

// One historical hit stored in a session file. freqHz/levelDb are the REAL
// values the scanner gate recorded; mode/bwHz are the demod mode + channel
// bandwidth active when the session was saved (a scan runs under one mode/bw).
struct ScanSessionHit {
    double  freqHz  = 0.0;
    float   levelDb = -200.0f;
    QString mode;        // demod mode text, e.g. "NFM"; empty = unspecified
    double  bwHz    = 0.0; // channel bandwidth Hz; 0 = unspecified
};

// A complete, restorable scan-session snapshot.
struct ScanSession {
    // ---- 频率扫描 parameter snapshot (exact UI control values) ----
    double  startMHz    = 88.0;    // scanStartSpin_ value
    double  stopMHz     = 108.0;   // scanStopSpin_ value
    int     stepIndex   = 2;       // scanStepCombo_ index {10k,12.5k,100k,1M}
    int     dwellMs     = 300;     // scanDwellSpin_
    double  thresholdDb = -50.0;   // scanThrSpin_
    int     dirIndex    = 0;       // scanDirCombo_: 0 向上 / 1 向下 / 2 来回
    int     holdIndex   = 0;       // scanHoldCombo_: 0 直到信号消失 / 1 固定时长
    int     lingerMs    = 1000;    // scanLingerSpin_
    int     holdMs      = 2000;    // scanHoldMsSpin_
    bool    bmOnly      = false;   // scanBmOnlyChk_
    // ---- receiving state the scan ran under ----
    QString mode;                  // demodCombo_ text; empty = unspecified
    double  bwHz        = 0.0;     // currentBwHz_; 0 = unspecified
    // ---- historical hits (real, empty when the band was quiet) ----
    QList<ScanSessionHit> hits;
};

// Serialise to compact JSON bytes (deterministic key order).
QByteArray scanSessionToJson(const ScanSession& s);

// Parse JSON bytes into `out`. On success returns true. On ANY structural /
// type problem returns false and sets *err to a short, honest, human-readable
// reason (the caller shows it verbatim; nothing is silently defaulted).
bool scanSessionFromJson(const QByteArray& bytes, ScanSession& out, QString* err);

// Thin file wrappers over the byte layer. Save writes UTF-8 (no BOM); Load
// reads the whole file then validates through scanSessionFromJson, so a
// truncated / hand-edited / wrong-type file is rejected loudly.
bool scanSessionSaveFile(const QString& path, const ScanSession& s, QString* err);
bool scanSessionLoadFile(const QString& path, ScanSession& out, QString* err);

} // namespace ui
} // namespace mbdsdr
