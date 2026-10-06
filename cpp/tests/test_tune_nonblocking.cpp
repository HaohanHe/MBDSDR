// SPDX-License-Identifier: MIT
// Regression for the reported defect: "clicking the panadapter freezes the app."
//
// Root cause was a synchronous tune on the caller's (GUI) thread: it took
// sourceMutex_ while the engine run() loop already owned it, and the actual USB
// retune on slow tuners blocked the UI. The fix routes every hardware/shared-
// state set command through a lightweight control mailbox (ctrlMutex_) that the
// engine thread drains; the caller only records the latest value and returns.
//
// This test runs the engine thread for real (synthetic test source) and hammers
// the tuning entry point from THIS thread, timing every call. If the call path
// ever waits on sourceMutex_ / the device, a call takes far longer than the
// mailbox budget and the (generous) upper-bound assertion fails.
#include <QtTest/QtTest>
#include <QElapsedTimer>
#include <memory>

#include "dsp/spectrum_engine.h"
#include "dsp/memory_audio_sink.h"

using namespace mbdsdr::dsp;

class TestTuneNonBlocking : public QObject {
    Q_OBJECT
private slots:
    void tuningWhileEngineRunningNeverBlocks();
    void gainWhileEngineRunningNeverBlocks();
};

// Upper bound for one mailbox set call. A real enqueue is microseconds; the
// bound only has to separate that from a blocking sourceMutex_ wait / a
// synchronous retune (hundreds of ms or a hang). Kept loose to avoid flaking on
// a busy CI scheduler.
static constexpr qint64 kMaxSetCallNs = 200 * 1000 * 1000;  // 200 ms

void TestTuneNonBlocking::tuningWhileEngineRunningNeverBlocks() {
    SpectrumEngine eng;
    eng.setTestSourceEnabled(true);
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(new MemoryAudioSink()));
    eng.start();
    QTest::qWait(800);   // let the engine thread spin and own sourceMutex_

    qint64 maxNs = 0;
    // Simulate rapid clicks / drags: 80 distinct tunes back to back.
    for (int i = 0; i < 80; ++i) {
        const double f = 90.0e6 + i * 0.1e6;   // 90.0 .. 97.9 MHz
        QElapsedTimer t; t.start();
        eng.onSetCenterFreq(f);
        maxNs = qMax(maxNs, t.nsecsElapsed());
    }

    QVERIFY2(maxNs < kMaxSetCallNs,
             qPrintable(QString("onSetCenterFreq blocked the caller for %1 ms")
                        .arg(maxNs / 1000000.0, 0, 'f', 3)));

    eng.shutdown();
    eng.wait(2000);
}

void TestTuneNonBlocking::gainWhileEngineRunningNeverBlocks() {
    SpectrumEngine eng;
    eng.setTestSourceEnabled(true);
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(new MemoryAudioSink()));
    eng.start();
    QTest::qWait(800);

    qint64 maxNs = 0;
    for (int i = 0; i < 40; ++i) {
        const double g = i * 1.0;   // sweep the gain
        QElapsedTimer t; t.start();
        eng.onSetGain(g);
        maxNs = qMax(maxNs, t.nsecsElapsed());
    }

    QVERIFY2(maxNs < kMaxSetCallNs,
             qPrintable(QString("onSetGain blocked the caller for %1 ms")
                        .arg(maxNs / 1000000.0, 0, 'f', 3)));

    eng.shutdown();
    eng.wait(2000);
}

QTEST_MAIN(TestTuneNonBlocking)
#include "test_tune_nonblocking.moc"
