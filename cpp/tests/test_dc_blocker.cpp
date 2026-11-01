// SPDX-License-Identifier: MIT
// Unit tests for dsp::DcBlocker (SDR++-style exponential DC removal).
// Deterministic: synthetic signals only, geometry assertions on the signal
// itself (residual DC), never on wall-clock or platform-dependent quantities.
// Every pass feeds a FRESH block, mirroring the engine where each run() loop
// reads new device samples -- reprocessing one buffer would re-subtract the
// converged offset and is not a scenario the block exists for.
#include <QtTest>
#include <cmath>
#include <complex>
#include <vector>
#include "dsp/dc_blocker.h"

using mbdsdr::dsp::DcBlocker;

class TestDcBlocker : public QObject {
    Q_OBJECT
private slots:
    // Pure DC input must converge to ~0 output (the tracked offset eats it).
    void dcOnlyConvergesToZero() {
        DcBlocker b;
        std::vector<std::complex<float>> last;
        for (int pass = 0; pass < 8; ++pass) {
            last.assign(4096, {0.5f, -0.25f});
            b.process(last);
        }
        for (const auto& c : last) {
            QVERIFY2(std::abs(c) < 1e-4,
                     qPrintable(QString("residual |%1|").arg(std::abs(c))));
        }
    }

    // An off-DC tone must pass with its amplitude intact: the blocker only
    // subtracts the near-DC component, not spectral content away from 0 Hz.
    void tonePassesUnharmed() {
        DcBlocker b;
        constexpr int N = 8192;
        std::vector<std::complex<float>> last(N);
        for (int pass = 0; pass < 4; ++pass) {
            for (int i = 0; i < N; ++i)
                last[i] = {std::cos(0.1 * i), std::sin(0.1 * i)};
            b.process(last);
        }
        double peak = 0;
        for (const auto& c : last) peak = std::max(peak, static_cast<double>(std::abs(c)));
        QVERIFY2(peak > 0.9 && peak < 1.1,
                 qPrintable(QString("tone amplitude drifted to %1").arg(peak)));
        // And no DC was invented: the output mean stays ~0.
        std::complex<double> mean{0, 0};
        for (const auto& c : last) mean += c;
        mean /= static_cast<double>(N);
        QVERIFY2(std::abs(mean) < 1e-3,
                 qPrintable(QString("mean |%1|").arg(std::abs(mean))));
    }

    // DC + tone: the DC component is removed while the tone survives.
    void dcPlusToneRemovesOnlyDc() {
        DcBlocker b;
        constexpr int N = 8192;
        std::vector<std::complex<float>> last(N);
        for (int pass = 0; pass < 6; ++pass) {
            for (int i = 0; i < N; ++i)
                last[i] = {0.3f + std::cos(0.1 * i), -0.2f + std::sin(0.1 * i)};
            b.process(last);
        }
        double peak = 0;
        std::complex<double> mean{0, 0};
        for (const auto& c : last) {
            peak = std::max(peak, static_cast<double>(std::abs(c)));
            mean += c;
        }
        mean /= static_cast<double>(N);
        QVERIFY2(std::abs(mean) < 1e-3,
                 qPrintable(QString("residual mean |%1|").arg(std::abs(mean))));
        QVERIFY2(peak > 0.9, "tone must survive DC removal");
    }

    // reset() must drop the tracked offset: feed DC to convergence, reset,
    // then the next fresh DC block must initially see the raw DC again.
    void resetDropsTrackedOffset() {
        DcBlocker b;
        std::vector<std::complex<float>> io(1024, {1.0f, 0.0f});
        b.process(io);            // offset now ~1
        b.reset();
        std::vector<std::complex<float>> fresh(1024, {1.0f, 0.0f});
        b.process(fresh);
        QVERIFY2(std::abs(fresh[0] - std::complex<float>(1.0f, 0.0f)) < 1e-3,
                 "reset must clear the tracked offset");
    }
};

QTEST_MAIN(TestDcBlocker)
#include "test_dc_blocker.moc"
