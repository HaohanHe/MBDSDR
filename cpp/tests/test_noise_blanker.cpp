// SPDX-License-Identifier: MIT
#include <QtTest/QtTest>
#include <vector>
#include <complex>
#include <cmath>
#include "dsp/noise_blanker.h"

using namespace mbdsdr::dsp;

class TestNoiseBlanker : public QObject {
    Q_OBJECT
private slots:
    void suppressesImpulses();
    void passthroughWhenOff();
};

void TestNoiseBlanker::suppressesImpulses() {
    // Quiet noise floor + a few big impulse spikes.
    std::vector<std::complex<float>> iq(512, std::complex<float>(0.01f, 0.01f));
    const float before = std::abs(iq[100]);
    iq[100] = std::complex<float>(3.0f, 0.0f);   // huge impulse
    iq[300] = std::complex<float>(-2.5f, 1.0f);

    NoiseBlanker nb;
    nb.setEnabled(true);
    nb.process(iq);

    // Impulses should be pulled down toward the quiet floor.
    QVERIFY(std::abs(iq[100]) < 0.5f);
    QVERIFY(std::abs(iq[300]) < 0.5f);
    (void)before;
}

void TestNoiseBlanker::passthroughWhenOff() {
    std::vector<std::complex<float>> iq(512, std::complex<float>(0.01f, 0.01f));
    iq[100] = std::complex<float>(3.0f, 0.0f);
    const float expected = std::abs(iq[100]);
    NoiseBlanker nb;   // disabled by default
    nb.process(iq);
    QCOMPARE(std::abs(iq[100]), expected);
}

QTEST_MAIN(TestNoiseBlanker)
#include "test_noise_blanker.moc"
