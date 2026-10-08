# Phase63 — Waterfall vertical time axis (时间轴刻度)

## What landed

The scrolling waterfall now carries a **vertical time axis** along its free left
gutter: `now` at the top edge, then `-Ns` labels counting seconds into the past
as rows scroll down. It is a pure display overlay — no new DSP, no new thread,
no storage. The only non-paint piece is an honest, measured **frame-rate
estimate** (see below), because the codebase publishes no fps constant.

```
setSpectrum(frame)  ──►  QElapsedTimer.restart()  ──►  real inter-frame dt
                                   │  (sanity-band EWMA)
                                   ▼
                       secPerRow = smoothed dt × everyNthFrame_
                                   │
                       paintEvent: row d → y, label = now / -Ns
```

## Geometry (read first)

`ui::SpectrumDisplay` history row 0 = the **NEWEST** sweep and it is painted at
the **TOP** of the falls box (`materialiseHistory` + the two `drawImage` paths
stretch the full `ringDepth_` image top-aligned). This is pinned by the existing
`realFrameDrivesHistory` test: after frame 2 the new peak sits in `history.row 0`
and the old peak "scrolled down into row 1". So, contrary to the task brief's
"now = bottom" assumption for a generic waterfall, **here now = top** and
"-Ns" ticks count downward into the past. The doc/brief asymmetry is recorded
honestly rather than flipped in code.

Ticks live in the **left gutter** (`x ∈ [0, plotX0)` = `kDispLeftInset`=50px):
over the waterfall band that gutter is empty (the dB grid labels are drawn
*inside* the trace at `trace.left()+pad`, not in the gutter), so the labels never
cover the spectrogram — the chosen "不遮挡数据" side.

## Time base — the honest part

Recon, before freezing:

- `core/spectrum_frame.h` carries **no timestamp** (only dbfs / f0 / fs / fftSize /
  sourceName).
- `dsp/spectrum_engine.h/cpp` publishes **no fps / frame-rate member**. The run
  loop emits one `spectrumReady` per ~25 ms IQ block (`spectrum_engine.cpp:1218`
  reads `sr*0.025`), but that cadence is an implementation detail, not a contract.
- There is no `kFrame*` fps constant anywhere.

So the widget **measures** the real gap between successive `setSpectrum()`
arrivals with a `QElapsedTimer` and smooths it:

- `spectrum_display.cpp:455` — on every accepted frame, `dt = restart()/1000.0`.
  Only `dt ∈ [kWfTimeMinFrameDtS, kWfTimeMaxFrameDtS]` feeds the EWMA
  (`kWfTimeEmaAlpha`); sub-ms gaps (a tight offline/test loop) and >0.5 s stalls
  are **rejected**, so the axis never snaps to 0 or stretches across a pause.
- `effectiveSecondsPerRow()` (`:283`) returns `smoothedDt × everyNthFrame_`
  (the waterfall pushes a row only every N frames via `setScrollSpeed`), or 0
  when no sane sample exists yet.
- **Honest empty state**: `ringCount_ == 0` **or** no measured base →
  `computeTimeTicks()` returns empty → nothing painted. A fresh canvas, a paused
  source, or a ring fed in a tight test loop all draw zero ticks. No fps is ever
  invented.

## Ticks

`computeTimeTicks()` (`:300`):

- row `d = 0` → `now` (top edge);
- interior ticks every `kWaterfallTimeTickRows`=32 rows down;
- a boundary tick on the oldest filled row (`d = ringCount_-1`), dropped if it
  would crowd the last interior tick;
- `formatTimeOffset()` (`:289`) renders `<1s` as one decimal (`-1.6s`), `<60s` as
  whole seconds (`-12s`), beyond that as minutes (`-3m`).

**Elastic thinning** (`:313`): the row stride is widened so the on-screen tick
pitch is at least one label height (`kTimeLabelH + kTimeLabelPadY`). On a short
waterfall the stride grows (fewer, spread-out ticks) instead of stacking labels;
a tall waterfall keeps the denser 32-row stride. Same elastic-thinning principle
as the S-meter unit labels. Verified in the narrow shot (below).

Row `d` → `y = falls.top() + falls.height()*d/ringDepth_` (the same linear map
`drawImage` uses). A 5px tick pokes left off the falls border; the label is
right-aligned in the gutter. Color/alpha/font reuse the existing family
(`kTextAlphaTertiary` pen, `kFontAuxPt`) — no new color token.

## Tokens (`cpp/src/core/tokens.h:326-356`)

Reused the pre-existing (previously-unused scaffold) `kTimeLabelW=44` /
`kTimeLabelH=12` / `kTimeLabelPadY=2`; added:

- `kWaterfallTimeTickRows = 32` — interior tick row stride;
- `kWaterfallTimeTickW = 5` — horizontal tick mark length (scaled);
- `kWfTimeEmaAlpha = 0.20` — EWMA weight on a fresh inter-frame sample;
- `kWfTimeMinFrameDtS = 0.002` / `kWfTimeMaxFrameDtS = 0.5` — sanity band.

No raw pixels/hex at the call site; the gutter geometry goes through `scaled()`.

## Test seams (`spectrum_display.h:239-244, 319-324`)

Production measures its own period. For deterministic offscreen tests the same
"pin the input" seam the suite already uses (`*ForTest` read-backs) is exposed:

- `setSecondsPerRowForTest(s>0)` pins the row period; `s<=0` releases it back to
  the live estimate.
- `waterfallTimeTickLabelsForTest()` → top-to-bottom `now/-Ns` list paintEvent uses.
- `waterfallRowCountForTest()` → `ringCount_` (so the top/bottom boundary ticks
  can be asserted).

## Tests (`cpp/tests/test_spectrum_display.cpp`)

Two new slots (target already links `mbdsdr_core`; 26 → 28 passing):

- `waterfallTimeTickLabelsAreRealSeconds` — 129 rows pushed (oldest offset 128, an
  exact stride multiple), pinned 0.125 s/row → labels exactly
  `[now, -4s, -8s, -12s, -16s]`; first=`now` (top), last=`-16s` (oldest row);
  releasing the pin drops the ticks again (no invented fps). Removes a possibly
  persisted `specFraction` first so the 1:1 split is deterministic.
- `waterfallTimeTickHonestEmptyState` — fresh canvas → no ticks; a fed ring in a
  tight loop (every inter-frame gap rejected as sub-ms) → still no ticks.

## Screenshots (`ui_screenshot_narrow.cpp`)

Opt-in `MBD_WATERTICK=1` channel (after `MBD_BMKSHOT`): fills the whole 256-row
ring with deterministic carrier frames and pins 0.05 s/row (≈12.8 s history).
Captured offscreen at 640 / 960 / 1920:

- `scratch/wf_tick/wf_640.png` (came out **960×640** — `kMainMinW=960` clamps,
  existing behavior), `wf_960.png` (960×700), `wf_1920.png` (1920×1080).
- Verdict: labels legible, **no clipping, no overlapping text** at all three
  widths after the elastic thinning (the first narrow build stacked 8 labels on
  a ~75px falls; widening the stride to keep ≥1 label-height pitch fixed it).
- `wf_default.png` (no `MBD_WATERTICK`, no source connected) shows the honest
  empty waterfall with **no ticks** — the axis only appears once real rows exist.

## Why these were NOT gaps

- **Stepping presets**: `kStepValuesHz` already spans 7 bands; the waterfall time
  axis has nothing to do with tuning step — no new preset needed.
- **Average mode**: `averageMode` Off/Slow/Fast already exists; it averages the
  trace/waterfall values, not the time base. The axis measures real arrival
  cadence independently, so average mode needs no wiring here.

## Hard-constraint audit

- Real data only — the time base is the measured inter-frame gap; the test pin
  is a seam, not a production mock. No invented fps, no TLE/call-sign, neutral
  wording, clean-room (no SDR++ source consulted for the axis).
- Active params all via tokens; only the allowed files touched.
- No `git add/commit/push`.
