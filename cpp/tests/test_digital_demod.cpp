// SPDX-License-Identifier: MIT
//
// NOT HARDWARE / 非硬件合成信号 -- unit test.
//
// This file synthesizes its own BPSK / QPSK IQ in software (no SDR hardware,
// no recorded over-the-air data). It adds a small Gaussian noise floor, a
// fixed carrier frequency offset and a fixed phase offset, then runs the real
// DigitalDemod carrier/timing/decision chain and compares differentially
// decoded bits against the transmit payload. After the pull-in transient the
// bit error rate must be ~0.

#include "dsp/digital_demod.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <complex>
#include <random>
#include <string>

using mbdsdr::dsp::DigMode;
using mbdsdr::dsp::DigitalDemod;
using mbdsdr::dsp::DigitalDemodConfig;

namespace {

// Deterministic PRNG so the test is repeatable.
std::mt19937 rng(20260928u);

std::vector<int> randomBits(size_t n) {
    std::uniform_int_distribution<int> b(0, 1);
    std::vector<int> v(n);
    for (auto& x : v) x = b(rng);
    return v;
}

// Rectangular (NRZ) pulse shaping: repeat each symbol `sps` times.
std::vector<std::complex<float>>
pulseShape(const std::vector<std::complex<float>>& syms, int sps) {
    std::vector<std::complex<float>> out;
    out.reserve(syms.size() * sps);
    for (auto s : syms) {
        for (int i = 0; i < sps; ++i) out.push_back(s);
    }
    return out;
}

// Apply fixed carrier frequency offset + phase offset + complex Gaussian noise.
void distort(std::vector<std::complex<float>>& x,
             double fsHz, double freqOffsetHz, double phaseRad, double noiseStd) {
    std::normal_distribution<float> g(0.0f, noiseStd);
    double ph = phaseRad;
    const double dph = 2.0 * M_PI * freqOffsetHz / fsHz;
    for (auto& s : x) {
        std::complex<float> osc(std::cos(ph), std::sin(ph));
        s *= osc;
        ph += dph;
        s += std::complex<float>(g(rng), g(rng));
    }
}

int runOne(DigMode mode, const std::string& name,
           double freqOffsetHz, double phaseRad, double noiseStd) {
    DigitalDemodConfig cfg;
    cfg.sampleRateHz = 48000.0;
    cfg.symbolRateBd = 2400.0;          // SPS = 20
    cfg.mode = mode;
    DigitalDemod demod(cfg);

    const int bitsPerSymbol = (mode == DigMode::QPSK) ? 2 : 1;
    const size_t nPayloadBits = 4000 * bitsPerSymbol;
    std::vector<int> txBits = randomBits(nPayloadBits);

    // Transmitter-side differential encode -> unit symbols.
    std::vector<std::complex<float>> syms = DigitalDemod::diffEncode(txBits, mode);

    const int sps = static_cast<int>(cfg.sampleRateHz / cfg.symbolRateBd);
    std::vector<std::complex<float>> iq = pulseShape(syms, sps);
    distort(iq, cfg.sampleRateHz, freqOffsetHz, phaseRad, noiseStd);

    // Feed in small chunks like a real chain would.
    const size_t chunk = 2048;
    for (size_t off = 0; off < iq.size(); off += chunk) {
        size_t n = std::min(chunk, iq.size() - off);
        std::vector<std::complex<float>> block(iq.data() + off, iq.data() + off + n);
        demod.process(block);
    }

    std::vector<std::complex<float>> decisions = demod.takeDecisions();
    std::vector<int> rxBits = demod.takeDecodedBits();
    auto st = demod.status();

    // diffDecode drops the reference symbol; decoded bits should align with
    // txBits starting at index 0. Allow a pull-in warmup.
    const size_t warmup = 300 * bitsPerSymbol;   // skip transient
    size_t errors = 0;
    size_t compared = 0;
    size_t n = std::min(rxBits.size(), txBits.size());
    for (size_t i = warmup; i < n; ++i) {
        if (rxBits[i] != txBits[i]) ++errors;
        ++compared;
    }
    double ber = compared ? static_cast<double>(errors) / compared : 1.0;

    std::printf("[%s] symbols=%zu decisions=%zu bits=%zu/%zu "
                "carrierLocked=%d symbolLocked=%d EVM=%.2f%% estOff=%.1fHz "
                "BER(after warmup)=%zu/%zu = %.2e\n",
                name.c_str(), st.symbolsProcessed, decisions.size(),
                rxBits.size(), txBits.size(),
                (int)st.carrierLocked, (int)st.symbolLocked,
                st.evmPercent, st.freqOffsetHz,
                errors, compared, ber);

    bool ok = st.carrierLocked && st.symbolLocked && ber < 1e-3;
    std::printf("  -> %s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}

} // namespace

int main() {
    int fails = 0;
    // Small offsets inside loop pull-in range; mild SNR (~15 dB).
    fails += runOne(DigMode::BPSK, "BPSK +150Hz +0.6rad n=0.18",  150.0,  0.6, 0.18);
    fails += runOne(DigMode::BPSK, "BPSK -120Hz -0.9rad n=0.18", -120.0, -0.9, 0.18);
    fails += runOne(DigMode::QPSK, "QPSK +100Hz +0.4rad n=0.18", 100.0,  0.4, 0.18);
    fails += runOne(DigMode::QPSK, "QPSK  -80Hz -0.5rad n=0.18", -80.0, -0.5, 0.18);

    if (fails == 0) {
        std::printf("ALL DIGITAL DEMOD TESTS PASSED\n");
        return 0;
    }
    std::printf("%d CASE(S) FAILED\n", fails);
    return 1;
}
