// SPDX-License-Identifier: MIT
// Demodulator + squelch + AGC tests.
#include <QtTest/QtTest>
#include <complex>
#include <vector>
#include <cmath>
#include <numeric>

#include "dsp/demod.h"
#include "dsp/squelch.h"
#include "dsp/agc.h"

using namespace mbdsdr::dsp;

class TestDemod : public QObject {
    Q_OBJECT
private slots:
    void amDemodFinds1k();
    void fmDemodFinds1k();
    void agcStability();
    void squelchGate();
    void rawPassthroughLIQ();
};

// Helper: FFT magnitude at a given bin (simple DFT bin extract)
static float binMag(const std::vector<float>& x, int bin, int N) {
    std::complex<double> s(0,0);
    for (int i = 0; i < N; ++i) {
        double ang = -2*M_PI*bin*i/N;
        s += std::complex<double>(x[i]*std::cos(ang), x[i]*std::sin(ang));
    }
    return static_cast<float>(std::abs(s));
}

void TestDemod::amDemodFinds1k() {
    // Generate AM signal: fc = sr/4, 1kHz audio, m=0.5
    const int N = 8192;
    const double sr = 48000;
    const double fc = sr/4, faudio = 1000, m = 0.5;
    std::vector<std::complex<float>> iq(N);
    for (int i = 0; i < N; ++i) {
        double env = 1.0 + m*std::cos(2*M_PI*faudio*i/sr);
        iq[i] = {static_cast<float>(env*std::cos(2*M_PI*fc*i/sr)),
                 static_cast<float>(env*std::sin(2*M_PI*fc*i/sr))};
    }
    DemodAM am;
    auto audio = am.process(iq);
    // 1 kHz bin in audio (sr=48k, N=8192): bin = 1000*N/sr ≈ 170
    const int targetBin = static_cast<int>(1000.0*N/sr);
    float peak = binMag(audio, targetBin, N);
    float noise = binMag(audio, targetBin+50, N);  // nearby bin as noise ref
    QVERIFY2(peak > noise * 3.0,
             qPrintable(QString("AM: peak %1 noise %2").arg(peak).arg(noise)));
}

void TestDemod::fmDemodFinds1k() {
    const int N = 8192;
    const double sr = 48000, fdev = 3000, faudio = 1000;
    std::vector<std::complex<float>> iq(N);
    double phase = 0;
    for (int i = 0; i < N; ++i) {
        phase += 2*M_PI*fdev/sr * std::sin(2*M_PI*faudio*i/sr);
        iq[i] = {static_cast<float>(std::cos(phase)),
                 static_cast<float>(std::sin(phase))};
    }
    DemodNFM nfm;
    auto audio = nfm.process(iq);
    const int targetBin = static_cast<int>(1000.0*N/sr);
    float peak = binMag(audio, targetBin, N);
    float noise = binMag(audio, targetBin+50, N);
    QVERIFY2(peak > noise * 3.0,
             qPrintable(QString("FM: peak %1 noise %2").arg(peak).arg(noise)));
}

void TestDemod::agcStability() {
    // Ramp amplitude from 0.1 to 1.0 over 4096 samples
    const int N = 4096;
    std::vector<float> in(N);
    for (int i = 0; i < N; ++i) {
        double amp = 0.1 + 0.9 * i / N;
        in[i] = static_cast<float>(amp * std::sin(2*M_PI*1000*i/48000.0));
    }
    Agc agc;
    auto out = agc.process(in);
    // Measure RMS of second half (should be ~target 0.3, stable)
    double sum = 0;
    int cnt = 0;
    for (int i = N/2; i < N; ++i) { sum += out[i]*out[i]; cnt++; }
    double rms = std::sqrt(sum/cnt);
    // Should be around 0.3/sqrt(2) ≈ 0.21, within 3 dB (0.15 to 0.30)
    QVERIFY2(rms > 0.12 && rms < 0.35,
             qPrintable(QString("AGC RMS %1").arg(rms)));
}

void TestDemod::squelchGate() {
    // Loud segment + quiet segment
    const int N = 2048;
    std::vector<float> loud(N/2, 0.8f);
    std::vector<float> quiet(N/2, 0.001f);
    Squelch sq;
    sq.setThresholdDb(-40.0f);
    auto g1 = sq.apply(loud, -5.0f);   // loud -> should open
    auto g2 = sq.apply(quiet, -80.0f); // quiet -> should close
    float rmsLoud = std::sqrt(std::accumulate(g1.begin(), g1.end(), 0.0f,
        [](float a, float b){return a+b*b;}) / g1.size());
    float rmsQuiet = std::sqrt(std::accumulate(g2.begin(), g2.end(), 0.0f,
        [](float a, float b){return a+b*b;}) / g2.size());
    QVERIFY2(rmsLoud > 0.3f, qPrintable(QString("loud RMS %1").arg(rmsLoud)));
    QVERIFY2(rmsQuiet < 0.01f, qPrintable(QString("quiet RMS %1").arg(rmsQuiet)));
}

// RAW direct-listen: channelized complex IQ must pass through as [I,Q,I,Q...]
// interleaved with NO scaling / filtering / state. Even samples == I (Left),
// odd samples == Q (Right), bit-exact on the float values.
void TestDemod::rawPassthroughLIQ() {
    const int N = 512;
    const double ifSr = 48000.0;
    std::vector<std::complex<float>> iq(N);
    for (int i = 0; i < N; ++i) {
        // Deterministic non-trivial I/Q (a rotating phasor at an offset).
        const double ang = 2 * M_PI * 0.13 * i;
        iq[i] = {static_cast<float>(0.6 * std::cos(ang)),
                 static_cast<float>(0.4 * std::sin(ang))};
    }
    DemodRaw raw(ifSr);
    QCOMPARE(raw.name(), QStringLiteral("RAW"));
    QCOMPARE(raw.outputSampleRate(), ifSr);

    auto out = raw.process(iq);
    QCOMPARE(out.size(), static_cast<std::size_t>(N * 2));
    for (int i = 0; i < N; ++i) {
        QCOMPARE(out[2 * i],     iq[i].real());  // even -> Left  = I
        QCOMPARE(out[2 * i + 1], iq[i].imag());  // odd  -> Right = Q
    }
    // Stateless: a second block streams identically (reset() is a no-op).
    raw.reset();
    auto out2 = raw.process(iq);
    QCOMPARE(out2.size(), out.size());
    for (std::size_t k = 0; k < out.size(); ++k)
        QCOMPARE(out2[k], out[k]);
}

QTEST_MAIN(TestDemod)
#include "test_demod.moc"