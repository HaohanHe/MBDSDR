// SPDX-License-Identifier: MIT
#include "vfo_manager.h"

#include <algorithm>
#include <cmath>

#include "core/bandwidth_preset.h"
#include "core/tokens.h"

namespace mbdsdr {
namespace dsp {

// Default IF bandwidth for a mode when the user picks it from the combo.
// Single source of truth: core/bandwidth_preset.h (named constants, shared
// with the UI preset + unit tests). Kept as a thin local wrapper so callers
// don't each spell out the namespace.
static double defaultBandwidthForMode(const QString& mode) {
    return mbdsdr::core::defaultBandwidthHzForMode(mode);
}

void VfoChannel::rebuild(double sourceSr, double sourceCenterHz) {
    // Mirrors the legacy SpectrumEngine::rebuildDemod() exactly, per channel.
    double sr = sourceSr;
    if (sr < 24000.0) sr = 24000.0;   // guard against an unconnected source

    double ifTarget = 48000.0;   // narrowband modes
    double chBw = bandwidthHz;
    if (mode == "WFM") {
        ifTarget = 240000.0;     // keeps +/-75 kHz deviation
        chBw = 200000.0;
    }
    if (sr < ifTarget) ifTarget = sr;
    if (chBw <= 0.0) chBw = ifTarget * 0.8;

    // Digital modes: fixed 48 kHz IF, ~4.8 kHz channel (covers the 2400-baud
    // main lobe), 2400 baud. No analog audio.
    if (isDigital()) {
        ifTarget = 48000.0;
        chBw = 12000.0;
        channelizer.configure(sr, ifTarget, chBw, 31);
        channelizer.setVfoOffsetHz(freqHz - sourceCenterHz);
        const double ifRate = channelizer.effectiveOutputRateHz();
        DigitalDemodConfig cfg;
        cfg.sampleRateHz = ifRate;
        cfg.symbolRateBd = 2400.0;
        cfg.mode = (mode == "QPSK") ? DigMode::QPSK : DigMode::BPSK;
        cfg.timingBw = 0.005f;   // Gardner TED gain
        digitalDemod = std::make_unique<DigitalDemod>(cfg);
        demod.reset();          // no analog demod on digital channels
        rds.reset();            // RDS rides the WFM analog path only
        stereo.reset();         // stereo rides the WFM analog path only
        resampler.configure(ifRate, 48000.0, 31);
        recoveredSymbols.clear();
        lastSr = sr;
        needsRebuild = false;
        return;
    }

    // ADS-B (1090 MHz Mode S): no analog demod, no RDS, no digital symbols.
    // The engine feeds full-rate IQ to its own ADSBDecoder on a separate path;
    // this VFO channel only keeps the channelizer configured (wideband
    // passthrough) so process() never crashes, and emits NO audio.
    if (mode == "ADS-B") {
        channelizer.configure(sr, sr, 2000000.0, 31);
        channelizer.setVfoOffsetHz(freqHz - sourceCenterHz);
        demod.reset();          // no analog demod
        digitalDemod.reset();
        rds.reset();
        stereo.reset();
        recoveredSymbols.clear();
        resampler.configure(sr, 48000.0, 31);
        lastSr = sr;
        needsRebuild = false;
        return;
    }

    // ---- POCSAG (1200 baud 2-FSK, +/-4.5 kHz deviation) --------------------
    // 48 kHz IF, ~12 kHz channel (Carson BW ~11.4 kHz). Channelized IQ feeds a
    // dedicated FskDemod whose bits are handed to PocsagDecoder. No analog audio.
    if (isPocsag()) {
        ifTarget = 48000.0;
        chBw = core::kBwPocsagHz;
        channelizer.configure(sr, ifTarget, chBw, 31);
        channelizer.setVfoOffsetHz(freqHz - sourceCenterHz);
        const double ifRate = channelizer.effectiveOutputRateHz();
        FskDemodConfig fcfg;
        fcfg.sampleRateHz = ifRate;
        fcfg.symbolRateBd = tokens::kPocsagBaudBd;
        fcfg.deviationHz  = tokens::kPocsagDeviationHz;
        fskDemod = std::make_unique<FskDemod>(fcfg);
        pocsag   = std::make_unique<PocsagDecoder>();
        demod.reset();          // no analog demod
        digitalDemod.reset();
        m17.reset();
        vor.reset();
        rds.reset();
        stereo.reset();
        pocsagMessages.clear();
        m17Calls.clear();
        vorResult = VorResult();
        resampler.configure(ifRate, 48000.0, 31);
        recoveredSymbols.clear();
        lastSr = sr;
        needsRebuild = false;
        return;
    }

    // ---- m17 (4800 sym/s 4FSK; decoder owns its analogue front-end) --------
    // 48 kHz IF, ~9.6 kHz channel. We hand channelized IQ straight to the
    // M17Decoder's built-in discriminator+4-level slicer; no FskDemod here (it
    // is a 2-FSK binary sign detector, not a 4FSK dibit recovery). No audio.
    if (isM17()) {
        ifTarget = 48000.0;
        // The m17 decoder owns its analogue front-end (discriminator + boxcar
        // matched filter + FREE-RUNNING 10-sps strobe). That strobe is aligned
        // to the boxcar's own ~L/2 group delay and is NOT adaptive, so an extra
        // channelizer FIR (~15-sample group delay) slides the sampling point onto
        // symbol transitions and breaks frame sync. We therefore let the
        // channelizer translate + decimate WITHOUT its band-limiting FIR here
        // (whole-band passthrough, zero group delay); the decoder's own LPF is
        // the matched filter. NCO offset still applies for out-of-centre VFOs.
        chBw = ifTarget;   // bandwidth == IF rate -> channelizer skips its FIR
        channelizer.configure(sr, ifTarget, chBw, 31);
        channelizer.setVfoOffsetHz(freqHz - sourceCenterHz);
        const double ifRate = channelizer.effectiveOutputRateHz();
        m17 = std::make_unique<M17Decoder>(ifRate);
        fskDemod.reset();
        pocsag.reset();
        demod.reset();          // no analog demod
        digitalDemod.reset();
        vor.reset();
        rds.reset();
        stereo.reset();
        pocsagMessages.clear();
        m17Calls.clear();
        vorResult = VorResult();
        resampler.configure(ifRate, 48000.0, 31);
        recoveredSymbols.clear();
        lastSr = sr;
        needsRebuild = false;
        return;
    }

    // ---- VOR (AM demod of the 108-118 MHz aeronautical composite) ----------
    // 48 kHz IF wide enough to keep the 9960 Hz subcarrier; an AM demod produces
    // the composite audio that VorReceiver turns into a radial. It DOES carry
    // analog audio (rides the normal AM path); we additionally tap audio48k
    // into the receiver in process().
    if (isVor()) {
        ifTarget = 48000.0;
        chBw = core::kBwVorHz;
        channelizer.configure(sr, ifTarget, chBw, 31);
        channelizer.setVfoOffsetHz(freqHz - sourceCenterHz);
        const double ifRate = channelizer.effectiveOutputRateHz();
        demod  = std::make_unique<DemodAM>(ifRate, chBw);
        vor    = std::make_unique<VorReceiver>(ifRate);
        fskDemod.reset();
        pocsag.reset();
        m17.reset();
        digitalDemod.reset();
        rds.reset();
        stereo.reset();
        pocsagMessages.clear();
        m17Calls.clear();
        vorResult = VorResult();
        resampler.configure(ifRate, 48000.0, 31);
        stereoMResampler.configure(ifRate, 48000.0, 31);
        stereoSResampler.configure(ifRate, 48000.0, 31);
        recoveredSymbols.clear();
        lastSr = sr;
        needsRebuild = false;
        return;
    }

    digitalDemod.reset();
    rds.reset();   // re-created below only when this channel is WFM
    stereo.reset(); // re-created below only when this channel is WFM
    // Generic analog mode (NFM/WFM/AM/USB/LSB/CW): tear down the digital-link
    // decoders and clear their read-only snapshots, so switching away from
    // POCSAG/m17/VOR never leaves a stale message / call / radial behind.
    fskDemod.reset();
    pocsag.reset();
    m17.reset();
    vor.reset();
    pocsagMessages.clear();
    m17Calls.clear();
    vorResult = VorResult();

    channelizer.configure(sr, ifTarget, chBw, 31);
    const double ifRate = channelizer.effectiveOutputRateHz();

    // Absolute channel frequency -> NCO offset relative to source center.
    channelizer.setVfoOffsetHz(freqHz - sourceCenterHz);

    if (mode == "AM")          demod = std::make_unique<DemodAM>(ifRate, bandwidthHz);
    else if (mode == "WFM") {
        demod = std::make_unique<DemodWFM>(ifRate, chBw);
        // RDS subcarrier lives in the de-emphasized MPX; sample rate = IF rate.
        rds = std::make_unique<RdsDecoder>(ifRate);
        // FM-stereo composite decoder consumes the PRE-de-emphasis raw MPX tap,
        // sample rate = IF rate.
        stereo = std::make_unique<WfmStereoDecoder>(ifRate);
    }
    else if (mode == "USB")     demod = std::make_unique<DemodSSB>(DemodSSB::Sideband::USB, ifRate, bandwidthHz);
    else if (mode == "LSB")     demod = std::make_unique<DemodSSB>(DemodSSB::Sideband::LSB, ifRate, bandwidthHz);
    else if (mode == "CW")      demod = std::make_unique<DemodSSB>(DemodSSB::Sideband::LSB, ifRate, bandwidthHz);
    else                        demod = std::make_unique<DemodNFM>(ifRate, bandwidthHz);

    // Final audio is always presented at a fixed rate.
    resampler.configure(ifRate, 48000.0, 31);
    // Stereo M/S share the same ifRate->48k parameters so they stay aligned with
    // the mono stream. Only WFM builds the decoder; configuring empty resamplers
    // on other modes is harmless (they are never fed).
    stereoMResampler.configure(ifRate, 48000.0, 31);
    stereoSResampler.configure(ifRate, 48000.0, 31);

    lastSr = sr;
    needsRebuild = false;
}

VfoManager::VfoManager() = default;

void VfoManager::initDefault(double sourceSr, double sourceCenterHz,
                             const QString& mode, double bandwidthHz) {
    channels_.clear();
    nextId_ = 1;
    addCount_ = 0;
    VfoChannel ch;
    ch.id = nextId_++;
    ch.freqHz = sourceCenterHz;
    ch.mode = mode;
    ch.bandwidthHz = (bandwidthHz > 0.0) ? bandwidthHz : defaultBandwidthForMode(mode);
    ch.color = nextColor(addCount_++);
    ch.name = "VFO A";
    ch.needsRebuild = true;
    channels_.push_back(std::move(ch));
    selectedId_ = channels_.back().id;
}

void VfoManager::sourceRateChanged() {
    for (auto& ch : channels_) ch.needsRebuild = true;
}

QColor VfoManager::nextColor(int index) {
    // Distinct, legible-on-dark accent colors (car-UI restrained). Cycled.
    static const char* kPalette[] = {
        "#7CC4FF",  // blue (active default)
        "#5fd08a",  // green
        "#e0b35a",  // amber
        "#ff8fb2",  // pink
        "#c39bff",  // purple
        "#5fe0d0",  // teal
        "#ffb07c",  // orange
        "#e74c3c",  // red
    };
    const int n = static_cast<int>(sizeof(kPalette) / sizeof(kPalette[0]));
    return QColor(QString::fromUtf8(kPalette[index % n]));
}

int VfoManager::addVfo(double freqHz) {
    VfoChannel ch;
    ch.id = nextId_++;
    ch.freqHz = freqHz;
    ch.mode = "NFM";
    ch.bandwidthHz = defaultBandwidthForMode("NFM");
    ch.color = nextColor(addCount_++);
    ch.name = QString("VFO %1").arg(static_cast<char>('A' + (addCount_ - 1) % 26));
    ch.needsRebuild = true;
    channels_.push_back(std::move(ch));
    selectedId_ = channels_.back().id;   // new VFO becomes the listened-to one
    return channels_.back().id;
}

bool VfoManager::removeVfo(int id) {
    if (channels_.size() <= 1) return false;   // must keep at least one
    auto it = std::find_if(channels_.begin(), channels_.end(),
                           [id](const VfoChannel& c) { return c.id == id; });
    if (it == channels_.end()) return false;
    channels_.erase(it);
    if (selectedId_ == id) selectedId_ = channels_.front().id;
    return true;
}

bool VfoManager::selectVfo(int id) {
    if (!hasId(id)) return false;
    selectedId_ = id;
    return true;
}

bool VfoManager::setFreq(int id, double freqHz) {
    VfoChannel* c = channel(id);
    if (!c) return false;
    c->freqHz = freqHz;
    // Frequency change only retunes the NCO; no filter/demod rebuild.
    return true;
}

bool VfoManager::setMode(int id, const QString& mode) {
    VfoChannel* c = channel(id);
    if (!c) return false;
    c->mode = mode;
    c->bandwidthHz = defaultBandwidthForMode(mode);
    c->needsRebuild = true;
    return true;
}

bool VfoManager::setBandwidth(int id, double hz) {
    VfoChannel* c = channel(id);
    if (!c || hz <= 0.0) return false;
    c->bandwidthHz = hz;
    c->needsRebuild = true;
    return true;
}

bool VfoManager::setColor(int id, const QColor& col) {
    VfoChannel* c = channel(id);
    if (!c) return false;
    c->color = col;
    return true;
}

bool VfoManager::hasId(int id) const {
    return channel(id) != nullptr;
}

const VfoChannel* VfoManager::channel(int id) const {
    for (const auto& c : channels_) if (c.id == id) return &c;
    return nullptr;
}
VfoChannel* VfoManager::channel(int id) {
    for (auto& c : channels_) if (c.id == id) return &c;
    return nullptr;
}

const VfoChannel* VfoManager::selected() const { return channel(selectedId_); }
VfoChannel* VfoManager::selected() { return channel(selectedId_); }

const std::vector<float>& VfoManager::process(
        const std::vector<std::complex<float>>& iq,
        double sourceSr, double sourceCenterHz) {
    for (auto& ch : channels_) {
        if (ch.needsRebuild || ch.lastSr != sourceSr) {
            ch.rebuild(sourceSr, sourceCenterHz);
        } else {
            // Cheap continuous-phase NCO retune for frequency / center moves.
            ch.channelizer.setVfoOffsetHz(ch.freqHz - sourceCenterHz);
        }
        if (ch.mode == "ADS-B") {
            // ADS-B has no audio: full-rate IQ is decoded by the engine's own
            // ADSBDecoder (separate path). This channel stays silent.
            ch.audio48k.clear();
            continue;
        }
        auto baseband = ch.channelizer.process(iq);
        if (ch.isDigital()) {
            // Digital channel: feed channelized complex baseband to the Costas
            // demod, pull post-Costas recovered symbols for the constellation.
            // No analog audio -- audio48k stays empty (silent on the speaker).
            ch.recoveredSymbols.clear();
            if (!baseband.empty() && ch.digitalDemod) {
                ch.digitalDemod->process(baseband);
                ch.recoveredSymbols = ch.digitalDemod->takeRecovered();
                ch.lockStatus = ch.digitalDemod->status();
            }
            ch.audio48k.clear();
            continue;
        }
        // POCSAG / m17 digital data-link: no analog audio. Channelized IQ goes
        // straight to the link decoder; decoded messages/calls are appended to
        // the channel's read-only snapshot (drained here, queued for Wave2 UI).
        if (ch.isPocsag() || ch.isM17()) {
            ch.audio48k.clear();
            if (!baseband.empty()) {
                if (ch.isPocsag() && ch.fskDemod) {
                    ch.fskDemod->process(baseband);
                    std::vector<int> bits = ch.fskDemod->takeBits();
                    if (!bits.empty() && ch.pocsag) {
                        ch.pocsag->feed(bits);
                        auto msgs = ch.pocsag->takeMessages();
                        ch.pocsagMessages.insert(ch.pocsagMessages.end(),
                                                 msgs.begin(), msgs.end());
                    }
                } else if (ch.isM17() && ch.m17) {
                    ch.m17->feed(baseband);
                    auto calls = ch.m17->takeCalls();
                    // Only a CRC-verified LSF is a real call. On noise the front
                    // end may false-match a sync and emit crcOk=false garbage;
                    // those are REJECTED frames, not calls, and must never reach
                    // the read-only snapshot (honest empty state).
                    for (auto& c : calls)
                        if (c.crcOk)
                            ch.m17Calls.push_back(std::move(c));
                }
            }
            continue;
        }
        std::vector<float> aif;
        if (!baseband.empty() && ch.demod) {
            aif = ch.demod->process(baseband);
            // WFM only: feed the de-emphasized MPX tap (57 kHz subcarrier still
            // present) into the channel's RDS decoder. Scales with the block so
            // block-sync converges exactly like a hardware receiver.
            if (ch.rds) {
                if (auto* wfm = dynamic_cast<DemodWFM*>(ch.demod.get()))
                    ch.rds->feed(wfm->mpxOut());
            }
            // WFM only: feed the PRE-de-emphasis raw MPX tap to the stereo
            // decoder, then resample its recovered M/S to 48 kHz. Cache the
            // decoder's pilot-lock / blend state for the engine's stereoState
            // signal. M and S share identical filters inside the decoder, so the
            // pair stays sample-aligned; the two resamplers share parameters and
            // get the same IF-block length, so they stay aligned with audio48k.
            if (ch.stereo) {
                if (auto* wfm = dynamic_cast<DemodWFM*>(ch.demod.get())) {
                    ch.stereo->feed(wfm->rawMpxOut());
                    ch.stereoM48k = ch.stereoMResampler.process(ch.stereo->monoOut());
                    ch.stereoS48k = ch.stereoSResampler.process(ch.stereo->sideOut());
                    ch.stereoBlend  = ch.stereo->blend();
                    ch.stereoLock   = ch.stereo->locked();
                    ch.stereoPilot  = ch.stereo->pilotQuality();
                }
            }
        }
        if (!ch.stereo) {
            ch.stereoM48k.clear();
            ch.stereoS48k.clear();
            ch.stereoBlend = 0.0f;
            ch.stereoLock = false;
            ch.stereoPilot = 0.0f;
        }
        ch.audio48k = ch.resampler.process(aif);
        // VOR: tap the 48 kHz composite audio into the radial receiver. take()
        // only returns a FINISHED 2 s measurement once per block; between blocks
        // it hands back a default (all-zero) result, which must NOT wipe a good
        // radial -- so we update the cached snapshot only when a real reading
        // finalized (non-zero coherence, locked, or a decoded Morse ID).
        if (ch.vor && !ch.audio48k.empty()) {
            ch.vor->feed(ch.audio48k);
            VorResult r = ch.vor->take();
            if (r.locked || r.varLevel > 0.0 || r.refLevel > 0.0 ||
                !r.morseId.isEmpty())
                ch.vorResult = r;
        }
    }
    static const std::vector<float> kEmpty;
    const VfoChannel* sel = selected();
    return sel ? sel->audio48k : kEmpty;
}

QVector<VfoMarker> VfoManager::markers() const {
    QVector<VfoMarker> out;
    out.reserve(channels_.size());
    for (const auto& c : channels_)
        out.push_back({c.id, c.freqHz, c.bandwidthHz, c.mode, c.color, c.name,
                       c.id == selectedId_});
    return out;
}

int VfoManager::maxDecimation() const {
    int d = 1;
    for (const auto& c : channels_)
        d = std::max(d, c.channelizer.decimation());
    return std::max(1, d);
}

std::vector<PocsagMessage> VfoManager::pocsagMessages(int channelId) const {
    if (const VfoChannel* c = channel(channelId)) return c->pocsagMessages;
    return {};
}

std::vector<M17Call> VfoManager::m17Calls(int channelId) const {
    if (const VfoChannel* c = channel(channelId)) return c->m17Calls;
    return {};
}

VorResult VfoManager::vorResult(int channelId) const {
    if (const VfoChannel* c = channel(channelId)) return c->vorResult;
    return VorResult{};
}

void VfoManager::clearDigitalOutputs(int channelId) {
    VfoChannel* c = channel(channelId);
    if (!c) return;
    c->pocsagMessages.clear();
    c->m17Calls.clear();
    c->vorResult = VorResult{};
    if (c->pocsag) c->pocsag->reset();
    if (c->m17)   c->m17->reset();
    if (c->vor)   c->vor->reset();
}

} // namespace dsp
} // namespace mbdsdr
