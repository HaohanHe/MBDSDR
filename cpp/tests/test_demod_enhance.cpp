// SPDX-License-Identifier: MIT
// Phase12 gap-closing: in-chain AM carrier AGC, NFM bandwidth-matched FIR,
// and real GFSK/FSK decision accuracy. Deterministic -- synthetic signals only,
// no hardware, no mocks.
#include <QtTest/QtTest>
#include <complex>
#include <vector>
#include <cmath>
#include <cstdint>
#include <algorithm>

#include "dsp/demod.h"
#include "dsp/fsk_demod.h"

using namespace mbdsdr::dsp;

class TestDemodEnhance : public QObject {
    Q_OBJECT
private slots:
    void amAgcNormalizesCarrierStep();
    void nuttallLpfBandwidthSelective();
    void nfmSetBandwidthRedesigns();
    void fskDecisionAccuracy();
};

// RMS of out[a..b)
static double rms(const std::vector<float>& x, int a, int b) {
    double s = 0.0; int c = 0;
    for (int i = a; i < b; ++i) { s += x[i] * x[i]; ++c; }
    return c ? std::sqrt(s / c) : 0.0;
}

// Measured gain |H(f)| of a NuttallLpf on a sine at fHz (tail RMS ratio).
static double measureGain(NuttallLpf& f, double fHz, double sr, int N = 16384) {
    f.reset();
    std::vector<float> in(N), out;
    for (int i = 0; i < N; ++i) in[i] = static_cast<float>(std::sin(2 * M_PI * fHz * i / sr));
    f.process(in, out);
    return rms(out, 700, N) / rms(in, 700, N);
}

// ---- G1: in-chain carrier AGC normalizes a carrier-level step --------------
void TestDemodEnhance::amAgcNormalizesCarrierStep() {
    const double sr = 48000.0;
    const int N = 16384;
    const double fc = sr / 4.0;

    auto run = [&](bool agcOn) -> std::pair<double,double> {
        DemodAM am(sr, 10000.0);
        am.setCarrierAgcEnabled(agcOn);
        std::vector<std::complex<float>> iq(N);
        for (int i = 0; i < N; ++i) {
            const double A = (i < N / 2) ? 0.2 : 1.5;       // 7.5x carrier step
            const double env = A * (1.0 + 0.3 * std::cos(2 * M_PI * 1000.0 * i / sr));
            iq[i] = {static_cast<float>(env * std::cos(2 * M_PI * fc * i / sr)),
                     static_cast<float>(env * std::sin(2 * M_PI * fc * i / sr))};
        }
        auto out = am.process(iq);
        const double lo = rms(out, 1500, N / 2 - 800);        // weak half (A=0.2)
        const double hi = rms(out, N / 2 + 1000, N - 800);   // strong half (A=1.5)
        return {lo, hi};
    };

    auto [agcLo, agcHi] = run(true);
    auto [offLo, offHi] = run(false);

    QVERIFY2(agcLo > 0.03 && agcHi > 0.03,
             qPrintable(QString("AGC halves too small: %1 %2").arg(agcLo).arg(agcHi)));
    // With carrier AGC the 7.5x carrier step must NOT show up as a 7.5x volume jump.
    const double agcRatio = std::max(agcLo, agcHi) / std::min(agcLo, agcHi);
    QVERIFY2(agcRatio < 3.0,
             qPrintable(QString("AGC did not normalize: lo=%1 hi=%2 ratio=%3")
                        .arg(agcLo).arg(agcHi).arg(agcRatio)));
    // Control: with AGC off, the strong half really is ~7.5x louder.
    const double offRatio = offHi / std::max(offLo, 1e-6);
    QVERIFY2(offRatio > 3.0,
             qPrintable(QString("Control AGC-off should be louder on strong side: %1")
                        .arg(offRatio)));
}

// ---- G3: Nuttall LPF is bandwidth-selective (12.5k vs 25k) ----------------
void TestDemodEnhance::nuttallLpfBandwidthSelective() {
    const double sr = 48000.0;

    // 12.5 kHz channel: cutoff 6.25k, trans 1.25k -> stopband edge 7.5k.
    NuttallLpf f12; f12.design(12500.0 / 2.0, 12500.0 * 0.1, sr);
    const double g1k_12  = measureGain(f12, 1000.0, sr);
    const double g9k_12  = measureGain(f12, 9000.0, sr);
    QVERIFY2(g1k_12 > 0.8, qPrintable(QString("12.5k passband @1k = %1").arg(g1k_12)));
    QVERIFY2(g9k_12 < 0.02,
             qPrintable(QString("12.5k stopband @9k should be >34dB down, got %1")
                        .arg(g9k_12)));

    // 25 kHz channel: cutoff 12.5k, trans 2.5k -> 9k now sits IN the passband.
    NuttallLpf f25; f25.design(25000.0 / 2.0, 25000.0 * 0.1, sr);
    const double g9k_25 = measureGain(f25, 9000.0, sr);
    QVERIFY2(g9k_25 > 0.8,
             qPrintable(QString("25k passband @9k should pass, got %1").arg(g9k_25)));
}

// ---- G3: DemodNFM::setBandwidth re-designs the FIR on the fly -------------
void TestDemodEnhance::nfmSetBandwidthRedesigns() {
    const double sr = 48000.0;
    DemodNFM nfm(sr, 12500.0);
    // A 1 kHz-modulated FM tone must always survive (always in passband).
    auto recovered1k = [&]() {
        const int N = 8192;
        std::vector<std::complex<float>> iq(N);
        double phase = 0;
        for (int i = 0; i < N; ++i) {
            phase += 2 * M_PI * 3000.0 / sr * std::sin(2 * M_PI * 1000.0 * i / sr);
            iq[i] = {static_cast<float>(std::cos(phase)), static_cast<float>(std::sin(phase))};
        }
        auto out = nfm.process(iq);
        double s = 0; int c = 0;
        for (int i = 600; i < N; ++i) {
            // DFT bin at 1 kHz
            double ang = -2 * M_PI * 1000.0 * i / sr;
            s += out[i] * std::cos(ang); c++;
        }
        return std::abs(s / c);
    };
    const double a = recovered1k();
    nfm.setBandwidth(25000.0);   // hot re-design
    const double b = recovered1k();
    QVERIFY2(a > 0.01 && b > 0.01,
             qPrintable(QString("1k audio lost after setBandwidth: %1 %2").arg(a).arg(b)));
    // Switching bandwidth must not throw / must keep producing samples.
    nfm.setBandwidth(6250.0);
    std::vector<std::complex<float>> iq(512, {1.f, 0.f});
    auto out = nfm.process(iq);
    QCOMPARE(out.size(), std::size_t(512));
}

// ---- G5: real FSK receiver recovers an injected bit stream ----------------
void TestDemodEnhance::fskDecisionAccuracy() {
    const double sr = 48000.0, sym = 1200.0, dev = 600.0;
    const int sps = static_cast<int>(sr / sym);
    const int nBits = 300;

    // Deterministic TX bits (LCG).
    std::vector<int> tx(nBits);
    std::uint32_t s = 12345u;
    for (int i = 0; i < nBits; ++i) {
        s = s * 1103515245u + 12345u;
        tx[i] = static_cast<int>((s >> 16) & 1u);
    }

    // Continuous-phase 2-FSK at complex baseband.
    std::vector<std::complex<float>> iq(nBits * sps);
    double phase = 0.0;
    for (int b = 0; b < nBits; ++b) {
        const double f = tx[b] ? dev : -dev;
        for (int k = 0; k < sps; ++k) {
            phase += 2 * M_PI * f / sr;
            iq[b * sps + k] = {static_cast<float>(std::cos(phase)),
                               static_cast<float>(std::sin(phase))};
        }
    }

    FskDemodConfig cfg;
    cfg.sampleRateHz = sr; cfg.symbolRateBd = sym; cfg.deviationHz = dev;
    FskDemod demod(cfg);
    demod.process(iq);
    auto bits = demod.takeBits();
    QVERIFY2(bits.size() > static_cast<std::size_t>(nBits - 5),
             qPrintable(QString("only %1 symbols recovered").arg(bits.size())));

    // Align against the fixed group-delay offset (BB LPF + discriminator adds a
    // few symbols of delay; the recovered stream can lead or lag the TX bits by
    // a small integer) and measure BER on the settled tail.
    double bestBer = 1.0; int bestLag = 0;
    for (int lag = -8; lag <= 8; ++lag) {
        int err = 0, cnt = 0;
        for (int i = 20; i + 1 < static_cast<int>(bits.size()); ++i) {
            const int j = i + lag;
            if (j < 0 || j >= nBits) continue;
            if (bits[i] != tx[j]) ++err;
            ++cnt;
        }
        const double ber = cnt ? static_cast<double>(err) / cnt : 1.0;
        if (ber < bestBer) { bestBer = ber; bestLag = lag; }
    }
    QVERIFY2(bestBer < 0.01,
             qPrintable(QString("FSK BER %1 at lag %2 (%3 bits recovered)")
                        .arg(bestBer).arg(bestLag).arg(bits.size())));
}

QTEST_MAIN(TestDemodEnhance)
#include "test_demod_enhance.moc"
