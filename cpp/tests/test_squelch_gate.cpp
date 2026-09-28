// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 ***
//
// Squelch gate + gated-recorder linkage test. Audio is synthesized in software
// (pure sine / silence) -- no microphone, no radio, no real signal. We verify:
//   * Gate mode opens on a loud signal, mutes on silence (after hangover);
//   * Off mode passes audio through and reports open;
//   * GatedRecorder only saves talk-spurts while the gate is open;
//   * saved filenames carry the selected VFO's mode + frequency (the bug where
//     they were hard-coded to NFM/98.5MHz);
//   * saved segments start cleanly (no long leading zero / dead-silence run).
#include <QtTest/QtTest>
#include <QDir>
#include <QFileInfo>
#include <QDebug>
#include <cmath>
#include <vector>

#include "dsp/squelch.h"
#include "dsp/gated_recorder.h"

using namespace mbdsdr::dsp;

namespace {
// Real sine block at 48 kHz. *** SYNTHETIC -- NOT HARDWARE ***
std::vector<float> sineBlock(int n, double freq, double amp, double& phase) {
    std::vector<float> v(n);
    for (int i = 0; i < n; ++i) {
        phase += 2.0 * M_PI * freq / 48000.0;
        v[i] = static_cast<float>(amp * std::sin(phase));
    }
    return v;
}
std::vector<float> silenceBlock(int n) { return std::vector<float>(n, 0.0f); }
} // namespace

class TestSquelchGate : public QObject {
    Q_OBJECT
private slots:
    void gateOpensOnSignalClosesOnSilence();
    void offModePassthrough();
    void gatedRecorderSavesOnlyWhenOpenAndLabelsFilename();
    void cleanupTestCase();
};

static QString scratchDir;

void TestSquelchGate::gateOpensOnSignalClosesOnSilence() {
    Squelch sq;
    sq.setMode(Squelch::Mode::Gate);
    sq.setThresholdDb(-30.0f);   // fairly low: loud sine opens, silence closes
    sq.reset();

    const int blk = 960;         // 20 ms @ 48 kHz
    double ph = 0.0;
    // Prime with loud signal; gate should open within a few blocks.
    bool opened = false;
    for (int i = 0; i < 30; ++i) {
        auto a = sineBlock(blk, 1000.0, 0.8, ph);
        bool g = sq.decide(a, rmsDbfs(a));
        if (g) opened = true;
    }
    QVERIFY2(opened, "Gate should open on a loud tone");

    // Now feed silence: attack/decay + hangover (~200ms) then it closes.
    bool closed = false;
    for (int i = 0; i < 40; ++i) {
        auto z = silenceBlock(blk);
        bool g = sq.decide(z, rmsDbfs(z));
        if (!g) closed = true;
    }
    QVERIFY2(closed, "Gate should close after hangover on silence");
    QVERIFY(!sq.open());
}

void TestSquelchGate::offModePassthrough() {
    Squelch sq;
    sq.setMode(Squelch::Mode::Off);
    sq.reset();
    auto quiet = silenceBlock(960);
    bool g = sq.decide(quiet, rmsDbfs(quiet));
    QVERIFY2(g, "Off mode must report open even on silence");
    auto out = sq.apply(quiet, rmsDbfs(quiet));
    QCOMPARE(out.size(), quiet.size());   // passthrough (same length)
}

void TestSquelchGate::gatedRecorderSavesOnlyWhenOpenAndLabelsFilename() {
    scratchDir = QDir::tempPath() + "/mbdsdr_squelch_test_" +
                 QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(scratchDir);

    GatedRecorder rec(48000.0);
    rec.setOutputDir(scratchDir);
    rec.setEnabled(true);
    rec.setContext("AM", 99.0e6);          // <-- the fixed context

    // Closed-gate phase: feed silence with gate=false. Must NOT produce files.
    for (int i = 0; i < 20; ++i) {
        auto z = silenceBlock(960);
        auto saved = rec.feed(z, false);
        QVERIFY2(saved.empty(), "Closed-gate audio must not be recorded");
    }

    // Open-gate phase: feed a real tone for ~1 second (>= 250ms min segment).
    double ph = 0.0;
    QStringList produced;
    for (int i = 0; i < 60; ++i) {
        auto s = sineBlock(960, 1000.0, 0.5, ph);
        auto saved = rec.feed(s, true);
        for (const QString& f : saved) produced << f;
    }
    auto tail = rec.flush();
    for (const QString& f : tail) produced << f;

    QVERIFY2(!produced.isEmpty(), "Open-gate tone must produce a WAV segment");

    // Filename must carry the context (AM + 99.0MHz), not the old hard-coded
    // NFM/98.5MHz.
    QFileInfo fi(produced.first());
    const QString name = fi.fileName();
    qInfo() << "saved segment:" << name;
    QVERIFY2(name.contains("AM"), "Filename must contain the mode (AM)");
    QVERIFY2(name.contains("99000000"), "Filename must contain the frequency (99000000Hz)");
    QVERIFY2(!name.contains("NFM_98500000"),
             "Filename must NOT be the old hard-coded NFM/98.5MHz");

    // The saved WAV must not start with a long run of near-zero samples.
    // Read the raw RIFF data chunk back.
    FILE* fp = fopen(produced.first().toLocal8Bit().constData(), "rb");
    QVERIFY(fp);
    unsigned char hdr[44];
    size_t nr = fread(hdr, 1, 44, fp);
    Q_UNUSED(nr);
    // int16 samples follow. Count leading near-zero samples.
    int leadingZero = 0;
    bool sawNonZero = false;
    for (int i = 0; i < 4800; ++i) {   // first 100 ms @ 48k int16
        unsigned char b[2];
        if (fread(b, 1, 2, fp) != 2) break;
        int16_t s = static_cast<int16_t>(b[0] | (b[1] << 8));
        if (std::abs(s) < 30) {
            if (!sawNonZero) ++leadingZero;
        } else {
            sawNonZero = true;
        }
    }
    fclose(fp);
    // 30 ms = 1440 samples. The leading dead-silence must be well under that.
    QVERIFY2(leadingZero < 1440,
             "Saved segment must not begin with >30ms of dead silence");
}

void TestSquelchGate::cleanupTestCase() {
    if (!scratchDir.isEmpty()) QDir(scratchDir).removeRecursively();
}

QTEST_MAIN(TestSquelchGate)
#include "test_squelch_gate.moc"
