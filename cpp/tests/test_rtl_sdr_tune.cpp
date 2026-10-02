// SPDX-License-Identifier: MIT
// RTL-SDR device-layer robustness unit tests (phase12 gap-analysis L3 + L4).
//
// These drive RtlSdrSource through an injected FAKE librtlsdr ops table
// (rtlSetLibOpsForTesting). The fake is a pure TEST DOUBLE: in-memory function
// pointers recording a call log plus an imaginary PLL "parked frequency". It
// is NOT a hardware mock -- no USB, no RTL2832, no driver code path, no real
// device is touched; every branch is deterministic.
//
// Covers:
//  - L3: first write lost (readback mismatch) -> retry loop converges;
//  - L3: every attempt lost -> honest exhaustion (fixed retry budget, loud
//        warning telemetry, request kept, device NOT silently left tuned);
//  - L4: start() calls tuner bandwidth exactly once with arg 0 (= driver auto);
//  - open failure -> start() returns false honestly;
//  - regression: with no ops table installed, start() stays a graceful stub.
#include <QtTest>
#include "dsp/rtl_sdr_source.h"
#include "dsp/rtl_sdr_ops.h"

using namespace mbdsdr::dsp;

namespace {

// Fake device state. Process-global on purpose: the ops table carries no ctx,
// and QtTest runs its cases single-threaded. init() resets it per case.
struct FakeRtl {
    uint32_t parkedFreq = 0;     // what get_center_freq reports
    int      setCFRc = 0;        // rc handed back by set_center_freq
    int      loseFirstN = 0;      // first N writes "lost" (PLL parks elsewhere)
    int      setCFCalls = 0;
    int      setBWCalls = 0;
    int      lastBW = -999999;
    bool     openSucceeds = true;
    int      closeCalls = 0;

    static FakeRtl& inst() { static FakeRtl f; return f; }

    static int open(void** dev, uint32_t) {
        if (!inst().openSucceeds) return -1;
        *dev = reinterpret_cast<void*>(0x1);   // opaque fake handle
        return 0;
    }
    static void close(void*) { ++inst().closeCalls; }
    static int setCF(void*, uint32_t freq) {
        FakeRtl& s = inst();
        ++s.setCFCalls;
        if (s.loseFirstN > 0) {
            --s.loseFirstN;
            s.parkedFreq = freq - 1000;   // write lost: PLL parked elsewhere
            return s.setCFRc;             // driver may still report "ok"
        }
        s.parkedFreq = freq;
        return s.setCFRc;
    }
    static uint32_t getCF(void*) { return inst().parkedFreq; }
    static int setSR(void*, uint32_t) { return 0; }
    static int setBW(void*, int bw) {
        ++inst().setBWCalls;
        inst().lastBW = bw;
        return 0;
    }
    static int setGainMode(void*, int) { return 0; }
    static int setGain(void*, int) { return 0; }
    static int getGains(void*, int*) { return 0; }   // no discrete table (passthrough)
    static int getGain(void*) { return 0; }
    static int setAgc(void*, int) { return 0; }
    static int setDS(void*, int) { return 0; }
    static int setOffT(void*, int) { return 0; }
    static int setBias(void*, int) { return 0; }
    static int setPpm(void*, int) { return 0; }
    static int resetBuf(void*) { return 0; }
    static void cancelAsync(void*) {}
    static int readSync(void*, unsigned char*, uint32_t, uint32_t*) { return -1; }
};

const RtlLibOps g_fakeOps{
    &FakeRtl::open,             &FakeRtl::close,
    &FakeRtl::setCF,            &FakeRtl::getCF,
    &FakeRtl::setSR,            &FakeRtl::setBW,
    &FakeRtl::setGainMode,      &FakeRtl::setGain,
    &FakeRtl::getGains,        &FakeRtl::getGain,
    &FakeRtl::setAgc,           &FakeRtl::setDS,
    &FakeRtl::setOffT,          &FakeRtl::setBias,
    &FakeRtl::setPpm,           &FakeRtl::resetBuf,
    &FakeRtl::cancelAsync,      &FakeRtl::readSync
};

} // namespace

class TestRtlSdrTune : public QObject {
    Q_OBJECT
private slots:
    void init() {
        FakeRtl::inst() = FakeRtl{};
        rtlSetLibOpsForTesting(&g_fakeOps);
    }
    void cleanup() {
        rtlSetLibOpsForTesting(nullptr);
        FakeRtl::inst() = FakeRtl{};
    }

    void retriesUntilConverged();
    void exhaustedIsHonest();
    void startSetsBandwidthAuto();
    void openFailureIsHonest();
    void noOpsMeansStubStartFails();
};

// L3: the first two runtime writes are lost (rc ok, but the PLL parks 1 kHz
// away); the retry loop must converge on the third write and report it.
void TestRtlSdrTune::retriesUntilConverged() {
    RtlSdrSource src;
    QVERIFY(src.start());                 // opens the fake; start's own tune converges
    const int writesBefore = FakeRtl::inst().setCFCalls;

    FakeRtl::inst().loseFirstN = 2;       // next two runtime writes are lost
    src.setCenterFreq(109.7e6);

    QCOMPARE(FakeRtl::inst().setCFCalls - writesBefore, 3);  // 2 lost + 1 converged
    QVERIFY(src.lastTuneConverged());
    QCOMPARE(src.tuneAttemptsLast(), 3);
    QCOMPARE(src.centerFreq(), 109.7e6);  // request honored after readback matched
    src.stop();
    QCOMPARE(FakeRtl::inst().closeCalls, 1);
}

// L3: every write is lost AND the driver reports write errors. The retry budget
// must be fully consumed (no silent extra retries), the failure must be flagged
// honestly, the device stays open (it IS connected), and the request is kept
// for the next attempt -- never a fake "tuned to the wrong frequency".
void TestRtlSdrTune::exhaustedIsHonest() {
    RtlSdrSource src;
    QVERIFY(src.start());
    const int writesBefore = FakeRtl::inst().setCFCalls;

    FakeRtl::inst().setCFRc = -1;         // write errors every time
    FakeRtl::inst().loseFirstN = 99;      // ...and the PLL never parks on target
    src.setCenterFreq(145.5e6);

    QCOMPARE(FakeRtl::inst().setCFCalls - writesBefore, kRtlMaxTuneAttempts);
    QVERIFY(!src.lastTuneConverged());
    QCOMPARE(src.tuneAttemptsLast(), kRtlMaxTuneAttempts);
    QVERIFY(src.isConnected());           // device connected; failure was reported
    QCOMPARE(src.centerFreq(), 145.5e6); // request kept (replayed on next start)
    src.stop();
}

// L4: start() must explicitly ask the driver to pick the tuner bandwidth (0 =
// auto), exactly once, with the literal argument 0 -- not left to implicit
// librtlsdr defaults that would drift on future backends.
void TestRtlSdrTune::startSetsBandwidthAuto() {
    RtlSdrSource src;
    QVERIFY(src.start());
    QCOMPARE(FakeRtl::inst().setBWCalls, 1);
    QCOMPARE(FakeRtl::inst().lastBW, 0);
    src.stop();
}

// Open failure is honest: start() returns false, isConnected() stays false.
void TestRtlSdrTune::openFailureIsHonest() {
    FakeRtl::inst().openSucceeds = false;
    RtlSdrSource src;
    QVERIFY(!src.start());
    QVERIFY(!src.isConnected());
}

// Regression guard: with no ops table installed (e.g. librtlsdr not compiled in)
// the source stays the graceful stub -- start() fails, engine falls back.
void TestRtlSdrTune::noOpsMeansStubStartFails() {
    rtlSetLibOpsForTesting(nullptr);
    RtlSdrSource src;
    QVERIFY(!src.start());
    QVERIFY(!src.isConnected());
    QCOMPARE(src.name(), QStringLiteral("RTL-SDR (unsupported)"));
}

QTEST_MAIN(TestRtlSdrTune)
#include "test_rtl_sdr_tune.moc"
