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
#include "dsp/rds_decoder.h"
#include "dsp/wfm_stereo.h"
#include "dsp/fsk_demod.h"
#include "dsp/pocsag_decoder.h"
#include "dsp/m17_decoder.h"
#include "dsp/vor_receiver.h"
#include "dsp/acars_decoder.h"
#include "dsp/navtex_decoder.h"

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
    bool    armed = false;        // user-enabled parallel monitoring (not selected)
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
    // User explicitly wants this NON-selected channel demodulated every block
    // (parallel monitoring / recording). The selected channel is always active;
    // other channels are skipped unless armed -> orphan VFOs cost ~zero CPU.
    bool   armed = false;

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

    // RDS (EN 300 401) data-link decoder. Built ONLY for WFM channels, at the
    // channel's IF rate (240 kHz); null on every other mode. Fed the de-
    // emphasized MPX tap (DemodWFM::mpxOut()) after each block. Rebuilt together
    // with the demod on rebuild(), so a mode/rate change naturally resets the
    // decoder's block-sync and PS/PTY/RadioText state.
    std::unique_ptr<RdsDecoder> rds;

    // WFM FM-stereo composite-baseband decoder. Built ONLY for WFM channels, at
    // the channel's IF rate (240 kHz); null on every other mode. Fed the
    // PRE-de-emphasis raw MPX tap (DemodWFM::rawMpxOut()) after each block, so
    // the 19 kHz pilot / 38 kHz DSB are at full strength. Same lifecycle as rds
    // (rebuilt together with the demod on rebuild()). It recovers M = (L+R)/2 and
    // S = (L-R)/2, sample-aligned.
    std::unique_ptr<WfmStereoDecoder> stereo;
    // Independent ifRate->48 kHz resamplers for the recovered M and S. Configured
    // with the SAME parameters as the mono audio resampler and fed the SAME number
    // of IF samples each block, so stereoM48k/stereoS48k stay sample-aligned with
    // the mono audio48k (and with each other).
    AudioResampler stereoMResampler, stereoSResampler;
    std::vector<float> stereoM48k, stereoS48k;   // last block's recovered M/S @48k
    // Cached decoder state for the engine's throttled stereoState signal.
    float stereoBlend = 0.0f;
    float stereoPilot = 0.0f;   // pilotQuality, normalised 0..1
    bool  stereoLock  = false;

    // --- POCSAG / m17 / VOR digital data-link (Wave1) ------------------------
    // Built ONLY for their matching mode; null otherwise. Same rebuild lifecycle
    // as rds/stereo: a mode/rate change recreates them, which naturally resets
    // their block-sync and flushed the snapshot below.
    //   POCSAG: channelized IQ -> FskDemod (2-FSK, 1200 baud, +/-4.5 kHz) ->
    //           drain bits -> PocsagDecoder. No analog audio.
    //   m17:    channelized IQ -> M17Decoder's OWN built-in 4FSK front-end
    //           (4800 sym/s @ 48 kHz). No analog audio.
    //   VOR:    AM demod -> 48 kHz composite audio -> VorReceiver. DOES carry
    //           analog audio (it rides the normal AM path).
    std::unique_ptr<FskDemod>    fskDemod;
    std::unique_ptr<PocsagDecoder> pocsag;
    std::unique_ptr<M17Decoder>  m17;
    std::unique_ptr<VorReceiver>  vor;
    std::unique_ptr<AcarsDecoder>  acars;
    std::unique_ptr<NavtexDecoder> navtex;

    // Accumulated READ-ONLY output snapshot. pocsagMessages/m17Calls grow as
    // frames arrive and are cleared only by rebuild (mode switch) or
    // VfoManager::clearDigitalOutputs(); vorResult holds the LATEST finished
    // measurement (locked=false = honest "no bearing yet", never fabricated).
    std::vector<PocsagMessage> pocsagMessages;
    std::vector<M17Call>       m17Calls;
    VorResult                  vorResult;
    std::vector<AcarsPacket>   acarsPackets;
    std::vector<NavtexMessage> navtexMessages;

    // Mode predicates (generic capability names, never a station).
    bool isPocsag() const { return mode == "POCSAG"; }
    bool isM17()    const { return mode == "m17"; }
    bool isVor()    const { return mode == "VOR"; }
    bool isAcars()  const { return mode == "ACARS"; }
    bool isNavtex() const { return mode == "NAVTEX"; }

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
    // Rename channel `id` (an empty name is rejected -> false). Minimal gap fill:
    // the band-box / list label simply reads back `name`; no DSP rebuild needed.
    bool renameVfo(int id, const QString& name);
    // Enable/disable parallel demod of a non-selected channel (false default).
    bool setArmed(int id, bool on);

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

    // ---- Digital read-out snapshots (FROZEN interface for Wave2) ----------
    // Read-only COPIES of channel `id`'s accumulated decode output. These run
    // on the engine run() thread (same sourceMutex_ as process()); callers
    // (SpectrumEngine forwarders / ControlHub / Agent / UI) take the mutex.
    // Empty list / unlocked VorResult = the honest empty state (a fresh channel
    // or pure noise produces nothing -- never a fabricated message/call/bearing).
    std::vector<PocsagMessage> pocsagMessages(int channelId) const;
    std::vector<M17Call>       m17Calls(int channelId) const;
    VorResult                   vorResult(int channelId) const;
    std::vector<AcarsPacket>   acarsPackets(int channelId) const;
    std::vector<NavtexMessage> navtexMessages(int channelId) const;
    // Reset channel `id`'s digital output queues AND re-initialise its decoders
    // (panel "clear" / write command). No-op for an unknown id.
    void clearDigitalOutputs(int channelId);

    // ---- Costas / carrier-lock read-out (Phase55 block2) ------------------
    // Snapshot of the SELECTED channel's Costas loop lock status. When the
    // selected channel is not a digital (BPSK/QPSK) mode, digitalDemod is null
    // and we return a default DigitalLockStatus{carrierLocked=false,...} -- the
    // honest "no carrier lock" state, never a fabricated lock. The engine takes
    // sourceMutex_ around this; ControlHub / Agent get_status / UI read it.
    DigitalLockStatus digitalLockStatus() const;

private:
    std::vector<VfoChannel> channels_;
    int nextId_ = 1;
    int selectedId_ = 0;
    int addCount_ = 0;   // drives color/name assignment
};

} // namespace dsp
} // namespace mbdsdr
