// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 — SYNTHETIC FIXTURE, TEST ONLY ***
//
// Phase24 Block3: scanner <-> scheduler linkage closed loop.
//
// The dsp-internal ScanActivityLink composes the FrequencyScanner and fires, on
// the Scanning->Hit edge, an "activity found" action; on the Hit->Scanning edge
// it fires "dwell ended". This test binds those seams to the REAL production
// pieces so the whole chain runs offline:
//
//   synthetic per-channel RSSI table  ->  FrequencyScanner (sweep + gate)
//     -> onRetune()        : receiver retunes to the scanner's channel
//     -> onActivityFound() : park on the hit channel, switch the VFO into the
//                            POCSAG decode mode, arm a real Recorder, and feed a
//                            synthetic 2-FSK POCSAG burst through the real
//                            VfoManager chain (channelizer -> FskDemod -> decoder)
//     -> onDwellEnded()    : stop/finalise the recorder, scan resumes
//
// We assert the FULL closed loop on the busy channel and the HONEST EMPTY state
// on a quiet band (no activity action, no decode, no file). No radio, no real
// RSSI, no network: the RSSI table and the 2-FSK burst are the synthetic fixture.
#include <QtTest/QtTest>

#include <algorithm>
#include <cmath>
#include <complex>
#include <string>
#include <vector>

#include <QDir>
#include <QFileInfo>

#include "dsp/scan_link.h"
#include "dsp/vfo_manager.h"
#include "dsp/pocsag_decoder.h"
#include "dsp/fsk_demod.h"
#include "dsp/recorder.h"
#include "core/tokens.h"

using namespace mbdsdr;
using namespace mbdsdr::dsp;

namespace {
constexpr double kPi    = 3.14159265358979323846;
constexpr double kTwoPi = 2.0 * kPi;
constexpr double kFs     = 48000.0;    // synthetic source rate (as in digital_link test)
constexpr double kCenter = 100.0e6;    // RF centre label; VFO offset is 0
constexpr int    kTickMs = 30;
constexpr float  kQuiet  = -80.0f;
constexpr float  kBusy   = -30.0f;
constexpr float  kThr    = -50.0f;

// --- 2-FSK modulator (POCSAG), identical paradigm to test_digital_link_integration
std::vector<std::complex<float>> fsk2Iq(const std::vector<int>& bits,
                                        double baud, double dev) {
    const int sps = static_cast<int>(std::lrint(kFs / baud));
    std::vector<std::complex<float>> iq;
    iq.reserve(bits.size() * sps);
    double phase = 0.0;
    for (int b : bits) {
        const double f = (b ? dev : -dev);
        for (int s = 0; s < sps; ++s) {
            phase += kTwoPi * f / kFs;
            iq.push_back({static_cast<float>(std::cos(phase)),
                          static_cast<float>(std::sin(phase))});
        }
    }
    return iq;
}

void feedAll(VfoManager& mgr, const std::vector<std::complex<float>>& iq) {
    constexpr std::size_t kChunk = 4096;
    for (std::size_t off = 0; off < iq.size(); off += kChunk) {
        std::vector<std::complex<float>> blk(iq.begin() + off,
            iq.begin() + std::min(off + kChunk, iq.size()));
        mgr.process(blk, kFs, kCenter);
    }
}

// Harness binding the link's action seams to a real VfoManager + Recorder.
struct LinkHarness {
    VfoManager   mgr;
    Recorder     rec;
    int          id = 0;
    QList<double> dwells;          // channels where onActivityFound fired
    QList<double> retunes;         // channels onRetune fired for
    int          dwellEnds = 0;
    bool         recWasArmed = false;   // recorder.start() succeeded during a dwell
    bool         recRecordingDuringDwell = false;
    std::vector<std::complex<float>> burst;
};

ScanConfig rangeCfg() {
    ScanConfig c;
    c.source = ScanSource::Range;
    c.startHz = 100.0e6;
    c.stopHz  = 100.3e6;
    c.stepHz  = 100e3;                 // -> [100.0,100.1,100.2,100.3] MHz
    c.dwellMs = 300;
    c.settleMs = 80;
    c.thresholdDb = kThr;
    c.direction = ScanDirection::Up;
    c.holdMode = HitHoldMode::FixedMs; // leave each hit after holdMs regardless
    c.holdMs   = 300;
    c.loop = false;
    return c;
}
} // namespace

class TestScanLink : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void closedLoopScanHitDwellDecodeRecord();
    void quietBandIsHonestEmpty();
};

void TestScanLink::initTestCase() {
    QDir().rmdir("test_tmp_scanlink");
    QDir().mkpath("test_tmp_scanlink");
}

// The one busy channel carries a real (synthetic) POCSAG burst. The link must
// sweep to it, park, decode the SAME message, and write a recording segment.
void TestScanLink::closedLoopScanHitDwellDecodeRecord() {
    LinkHarness h;
    h.mgr.initDefault(kFs, kCenter, "NFM", 12000.0);
    h.id = h.mgr.selectedId();
    h.burst = fsk2Iq(PocsagDecoder::buildFrameBits(12345, "08671234"),
                     tokens::kPocsagBaudBd, tokens::kPocsagDeviationHz);

    ScanActivityLink link;
    link.setConfig(rangeCfg());

    ScanLinkActions a;
    a.onRetune = [&](double f) { h.retunes.append(f); };
    a.onActivityFound = [&](double f, float /*lvl*/) {
        h.dwells.append(f);
        // 驻留/调谐到解码: switch the parked VFO into the POCSAG data-link.
        h.mgr.setMode(h.id, "POCSAG");
        // 触发录制: arm a real SigMF recorder for this activity.
        h.recWasArmed = h.rec.start("test_tmp_scanlink", kFs, f, 0.0,
                                    "Synthetic-Not-HW");
        h.rec.writeIQ(h.burst);
        h.recRecordingDuringDwell = h.rec.isRecording();
        // Decode through the real chain.
        feedAll(h.mgr, h.burst);
    };
    a.onDwellEnded = [&](double /*f*/) {
        ++h.dwellEnds;
        h.rec.stop();   // finalise the segment
    };
    link.setActions(a);

    // *** FIXTURE ***: 100.2 MHz is the only occupied channel.
    QMap<double, float> rssi;
    rssi[100.2e6] = kBusy;

    link.start();
    int guard = 5000;
    while (link.state() != ScanLinkState::Idle && guard-- > 0) {
        const double f = link.scanner().currentFrequency();
        link.tick(kTickMs, rssi.value(f, kQuiet));
    }
    QCOMPARE(link.state(), ScanLinkState::Idle);

    // --- Sweep reached exactly the busy channel and parked there. ---
    QCOMPARE(h.dwells.size(), 1);
    QCOMPARE(h.dwells[0], 100.2e6);
    QCOMPARE(link.dwellCount(), 1);
    QCOMPARE(link.parkedFrequency(), 0.0);   // dwell ended -> no longer parked

    // Retune walk covers the whole lattice (scan -> retune to the hit channel).
    QVERIFY(h.retunes.contains(100.2e6));
    QVERIFY(h.retunes.size() >= 4);

    // --- Recording was triggered DURING the dwell and closed at dwell end. ---
    QVERIFY2(h.recWasArmed, "activity must arm a real recorder");
    QVERIFY2(h.recRecordingDuringDwell, "recorder must be recording while parked");
    QCOMPARE(h.dwellEnds, 1);
    QVERIFY2(!h.rec.isRecording(), "dwell end must finalise (stop) the recorder");

    // A real SigMF segment must have been written for the activity.
    bool wroteFile = false;
    for (const QFileInfo& fi : QDir("test_tmp_scanlink").entryInfoList({"*.sigmf-data"}))
        if (fi.size() > 0) wroteFile = true;
    QVERIFY2(wroteFile, "the activity must produce a non-empty SigMF recording");

    // --- Decode: the parked POCSAG channel surfaced the SAME known message. ---
    auto msgs = h.mgr.pocsagMessages(h.id);
    QCOMPARE(msgs.size(), std::size_t(1));
    QCOMPARE(msgs[0].address, std::uint32_t(12345));
    QCOMPARE(msgs[0].text, std::string("08671234"));
}

// A perfectly quiet band must NOT fabricate an activity: no dwell, no decode,
// no recorder arm, no file. This guards against the closed loop self-triggering.
void TestScanLink::quietBandIsHonestEmpty() {
    LinkHarness h;
    h.mgr.initDefault(kFs, kCenter, "NFM", 12000.0);
    h.id = h.mgr.selectedId();

    ScanActivityLink link;
    link.setConfig(rangeCfg());
    ScanLinkActions a;
    a.onRetune = [&](double f) { h.retunes.append(f); };
    a.onActivityFound = [&](double f, float) {
        h.dwells.append(f);   // must NEVER fire
    };
    a.onDwellEnded = [&](double) { ++h.dwellEnds; };
    link.setActions(a);

    QMap<double, float> rssi;   // empty map -> every channel reads kQuiet

    link.start();
    int guard = 5000;
    while (link.state() != ScanLinkState::Idle && guard-- > 0) {
        const double f = link.scanner().currentFrequency();
        link.tick(kTickMs, rssi.value(f, kQuiet));
    }
    QCOMPARE(link.state(), ScanLinkState::Idle);

    // Scanning still walked the whole lattice, but found nothing.
    QVERIFY(h.retunes.size() >= 4);
    QCOMPARE(h.dwells.size(), 0);
    QCOMPARE(link.dwellCount(), 0);
    QCOMPARE(h.dwellEnds, 0);
    QVERIFY2(!h.recWasArmed, "a quiet band must NOT arm a recorder");
    QVERIFY2(h.mgr.pocsagMessages(h.id).empty(),
             "a quiet band must NOT fabricate a decoded message");
}

QTEST_MAIN(TestScanLink)
#include "test_scan_link.moc"
