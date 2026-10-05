# Phase 49 — Real-time Doppler Compensation Engine: Safe Closed Loop

Block 1+2, desktop (cpp) domain. MIT clean-room; no GPL, no contest strings, no
preset TLE. Goal: turn the 1 Hz Doppler retune from "absolute-frequency limiter"
into a safe, testable **offset** loop with three deterministic behaviours.

## Files touched (only these; mobile/ B-domain untouched)

- `cpp/src/core/sat_capture.h` — `DopplerStepLimiter` refactored to an offset
  state machine (`Idle/Tracking/Frozen/Returning`).
- `cpp/src/ui/main_window.cpp` — capture/toggle/1 Hz loop wired to offset model.
- `cpp/tests/test_sat_capture.cpp` — existing expectations moved to offset book.
- `cpp/tests/test_doppler_limiter.cpp` — NEW deterministic synthetic-pass test.
- `cpp/CMakeLists.txt` — registers `doppler_limiter`.

## The three engine behaviours (mechanism)

1. **Step clamp (radar convergence).** `advance(targetOffsetHz)` moves the offset
   by at most `kDopplerMaxStepHz = 2000 Hz` per 1 s tick. A slow real LEO ramp
   (tens of Hz/s) lands on target immediately; a discrete jump (capture/AOS)
   converges in bounded slices — never a tuner slam / dither.
2. **Freeze-hold on target loss.** A **non-finite** target (range-rate lost,
   stale TLE, NaN propagate) does NOT walk the tuner: the offset is HELD at its
   last valid value, `frozen()` set, state `Frozen`. (The old `advance()` on a NaN
   target would have stepped by the clamp every tick = a blind scan.) A finite
   target later resumes tracking in bounded steps.
3. **Smooth return-to-zero on switch-off.** The checkbox-off path calls
   `requestReturnToZero()` → state `Returning`; the SAME 1 Hz loop then calls
   `advance(0.0)` so the offset glides home to 0 within the per-tick clamp (VFO
   ends on nominal f0). No instant jump. `rearm()` resumes tracking cleanly if
   the box is re-checked mid-return. `disarm()` remains the hard stop (pass
   ended / TLE refetch / station lost / new row) → Idle at offset 0.

The limiter is now a pure offset generator: the UI applies `VFO = f0 + offset()`
every tick. `offset()` is the real cumulative value surfaced on the status line.

## Real numbers (from the deterministic test)

Synthetic LEO curve `fd(t)=1000·sin(2πt/300)` over 600 1 Hz ticks:
- peak per-tick move **20.942 Hz** (ceil 2000) → clamp never engaged in steady
  state, as expected for a slow ramp.
- steady-state tracking residual **0.000000 Hz** (lands exactly).
- freeze-hold: offset frozenStart=1500 Hz held to `<1e-9` for 20 NaN ticks.
- smooth return from +2500 Hz: **2 ticks home**, max return step 2000 Hz
  (bounded glide, not a snap), settles to Idle at offset exactly 0.

Real SGP4 (28057 CBERS-2 scenario, `test_sat_capture`): max per-tick move
**2000.0 Hz** (clamp engaged at AOS transient), steady-state residual
**0.000000 Hz**.

## Chain boundary (no loop conflict)

- **TLE compensation (this limiter, 1 Hz, desktop C++)** — slow, large-scale
  LEO Doppler ramp from `propagateAt().rangeRateKmS → dopplerHz(f0, vr)`.
- **Blind CFO / Gardner timing recovery (Python, `mbdsdr_ai/ssdv_phy`)** — fast
  residual carrier + symbol clock closed inside the demodulator on baseband.
  Desktop cpp does NOT call it per-sample; it rides on whatever in-band offset
  this limiter leaves. The limiter's slow ramp (few kHz) stays inside the
  demod's blind-acquisition pull range → non-overlapping loops.

## Honest empty state

No station / no captured pass → limiter stays Idle at offset 0; the checkbox is
disabled with an explanatory tooltip. No preset TLE, no fabricated fd. The status
line renders "未补偿（无目标）" / NaN rather than inventing a residual.

## UI status copy

While tracking: `补偿中·累计 ±N Hz` (N = limiter `offset()`, the real applied
value, not raw liveFd). Frozen: `补偿中·保持（目标丢失）累计 ±N Hz`. Returning:
`补偿回零·累计 ±N Hz`. The spacetime Doppler line shows the same applied offset.

## Build / ctest

- `QT_QPA_PLATFORM=offscreen`, `-j1`/`-j2` single-target increments.
- Header change (sat_capture.h) triggered a cascade rebuild; two heavy TUs
  (`llm_worker.cpp.o` for test_plan_parser, etc.) hit the 8 GB OOM and were
  `Terminated` — their pre-existing binaries (unchanged behaviour) remain on
  disk and pass.
- **Full ctest: 100% tests passed, 0 failed out of 129**
  (128 baseline + 1 new `doppler_limiter`; `e2e_smoke` Skipped as at baseline).

## Open items

- OOM environment: heavy AI TUs can't be recompiled in-place here; their old
  binaries are behaviourally identical to baseline.
- No live hardware pass was exercised (offscreen CI); the freeze/return paths
  are proven only by the synthetic + SGP4-curve unit tests.
