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
    // ---- sample rate (F2/F3) ----
    uint32_t lastSR = 0;           // rate handed to set_sample_rate
    uint32_t readbackSR = 0;       // what get_sample_rate reports (0 = unknown)
    int      setSRRc = 0;          // rc handed back by set_sample_rate
    int      setSRCalls = 0;       // how many pushes were really attempted
    bool     srReadbackDiffers = false;  // rc ok but driver parks 1 kHz lower

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
    static int setSR(void*, uint32_t rate) {
        FakeRtl& s = inst();
        ++s.setSRCalls;
        s.lastSR = rate;
        if (s.setSRRc != 0) return s.setSRRc;   // API refuses the rate
        s.readbackSR = s.srReadbackDiffers ? rate - 1000 : rate;
        return 0;
    }
    static uint32_t getSR(void*) { return inst().readbackSR; }
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
    static int getTunerType(void*) { return 5; }   // R820T on the wire enum
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
    &FakeRtl::cancelAsync,      &FakeRtl::readSync,
    &FakeRtl::getTunerType,     &FakeRtl::getSR
};

// Same fake minus the appended getSampleRate entry: an older positional init
// compiles with the tail zero-filled. There is NO readback then -- the source
// must say "采样率=请求值，硬件读回不可用" instead of echoing the request.
const RtlLibOps g_fakeOpsNoSampleRateReadback{
    &FakeRtl::open,             &FakeRtl::close,
    &FakeRtl::setCF,            &FakeRtl::getCF,
    &FakeRtl::setSR,            &FakeRtl::setBW,
    &FakeRtl::setGainMode,      &FakeRtl::setGain,
    &FakeRtl::getGains,        &FakeRtl::getGain,
    &FakeRtl::setAgc,           &FakeRtl::setDS,
    &FakeRtl::setOffT,          &FakeRtl::setBias,
    &FakeRtl::setPpm,           &FakeRtl::resetBuf,
    &FakeRtl::cancelAsync,      &FakeRtl::readSync,
    &FakeRtl::getTunerType
};

// Same fake minus the appended getTunerType entry: an older positional init
// compiles with the tail zero-filled, and the source must degrade to the
// honest "未知调谐器" instead of crashing or inventing an identity.
const RtlLibOps g_fakeOpsNoTunerType{
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
    void capabilitiesReportRealIdentity();
    void capabilitiesWithoutTunerTypeQueryAreHonest();
    void capabilitiesNoDeviceIsEmpty();
    // ---- F2/F3: sample-rate push is checked, readback is honest -----------
    void sampleRateRcFailureIsVisible();      // runtime push refuses (rc != 0)
    void sampleRateRcFailureFailsStart();     // first push refuses -> start fails
    void sampleRateReadbackMismatchIsExposed();// rc ok, device parked elsewhere
    void sampleRateReadbackUnavailableIsHonest();// no getSampleRate -> request only
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

// Regression guard: with the ops table forced null (e.g. librtlsdr not compiled
// in) the source stays the graceful stub -- start() fails, engine falls back.
void TestRtlSdrTune::noOpsMeansStubStartFails() {
    rtlForceNoLibOpsForTesting();
    RtlSdrSource src;
    QVERIFY(!src.start());
    QVERIFY(!src.isConnected());
    QCOMPARE(src.name(), QStringLiteral("RTL-SDR (unsupported)"));
}

// The UI device-info panel reads capabilities(); while a device streams the
// source must report the tuner identity the driver returned plus the real
// driver-known ranges -- never the base-class "no device" empty state.
void TestRtlSdrTune::capabilitiesReportRealIdentity() {
    RtlSdrSource src;
    QVERIFY(!src.capabilities().connected);   // honest empty before start
    QVERIFY(src.start());
    const DeviceCapabilities c = src.capabilities();
    QVERIFY(c.connected);
    QVERIFY(c.deviceName.contains("R820T"));  // fake getTunerType returns 5
    QCOMPARE(c.tunableMinHz, 24.0e6);
    QCOMPARE(c.tunableMaxHz, 1766.0e6);
    QVERIFY(c.provenance.contains(QStringLiteral("本机 librtlsdr 驱动查询")));
    src.stop();
    QVERIFY(!src.capabilities().connected);   // honest empty after stop
}

// An ops table without the getTunerType entry (older positional test init):
// capabilities stay connected but the tuner is honestly Unknown -- no crash,
// no fabricated identity, no guessed range (0 = 未知).
void TestRtlSdrTune::capabilitiesWithoutTunerTypeQueryAreHonest() {
    rtlSetLibOpsForTesting(&g_fakeOpsNoTunerType);
    RtlSdrSource src;
    QVERIFY(src.start());
    const DeviceCapabilities c = src.capabilities();
    QVERIFY(c.connected);
    QVERIFY(c.deviceName.contains(QStringLiteral("未知调谐器")));
    QVERIFY(c.tunableMinHz == 0.0);
    QVERIFY(c.tunableMaxHz == 0.0);
    src.stop();
}

// start() that never opened a device leaves the honest empty state untouched.
void TestRtlSdrTune::capabilitiesNoDeviceIsEmpty() {
    FakeRtl::inst().openSucceeds = false;
    RtlSdrSource src;
    QVERIFY(!src.start());
    QVERIFY(!src.capabilities().connected);
    QVERIFY(src.capabilities().deviceName.isEmpty()
           || src.capabilities().deviceName.contains(QStringLiteral("未连接")));
}

// F2 counter-proof: before the fix setSampleRate() ignored the driver rc, so a
// refused rate looked applied. Now the rc is recorded, the failure is loud, the
// request stays for the next retry, and no readback is invented.
void TestRtlSdrTune::sampleRateRcFailureIsVisible() {
    RtlSdrSource src;
    QVERIFY(src.start());
    FakeRtl::inst().setSRRc = -22;                    // -EINVAL from rtl-sdr.h
    src.setSampleRate(3200000.0);

    QCOMPARE(FakeRtl::inst().lastSR, 3200000u);        // the write was really tried
    QCOMPARE(FakeRtl::inst().setSRCalls, 2);           // start's 2.4 MS/s + this push
    QCOMPARE(src.lastSampleRateRc(), -22);
    QVERIFY(!src.lastSampleRateApplied());
    QVERIFY(!src.lastSampleRateError().isEmpty());
    QVERIFY(src.lastSampleRateError().contains(QStringLiteral("rc=-22")));
    QVERIFY(src.lastSampleRateError().contains(QStringLiteral("未生效")));
    QVERIFY(src.lastSampleRateError().contains(QStringLiteral("保留原采样率 2400000")));
    QCOMPARE(src.sampleRate(), 2400000.0);             // previous rate kept, not 3.2 MS/s
    QVERIFY(!src.sampleRateReadbackAvailable());       // nothing applied -> no readback
    QVERIFY(src.sampleRateStatusNote().contains(QStringLiteral("硬件读回不可用")));
    src.stop();
}

// F2 at start(): a refused first push must fail start() (there is no honest way
// to stream at an assumed rate); the device is closed again and nothing pretends.
void TestRtlSdrTune::sampleRateRcFailureFailsStart() {
    FakeRtl::inst().setSRRc = -22;
    RtlSdrSource src;
    QVERIFY(!src.start());
    QVERIFY(!src.isConnected());
    QVERIFY(src.lastSampleRateError().contains(QStringLiteral("rc=-22")));
    QCOMPARE(FakeRtl::inst().closeCalls, 1);           // opened then closed
    QCOMPARE(FakeRtl::inst().setBWCalls, 0);           // aborted before later steps
}

// F3 counter-proof: rc ok but the driver parks 1 kHz lower. Both values must be
// reported (never the request posing as applied), and sampleRate() must expose
// the DEVICE value so the DSP axis uses what is really running.
void TestRtlSdrTune::sampleRateReadbackMismatchIsExposed() {
    FakeRtl::inst().srReadbackDiffers = true;
    RtlSdrSource src;
    QVERIFY(src.start());                              // rc ok -> start still succeeds
    QVERIFY(src.sampleRateReadbackAvailable());
    QCOMPARE(src.sampleRateReadbackHz(), 2399000.0);   // 2.4 MS/s parked at -1 kHz
    QCOMPARE(src.sampleRate(), 2399000.0);             // device value, not the request
    QVERIFY(src.lastSampleRateError().contains(QStringLiteral("不一致")));
    QVERIFY(src.lastSampleRateError().contains(QStringLiteral("请求值 2400000")));
    QVERIFY(src.lastSampleRateError().contains(QStringLiteral("读回 2399000")));
    QVERIFY(!src.lastSampleRateConverged());

    src.setSampleRate(2048000.0);
    QCOMPARE(src.sampleRateReadbackHz(), 2047000.0);
    QCOMPARE(src.sampleRate(), 2047000.0);
    QVERIFY(src.sampleRateStatusNote().contains(QStringLiteral("硬件读回")));
    src.stop();
    QVERIFY(!src.sampleRateReadbackAvailable());       // stale readback dropped
    QCOMPARE(src.sampleRate(), 2048000.0);             // back to the honest request
}

// F3 counter-proof: an ops table without getSampleRate (older positional init /
// stub build) has NO readback. The status must label the request as a request.
void TestRtlSdrTune::sampleRateReadbackUnavailableIsHonest() {
    rtlSetLibOpsForTesting(&g_fakeOpsNoSampleRateReadback);
    RtlSdrSource src;
    QVERIFY(src.start());
    QVERIFY(!src.sampleRateReadbackAvailable());
    QCOMPARE(src.sampleRateReadbackHz(), 0.0);
    QCOMPARE(src.sampleRate(), 2400000.0);             // the request, honestly labelled
    QCOMPARE(src.sampleRateStatusNote(),
             QStringLiteral("采样率=请求值，硬件读回不可用"));

    src.setSampleRate(2048000.0);
    QVERIFY(!src.sampleRateReadbackAvailable());
    QCOMPARE(src.sampleRate(), 2048000.0);
    QVERIFY(src.lastSampleRateError().isEmpty());      // rc ok, no fake mismatch
    src.stop();
}

QTEST_MAIN(TestRtlSdrTune)
#include "test_rtl_sdr_tune.moc"
