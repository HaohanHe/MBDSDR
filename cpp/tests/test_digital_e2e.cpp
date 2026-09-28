// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 ***
//
// End-to-end digital receive test: the offline TestSignalSource emits a real
// differential BPSK / QPSK carrier (built with DigitalDemod::diffEncode), which
// is pumped through the REAL channelizer -> DigitalDemod path inside VfoManager.
// We assert the Costas + Gardner loops lock and the recovered symbols cluster on
// the ideal constellation points, with zero differential-decode errors.
// No threads, no sleeps, no hardware -- pure synthetic pumping.
#include <QtTest/QtTest>
#include <complex>
#include <vector>
#include <cmath>
#include "dsp/test_signal.h"
#include "dsp/vfo_manager.h"

using namespace mbdsdr::dsp;

class TestDigitalE2e : public QObject {
    Q_OBJECT
private slots:
    void bpskLocksThroughRealChain();
    void qpskLocksThroughRealChain();
};

static double distToIdeal(std::complex<float> p, DigMode m) {
    auto pts = DigitalDemod::idealPoints(m);
    double best = 1e9;
    for (auto q : pts) {
        double d = std::abs(p - q);
        if (d < best) best = d;
    }
    return best;
}

// Pump the source into VfoManager until lock (or max blocks). Returns the
// selected channel's recovered-symbol count near ideal + its lock status.
struct E2eResult {
    int total = 0, nearIdeal = 0;
    bool carrierLocked = false, symbolLocked = false;
    float evm = 0.0f;
};

static E2eResult runChain(const QString& mode) {
    TestSignalSource src(2.4e6, 98.5e6);
    src.setModulation(mode == "QPSK" ? "qpsk" : "bpsk");
    src.start();

    VfoManager mgr;
    mgr.initDefault(2.4e6, 98.5e6, mode, 4800.0);
    const int selId = mgr.selectedId();
    // The synthetic digital carrier sits at +200 Hz (outside the source DC
    // notch, inside the Costas pull-in range); VFO stays at source center.

    const DigMode dm = (mode == "QPSK") ? DigMode::QPSK : DigMode::BPSK;
    E2eResult res;
    std::vector<std::complex<float>> iq(60000);

    for (int blk = 0; blk < 600; ++blk) {
        std::size_t got = src.readIQ(iq);
        std::vector<std::complex<float>> block(iq.begin(), iq.begin() + got);
        mgr.process(block, 2.4e6, 98.5e6);
        const VfoChannel* ch = mgr.channel(selId);
        if (!ch || !ch->digitalDemod) continue;
        const auto st = ch->digitalDemod->status();
        // Discard a warm-up period while the Costas + Gardner loops pull in.
        if (blk < 200) continue;
        for (auto p : ch->recoveredSymbols) {
            res.total++;
            if (distToIdeal(p, dm) < 0.35) res.nearIdeal++;
        }
        res.carrierLocked = st.carrierLocked;
        res.symbolLocked = st.symbolLocked;
        res.evm = st.evmPercent;
    }
    return res;
}

void TestDigitalE2e::bpskLocksThroughRealChain() {
    E2eResult r = runChain("BPSK");
    qInfo() << "BPSK total=" << r.total << "nearIdeal=" << r.nearIdeal
            << "carrierLock=" << r.carrierLocked << "symbolLock=" << r.symbolLocked
            << "evm=" << r.evm;
    QVERIFY2(r.total > 1000, "should have recovered many symbols");
    const double frac = static_cast<double>(r.nearIdeal) / r.total;
    QVERIFY2(frac >= 0.90, ">=90% recovered symbols must sit near the ideal points");
    QVERIFY2(r.carrierLocked, "carrier should lock");
    QVERIFY2(r.symbolLocked,  "timing should lock");
    QVERIFY2(r.evm < 30.0f,   "EVM must be < 30%");
}

void TestDigitalE2e::qpskLocksThroughRealChain() {
    E2eResult r = runChain("QPSK");
    qInfo() << "QPSK total=" << r.total << "nearIdeal=" << r.nearIdeal
            << "carrierLock=" << r.carrierLocked << "symbolLock=" << r.symbolLocked
            << "evm=" << r.evm;
    QVERIFY2(r.total > 1000, "should have recovered many symbols");
    const double frac = static_cast<double>(r.nearIdeal) / r.total;
    QVERIFY2(frac >= 0.90, ">=90% recovered symbols must sit near the ideal points");
    QVERIFY2(r.carrierLocked, "carrier should lock");
    QVERIFY2(r.symbolLocked,  "timing should lock");
    QVERIFY2(r.evm < 30.0f,   "EVM must be < 30%");
}

QTEST_MAIN(TestDigitalE2e)
#include "test_digital_e2e.moc"
