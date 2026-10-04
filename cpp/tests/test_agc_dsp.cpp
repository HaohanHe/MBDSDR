// SPDX-License-Identifier: MIT
// Block-level lookahead anti-clip tests for dsp::Agc (clean-room mechanism).
//
// Acceptance (Phase35 W2):
//  (a) Burst: after the envelope has settled on a quiet floor, a sudden strong
//      block peak must NOT slam the first samples into the output clamp -- the
//      lookahead pulls the gain down early so the burst lands near target.
//  (b) Steady state: a well-received steady tone is NOT over-suppressed; the
//      lookahead stays out of the way and the output settles at ~target.
//  (c) Boundaries: empty / all-zero / single-sample blocks do not crash and do
//      not divide by zero.
//
// A small blockDurMs is chosen on purpose: it makes the per-sample attack pole
// slow enough that the envelope genuinely lags a burst, which is the exact
// condition the lookahead exists to fix. Run via `ctest` or ./test_agc_dsp.
#include <QtTest/QtTest>
#include <vector>
#include <cmath>
#include <algorithm>

#include "dsp/agc.h"

using namespace mbdsdr::dsp;

class TestAgcDsp : public QObject {
    Q_OBJECT
private slots:
    void burstFirstSamplesDoNotClip();
    void steadyStateNotOverSuppressed();
    void boundaryBlocksAreSafe();
};

// Quiet floor amplitude used to settle the envelope low (gain rides high).
static constexpr float kFloor = 0.01f;
// Burst peak injected on the first sample of the burst block.
static constexpr float kBurst = 0.9f;

void TestAgcDsp::burstFirstSamplesDoNotClip() {
    // Slow per-sample poles: blockDurMs=1ms -> attack alpha ~0.18, so the
    // envelope cannot jump to the burst in one sample on its own.
    Agc agc(/*blockDurMs=*/1.0);
    agc.reset();

    // Settle on a long quiet floor: env_ -> kFloor, gain -> maxGain ceiling.
    std::vector<float> quiet(4000, kFloor);
    std::vector<float> out, gain;
    agc.processWithGain(quiet, &out, &gain);
    QVERIFY2(agc.currentLevelDb() < -20.0f,
             "quiet floor must leave the envelope low before the burst");

    // Burst block: loudest sample first, then back to the quiet floor.
    std::vector<float> burst(64, kFloor);
    burst[0] = kBurst;
    agc.processWithGain(burst, &out, &gain);

    // The first burst sample must NOT be pinned at the output clamp. Without the
    // lookahead it would be ~kBurst*maxGain clipped to OutputCeiling (1.0); with
    // the lookahead the gain is pulled to ~target/kBurst so the peak lands near
    // target (0.3), well clear of the clamp.
    const float firstOut = std::abs(out[0]);
    QVERIFY2(firstOut < 0.6f,
             "burst first sample must not clip (lookahead must pull gain early); "
             "expected near target, got a value pinned at the clamp");
    // And across the whole block nothing touches the clamp.
    float peakOut = 0.0f;
    for (float v : out) peakOut = std::max(peakOut, std::abs(v));
    QVERIFY2(peakOut < Agc::OutputCeiling - 1e-3f,
             "no sample in the burst block may sit on the output clamp");
}

void TestAgcDsp::steadyStateNotOverSuppressed() {
    Agc agc(/*blockDurMs=*/1.0);
    agc.reset();

    // Feed a steady, well-received tone (0.5) until the envelope settles.
    const float tone = 0.5f;
    std::vector<float> block(256, tone);
    std::vector<float> out, gain;
    for (int b = 0; b < 60; ++b) agc.processWithGain(block, &out, &gain);

    // One more settled block: output should sit at ~target (0.3), i.e. the
    // lookahead did NOT extra-suppress a steady signal. blockPeak*g ~= target,
    // which is under the ceiling, so the lookahead never triggers.
    agc.processWithGain(block, &out, &gain);
    const float target = Agc::DefaultTarget;
    for (std::size_t i = 0; i < out.size(); ++i) {
        QVERIFY2(std::abs(std::abs(out[i]) - target) < 0.05f,
                 "steady tone must level to ~target, not be over-suppressed");
    }
}

void TestAgcDsp::boundaryBlocksAreSafe() {
    Agc agc(/*blockDurMs=*/1.0);
    agc.reset();
    std::vector<float> out, gain;

    // Empty block: no crash, no division by zero.
    std::vector<float> empty;
    agc.processWithGain(empty, &out, &gain);
    QVERIFY(out.empty());

    // All-zero block: blockPeak == 0 must take the no-trigger path; output 0.
    std::vector<float> zeros(128, 0.0f);
    agc.processWithGain(zeros, &out, &gain);
    QCOMPARE(out.size(), zeros.size());
    for (float v : out) QVERIFY(std::abs(v) < 1e-6f);

    // Single-sample block: no crash, output within the clamp.
    std::vector<float> one{0.5f};
    agc.processWithGain(one, &out, &gain);
    QCOMPARE(out.size(), std::size_t(1));
    QVERIFY(std::abs(out[0]) <= Agc::OutputCeiling + 1e-6f);
}

QTEST_MAIN(TestAgcDsp)
#include "test_agc_dsp.moc"
