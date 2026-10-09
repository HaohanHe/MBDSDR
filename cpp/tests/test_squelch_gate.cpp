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
#include "dsp/ctcss.h"
#include "dsp/cdcss.h"
#include "core/tokens.h"

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

// Deterministic white-noise source (fixed-seed LCG, [-1,1]) so the CTCSS
// detector tests are reproducible. *** SYNTHETIC -- NOT HARDWARE ***
double lcg = 0xC0FFEE;
double whiteNoise() {
    lcg = lcg * 1664525.0 + 1013904223.0;
    return (static_cast<double>(static_cast<unsigned long>(lcg) & 0xFFFFFFu)
            / static_cast<double>(0xFFFFFFu)) * 2.0 - 1.0;
}
// Audio block = sine at toneHz + white noise (std ~= 0.03). Real numbers --
// the detector sees a genuine sub-audible tone riding on band noise.
std::vector<float> tonePlusNoiseBlock(int n, double toneHz, double amp,
                                      double noiseStd, double& phase) {
    std::vector<float> v(n);
    for (int i = 0; i < n; ++i) {
        phase += 2.0 * M_PI * toneHz / 48000.0;
        v[i] = static_cast<float>(amp * std::sin(phase) + noiseStd * whiteNoise());
    }
    return v;
}
} // namespace

// ---------------------------------------------------------------------------
// CDCSS/DCS test-side synthesizer (*** SYNTHETIC -- NOT HARDWARE ***).
//
// Clean-room Golay(23,12) encoder (same public polynomial 0xC75, re-implemented
// here so the product code is not exercised by its own test synthesizer).
// Bits are emitted LSB-first, Manchester-II encoded (bit 1 = high-then-low),
// and key a 1500 Hz sine at +/- amplitude (BPSK).  A fixed-seed LCG adds
// band noise so the test sees a real signal-plus-noise waveform, not a bit array.
namespace {
constexpr uint32_t kTestGolayGen = 0xC75u;
uint32_t testGolayEncode(uint32_t data12) {
    const uint32_t cw = (data12 & 0xFFFu) << 11;
    uint32_t work = cw;
    for (int i = 22; i >= 11; --i)
        if ((work >> i) & 1u) work ^= kTestGolayGen << (i - 11);
    return (cw | (work & 0x7FFu)) & 0x7FFFFFu;
}

// Repeat-emit the 23-bit Golay word for durationSec seconds, LSB-first.
// The half-bit grid mirrors the decoder's fractional countdown exactly, so
// the strobe windows stay aligned over long runs (no integer-truncation drift).
std::vector<float> synthesizeDcs(int code12, double durationSec,
                                 double amp, double noiseStd, double& phase) {
    const double sr = 48000.0;
    const double halfBit = sr / (2.0 * 134.4);
    const int total = static_cast<int>(durationSec * sr);
    std::vector<float> out(total, 0.0f);
    const uint32_t cw = testGolayEncode(static_cast<uint32_t>(code12));
    double fbCountdown = halfBit;
    int half = 0;
    for (int s = 0; s < total; ++s) {
        const int bitIdx = (half / 2) % 23;
        const bool firstHalf = (half % 2) == 0;
        const bool bit = (cw >> bitIdx) & 1u;
        const bool level = firstHalf ? bit : !bit;
        const double pol = level ? +1.0 : -1.0;
        phase += 2.0 * M_PI * 1500.0 / sr;
        out[s] = static_cast<float>(amp * pol * std::sin(phase) + noiseStd * whiteNoise());
        fbCountdown -= 1.0;
        if (fbCountdown <= 0.0) { fbCountdown += halfBit; ++half; }
    }
    return out;
}
} // namespace

class TestSquelchGate : public QObject {
    Q_OBJECT
private slots:
    void gateOpensOnSignalClosesOnSilence();
    void offModePassthrough();
    void gatedRecorderSavesOnlyWhenOpenAndLabelsFilename();
    void ctcssDetectsConfiguredTone();
    void ctcssFalseOnNoiseOnly();
    void ctcssRejectsWrongFrequency();
    void ctcssDisabledStaysFalse();
    void cdcssDetectsConfiguredCode();
    void cdcssFalseOnWrongCode();
    void cdcssCorrectsBitFlips();
    void cdcssDisabledStaysFalse();
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

// (a) Real 88.5 Hz sub-audible tone riding on noise, configured for 88.5 Hz:
// the Goertzel latch must come up true (and drop again when the tone leaves).
void TestSquelchGate::ctcssDetectsConfiguredTone() {
    CtcssToneDetector det;
    det.configure(48000.0, 88.5);
    det.setEnabled(true);
    QCOMPARE(det.binBandwidthHz(), 5.0);   // N = 48000/5 = 9600

    double ph = 0.0;
    bool seen = false;
    // ~1.2 s of tone+noise (>= 1 measurement cycle + margin).
    for (int i = 0; i < 60 && !seen; ++i) {
        auto blk = tonePlusNoiseBlock(960, 88.5, 0.10, 0.03, ph);
        det.process(blk.data(), static_cast<int>(blk.size()));
        if (det.tonePresent()) seen = true;
    }
    QVERIFY2(seen, "Configured 88.5 Hz tone must latch present");

    // Tone gone (noise only): hangover then drops it honest.
    bool dropped = false;
    for (int i = 0; i < 120 && !dropped; ++i) {
        std::vector<float> blk(960);
        for (auto& s : blk) s = static_cast<float>(0.03 * whiteNoise());
        det.process(blk.data(), static_cast<int>(blk.size()));
        if (!det.tonePresent()) dropped = true;
    }
    QVERIFY2(dropped, "Present must drop after hangover when tone leaves");
}

// (b) Noise-only audio, no sub-audible tone: the latch must stay false.
void TestSquelchGate::ctcssFalseOnNoiseOnly() {
    CtcssToneDetector det;
    det.configure(48000.0, 88.5);
    det.setEnabled(true);
    for (int i = 0; i < 120; ++i) {
        std::vector<float> blk(960);
        for (auto& s : blk) s = static_cast<float>(0.03 * whiteNoise());
        det.process(blk.data(), static_cast<int>(blk.size()));
        QVERIFY2(!det.tonePresent(), "Noise-only must never latch a tone");
    }
}

// (c) Frequency discrimination: a real 88.5 Hz tone, but the detector is
// configured for 67.0 Hz -> wrong bin -> must stay false.
void TestSquelchGate::ctcssRejectsWrongFrequency() {
    CtcssToneDetector det;
    det.configure(48000.0, 67.0);
    det.setEnabled(true);
    double ph = 0.0;
    for (int i = 0; i < 80; ++i) {
        auto blk = tonePlusNoiseBlock(960, 88.5, 0.10, 0.03, ph);
        det.process(blk.data(), static_cast<int>(blk.size()));
        QVERIFY2(!det.tonePresent(),
                 "88.5 Hz tone tuned into the 67.0 Hz bin must not latch");
    }
}

// (d) Disabled: even with the real tone present, tonePresent() is always false.
void TestSquelchGate::ctcssDisabledStaysFalse() {
    CtcssToneDetector det;
    det.configure(48000.0, 88.5);
    det.setEnabled(false);
    double ph = 0.0;
    for (int i = 0; i < 40; ++i) {
        auto blk = tonePlusNoiseBlock(960, 88.5, 0.10, 0.03, ph);
        det.process(blk.data(), static_cast<int>(blk.size()));
        QVERIFY2(!det.tonePresent(), "Disabled detector must read false");
    }
    // An out-of-domain configure must be rejected (keeps 88.5).
    det.configure(48000.0, 400.0);
    QCOMPARE(det.toneHz(), 88.5);
}

// (e) CDCSS: a real 023 DCS stream (Golay+Manchester, on a 1500 Hz keyed sine
// riding on noise) must latch codePresent() and report lastCode()==023.
void TestSquelchGate::cdcssDetectsConfiguredCode() {
    CdcssDecoder dec;
    dec.configure(48000.0, 023);        // octal 023
    dec.setEnabled(true);
    QCOMPARE(dec.configuredCode(), 023);

    double ph = 0.0;
    bool seen = false;
    // ~1.2 s of DCS stream (>= 3 latch words).
    auto blk = synthesizeDcs(023, 1.2, 0.20, 0.02, ph);
    // Feed in 20 ms chunks to exercise the streaming block path.
    for (size_t off = 0; off + 960 <= blk.size(); off += 960) {
        dec.process(blk.data() + off, 960);
        if (dec.codePresent()) { seen = true; break; }
    }
    QVERIFY2(seen, "Configured 023 DCS stream must latch present");
    QCOMPARE(dec.lastCode(), 023);
}

// (f) CDCSS: the on-air stream is code 025 but the decoder is tuned to 023 ->
// codePresent() must stay false (no fabrication).
void TestSquelchGate::cdcssFalseOnWrongCode() {
    CdcssDecoder dec;
    dec.configure(48000.0, 023);
    dec.setEnabled(true);
    double ph = 0.0;
    auto blk = synthesizeDcs(025, 1.5, 0.20, 0.02, ph);
    for (size_t off = 0; off + 960 <= blk.size(); off += 960) {
        dec.process(blk.data() + off, 960);
        QVERIFY2(!dec.codePresent(),
                 "On-air 025 must not latch when tuned to 023");
    }
    QCOMPARE(dec.lastCode(), 025);       // honest: it DID decode 025, just not matched
}

// (g) CDCSS: Golay(23,12) must correct 1-2 bit flips.  We synthesize the 023
// stream, then in the test-side bitstream we flip 1 and 2 bits per word before
// re-emitting; the decoder must still latch 023.
void TestSquelchGate::cdcssCorrectsBitFlips() {
    // Build a corrupted stream: encode 023, then XOR 1 and 2 bit-flips into
    // every emitted 23-bit word.  The DSP decoder's Golay table must fix them.
    const double sr = 48000.0;
    const double halfBit = sr / (2.0 * 134.4);
    const int total = static_cast<int>(1.5 * sr);
    const uint32_t clean = testGolayEncode(023);
    // Flip bit 5 and bit 17 of every word (2-bit error).
    const uint32_t corrupted = clean ^ (1u << 5) ^ (1u << 17);

    double ph = 0.0;
    std::vector<float> blk(total, 0.0f);
    double fbCountdown = halfBit;
    int half = 0;
    for (int s = 0; s < total; ++s) {
        const int bitIdx = (half / 2) % 23;
        const bool firstHalf = (half % 2) == 0;
        const bool bit = (corrupted >> bitIdx) & 1u;
        const bool level = firstHalf ? bit : !bit;
        const double pol = level ? +1.0 : -1.0;
        ph += 2.0 * M_PI * 1500.0 / sr;
        blk[s] = static_cast<float>(0.20 * pol * std::sin(ph) + 0.02 * whiteNoise());
        fbCountdown -= 1.0;
        if (fbCountdown <= 0.0) { fbCountdown += halfBit; ++half; }
    }

    CdcssDecoder dec;
    dec.configure(48000.0, 023);
    dec.setEnabled(true);
    bool seen = false;
    for (size_t off = 0; off + 960 <= blk.size(); off += 960) {
        dec.process(blk.data() + off, 960);
        if (dec.codePresent()) { seen = true; break; }
    }
    QVERIFY2(seen, "Golay(23,12) must correct 2 bit flips and still latch 023");
    QCOMPARE(dec.lastCode(), 023);
}

// (h) CDCSS: disabled -> even with the real 023 stream present, codePresent()
// is always false.
void TestSquelchGate::cdcssDisabledStaysFalse() {
    CdcssDecoder dec;
    dec.configure(48000.0, 023);
    dec.setEnabled(false);
    double ph = 0.0;
    auto blk = synthesizeDcs(023, 1.0, 0.20, 0.02, ph);
    for (size_t off = 0; off + 960 <= blk.size(); off += 960) {
        dec.process(blk.data() + off, 960);
        QVERIFY2(!dec.codePresent(), "Disabled CDCSS must read false");
    }
    // Out-of-table configure must be rejected.
    dec.configure(48000.0, 0777);
    QCOMPARE(dec.configuredCode(), 023);
}

void TestSquelchGate::cleanupTestCase() {
    if (!scratchDir.isEmpty()) QDir(scratchDir).removeRecursively();
}

QTEST_MAIN(TestSquelchGate)
#include "test_squelch_gate.moc"
