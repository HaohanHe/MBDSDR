// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 — SYNTHETIC FIXTURE, TEST ONLY ***
//
// Phase27 Block2: ScanActivityLink ENGINE-level wiring.
//
// test_scan_link.cpp proved the link's state machine in isolation with a hand
// per-channel RSSI table. This test closes the REAL production seam instead:
// the RSSI fed to the link is the LIVE SpectrumEngine measurement
// (SpectrumEngine::rssiDbfs() — the raw-capture total-power dBFS the run loop
// computes every block), the link's action seams are bound to the engine's REAL
// primitives, and the whole thing runs through the engine's own QThread run()
// loop:
//
//   synthetic TestSignalSource IQ -> engine run() chain -> measured rssiDbfs()
//     -> ScanActivityLink.tick(elapsed, rssi)          [decoupled driver thread]
//        -> onRetune(f)       : engine.onSetCenterFreq(f)        (REAL tuner retune)
//        -> onActivityFound   : engine.startRecording()          (REAL recorder arm)
//        -> onDwellEnded      : engine.stopRecording()           (REAL segment close)
//     parked NFM VFO demods the synthetic FM -> MemoryAudioSink  (REAL demod)
//
// We assert the busy band parks + arms a real SigMF segment + demods, and that a
// band whose threshold sits ABOVE the measured level is an honest empty (no
// dwell, no record arm, no file). No radio, no network: the carrier is the
// offline TestSignalSource (explicit opt-in), the RSSI is the engine's own.
#include <QtTest/QtTest>
#include <QSignalSpy>
#include <QElapsedTimer>
#include <QDir>
#include <QFileInfo>

#include <cmath>
#include <vector>

#include "dsp/spectrum_engine.h"
#include "dsp/scan_link.h"
#include "dsp/memory_audio_sink.h"

using namespace mbdsdr;
using namespace mbdsdr::dsp;

namespace {
// DFT-bin power of mono audio x at fHz (48 kHz), Goertzel-style — no FFT dep.
double binPower(const std::vector<float>& x, double fHz) {
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

const char* kRecDir = "test_tmp_scanlink_engine";
} // namespace

class TestScanLinkEngine : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void busyBandParksRecordsAndDemodulates();
    void quietBandIsHonestEmpty();
};

void TestScanLinkEngine::initTestCase() {
    // The offline synthetic source is an EXPLICIT opt-in, read in the engine ctor.
    qputenv("MBDSDR_TEST_SOURCE", "1");
    QDir().rmdir(kRecDir);
    QDir().mkpath(kRecDir);
}

void TestScanLinkEngine::busyBandParksRecordsAndDemodulates() {
    SpectrumEngine eng;
    eng.onSetSampleRate(2.4e6);
    eng.onSetCenterFreq(100.0e6);
    eng.setDemodMode("NFM");
    eng.setSquelchEnabled(false);          // keep the gate open (honest offline run)
    eng.setRecTarget(RecTarget::BasebandIQ);
    eng.setRecordingDir(QString::fromLatin1(kRecDir));

    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));

    eng.start();
    QTest::qWait(1500);                    // let blocks flow + RSSI settle
    const float busyDb = eng.rssiDbfs();
    qInfo("measured synthetic RSSI = %.1f dBFS", busyDb);
    QVERIFY2(busyDb > -90.0f, "a real synthetic block must have flowed (honest RSSI)");

    // Scan a 300 kHz window in 100 kHz steps. Threshold sits BELOW the real
    // carrier level, so every parked channel reads as busy.
    ScanConfig cfg;
    cfg.source = ScanSource::Range;
    cfg.startHz = 100.0e6;
    cfg.stopHz  = 100.3e6;
    cfg.stepHz  = 100e3;                    // 100.0 / 100.1 / 100.2 / 100.3 MHz
    cfg.dwellMs = 200;
    cfg.settleMs = 60;
    cfg.thresholdDb = busyDb - 10.0f;      // clearly under the real carrier
    cfg.direction = ScanDirection::Up;
    cfg.holdMode = HitHoldMode::FixedMs;
    cfg.holdMs   = 400;
    cfg.loop = false;

    ScanActivityLink link;
    link.setConfig(cfg);

    QSignalSpy recSpy(&eng, &SpectrumEngine::recordingStateChanged);
    QList<double> retunes;
    int dwellStarts = 0, dwellEnds = 0;
    ScanLinkActions a;
    a.onRetune = [&](double f) { retunes.append(f); eng.onSetCenterFreq(f); };
    a.onActivityFound = [&](double, float) { ++dwellStarts; eng.startRecording(); };
    a.onDwellEnded    = [&](double)       { ++dwellEnds;    eng.stopRecording(); };
    link.setActions(a);

    mem->clear();
    link.start();
    QElapsedTimer clk; clk.start();
    qint64 last = 0;
    int guard = 200;
    while (link.state() != ScanLinkState::Idle && guard-- > 0) {
        QTest::qWait(30);
        const qint64 now = clk.elapsed();
        const int elapsed = static_cast<int>(now - last);
        last = now;
        link.tick(elapsed, eng.rssiDbfs());   // feed the LIVE engine RSSI
    }
    QCOMPARE(link.state(), ScanLinkState::Idle);

    // --- Sweep walked the lattice by retuning the REAL engine tuner. ---
    QVERIFY2(retunes.size() >= 4, "scan must retune the engine across the lattice");
    QVERIFY2(link.dwellCount() >= 1, "the busy band must be parked on at least once");
    QVERIFY2(dwellStarts >= 1, "onActivityFound must have fired");
    QCOMPARE(dwellStarts, dwellEnds);          // every dwell closed honestly

    // --- Recording was armed via the REAL engine recorder path. ---
    int startedCount = 0;
    for (const auto& s : recSpy) if (s.value(0).toBool()) ++startedCount;
    QVERIFY2(startedCount >= 1, "onActivityFound must arm the real engine recorder");
    QVERIFY2(recSpy.count() >= 2, "each arm must be paired with a close edge");

    // --- A non-empty SigMF segment was written for an activity. ---
    bool wroteFile = false;
    for (const QFileInfo& fi : QDir(QString::fromLatin1(kRecDir))
                                    .entryInfoList({"*.sigmf-data"}))
        if (fi.size() > 0) wroteFile = true;
    QVERIFY2(wroteFile, "the activity must produce a non-empty SigMF recording");

    // --- Demod: the parked NFM VFO actually demodulated the synthetic FM. ---
    QTest::qWait(400);
    const std::vector<float>& buf = mem->buffer();
    qInfo("demod audio frames = %zu, binPower@1k = %.3g, binPower@5k = %.3g",
          buf.size(), binPower(buf, 1000.0), binPower(buf, 5000.0));
    QVERIFY2(buf.size() > 8000, "demod audio must flow to the injected sink");
    QVERIFY2(binPower(buf, 1000.0) > 100.0,
             "the parked NFM channel must demodulate the synthetic 1 kHz FM tone");

    eng.shutdown();
    eng.wait(2000);
}

// A band whose gate sits ABOVE the measured level is quiet: the scanner walks
// the whole lattice but must NOT open a dwell, arm a recorder, or write a file.
void TestScanLinkEngine::quietBandIsHonestEmpty() {
    SpectrumEngine eng;
    eng.onSetSampleRate(2.4e6);
    eng.onSetCenterFreq(100.0e6);
    eng.setDemodMode("NFM");
    eng.setSquelchEnabled(false);

    eng.start();
    QTest::qWait(1500);
    const float busyDb = eng.rssiDbfs();
    QVERIFY2(busyDb > -90.0f, "a real synthetic block must have flowed");

    ScanConfig cfg;
    cfg.source = ScanSource::Range;
    cfg.startHz = 100.0e6;
    cfg.stopHz  = 100.3e6;
    cfg.stepHz  = 100e3;
    cfg.dwellMs = 150;
    cfg.settleMs = 60;
    cfg.thresholdDb = busyDb + 15.0f;       // clearly ABOVE the real level -> quiet
    cfg.direction = ScanDirection::Up;
    cfg.holdMode = HitHoldMode::FixedMs;
    cfg.holdMs   = 200;
    cfg.loop = false;

    ScanActivityLink link;
    link.setConfig(cfg);

    QSignalSpy recSpy(&eng, &SpectrumEngine::recordingStateChanged);
    QList<double> retunes;
    int dwellStarts = 0, dwellEnds = 0;
    ScanLinkActions a;
    a.onRetune = [&](double f) { retunes.append(f); eng.onSetCenterFreq(f); };
    a.onActivityFound = [&](double, float) { ++dwellStarts; };
    a.onDwellEnded    = [&](double)       { ++dwellEnds; };
    link.setActions(a);

    link.start();
    QElapsedTimer clk; clk.start();
    qint64 last = 0;
    int guard = 200;
    while (link.state() != ScanLinkState::Idle && guard-- > 0) {
        QTest::qWait(30);
        const qint64 now = clk.elapsed();
        const int elapsed = static_cast<int>(now - last);
        last = now;
        link.tick(elapsed, eng.rssiDbfs());
    }
    QCOMPARE(link.state(), ScanLinkState::Idle);

    // It still swept the lattice (real retunes) but found nothing.
    QVERIFY2(retunes.size() >= 4, "the scanner must still walk the lattice");
    QCOMPARE(link.dwellCount(), 0);
    QCOMPARE(dwellStarts, 0);
    QCOMPARE(dwellEnds, 0);
    QVERIFY2(recSpy.isEmpty(), "a quiet band must NOT arm the engine recorder");

    eng.shutdown();
    eng.wait(2000);
}

QTEST_MAIN(TestScanLinkEngine)
#include "test_scan_link_engine.moc"
