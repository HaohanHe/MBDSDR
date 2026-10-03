// SPDX-License-Identifier: MIT
// Phase21 Step1: honest empty state. The engine must NEVER synthesize IQ on its
// own. With no RTL-SDR hardware (this cloud VM has none) AND no explicit
// opt-in, a freshly constructed engine has no real source, is not synthetic,
// and produces no data / no spectrumReady frame. Only an EXPLICIT opt-in --
// setTestSourceEnabled(true), the --test-source CLI flag, or the
// MBDSDR_TEST_SOURCE=1 environment variable -- brings up the synthetic source,
// which is then honestly labeled isSynthetic()==true.
#include <QtTest/QtTest>
#include <QSignalSpy>
#include <QByteArray>
#include <cstring>

#include "dsp/spectrum_engine.h"
#include "dsp/memory_audio_sink.h"

using namespace mbdsdr::dsp;

class TestEngineEmptyState : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void defaultIsHonestEmpty();
    void explicitApiTogglesSynthetic();
    void envVarEnablesSynthetic();
};

void TestEngineEmptyState::initTestCase() {
    // Start from a clean environment: the synthetic source must not leak in from
    // a parent process env. Each case controls the var it needs.
    qunsetenv("MBDSDR_TEST_SOURCE");
}

// No hardware + no opt-in -> honest empty state, and the run loop emits NO IQ.
void TestEngineEmptyState::defaultIsHonestEmpty() {
    qunsetenv("MBDSDR_TEST_SOURCE");
    SpectrumEngine eng;

    // Static state right after construction (no start needed).
    QVERIFY2(!eng.isTestSourceEnabled(), "test source must be OFF by default");
    QVERIFY2(!eng.hasRealSource(), "no hardware -> hasRealSource() false");
    QVERIFY2(!eng.isSynthetic(), "must not silently synthesize");
    QVERIFY2(!eng.hasData(), "empty idle source must report no data");
    QVERIFY2(!eng.isTestSignalActive(), "empty state must NOT be mislabeled test");

    // Run the loop briefly: with the NullSource it must stay silent.
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(new MemoryAudioSink()));
    QSignalSpy frames(&eng, &SpectrumEngine::spectrumReady);
    eng.start();
    QTest::qWait(700);
    eng.shutdown();
    eng.wait(2000);

    QCOMPARE(frames.count(), 0);
    QVERIFY2(!eng.hasData(), "after an idle run there is still no data");
    QVERIFY2(!eng.isSynthetic(), "idle run must not flip into synthetic");
}

// The explicit API opt-in brings up the synthetic source; turning it off drops
// back to the honest empty state. A live real device is never disturbed (there
// is none here, so the idle source swaps both ways).
void TestEngineEmptyState::explicitApiTogglesSynthetic() {
    qunsetenv("MBDSDR_TEST_SOURCE");
    SpectrumEngine eng;
    QVERIFY(!eng.hasData());
    QVERIFY(!eng.isSynthetic());

    // Opt in via the API.
    eng.setTestSourceEnabled(true);
    QVERIFY2(eng.isTestSourceEnabled(), "opt-in flag must stick");
    QVERIFY2(!eng.hasRealSource(), "synthetic is not real hardware");
    QVERIFY2(eng.isSynthetic(), "explicit opt-in -> synthetic source active");
    QVERIFY2(eng.hasData(), "synthetic source produces data");

    // Run the loop: synthetic IQ must now flow (spectrumReady emitted).
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(new MemoryAudioSink()));
    QSignalSpy frames(&eng, &SpectrumEngine::spectrumReady);
    eng.start();
    QTest::qWait(900);
    QVERIFY2(frames.count() > 0, "an explicitly-enabled test source must emit frames");

    // Opt out: back to honest empty, the run loop goes silent again.
    eng.setTestSourceEnabled(false);
    QVERIFY2(!eng.isTestSourceEnabled(), "opt-out flag must stick");
    QVERIFY2(!eng.isSynthetic(), "after opt-out we are not synthetic");
    QVERIFY2(!eng.hasData(), "after opt-out there is no data");
    const int framesBefore = frames.count();
    QTest::qWait(600);
    eng.shutdown();
    eng.wait(2000);
    // No NEW frames after the swap to NullSource (count must not keep growing).
    QVERIFY2(frames.count() == framesBefore,
             "disabling the test source must stop IQ flow");
}

// The environment-variable channel (equivalent to the --test-source CLI flag)
// must also opt in -- it is read in the engine constructor.
void TestEngineEmptyState::envVarEnablesSynthetic() {
    qputenv("MBDSDR_TEST_SOURCE", "1");
    SpectrumEngine eng;
    // Unset promptly so it cannot leak into any later process / case.
    qunsetenv("MBDSDR_TEST_SOURCE");

    QVERIFY2(eng.isTestSourceEnabled(), "MBDSDR_TEST_SOURCE=1 must opt in");
    QVERIFY2(eng.isSynthetic(), "env opt-in -> synthetic source active");
    QVERIFY2(eng.hasData(), "env opt-in must produce data");
    QVERIFY2(!eng.hasRealSource(), "env synthetic is not real hardware");

    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(new MemoryAudioSink()));
    QSignalSpy frames(&eng, &SpectrumEngine::spectrumReady);
    eng.start();
    QTest::qWait(900);
    eng.shutdown();
    eng.wait(2000);
    QVERIFY2(frames.count() > 0, "env-enabled test source must emit frames");
}

QTEST_MAIN(TestEngineEmptyState)
#include "test_engine_empty_state.moc"
