// SPDX-License-Identifier: MIT
// Integration test: every UI control that calls an engine setter must actually
// land in the DSP state. Uses TestSignalSource (no hardware, no network).
#include <QtTest/QtTest>
#include "dsp/spectrum_engine.h"

using namespace mbdsdr::dsp;

class TestEngineIntegration : public QObject {
    Q_OBJECT
private slots:
    void statePropagates();
};

void TestEngineIntegration::statePropagates() {
    SpectrumEngine eng;
    // Defaults.
    QCOMPARE(eng.fftSize(), 2048);

    // FFT size.
    eng.setFftSize(4096);
    QCOMPARE(eng.fftSize(), 4096);

    // Demod mode + bandwidth preset.
    eng.setDemodMode("WFM");
    QCOMPARE(eng.demodMode(), QString("WFM"));
    QVERIFY(eng.bandwidth() >= 190000);   // WFM preset ~200k

    eng.setBandwidth(7500.0);
    QCOMPARE(eng.bandwidth(), 7500.0);

    // Window / average / noise blanker reach the DSP objects.
    eng.setWindowType(1);   // Flattop
    QCOMPARE(eng.windowType(), 1);
    eng.setAverageMode(2);  // Fast
    QCOMPARE(eng.averageMode(), 2);
    eng.setNoiseBlanker(true);
    QVERIFY(eng.noiseBlankerEnabled());
    eng.setNoiseBlanker(false);
    QVERIFY(!eng.noiseBlankerEnabled());

    // Center / sample rate / gain forward to the live source (no crash).
    eng.onSetCenterFreq(145.0e6);
    eng.onSetSampleRate(2.4e6);
    eng.onSetGain(20.0);
    // Squelch threshold/enabled no-op-crash.
    eng.setSquelchThreshold(-30.0f);
    eng.setSquelchEnabled(true);
}

QTEST_MAIN(TestEngineIntegration)
#include "test_engine_integration.moc"
