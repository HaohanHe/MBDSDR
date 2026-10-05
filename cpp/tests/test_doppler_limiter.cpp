// SPDX-License-Identifier: MIT
//
// Deterministic, offscreen unit test for the DopplerStepLimiter safety loop
// (core/sat_capture.h).  No radio, no SGP4, no QMainWindow -- the limiter is a
// pure function over a 1 Hz tick, so we synthesize a LEO Doppler curve and
// assert REAL tracking-error numbers plus the three safety behaviours:
//
//   1. Step clamp  -- every per-tick offset move is <= kDopplerMaxStepHz.
//   2. Freeze-hold -- a non-finite target (target lost) HOLDS the offset; the
//                     tuner is never walked.  A finite target resumes.
//   3. Smooth return -- requestReturnToZero() glides the offset home to 0 in
//                     bounded steps (no instant jump).
//
// The synthetic fd(t) = A*sin(2*pi*t/T) is a stand-in for a real LEO pass
// Doppler (slow, smooth, tens of Hz/s) -- slow enough that a healthy limiter
// tracks it with ~zero lag, which is the number we print and assert.
#include "core/sat_capture.h"
#include "core/tokens.h"

#include <cmath>
#include <cstdio>
#include <limits>

static const double kPi = 3.14159265358979323846;

using namespace mbdsdr;
using namespace mbdsdr::core;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

int main() {
    const double kMax = tokens::kDopplerMaxStepHz;   // 2000 Hz
    const double NaNv = std::numeric_limits<double>::quiet_NaN();

    // ------------------------------------------------------------------
    // 1. Step clamp: a big discrete target jump is sliced, never slammed.
    // ------------------------------------------------------------------
    {
        DopplerStepLimiter lim;
        lim.reset(0.0);
        double m1 = lim.advance(5000.0);
        double m2 = lim.advance(5000.0);
        double m3 = lim.advance(5000.0);
        check(std::fabs(m1 - 2000.0) < 1e-6, "clamp: step1 = +2000 (not +5000 slam)");
        check(std::fabs(m2 - 4000.0) < 1e-6, "clamp: step2 = +4000");
        check(std::fabs(m3 - 5000.0) < 1e-6, "clamp: step3 lands exactly on target");
        // The negative direction must clamp too.
        double n1 = lim.advance(-1000.0);   // 5000 -> wants -1000 = -6000 move
        check(std::fabs(n1 - 3000.0) < 1e-6, "clamp: negative step = -2000 bounded");
    }

    // ------------------------------------------------------------------
    // 2. Real tracking error on a slow synthetic LEO curve.
    //    fd(t) = 1000*sin(2*pi*t/300) Hz -> max slope ~20.9 Hz/s, far under
    //    the 2000 Hz/tick ceiling, so the limiter should follow it with ~0 lag.
    //    We print the measured peak residual and assert it is tiny.
    // ------------------------------------------------------------------
    double peakResidual = 0.0, peakPerMove = 0.0;
    {
        DopplerStepLimiter lim;
        lim.reset(0.0);
        const int T = 600;                 // 1 Hz ticks
        const double A = 1000.0, period = 300.0;
        for (int t = 0; t < T; ++t) {
            const double fd = A * std::sin(2.0 * kPi * t / period);
            const double before = lim.offset();
            const double off = lim.advance(fd);
            peakPerMove = std::max(peakPerMove, std::fabs(off - before));
            // Steady-state after warm-up (skip first 20 ticks of convergence).
            if (t >= 20) peakResidual = std::max(peakResidual, std::fabs(off - fd));
        }
        std::printf("  synthetic LEO: peak per-tick move = %.3f Hz (ceil %.0f)\n",
                    peakPerMove, kMax);
        std::printf("  synthetic LEO: steady-state tracking residual = %.6f Hz\n",
                    peakResidual);
        check(peakPerMove <= kMax + 1e-6,
              "tracking: every 1 Hz move bounded by kDopplerMaxStepHz");
        check(peakResidual < 1.0,
              "tracking: slow curve tracked with <1 Hz residual (real number)");
    }

    // ------------------------------------------------------------------
    // 3. Freeze-hold on target loss (the critical anti-blind-scan behaviour).
    //    Drive to a known offset, then feed NaN for 20 ticks.  The offset must
    //    NOT move by a single Hz -- the old buggy behaviour walked it by the
    //    clamp every tick (a blind scan).
    // ------------------------------------------------------------------
    {
        DopplerStepLimiter lim;
        lim.reset(0.0);
        lim.advance(1500.0);               // offset = 1500 (one step, lands)
        const double frozenStart = lim.offset();
        check(frozenStart > 0.0 && std::fabs(frozenStart - 1500.0) < 1e-6,
              "freeze: pre-loss offset established at 1500 Hz");
        for (int k = 0; k < 20; ++k) {
            const double off = lim.advance(NaNv);   // target lost
            check(lim.frozen(), "freeze: frozen() true while target non-finite");
            check(lim.state() == DopplerLimiterState::Frozen, "freeze: state==Frozen");
            check(std::fabs(off - frozenStart) < 1e-9,
                  "freeze: offset HELD (not walked by the clamp)");
        }
        // Refind: a finite target resumes tracking from the held offset.
        const double before = lim.offset();
        const double off = lim.advance(800.0);
        check(!lim.frozen(), "freeze: finite target clears frozen()");
        check(lim.state() == DopplerLimiterState::Tracking, "freeze: resumes Tracking");
        // Held 1500 -> wants 800 = -700 move, within one step -> lands exactly.
        check(std::fabs(off - 800.0) < 1e-6, "freeze: refind lands on finite target");
        check(std::fabs(off - before) <= kMax + 1e-6,
              "freeze: refind step still clamp-bounded");
    }

    // ------------------------------------------------------------------
    // 4. Smooth return-to-zero on switch-off (no instant jump).
    //    Drive offset to +2500, requestReturnToZero(), then keep advancing(0).
    //    Each return step must be <= kMax; the offset must arrive at exactly 0
    //    and the limiter must settle to Idle.
    // ------------------------------------------------------------------
    {
        DopplerStepLimiter lim;
        lim.reset(0.0);
        lim.advance(2500.0);               // one bounded step: +2000
        lim.advance(2500.0);               // lands at +2500? no: 2000->2500 +500
        check(std::fabs(lim.offset() - 2500.0) < 1e-6, "return: offset at +2500");
        lim.requestReturnToZero();
        check(lim.state() == DopplerLimiterState::Returning,
              "return: requestReturnToZero() -> Returning");
        double maxReturnMove = 0.0;
        int returnTicks = 0;
        while (lim.state() != DopplerLimiterState::Idle) {
            const double before = lim.offset();
            const double off = lim.advance(0.0);
            maxReturnMove = std::max(maxReturnMove, std::fabs(off - before));
            ++returnTicks;
            if (returnTicks > 100) break;  // safety
        }
        std::printf("  smooth-return: %d ticks home, max step %.1f Hz\n",
                    returnTicks, maxReturnMove);
        check(std::fabs(lim.offset()) < 1e-9, "return: offset arrives exactly at 0");
        check(lim.state() == DopplerLimiterState::Idle, "return: settles to Idle");
        check(maxReturnMove <= kMax + 1e-6,
              "return: every return step is clamp-bounded (no jump to f0)");
        check(returnTicks >= 2,
              "return: took multiple ticks (glide, not an instant snap)");
    }

    // ------------------------------------------------------------------
    // 5. Honest empty state: a fresh limiter is Idle at offset 0, and an
    //    advance(NaN) while Idle must NOT poison the offset with NaN.
    // ------------------------------------------------------------------
    {
        DopplerStepLimiter lim;
        check(lim.offset() == 0.0 && lim.state() == DopplerLimiterState::Idle,
              "empty: fresh limiter Idle at offset 0 (no fabricated TLE/fd)");
        const double off = lim.advance(NaNv);
        check(std::isfinite(off) && off == 0.0,
              "empty: Idle advance(NaN) stays at 0 (no NaN poison)");
        check(lim.state() == DopplerLimiterState::Idle, "empty: stays Idle");
    }

    if (failures == 0) std::printf("test_doppler_limiter: ALL PASS\n");
    else std::printf("test_doppler_limiter: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
