// SPDX-License-Identifier: MIT
// B3/B4 offscreen tests: per-mode default bandwidth table, the mode-switch
// coverage rule, the engine applying the preset via the real setBandwidth, and
// a lock on the REAL PowerSpectrum frame-average (ring-buffer linear-power
// moving average), not a demo.
#include <QtTest/QtTest>
#include <QString>
#include <cmath>
#include <vector>
#include <complex>

#include "core/bandwidth_preset.h"
#include "dsp/power_spectrum.h"
#include "dsp/spectrum_engine.h"

using namespace mbdsdr;

class TestBandwidthPreset : public QObject {
    Q_OBJECT
private slots:
    void tableCoversEveryMode();
    void coverageRuleAdoptsDefaultWhenUntouched();
    void coverageRulePreservesManualBandwidth();
    void engineDemodModeAppliesRealBandwidth();
    void powerSpectrumAverageIsRealMovingAverage();
};

void TestBandwidthPreset::tableCoversEveryMode() {
    QCOMPARE(core::defaultBandwidthHzForMode("NFM"),   core::kBwNfmHz);
    QCOMPARE(core::defaultBandwidthHzForMode("WFM"),   core::kBwWfmHz);
    QCOMPARE(core::defaultBandwidthHzForMode("AM"),    core::kBwAmHz);
    QCOMPARE(core::defaultBandwidthHzForMode("USB"),    core::kBwSsbHz);
    QCOMPARE(core::defaultBandwidthHzForMode("LSB"),    core::kBwSsbHz);
    QCOMPARE(core::defaultBandwidthHzForMode("CW"),     core::kBwCwHz);
    QCOMPARE(core::defaultBandwidthHzForMode("BPSK"),   core::kBwDigitalHz);
    QCOMPARE(core::defaultBandwidthHzForMode("QPSK"),   core::kBwDigitalHz);
    QCOMPARE(core::defaultBandwidthHzForMode("ADS-B"), core::kBwAdsbHz);
    // Explicit numbers from the spec.
    QCOMPARE(core::defaultBandwidthHzForMode("NFM"),   12500.0);
    QCOMPARE(core::defaultBandwidthHzForMode("WFM"),   200000.0);
    QCOMPARE(core::defaultBandwidthHzForMode("AM"),    9000.0);
    QCOMPARE(core::defaultBandwidthHzForMode("USB"),   2400.0);
    QCOMPARE(core::defaultBandwidthHzForMode("CW"),    500.0);
    QCOMPARE(core::defaultBandwidthHzForMode("BPSK"),  12000.0);
    QCOMPARE(core::defaultBandwidthHzForMode("ADS-B"), 2000000.0);
    // Unknown mode -> fallback.
    QCOMPARE(core::defaultBandwidthHzForMode("ZZZ"),   core::kBwFallbackHz);
}

void TestBandwidthPreset::coverageRuleAdoptsDefaultWhenUntouched() {
    // NFM default 12500; user never tweaked it -> switching to WFM adopts 200k.
    const double r = core::bandwidthOnModeSwitch("NFM", "WFM", 12500.0);
    QCOMPARE(r, core::kBwWfmHz);
    // Switching to CW adopts 500 Hz default.
    QCOMPARE(core::bandwidthOnModeSwitch("NFM", "CW", 12500.0), core::kBwCwHz);
}

void TestBandwidthPreset::coverageRulePreservesManualBandwidth() {
    // User manually nudged NFM to 9000 Hz (!= NFM default 12500) -> switching
    // to WFM must PRESERVE the 9000 Hz instead of stomping it with 200k.
    const double r = core::bandwidthOnModeSwitch("NFM", "WFM", 9000.0);
    QCOMPARE(r, 9000.0);
}

void TestBandwidthPreset::engineDemodModeAppliesRealBandwidth() {
    dsp::SpectrumEngine eng;
    eng.setDemodMode("NFM");
    QCOMPARE(eng.demodMode(), QString("NFM"));
    QCOMPARE(eng.bandwidth(), core::kBwNfmHz);       // 12500
    eng.setDemodMode("WFM");
    QVERIFY(std::abs(eng.bandwidth() - core::kBwWfmHz) < 1.0);
    eng.setDemodMode("CW");
    QCOMPARE(eng.bandwidth(), core::kBwCwHz);
    eng.setDemodMode("ADS-B");
    QCOMPARE(eng.bandwidth(), core::kBwAdsbHz);
    // Real setBandwidth API read-back.
    eng.setBandwidth(7500.0);
    QCOMPARE(eng.bandwidth(), 7500.0);
}

void TestBandwidthPreset::powerSpectrumAverageIsRealMovingAverage() {
    // Build an input whose power jumps around (tone + noise); with averaging ON
    // the smoothed trace must vary LESS frame-to-frame than with averaging OFF.
    dsp::PowerSpectrum off, slow;
    off.setAverage(dsp::PowerSpectrum::Off);
    slow.setAverage(dsp::PowerSpectrum::Slow);
    std::vector<float> a, b;
    double prevOff = 0.0, prevSlow = 0.0;
    double sumAbsOff = 0.0, sumAbsSlow = 0.0;
    for (int f = 0; f < 20; ++f) {
        std::vector<std::complex<float>> iq(1024);
        const float ph = static_cast<float>(f) * 0.7f;
        for (int i = 0; i < 1024; ++i)
            iq[i] = std::polar(1.0f, 2.0f * 3.14159265f * 0.1f * i + ph);
        off.process(iq, a);
        slow.process(iq, b);
        const int mid = 512;
        if (f > 0) {
            sumAbsOff  += std::abs(a[mid] - prevOff);
            sumAbsSlow += std::abs(b[mid] - prevSlow);
        }
        prevOff = a[mid]; prevSlow = b[mid];
    }
    // The moving average must materially reduce frame-to-frame jitter.
    QVERIFY2(sumAbsSlow < sumAbsOff,
             "averaged trace must move less than the unaveraged one");
}

QTEST_MAIN(TestBandwidthPreset)
#include "test_bandwidth_preset.moc"
