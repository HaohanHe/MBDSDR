// SPDX-License-Identifier: MIT
//
// Multi-VFO manager (SDR++-style fan-out). The wideband source IQ is fed once
// to every VFO channel; each channel owns an INDEPENDENT Channelizer + IDemod +
// AudioResampler and keeps its own streaming state across blocks. A channel's
// absolute frequency maps to a channelizer NCO offset (freq - source center);
// changing mode/bandwidth rebuilds only that channel, changing frequency only
// retunes the NCO (continuous phase, no state reset).
//
// Shared downstream blocks (squelch / AGC / audio output / gated recorder /
// wav writer) are deliberately NOT owned here: they live in the engine and act
// ONLY on the selected VFO's 48 kHz audio, so the single-VFO path stays
// byte-for-byte identical to the legacy single-channel receiver.
//
// Threading: all public methods are called from the engine run() thread under
// the engine's sourceMutex_ (UI-thread commands reach it through engine slots
// that take the same mutex). No internal locking is needed.
//
// Extension slots for a future round (NOT implemented here):
//   - VfoChannel::digitalMode  -> BPSK/QPSK symbol path (no audio)
//   - VfoChannel::anrEnabled   -> audio-chain ANR insert before the resampler
#pragma once

#include <QString>
#include <QColor>
#include <complex>
#include <memory>
#include <vector>

#include "dsp/channelizer.h"
#include "dsp/audio_resampler.h"
#include "dsp/demod.h"
#include "dsp/digital_demod.h"

namespace mbdsdr {
namespace dsp {

// Lightweight description used by the UI to draw per-VFO band boxes and to
// drive the VFO list. Carries no DSP state.
struct VfoMarker {
    int     id = 0;
    double  freqHz = 0.0;      // absolute RF frequency
    double  bandwidthHz = 0.0; // channel / IF bandwidth
    QString mode = "NFM";
    QColor  color;
    QString name;
    bool    selected = false;
    // Signed IF offset of this VFO relative to the source's capture center:
    //   centerOffsetHz = freqHz - referenceHz.
    // This is what the channelizer NCO actually tunes. During an in-band
    // (SDR++-style) move the tuner stays parked and ONLY this offset rides; when
    // the tuner itself moves, freqHz is unchanged and this value is recomputed
    // against the new center on the next snapshot.
    double  centerOffsetHz = 0.0;
    // The source capture center this snapshot was taken against (reference
    // frequency). Lets the UI draw the capture window / band edges without a
    // second source-center read.
    double  referenceHz = 0.0;
};

// One receive channel: independent channelizer + demod + resampler.
struct VfoChannel {
    int     id = 0;
    double  freqHz = 0.0;       // absolute RF frequency (offset = freq - source center)
    QString mode = "NFM";
    double  bandwidthHz = 12500.0;
    QColor  color = QColor("#7CC4FF");
    QString name = "VFO A";

    // Per-channel DSP state (streaming, preserved across blocks).
    Channelizer    channelizer;
    std::unique_ptr<IDemod> demod;
    AudioResampler resampler;
    std::vector<float> audio48k;   // last block's 48 kHz mono output

    bool   needsRebuild = true;    // mode/bandwidth/sr changed -> reconfigure
    double lastSr = 0.0;           // source rate the channelizer was built for

    // --- Reserved slots for a future round (digital demod / ANR) ------------
    // When these land, process() will branch here: digital modes skip the audio
    // resampler and emit symbols on a separate outlet; anrEnabled inserts an
    // ANR stage between demod() and the resampler. No API is fixed yet.
    enum class DigitalMode { None, BPSK, QPSK };
    DigitalMode digitalMode = DigitalMode::None;
    bool        anrEnabled  = false;

    // Real digital demodulator (BPSK/QPSK). Built only when mode is digital;
    // otherwise null. When built, analog `demod` stays null and this channel
    // produces NO audio -- only recovered symbols via `recoveredSymbols`.
    std::unique_ptr<DigitalDemod> digitalDemod;
    std::vector<std::complex<float>> recoveredSymbols;   // last block's post-Costas symbols
    DigitalLockStatus lockStatus{};

    // True when this VFO runs a digital (rather than analog) demod.
    bool isDigital() const { return mode == "BPSK" || mode == "QPSK"; }
    static bool modeIsDigital(const QString& m) { return m == "BPSK" || m == "QPSK"; }

    // Build (or rebuild) channelizer + demod + resampler for this channel at
    // the given source rate / center. Must mirror SpectrumEngine's legacy
    // rebuildDemod() so a single centered VFO is indistinguishable from the old
    // single-channel path.
    void rebuild(double sourceSr, double sourceCenterHz);
};

class VfoManager {
public:
    VfoManager();

    // Create the initial single VFO tuned to the source center.
    void initDefault(double sourceSr, double sourceCenterHz, const QString& mode,
                     double bandwidthHz);

    // Source sample rate changed: every channel must rebuild (decimation/taps
    // depend on the input rate).
    void sourceRateChanged();

    // ---- Mutation (called under sourceMutex_) ----------------------------
    int  addVfo(double freqHz);                 // next color/name, at given freq
    bool removeVfo(int id);                     // refuses to drop the last one
    bool selectVfo(int id);
    bool setFreq(int id, double freqHz);
    bool setMode(int id, const QString& mode);
    bool setBandwidth(int id, double hz);
    bool setColor(int id, const QColor& c);

    // ---- Process ----------------------------------------------------------
    // Fan the source IQ out to every channel; fills each channel's audio48k.
    // Returns the SELECTED channel's 48 kHz audio (empty reference if none).
    const std::vector<float>& process(const std::vector<std::complex<float>>& iq,
                                      double sourceSr, double sourceCenterHz);

    // ---- Read-out ---------------------------------------------------------
    int  count() const { return static_cast<int>(channels_.size()); }
    int  selectedId() const { return selectedId_; }
    bool hasId(int id) const;
    const VfoChannel* channel(int id) const;
    VfoChannel* channel(int id);
    const VfoChannel* selected() const;
    VfoChannel* selected();
    QVector<VfoMarker> markers() const;
    // Largest channelizer decimation among channels (used to size the read
    // block so the loop paces to whole branch counts).
    int  maxDecimation() const;
    // Cycle of colors handed out to newly added VFOs.
    static QColor nextColor(int index);

private:
    std::vector<VfoChannel> channels_;
    int nextId_ = 1;
    int selectedId_ = 0;
    int addCount_ = 0;   // drives color/name assignment
};

} // namespace dsp
} // namespace mbdsdr
