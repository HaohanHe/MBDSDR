// SPDX-License-Identifier: MIT
#include "vfo_manager.h"

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace dsp {

// Default IF bandwidth for a mode when the user picks it from the combo. Kept
// in lock-step with SpectrumEngine::setDemodMode() and MainWindow's bw presets.
static double defaultBandwidthForMode(const QString& mode) {
    if (mode == "AM")   return 8000.0;
    if (mode == "WFM")  return 200000.0;
    if (mode == "CW")   return 500.0;
    if (mode == "NFM")  return 12500.0;
    if (mode == "BPSK" || mode == "QPSK") return 3000.0;
    return 2400.0; // USB / LSB / anything else
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
        resampler.configure(ifRate, 48000.0, 31);
        recoveredSymbols.clear();
        lastSr = sr;
        needsRebuild = false;
        return;
    }
    digitalDemod.reset();

    channelizer.configure(sr, ifTarget, chBw, 31);
    const double ifRate = channelizer.effectiveOutputRateHz();

    // Absolute channel frequency -> NCO offset relative to source center.
    channelizer.setVfoOffsetHz(freqHz - sourceCenterHz);

    if (mode == "AM")          demod = std::make_unique<DemodAM>(ifRate, bandwidthHz);
    else if (mode == "WFM")    demod = std::make_unique<DemodWFM>(ifRate, chBw);
    else if (mode == "USB")     demod = std::make_unique<DemodSSB>(DemodSSB::Sideband::USB, ifRate, bandwidthHz);
    else if (mode == "LSB")     demod = std::make_unique<DemodSSB>(DemodSSB::Sideband::LSB, ifRate, bandwidthHz);
    else if (mode == "CW")      demod = std::make_unique<DemodSSB>(DemodSSB::Sideband::LSB, ifRate, bandwidthHz);
    else                        demod = std::make_unique<DemodNFM>(ifRate, bandwidthHz);

    // Final audio is always presented at a fixed rate.
    resampler.configure(ifRate, 48000.0, 31);

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
        std::vector<float> aif;
        if (!baseband.empty() && ch.demod) aif = ch.demod->process(baseband);
        ch.audio48k = ch.resampler.process(aif);
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

} // namespace dsp
} // namespace mbdsdr
