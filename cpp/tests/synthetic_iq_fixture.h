// SPDX-License-Identifier: MIT
//
// *** SYNTHETIC TEST FIXTURE -- 仅验证链路，非真实接收 (NOT REAL RECEPTION) ***
//
// Deterministic synthetic IQ generators + a cf32_le raw writer for the
// end-to-end "demodulate -> sound" harness. These exist ONLY to drive the REAL
// SpectrumEngine (FileSource.openRaw -> channelizer -> demod -> AGC/squelch ->
// MemoryAudioSink) with a KNOWN signal so we can assert the recovered audio pitch
// and level. They are NEVER wired into the production receive path and MUST NOT
// be presented as a real antenna capture.
//
//   - writeNfmIq()  : narrowband FM, 1 kHz voice tone, controlled deviation.
//   - writeWfmIq()  : broadcast FM, 1 kHz tone + optional 19 kHz pilot MPX.
//   - writeAmIq()   : AM, known modulating tone on the envelope.
//   - writeNoiseIq(): pure band-limited Gaussian noise (no signal) -- the
//                     "quiet background" control.
//
// The signal sits at baseband DC (the engine's default VFO is offset 0),
// mirroring a receiver already tuned to the channel under test.
#pragma once

#include <complex>
#include <vector>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <random>

#include <QString>

namespace mbdsdr {
namespace dsp {
namespace fixture {

// Write `iq` as little-endian complex float32 (cf32_le) raw IQ. Returns true on
// success. This is exactly the format FileSource::openRaw streams.
inline bool writeRawCf32(const QString& path,
                         const std::vector<std::complex<float>>& iq) {
    FILE* f = std::fopen(path.toLocal8Bit().constData(), "wb");
    if (!f) return false;
    std::size_t wr = std::fwrite(iq.data(), sizeof(std::complex<float>),
                                 iq.size(), f);
    std::fclose(f);
    return wr == iq.size();
}

// Narrowband FM: carrier at `carrierOffsetHz` from baseband DC (offset chosen to
// sit clear of the IQ-front-end DC notch, mirroring how a real receiver tunes a
// channel off the DC spike), modulated by a 1 kHz tone with `deviationHz` peak
// frequency deviation. `noiseAmp` adds additive Gaussian noise on I/Q (0 = clean).
inline std::vector<std::complex<float>>
makeNfmIq(double sampleRateHz, double seconds, double toneHz,
          double deviationHz, double noiseAmp, double carrierOffsetHz = 50000.0,
          unsigned seed = 12345) {
    const long n = static_cast<long>(sampleRateHz * seconds);
    std::vector<std::complex<float>> x(n);
    std::mt19937 rng(seed);
    std::normal_distribution<float> gauss(0.0f, 1.0f);
    double phase = 0.0;
    for (long i = 0; i < n; ++i) {
        const double t = static_cast<double>(i) / sampleRateHz;
        // instantaneous frequency = carrier offset + voice deviation.
        const double inst = carrierOffsetHz + deviationHz * std::sin(2.0 * M_PI * toneHz * t);
        phase += 2.0 * M_PI * inst / sampleRateHz;
        const float re = static_cast<float>(std::cos(phase))
                       + noiseAmp * gauss(rng);
        const float im = static_cast<float>(std::sin(phase))
                       + noiseAmp * gauss(rng);
        x[i] = std::complex<float>(re, im);
    }
    return x;
}

// Narrowband FM with an OPTIONAL embedded sub-audible CTCSS PL tone. The
// instantaneous frequency deviation is the SUM of a (voice) tone and an optional
// sub-audible tone: inst = carrier + voiceDev*sin(2π·voice·t) + subDev*sin(2π·
// subTone·t). After the NFM discriminator both components appear in the mono
// audio -- exactly how a real PL transmitter embeds the sub-audible tone. Set
// voiceDev=0 / subDev=0 to omit that component. *** SYNTHETIC, NOT HARDWARE ***
inline std::vector<std::complex<float>>
makeNfmCtcssIq(double sampleRateHz, double seconds,
               double voiceToneHz, double voiceDev,
               double subToneHz, double subDev,
               double noiseAmp, double carrierOffsetHz = 50000.0,
               unsigned seed = 12345) {
    const long n = static_cast<long>(sampleRateHz * seconds);
    std::vector<std::complex<float>> x(n);
    std::mt19937 rng(seed);
    std::normal_distribution<float> gauss(0.0f, 1.0f);
    double phase = 0.0;
    for (long i = 0; i < n; ++i) {
        const double t = static_cast<double>(i) / sampleRateHz;
        double inst = carrierOffsetHz;
        if (voiceDev > 0.0)
            inst += voiceDev * std::sin(2.0 * M_PI * voiceToneHz * t);
        if (subDev > 0.0)
            inst += subDev * std::sin(2.0 * M_PI * subToneHz * t);
        phase += 2.0 * M_PI * inst / sampleRateHz;
        const float re = static_cast<float>(std::cos(phase))
                       + noiseAmp * gauss(rng);
        const float im = static_cast<float>(std::sin(phase))
                       + noiseAmp * gauss(rng);
        x[i] = std::complex<float>(re, im);
    }
    return x;
}

// Broadcast FM: carrier at `carrierOffsetHz`, modulating baseband m(t) = tone
// (1 kHz) plus an optional 19 kHz pilot tone, frequency-modulated with 75 kHz
// peak deviation.
inline std::vector<std::complex<float>>
makeWfmIq(double sampleRateHz, double seconds, double toneHz,
          double deviationHz, double pilotAmp, double noiseAmp,
          double carrierOffsetHz = 50000.0, unsigned seed = 22221) {
    const long n = static_cast<long>(sampleRateHz * seconds);
    std::vector<std::complex<float>> x(n);
    std::mt19937 rng(seed);
    std::normal_distribution<float> gauss(0.0f, 1.0f);
    double phase = 0.0;
    for (long i = 0; i < n; ++i) {
        const double t = static_cast<double>(i) / sampleRateHz;
        double m = std::sin(2.0 * M_PI * toneHz * t);
        if (pilotAmp > 0.0)
            m += pilotAmp * std::sin(2.0 * M_PI * 19000.0 * t);
        const double inst = carrierOffsetHz + deviationHz * m;
        phase += 2.0 * M_PI * inst / sampleRateHz;
        const float re = static_cast<float>(std::cos(phase))
                       + noiseAmp * gauss(rng);
        const float im = static_cast<float>(std::sin(phase))
                       + noiseAmp * gauss(rng);
        x[i] = std::complex<float>(re, im);
    }
    return x;
}

// AM: carrier at `carrierOffsetHz` with envelope (1 + modDepth * tone).
inline std::vector<std::complex<float>>
makeAmIq(double sampleRateHz, double seconds, double toneHz,
         double modDepth, double noiseAmp, double carrierOffsetHz = 50000.0,
         unsigned seed = 33331) {
    const long n = static_cast<long>(sampleRateHz * seconds);
    std::vector<std::complex<float>> x(n);
    std::mt19937 rng(seed);
    std::normal_distribution<float> gauss(0.0f, 1.0f);
    for (long i = 0; i < n; ++i) {
        const double t = static_cast<double>(i) / sampleRateHz;
        const float env = static_cast<float>(1.0 + modDepth *
                            std::sin(2.0 * M_PI * toneHz * t));
        const double c = std::cos(2.0 * M_PI * carrierOffsetHz * t);
        const double s = std::sin(2.0 * M_PI * carrierOffsetHz * t);
        const float re = static_cast<float>(env * c) + noiseAmp * gauss(rng);
        const float im = static_cast<float>(env * s) + noiseAmp * gauss(rng);
        x[i] = std::complex<float>(re, im);
    }
    return x;
}

// Pure additive Gaussian noise, no signal at all -- the quiet-background
// control. `noiseAmp` is the per-sample std-dev on each of I/Q.
inline std::vector<std::complex<float>>
makeNoiseIq(double sampleRateHz, double seconds, double noiseAmp,
            unsigned seed = 99991) {
    const long n = static_cast<long>(sampleRateHz * seconds);
    std::vector<std::complex<float>> x(n);
    std::mt19937 rng(seed);
    std::normal_distribution<float> gauss(0.0f, 1.0f);
    for (long i = 0; i < n; ++i)
        x[i] = std::complex<float>(noiseAmp * gauss(rng),
                                   noiseAmp * gauss(rng));
    return x;
}

} // namespace fixture
} // namespace dsp
} // namespace mbdsdr
