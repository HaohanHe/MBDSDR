// SPDX-License-Identifier: MIT
//
// *** FIXTURE / SYNTHETIC -- NOT HARDWARE ***
//
// Offline, deterministic tests for the FrequencyScanner state machine. The
// scanner itself is pure logic: it never touches a radio, never sleeps on the
// wall clock, and never fabricates a frequency or an RSSI. These tests drive it
// with a fixed 30 ms tick and an in-memory table (QMap<double,float>) that maps
// a tuned frequency to a synthetic measured RSSI(dBFS). That table is the
// test fixture -- the "measurements" it hands over are NOT from any hardware;
// the scanner merely gates them through the reused SignalWatch threshold.
//
// We assert: exact range/step sequence, strict tuning order, empty channels
// never hit, exactly the expected hits, dwell timing, UntilSignalGone linger,
// Down / PingPong direction, pause/resume freeze + stop->Idle, bookmark-only
// tuning, and FixedMs hold timing.
#include <QtTest/QtTest>
#include <QMap>
#include <QList>

#include "dsp/frequency_scanner.h"

using namespace mbdsdr::dsp;

namespace {
constexpr int kTickMs = 30;          // fixed 30 ms per tick
constexpr float kQuiet = -80.0f;    // empty channel RSSI
constexpr float kBusy  = -30.0f;    // occupied channel RSSI
constexpr float kThr   = -50.0f;    // threshold (between -80 and -30)

// *** FIXTURE / SYNTHETIC -- NOT HARDWARE ***
// Memory backend: frequency (Hz) -> synthetic measured RSSI (dBFS). The scanner
// does NOT invent these; the test supplies them as if from the engine.
struct MemBackend {
    QMap<double, float> rssi;
    float value(double f) const {
        auto it = rssi.constFind(f);
        return it == rssi.cend() ? kQuiet : it.value();
    }
};

// Drive one tick against the scanner using the fixture for the current freq.
// Returns true iff this tick asked for a retune.
bool step(FrequencyScanner& sc, const MemBackend& fx) {
    bool nt = false;
    sc.tick(kTickMs, fx.value(sc.currentFrequency()), &nt);
    return nt;
}
} // namespace

class TestScanner : public QObject {
    Q_OBJECT
private slots:
    // 1+2: range stepping, exact sequence, strict order, hits only on busy freqs,
    //      empty channels dwell exactly 300 ms.
    void rangeStep();
    // 3: UntilSignalGone -- stay while signal present, linger after it vanishes.
    void untilSignalGone();
    // 4: Down visits frequencies descending; PingPong reverses at stop and
    //    revisits start.
    void downAndPingPong();
    // 5: pause freezes clocks (no tune-out during pause), resume continues,
    //    stop -> Idle.
    void pauseResumeStop();
    // 6: bookmarks mode touches only the bookmark frequencies.
    void bookmarks();
    // 7: FixedMs holds exactly holdMs regardless of signal.
    void fixedMs();
};

void TestScanner::rangeStep() {
    FrequencyScanner sc;
    ScanConfig c;
    c.source = ScanSource::Range;
    c.startHz = 100.0e6;
    c.stopHz  = 100.3e6;
    c.stepHz  = 100e3;       // -> [100.0,100.1,100.2,100.3] MHz
    c.dwellMs = 300;
    c.settleMs = 80;
    c.thresholdDb = kThr;
    c.direction = ScanDirection::Up;
    // Busy signals are always-on, so use FixedMs with a short hold to leave each
    // hit; the empty-channel dwell timing is what we pin here.
    c.holdMode = HitHoldMode::FixedMs;
    c.holdMs   = 300;
    c.loop = false;
    sc.setConfig(c);

    MemBackend fx;
    fx.rssi[100.1e6] = kBusy;   // only 100.1 and 100.3 occupied
    fx.rssi[100.3e6] = kBusy;
    // 100.0 and 100.2 fall through to kQuiet.

    sc.start();
    QList<double> tuned;
    QList<long>  gaps;        // ms between consecutive tune requests
    long cumMs = 0, lastTune = -1;
    int guard = 5000;
    while (sc.state() != ScanState::Idle && guard-- > 0) {
        const bool nt = step(sc, fx);
        cumMs += kTickMs;
        if (nt) {
            if (lastTune >= 0) gaps.append(cumMs - lastTune);
            tuned.append(sc.currentFrequency());
            lastTune = cumMs;
        }
    }
    QCOMPARE(sc.state(), ScanState::Idle);

    // Exact sequence.
    QCOMPARE(tuned.size(), 4);
    QCOMPARE(tuned[0], 100.0e6);
    QCOMPARE(tuned[1], 100.1e6);
    QCOMPARE(tuned[2], 100.2e6);
    QCOMPARE(tuned[3], 100.3e6);

    // Exactly two hits, on the two occupied frequencies only.
    QCOMPARE(sc.hitCount(), 2);
    QCOMPARE(sc.hits().size(), 2);
    QCOMPARE(sc.hits()[0].freqHz, 100.1e6);
    QCOMPARE(sc.hits()[1].freqHz, 100.3e6);
    QVERIFY(sc.hits()[0].levelDb > kThr);
    QVERIFY(sc.hits()[1].levelDb > kThr);

    // gaps[0] = dwell on 100.0 (empty) ; gaps[2] = dwell on 100.2 (empty).
    QCOMPARE(gaps.size(), 3);
    QCOMPARE(gaps[0], 300);   // empty 100.0 -> next after exactly 300 ms
    QCOMPARE(gaps[2], 300);   // empty 100.2 -> next after exactly 300 ms
}

void TestScanner::untilSignalGone() {
    FrequencyScanner sc;
    ScanConfig c;
    c.source = ScanSource::Range;
    c.startHz = 100.0e6;
    c.stopHz  = 100.3e6;
    c.stepHz  = 100e3;
    c.dwellMs = 300;
    c.settleMs = 80;
    c.thresholdDb = kThr;
    c.direction = ScanDirection::Up;
    c.holdMode = HitHoldMode::UntilSignalGone;
    c.lingerMs = 1000;
    c.loop = false;
    sc.setConfig(c);

    MemBackend fx;
    fx.rssi[100.1e6] = kBusy;   // signal only on 100.1

    sc.start();
    // Run until we are sitting in Hit on 100.1 (skips empty 100.0 dwell).
    int guard = 5000;
    while (sc.state() != ScanState::Hit && guard-- > 0) step(sc, fx);
    QCOMPARE(sc.state(), ScanState::Hit);
    QCOMPARE(sc.currentFrequency(), 100.1e6);

    // While the signal stays up, several ticks must NOT move us off 100.1.
    for (int i = 0; i < 8; ++i) {
        QVERIFY(!step(sc, fx));
        QCOMPARE(sc.currentFrequency(), 100.1e6);
        QCOMPARE(sc.state(), ScanState::Hit);
    }

    // Now remove the signal.
    fx.rssi[100.1e6] = kQuiet;

    // SignalWatch decays over ~90 ms before it judges the signal gone; the linger
    // clock then needs lingerMs(1000) of continuous absence. Drive every tick from
    // here and count them: we must stay on 100.1 for the first ~600 ms, and only
    // retune after the full linger.
    int offTicks = 0;
    bool advanced = false;
    while (offTicks < 200) {
        const bool nt = step(sc, fx);
        ++offTicks;
        if (nt) { advanced = true; break; }
        if (offTicks == 20)   // 20*30 = 600 ms after the signal vanished
            QCOMPARE(sc.currentFrequency(), 100.1e6);
    }
    QVERIFY(advanced);
    QCOMPARE(sc.currentFrequency(), 100.2e6);
    // linger(1000) + decay(~90)  =>  between 1000 and 1300 ms.
    QVERIFY2(offTicks * kTickMs >= 1000, "must wait the full linger after signal gone");
    QVERIFY2(offTicks * kTickMs <= 1300, "must not wait far beyond linger");
}

void TestScanner::downAndPingPong() {
    // ---- Down: descending order ----
    {
        FrequencyScanner sc;
        ScanConfig c;
        c.source = ScanSource::Range;
        c.startHz = 100.0e6;
        c.stopHz  = 100.3e6;
        c.stepHz  = 100e3;
        c.dwellMs = 300;
        c.settleMs = 80;
        c.thresholdDb = kThr;
        c.direction = ScanDirection::Down;
        c.loop = false;
        sc.setConfig(c);

        MemBackend fx;   // all quiet
        sc.start();
        QList<double> tuned;
        int guard = 5000;
        while (sc.state() != ScanState::Idle && guard-- > 0) {
            if (step(sc, fx)) tuned.append(sc.currentFrequency());
        }
        QCOMPARE(sc.state(), ScanState::Idle);
        QCOMPARE(tuned.size(), 4);
        QCOMPARE(tuned[0], 100.3e6);
        QCOMPARE(tuned[1], 100.2e6);
        QCOMPARE(tuned[2], 100.1e6);
        QCOMPARE(tuned[3], 100.0e6);
    }

    // ---- PingPong: up to stop, reverse, come back to start ----
    {
        FrequencyScanner sc;
        ScanConfig c;
        c.source = ScanSource::Range;
        c.startHz = 100.0e6;
        c.stopHz  = 100.3e6;
        c.stepHz  = 100e3;
        c.dwellMs = 300;
        c.settleMs = 80;
        c.thresholdDb = kThr;
        c.direction = ScanDirection::PingPong;
        sc.setConfig(c);

        MemBackend fx;   // all quiet
        sc.start();
        QList<double> tuned;
        int guard = 5000;
        int startRevisits = 0;
        bool sawStop = false;
        while (guard-- > 0) {
            if (step(sc, fx)) {
                tuned.append(sc.currentFrequency());
                if (sc.currentFrequency() == 100.3e6) sawStop = true;
                if (sc.currentFrequency() == 100.0e6) {
                    ++startRevisits;
                    if (startRevisits >= 2 && sawStop) break; // went up, came back
                }
            }
        }
        QVERIFY(sawStop);
        QVERIFY2(startRevisits >= 2, "PingPong must revisit start after reaching stop");
        // The walk must stay on the 4-channel lattice (no invented frequencies).
        for (double f : tuned) {
            QVERIFY(f == 100.0e6 || f == 100.1e6 || f == 100.2e6 || f == 100.3e6);
        }
        sc.stop();
        QCOMPARE(sc.state(), ScanState::Idle);
    }
}

void TestScanner::pauseResumeStop() {
    FrequencyScanner sc;
    ScanConfig c;
    c.source = ScanSource::Range;
    c.startHz = 100.0e6;
    c.stopHz  = 100.3e6;
    c.stepHz  = 100e3;
    c.dwellMs = 300;
    c.settleMs = 80;
    c.thresholdDb = kThr;
    c.direction = ScanDirection::Up;
    c.loop = false;
    sc.setConfig(c);

    MemBackend fx;   // all quiet

    sc.start();
    QVERIFY(step(sc, fx));                 // tune request to 100.0
    QCOMPARE(sc.currentFrequency(), 100.0e6);
    // Drive ~90 ms of dwell (past the 80 ms settle).
    step(sc, fx); step(sc, fx); step(sc, fx);   // freqTimer = 90

    sc.pause();
    QCOMPARE(sc.state(), ScanState::Paused);
    // Drive 10 ticks (300 ms) while paused: must not retune, must not advance.
    for (int i = 0; i < 10; ++i) {
        QVERIFY(!step(sc, fx));
        QCOMPARE(sc.currentFrequency(), 100.0e6);
    }
    QCOMPARE(sc.state(), ScanState::Paused);

    sc.resume();
    QCOMPARE(sc.state(), ScanState::Scanning);
    // Remaining dwell = 300 - 90 = 210 ms = 7 ticks. Six ticks stay put...
    for (int i = 0; i < 6; ++i) {
        QVERIFY(!step(sc, fx));
        QCOMPARE(sc.currentFrequency(), 100.0e6);
    }
    // ...the 7th tick crosses 300 ms and retunes to 100.1.
    QVERIFY(step(sc, fx));
    QCOMPARE(sc.currentFrequency(), 100.1e6);

    sc.stop();
    QCOMPARE(sc.state(), ScanState::Idle);
    bool nt = false;
    sc.tick(kTickMs, kQuiet, &nt);
    QVERIFY(!nt);
    QCOMPARE(sc.state(), ScanState::Idle);
}

void TestScanner::bookmarks() {
    FrequencyScanner sc;
    ScanConfig c;
    c.source = ScanSource::Bookmarks;
    c.thresholdDb = kThr;
    c.dwellMs = 300;
    c.settleMs = 80;
    c.holdMode = HitHoldMode::FixedMs;
    c.holdMs = 200;
    c.loop = false;
    sc.setConfig(c);
    sc.setBookmarkFrequencies({92.5e6, 145.0e6, 446.0e6});

    MemBackend fx;
    fx.rssi[145.0e6] = kBusy;   // only the middle bookmark is occupied

    sc.start();
    QList<double> tuned;
    int guard = 5000;
    while (sc.state() != ScanState::Idle && guard-- > 0) {
        if (step(sc, fx)) tuned.append(sc.currentFrequency());
    }
    QCOMPARE(sc.state(), ScanState::Idle);

    // Touched exactly the three bookmarks -- no intermediate frequencies.
    QCOMPARE(tuned.size(), 3);
    QCOMPARE(tuned[0], 92.5e6);
    QCOMPARE(tuned[1], 145.0e6);
    QCOMPARE(tuned[2], 446.0e6);

    // Exactly one hit, on the occupied bookmark.
    QCOMPARE(sc.hitCount(), 1);
    QCOMPARE(sc.hits().size(), 1);
    QCOMPARE(sc.hits()[0].freqHz, 145.0e6);
}

void TestScanner::fixedMs() {
    FrequencyScanner sc;
    ScanConfig c;
    c.source = ScanSource::Range;
    c.startHz = 100.0e6;
    c.stopHz  = 100.3e6;
    c.stepHz  = 100e3;
    c.dwellMs = 300;
    c.settleMs = 80;
    c.thresholdDb = kThr;
    c.direction = ScanDirection::Up;
    c.holdMode = HitHoldMode::FixedMs;
    c.holdMs = 500;
    c.loop = false;
    sc.setConfig(c);

    MemBackend fx;
    fx.rssi[100.1e6] = kBusy;   // stays up the whole time

    sc.start();
    int guard = 5000;
    while (sc.state() != ScanState::Hit && guard-- > 0) step(sc, fx);
    QCOMPARE(sc.state(), ScanState::Hit);
    QCOMPARE(sc.currentFrequency(), 100.1e6);

    // Signal stays up; we must still leave after exactly holdMs(500).
    int holdTicks = 0;
    bool advanced = false;
    while (holdTicks < 200) {
        ++holdTicks;
        if (step(sc, fx)) { advanced = true; break; }
    }
    QVERIFY(advanced);
    QCOMPARE(sc.currentFrequency(), 100.2e6);
    QVERIFY2(holdTicks * kTickMs >= 500, "FixedMs must hold the full holdMs");
    QVERIFY2(holdTicks * kTickMs <= 700, "FixedMs must not overshoot by much");
}

QTEST_MAIN(TestScanner)
#include "test_scanner.moc"
