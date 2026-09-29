// SPDX-License-Identifier: MIT
// Integration test: every UI control that calls an engine setter must actually
// land in the DSP state. Uses TestSignalSource (no hardware, no network).
#include <QtTest/QtTest>
#include <cmath>
#include "dsp/spectrum_engine.h"

using namespace mbdsdr::dsp;

class TestEngineIntegration : public QObject {
    Q_OBJECT
private slots:
    void statePropagates();
    void vfoOffsetInBandNoRetuneUntilEdge();
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

    // Adversarial: bogus sample rate / bandwidth must not crash rebuildDemod.
    eng.onSetSampleRate(0);     // was divide-by-zero in channelizer
    eng.onSetSampleRate(2.4e6);
    eng.setBandwidth(0);        // zero bandwidth
    eng.setBandwidth(8000.0);   // restore
}

// *** NOT HARDWARE / 非硬件合成 ***
// In-band IF-offset move for the active VFO (SDR++-style). Dragging the
// selected VFO inside the capture band must retune ONLY the channelizer NCO
// offset and leave the RTL tuner parked; only when the target crosses the band
// edge do we genuinely retune the source. Runs against the offline
// TestSignalSource (synthetic IQ): center=98.5 MHz, sr=2.4 MHz, so the usable
// half-band = 2.4e6*0.5*0.85 = 1.02 MHz.
void TestEngineIntegration::vfoOffsetInBandNoRetuneUntilEdge() {
    SpectrumEngine eng;
    eng.onSetSampleRate(2.4e6);
    eng.onSetCenterFreq(98.5e6);
    QCOMPARE(eng.centerFreq(), 98.5e6);
    const int id = eng.selectedVfoId();
    QVERIFY(id != 0);

    // 1) In-band move to +500 kHz: |500k| < 1.02 MHz -> tuner stays parked.
    const bool r1 = eng.vfoSetOffset(id, 98.5e6 + 500e3);
    QVERIFY(!r1);
    QCOMPARE(eng.centerFreq(), 98.5e6);          // tuner NOT retuned
    auto mk1 = eng.vfoMarkers();
    bool found = false;
    for (const auto& m : mk1) {
        if (m.id == id) {
            found = true;
            QVERIFY2(std::abs(m.centerOffsetHz - 500000.0) < 1.0,
                     "in-band offset must be +500 kHz");
            QCOMPARE(m.referenceHz, 98.5e6);
        }
    }
    QVERIFY(found);

    // 2) Move to +1.5 MHz: |1.5 MHz| > 1.02 MHz -> genuine retune to target.
    const bool r2 = eng.vfoSetOffset(id, 98.5e6 + 1.5e6);
    QVERIFY(r2);
    QCOMPARE(eng.centerFreq(), 100.0e6);         // tuner retuned to the target
    auto mk2 = eng.vfoMarkers();
    found = false;
    for (const auto& m : mk2) {
        if (m.id == id) {
            found = true;
            QVERIFY2(std::abs(m.centerOffsetHz - 0.0) < 1.0,
                     "after retune the VFO should sit back at offset ~0");
            QCOMPARE(m.referenceHz, 100.0e6);
        }
    }
    QVERIFY(found);
}

QTEST_MAIN(TestEngineIntegration)
#include "test_engine_integration.moc"
