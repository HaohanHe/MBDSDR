// SPDX-License-Identifier: MIT
// Integration test: every UI control that calls an engine setter must actually
// land in the DSP state. Uses TestSignalSource (no hardware, no network).
#include <QtTest/QtTest>
#include <QSignalSpy>
#include <cmath>
#include "dsp/spectrum_engine.h"
#include "dsp/memory_audio_sink.h"

using namespace mbdsdr::dsp;

class TestEngineIntegration : public QObject {
    Q_OBJECT
private slots:
    // Phase21: the engine no longer auto-falls back to the synthetic test source.
    // These cases exercise the offline TestSignalSource, so opt in explicitly
    // BEFORE any engine is constructed (the flag is read in the ctor).
    void initTestCase();
    void statePropagates();
    void vfoOffsetInBandNoRetuneUntilEdge();
    void wfmStereoEndToEnd();
};

void TestEngineIntegration::initTestCase() {
    qputenv("MBDSDR_TEST_SOURCE", "1");   // explicit synthetic offline source
}

// DFT-bin power of `x` at frequency `fHz` (real cos/sin correlation), sampled
// at 48 kHz. Pure Goertzel-style magnitude squared -- no FFT dependency.
static double binPower(const std::vector<float>& x, double fHz) {
    if (x.empty()) return 0.0;
    const double w = 2.0 * M_PI * fHz / 48000.0;
    double re = 0.0, im = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        const double c = std::cos(w * i), s = std::sin(w * i);
        re += x[i] * c;
        im += x[i] * s;
    }
    return re * re + im * im;
}

// Ratio (dB) of the energy of `want` in `present` vs the same bin leaking into
// `other`. Positive = the tone belongs to `present`, not `other`.
static double crosstalkDb(const std::vector<float>& present,
                          const std::vector<float>& other, double fHz) {
    const double a = binPower(present, fHz);
    const double b = binPower(other, fHz);
    return 10.0 * std::log10((a + 1e-12) / (b + 1e-12));
}

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

// *** SYNTHETIC -- NOT HARDWARE *** End-to-end WFM stereo: the offline test
// source emits a composite MPX (1 kHz left / 3 kHz right + 19 kHz pilot + 38 kHz
// DSB), the engine demodulates it, the WfmStereoDecoder locks on the real pilot,
// and the MemoryAudioSink captures the L/R matrix output. Asserts the recovered
// channel separation, the stereoState signal honesty, and the fall-backs to
// mono when the pilot is removed or the user forces mono.
void TestEngineIntegration::wfmStereoEndToEnd() {
    SpectrumEngine eng;
    eng.onSetSampleRate(2.4e6);
    eng.onSetCenterFreq(98.5e6);
    eng.setDemodMode("WFM");
    eng.setSquelchEnabled(false);   // keep the gate open (honest offline run)
    eng.setMuted(false);

    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));

    QSignalSpy spy(&eng, &SpectrumEngine::stereoState);

    // 1) Stereo ON: PLL must lock, blend must come up, stereoState must report
    //    true, and the L/R outputs must separate 1k (left) from 3k (right).
    eng.setTestFmStereo(true);
    eng.start();
    QTest::qWait(4500);   // let the 19 kHz pilot PLL lock + blend pump up

    bool sawStereoTrue = false; float measuredBlend = 0.0f, measuredPilot = 0.0f;
    for (const auto& sig : spy) {
        if (sig.value(0).toBool()) { sawStereoTrue = true; }
        measuredBlend = sig.value(1).toFloat();
        measuredPilot = sig.value(2).toFloat();
    }
    QVERIFY2(sawStereoTrue, "a clean synthetic 19 kHz pilot must lock stereo");

    mem->clear();
    QTest::qWait(1500);   // clean capture window
    const std::vector<float>& L = mem->stereoLeft();
    const std::vector<float>& R = mem->stereoRight();
    QVERIFY2(L.size() > 5000 && L.size() == R.size(),
             "stereo L/R streams must be captured and frame-aligned");
    // 1 kHz source belongs to the LEFT, 3 kHz source to the RIGHT.
    const double sepLeft1k  = crosstalkDb(L, R, 1000.0);   // left > right @1k
    const double sepRight3k = crosstalkDb(R, L, 3000.0);   // right > left @3k
    const double separationDb = std::min(sepLeft1k, sepRight3k);
    qInfo("stereo: blend=%.3f pilotQ=%.3f  sep(1k->L)=%.1f dB sep(3k->R)=%.1f dB",
          measuredBlend, measuredPilot, sepLeft1k, sepRight3k);
    qInfo("abs bins: L1=%.3g L3=%.3g R1=%.3g R3=%.3g",
          binPower(L,1000.0), binPower(L,3000.0), binPower(R,1000.0), binPower(R,3000.0));
    QVERIFY2(separationDb > 20.0, "stereo channel separation must exceed 20 dB");

    // 2) Pilot OFF: same chain, no pilot -> blend falls back to 0 -> L == R.
    eng.setTestFmStereo(false);
    QTest::qWait(1000);   // let the pilot unlock + blend fully decay before capturing
    spy.clear();
    mem->clear();
    QTest::qWait(2500);   // steady-state mono capture
    bool sawStereoFalse = false;
    for (const auto& sig : spy) if (!sig.value(0).toBool()) sawStereoFalse = true;
    QVERIFY2(sawStereoFalse, "without a pilot the engine must report mono");
    double eDiff = 0.0, eSig = 0.0;
    const std::vector<float>& Lm = mem->stereoLeft();
    const std::vector<float>& Rm = mem->stereoRight();
    QCOMPARE(Lm.size(), Rm.size());
    for (std::size_t i = 0; i < Lm.size(); ++i) {
        eDiff += (Lm[i] - Rm[i]) * (Lm[i] - Rm[i]);
        eSig  += Lm[i] * Lm[i] + Rm[i] * Rm[i];
    }
    const double monoCrosstalkDb = 10.0 * std::log10((eDiff + 1e-12) / (eSig + 1e-12));
    qInfo("no-pilot fallback: L/R difference = %.1f dB (relative to signal)", monoCrosstalkDb);
    QVERIFY2(monoCrosstalkDb < -20.0, "without a pilot L and R must collapse to mono");

    // 3) Stereo ON again but user forces mono: even with a real pilot locked,
    //    blend is pinned to 0 -> L == R.
    eng.setTestFmStereo(true);
    QTest::qWait(2000);   // pilot re-locks
    // Engage force-mono and let blend decay to 0 BEFORE opening the capture
    // window, so the measured buffer holds only settled mono blocks (the first
    // blocks after the toggle still carry the blend decay transient).
    eng.setForceMono(true);
    QTest::qWait(500);
    mem->clear();
    QTest::qWait(1000);
    double eDiff2 = 0.0, eSig2 = 0.0;
    const std::vector<float>& Lf = mem->stereoLeft();
    const std::vector<float>& Rf = mem->stereoRight();
    QCOMPARE(Lf.size(), Rf.size());
    for (std::size_t i = 0; i < Lf.size(); ++i) {
        eDiff2 += (Lf[i] - Rf[i]) * (Lf[i] - Rf[i]);
        eSig2  += Lf[i] * Lf[i] + Rf[i] * Rf[i];
    }
    const double forcedMonoDb = 10.0 * std::log10((eDiff2 + 1e-12) / (eSig2 + 1e-12));
    qInfo("force-mono: L/R difference = %.1f dB", forcedMonoDb);
    QVERIFY2(forcedMonoDb < -20.0, "force-mono must collapse L and R");

    eng.setForceMono(false);
    eng.shutdown();
    eng.wait(2000);
}

QTEST_MAIN(TestEngineIntegration)
#include "test_engine_integration.moc"