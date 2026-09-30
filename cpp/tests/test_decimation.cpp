// SPDX-License-Identifier: MIT
// Frontend software-decimation self-check: prove the decimator is a REAL
// anti-aliasing low-pass + integer decimation (not bare decimation which would
// fold out-of-band energy back into the band).
#include <QtTest>
#include <vector>
#include <complex>
#include <cmath>
#include "dsp/channelizer.h"

using namespace mbdsdr;

class TestDecimation : public QObject {
    Q_OBJECT
private slots:
    void integerRateReduction();
    void outOfBandToneIsNotAliased();
};

static std::vector<std::complex<float>> tone(double fNorm, int n) {
    std::vector<std::complex<float>> x(n);
    for (int i = 0; i < n; ++i)
        x[i] = std::exp(std::complex<double>(0, 2.0 * M_PI * fNorm * i));
    return x;
}

void TestDecimation::integerRateReduction() {
    // 1 MHz -> 250 kHz => D = 4.
    dsp::Channelizer dec;
    dec.configure(1.0e6, 250.0e3, (250.0e3 / 2.0) * 0.80);
    QCOMPARE(dec.decimation(), 4);
    auto out = dec.process(tone(0.0, 4096));
    // Output length ~= input / D (filter tail makes it a little smaller).
    QVERIFY(out.size() > 0);
    QVERIFY(out.size() <= 4096 / 4 + 64);
    QVERIFY(out.size() >= 4096 / 4 - 64);
}

void TestDecimation::outOfBandToneIsNotAliased() {
    // Decimate 1 MHz -> 250 kHz (D=4). Decimated Nyquist = 125 kHz.
    dsp::Channelizer dec;
    dec.configure(1.0e6, 250.0e3, (250.0e3 / 2.0) * 0.80);

    // A tone INSIDE the passband (20 kHz @ 1 MHz) must survive.
    auto inBand = tone(20.0e3 / 1.0e6, 16384);
    auto ob = dec.process(inBand);
    double pb = 0; for (auto c : ob) pb += std::norm(c);
    pb /= ob.size();

    // A tone ABOVE the decimated Nyquist (200 kHz @ 1 MHz). Bare decimation
    // would alias this back into band; the low-pass must attenuate it hard.
    dec.reset();
    auto outOf = tone(200.0e3 / 1.0e6, 16384);
    auto oo = dec.process(outOf);
    double po = 0; for (auto c : oo) po += std::norm(c);
    po /= oo.size();

    // Out-of-band energy must be at least 30 dB below in-band energy.
    const double dB = 10.0 * std::log10((po + 1e-12) / (pb + 1e-12));
    QVERIFY2(dB < -30.0, qPrintable(
        QString("out-of-band not attenuated enough: %1 dB").arg(dB)));
}

QTEST_MAIN(TestDecimation)
#include "test_decimation.moc"
