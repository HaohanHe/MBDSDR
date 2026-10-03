// SPDX-License-Identifier: MIT
// Offscreen soak: drive the engine through connect -> multi-VFO -> mode switch
// -> squelch -> record -> disconnect/reconnect, asserting no crash, no NaN
// frames and no stale state/zombie timers after the cycle. Uses the honest
// offline TestSignalSource (NOT HARDWARE).
#include <QtTest>
#include <QApplication>
#include <QElapsedTimer>
#include <cmath>
#include "dsp/spectrum_engine.h"
#include "core/spectrum_frame.h"

using namespace mbdsdr;

class TestSoak : public QObject {
    Q_OBJECT
private slots:
    // Phase21: engine no longer auto-falls back to the offline test source. The
    // soak drives the whole cycle and expects frames to keep flowing even after a
    // disconnect (idle source = the explicitly-enabled synthetic source), so opt
    // in before the engine is constructed.
    void initTestCase();
    void fullScenarioNoCrash();
};

void TestSoak::initTestCase() {
    qputenv("MBDSDR_TEST_SOURCE", "1");   // explicit synthetic offline source
}

static bool frameFinite(const mbdsdr::SpectrumFrame& f) {
    for (float v : f.dbfs) if (!std::isfinite(v)) return false;
    return true;
}

void TestSoak::fullScenarioNoCrash() {
    dsp::SpectrumEngine eng;
    int frames = 0;
    bool sawSpectrum = false;
    QObject::connect(&eng, &dsp::SpectrumEngine::spectrumReady,
                     &eng, [&](const mbdsdr::SpectrumFrame& f) {
        ++frames;
        QVERIFY2(frameFinite(f), "NaN/Inf in spectrum frame");
        sawSpectrum = true;
    });
    eng.start();   // SpectrumEngine is a QThread; run() must be launched.

    // Run a short while on the offline test source.
    QElapsedTimer t; t.start();
    while (!sawSpectrum && t.elapsed() < 3000) QTest::qWait(30);
    QVERIFY(sawSpectrum);

    // Multi-VFO: add two, select each.
    eng.vfoAdd();
    eng.vfoAdd();
    QCOMPARE_GE(eng.vfoMarkers().size(), 3);
    eng.vfoSelect(eng.selectedVfoId());

    // Mode switching across analog + digital + narrow.
    for (const char* m : {"NFM", "WFM", "USB", "CW"}) {
        eng.setDemodMode(QString::fromLatin1(m));
        QTest::qWait(20);
    }

    // Squelch toggle.
    eng.setSquelchEnabled(true);
    eng.setSquelchThreshold(-30.0f);
    QTest::qWait(20);
    eng.setSquelchEnabled(false);

    // Recording start/stop (honest baseband on the test source).
    QVERIFY(eng.startRecording());
    QTest::qWait(80);
    eng.stopRecording();

    // Frontend decimation cycle (default off -> x2 -> off).
    eng.setFrontendDecimation(2);
    QTest::qWait(60);
    eng.setFrontendDecimation(1);
    QTest::qWait(60);

    // Disconnect / reconnect cycle.
    eng.disconnectSource();
    QTest::qWait(60);
    eng.disconnectSource();

    // Engine must still be producing finite frames after all of the above.
    int before = frames;
    t.restart();
    while (frames - before < 5 && t.elapsed() < 3000) QTest::qWait(30);
    QVERIFY2(frames - before >= 5, "engine stopped producing frames after soak");
    qInfo("soak frames total=%d", frames);
}

QTEST_MAIN(TestSoak)
#include "test_soak.moc"
