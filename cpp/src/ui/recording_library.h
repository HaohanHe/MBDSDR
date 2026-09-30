// SPDX-License-Identifier: MIT
// Recording-library core: scan a real recording directory for .wav captures,
// read the sibling sidecar .json proof (written by the engine for both
// continuous demod-audio recordings and gated watch segments), and fall back to
// the {time}_{freq}_{mode} filename template when no sidecar exists. Also
// probes the RIFF/WAVE header of a 16-bit PCM WAV so the UI can honestly report
// sample rate / frame count / "unsupported format" without pretending to play.
//
// This class deliberately has NO widget and NO audio-device dependency: every
// result here is derived from files actually on disk (or an honest "unknown"
// fallback). The widget wiring lives in main_window.cpp.
#pragma once

#include <QString>
#include <QVector>
#include <cstdint>
#include <vector>

namespace mbdsdr {
namespace ui {

// Metadata provenance for one captured .wav. Values come EITHER from the real
// sidecar .json (preferred) OR from the filename template (honest fallback).
// A field stays empty when neither source provides it -- the UI then shows the
// raw filename, never a fabricated value.
struct RecordingMeta {
    // Human-displayed time (already formatted), e.g. "2026-09-30 12:00:00".
    QString time;
    // Center / channel frequency in Hz (0 = unknown).
    double frequencyHz = 0.0;
    // Demod mode string (NFM/WFM/...), empty = unknown.
    QString mode;
    // Sidecar type tag ("mbdsdr-audio-recording" / "mbdsdr-watch-recording") or
    // empty when there is no sidecar at all.
    QString sidecarType;
    // Watch-only: trigger threshold (dB), NaN when not a watch segment.
    double triggerThresholdDb = std::numeric_limits<double>::quiet_NaN();
    // Watch-only: measured duration seconds, 0 when unknown.
    double durationS = 0.0;
    // True when the sidecar marks a real-RF capture; false when it explicitly
    // says NOT HARDWARE. Undecided (no sidecar) leaves it false.
    bool isHardware = false;
};

struct RecordingEntry {
    QString wavPath;    // absolute path to the .wav
    QString jsonPath;   // absolute path to the sidecar .json (may not exist)
    RecordingMeta meta;
    qint64 bytes = 0;   // on-disk size of the .wav
};

// Probed RIFF/WAVE header result. ok=false carries an honest `error` string
// (not a RIFF/WAVE file, not PCM, truncated header, ...).
struct WavProbe {
    bool ok = false;
    QString error;
    quint16 audioFormat = 0;     // 1 = PCM
    quint16 channels = 0;
    quint32 sampleRate = 0;
    quint16 bitsPerSample = 0;
    quint32 dataBytes = 0;       // payload size claimed by the `data` chunk
    quint32 frames = 0;          // dataBytes / (channels * bytesPerSample)
};

class RecordingLibrary {
public:
    // Scan `dir` for *.wav (non-recursive, like the writers place files flat).
    // Each .wav is paired with a same-basename .json sidecar when present.
    // Returns entries sorted newest-first by file modification time. An empty /
    // missing directory yields an EMPTY vector (the caller shows the honest
    // "暂无录音" empty state -- never a fabricated row).
    static QVector<RecordingEntry> scan(const QString& dir);

    // Read a sidecar .json into RecordingMeta. Returns an empty/unknown meta on
    // any parse error (caller falls back to the filename). Never throws.
    static RecordingMeta readSidecar(const QString& jsonPath);

    // Honest filename fallback: parse the basename (without extension) against
    // the templates the writers actually use:
    //   audio: <yyyyMMdd_HHmmss>_<freqMHz>_<MODE>
    //   watch: <yyyyMMdd_HHmmss>_<MODE>_<freqHz>Hz[_n]
    // Fields that cannot be parsed stay empty/0 so the UI degrades to showing
    // the raw file name instead of guessing.
    static RecordingMeta parseFileName(const QString& baseName);

    // Parse a RIFF/WAVE header. Supports uncompressed PCM only: on a non-PCM
    // format tag ok stays false with error "不支持的格式 (非 PCM)".
    static WavProbe probeWav(const QString& path);

    // Decode the payload of a 16-bit PCM WAV (mono or first channel of stereo)
    // into unit-range float [-1,1]. Returns false if the file cannot be opened,
    // is not the probed PCM, or has no data chunk. Used by the on-demand
    // playback path; offscreen tests only exercise probeWav().
    static bool decodePcmMonoToFloat(const WavProbe& probe, const QString& path,
                                     std::vector<float>& out);

    // Delete a capture: removes the .wav AND its sibling sidecar .json (if
    // any). Returns true if the .wav was removed. The sidecar removal is
    // best-effort (a missing sidecar does not fail the call).
    static bool removeEntry(const RecordingEntry& e);
};

} // namespace ui
} // namespace mbdsdr
