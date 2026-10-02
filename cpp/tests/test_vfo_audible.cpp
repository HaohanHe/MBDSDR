// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 ***
//
// "Currently-audible VFO" contract test. The UI renders a [出声] tag on the row
// whose VfoMarker.selected == true; this test pins the underlying data contract:
// exactly one marker is selected, process() returns THAT channel's audio48k, and
// switching selection flips which buffer is routed to the speaker. Synthetic wide
// band IQ only, no radio.
#include <QtTest/QtTest>
#include <complex>
#include <vector>
#include <cmath>

#include "dsp/vfo_manager.h"

using namespace mbdsdr::dsp;

namespace {
// Two FM carriers at fixed offsets, each with its own tone (same synth as the
// multi-VFO test). We only need the buffers to be distinguishable.
std::vector<std::complex<float>> wideband(double fs, int n) {
    std::vector<std::complex<float>> out(n);
    double phA = 0.0, phB = 0.0;
    const double offA = 100e3, offB = -300e3, fdev = 3e3, tA = 1000.0, tB = 2000.0;
    for (int i = 0; i < n; ++i) {
        const double t = static_cast<double>(i);
        phA += 2.0 * M_PI * fdev / fs * std::cos(2.0 * M_PI * tA * t / fs);
        phB += 2.0 * M_PI * fdev / fs * std::cos(2.0 * M_PI * tB * t / fs);
        const double cA = 2.0 * M_PI * offA * t / fs + phA;
        const double cB = 2.0 * M_PI * offB * t / fs + phB;
        out[i] = {static_cast<float>(std::cos(cA) + std::cos(cB)),
                  static_cast<float>(std::sin(cA) + std::sin(cB))};
    }
    return out;
}
} // namespace

class TestVfoAudible : public QObject {
    Q_OBJECT
private slots:
    void audibleFollowsSelection();
};

void TestVfoAudible::audibleFollowsSelection() {
    const double fs = 2.4e6, center = 0.0;
    VfoManager mgr;
    mgr.initDefault(fs, center, "NFM", 12500.0);   // VFO A at +100 kHz carrier
    mgr.setFreq(mgr.selectedId(), +100e3);
    const int idA = mgr.selectedId();
    const int idB = mgr.addVfo(-300e3);            // VFO B at -300 kHz carrier
    mgr.setMode(idB, "NFM");

    // Warm up both channelizers.
    for (int blk = 0; blk < 30; ++blk)
        mgr.process(wideband(fs, 8192), fs, center);

    auto selectedMarkers = [&]() -> const VfoMarker* {
        for (const auto& mk : mgr.markers()) if (mk.selected) return new VfoMarker(mk);
        return nullptr;
    };

    // addVfo selected B -> B is audible.
    QCOMPARE(mgr.selectedId(), idB);
    {
        auto iq = wideband(fs, 8192);
        const std::vector<float>& out = mgr.process(iq, fs, center);
        QCOMPARE(&out, &mgr.channel(idB)->audio48k);   // speaker gets B
        auto* mB = selectedMarkers();
        QVERIFY(mB && mB->id == idB && mB->selected);
        delete mB;
    }

    // Switch to A -> the routed buffer and the selected marker both flip.
    QVERIFY(mgr.selectVfo(idA));
    {
        auto iq = wideband(fs, 8192);
        const std::vector<float>& out = mgr.process(iq, fs, center);
        QCOMPARE(&out, &mgr.channel(idA)->audio48k);   // speaker now gets A
        auto* mA = selectedMarkers();
        QVERIFY(mA && mA->id == idA && mA->selected);
        delete mA;
    }

    // Exactly one selected marker at all times.
    int selCount = 0;
    for (const auto& mk : mgr.markers()) if (mk.selected) ++selCount;
    QCOMPARE(selCount, 1);
}

QTEST_MAIN(TestVfoAudible)
#include "test_vfo_audible.moc"
