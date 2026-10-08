# Phase63 — Mini RSSI/dBfs history trend strip (信号强度历史 mini 图)

## What landed

A small **RSSI/dBfs history trend strip** sits in the status bar immediately to
the right of the existing S-meter. It records the **same real engine RSSI sample**
that already drives the S-meter and the `sbRssi_` readout (`onRssiLevel`), and
paints the recent-N-sample line so signal fading / flutter over time is visible at
a glance. There is **no second data path** — the trend buffer and the S-meter are
both fed the one `float dbfs` the engine emits.

```
engine rssiLevel(float dbfs)
        │
        ▼
MainWindow::onRssiLevel(dbfs)              cpp/src/ui/main_window.cpp:4776
        ├──► sMeter_->setSignalDbfs(dbfs)          (existing instantaneous + peak-hold)
        └──► rssiTrend_->pushDbfs(dbfs)            (NEW rolling sample buffer)
                        │
            finite? ── no ──► clear()  (honest empty: link down / no frame)
                        │
                       yes
                        ▼
              hist_  (back = newest), trim to kRssiTrendMaxSamples
                        ▼
              paintEvent: oldest(left) → newest(right), y = real min..max
```

## Why it was a real gap (recon)

- `SMeterWidget` already carries the **instantaneous** dBfs plus a slow
  peak-hold decay (`s_meter.cpp:24-44`, `tickDecay` called 1 Hz at
  `main_window.cpp:5934`), but there was **no history / trend** anywhere. A grep
  for `trend|history|ring|deque|vector.*dbfs` across `cpp/src/ui/` returned no
  RSSI-history structure.
- The real sample is already on hand: `onRssiLevel(float dbfs)`
  (`main_window.cpp:4776-4784`) is the single source; the trend just appends to it.
- So the missing capability was specifically "recent-N-seconds dBfs line to watch
  fading", not a missing data feed.

## The widget (`cpp/src/ui/rssi_trend.{h,cpp}`)

- `pushDbfs(double dbfs)` (`rssi_trend.cpp`):
  - **non-finite (NaN/inf) → `clear()`**. A dropped link / absent device must not
    keep drawing a stale line. This mirrors the S-meter's own `NaN ⇒ shownUnits_=-1`
    empty rule (`s_meter.cpp:26`).
  - finite → append; while size > `kRssiTrendMaxSamples` drop the oldest. Back =
    newest, front = oldest (order invariant the painter relies on).
- `clear()`: empty the buffer (also called from `onSourceDropped`,
  `main_window.cpp:4729`, so a torn link drops the trend to its honest blank).
- `paintEvent`:
  - Shared card framing (`cardEdge`/`card1`, rounded) matching the S-meter.
  - **Empty state**: faint centered caption `RSSI 趋势` only — no baseline, no ghost
    line, no fabricated history.
  - Non-empty: y auto-scales to the **real buffered min..max** (flat buffer gets a
    2 dB band so the line still sits mid-height); x maps front→left, back→right
    over a small inset so the newest-sample dot sits fully inside the card.
  - Newest-sample dot at the right end so the current level is readable.
- Test read-backs: `countForTest()` / `emptyForTest()`.

## Tokens (`cpp/src/core/tokens.h`)

All tunables are named tokens — no raw pixel/hex at the call site:

- `kRssiTrendMaxSamples = 120` (`:465`) — rolling buffer depth (oldest dropped);
- `kRssiTrendColor = "#7CC4FF"` (`:466`) — trend line (accent blue);
- `kRssiTrendLineAlpha = 0.8` (`:467`);
- `kRssiTrendW = 140` (`:691`) — strip width (base px, `scaled()`); height reuses
  `kSMeterH=34` so the two status widgets line up vertically.

## Wiring (`cpp/src/ui/main_window.{h,cpp}`)

- forward declaration `class RssiTrendWidget;` (`main_window.h:52`), member
  `ui::RssiTrendWidget* rssiTrend_` (`main_window.h:260`).
- created beside `sMeter_` (`main_window.cpp:2172`), added as a permanent widget
  after a 6px breathing gap (`:2181-2186`) so the S-meter's S9 end-cap label never
  crowds the trend card border regardless of DPI scaling.
- fed real samples in `onRssiLevel` (`main_window.cpp:4783`).
- cleared on source drop (`main_window.cpp:4729`).

## CMake

`src/ui/rssi_trend.cpp` added to the existing `SOURCES` list (`CMakeLists.txt:74`)
and `src/ui/rssi_trend.h` to `HEADERS` (`:214`). Both already flow into
`mbdsdr_core` (`CMakeLists.txt:242-244`), so the existing `test_s_meter` target
(which links `mbdsdr_core`) and `ui_shot_narrow` pick it up with **no new CMake
target**.

## Tests (`cpp/tests/test_s_meter.cpp`)

Extended the existing `s_meter` QTest target (4 → 9 slots; 11 → 11 passing with
init/cleanup). New slots assert **real buffer semantics**, offscreen:

- `trendStartsEmpty` — fresh widget empty, no fabricated history.
- `trendAccumulatesRealSamples` — finite samples accumulate one-to-one.
- `trendCapsAtMaxDepth` — feeding `cap+25` leaves exactly `kRssiTrendMaxSamples`
  (trim-oldest, no unbounded growth).
- `trendNaNClearsToEmpty` — a NaN sample (link down) clears to empty, never stale.
- `trendClearEmpties` — explicit `clear()` empties; `clear()` on empty is a safe no-op.

Run: `./test_s_meter` → **Totals: 11 passed, 0 failed**.

## Screenshots (`ui_screenshot_narrow.cpp`)

New opt-in env gate `MBD_RSSITREND=1` (same precedent as `MBD_PEAKSHOT`): it
finds the live `RssiTrendWidget` child and pushes a deterministic fading envelope
through the **same `pushDbfs()` production path**, and seeds the S-meter so the two
strips line up in state. Off by default, so every other screenshot is unchanged.
Pure display-harness injection; no mock signal is invented inside the app.

Captured offscreen (`QT_QPA_PLATFORM=offscreen`):

- `docs/learn/phase63/rssi-trend_960.png` (960×640)
- `docs/learn/phase63/rssi-trend_1920.png` (1920×800)

Read-back verdict: the trend card frame is intact, the fading wave renders inside,
the newest dot is fully inside the card (x inset applied), and there is a clear
gap between the S-meter's S9 end-cap and the trend border — **0 clipping, 0
overlapping text** at both widths. The S9 half-label is the S-meter's pre-existing
end-cap convention (centered on its right-edge tick), not a regression.

## Three-candidate judgement table

| # | Candidate | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | RSSI/dBfs history trend mini-plot | **GAP → landed** | S-meter had instantaneous + peak-hold only (`s_meter.cpp:24-44`); no history structure in `cpp/src/ui/`. Real feed already on hand at `main_window.cpp:4776-4784`. |
| 2 | Frequency-axis scale major/minor + zoom-adaptive | **NON-GAP** | Zoom-adaptive "nice" step already exists: `niceStepForSpan`/`freqTicksNice` pick {1,2,2.5,5,10}·10^k from the span (`spectrum_render.h:261-279`), called at `spectrum_display.cpp:1189-1190`; zoom in → finer step, pan → ticks slide. Adaptive label precision `freqTickDecimals` (`spectrum_display.cpp:568-575`). dB grid round-snapped to 10 dB (`:32-33`, painted `:864-871`). Waterfall time axis already elastically thins (`:336-368`). A separate faint minor-tick layer would be cosmetic, not a missing capability, and matches SDR++ ~5-label strip convention. |
| 3 | Waterfall/spectrum sync scroll (shared zoom+pan) | **NON-GAP** | One shared `zoomFactor_`/`viewCenterHz_` state (`spectrum_display.h:401-402`); `visibleWindow()` derives span=f_s/zoom centered on viewCentre (`spectrum_display.cpp:157-166`). Trace maps bins through `xForFreq` on that window (`:884`); the waterfall crop uses the **same** `visibleWindow` — `waterfallCropLeftBin` (`:546-552`) and `waterfallSourceRect` (`:554-566`) both call it. Offscreen test seams already pin the alignment (`spectrum_display.h:236,242`). Trace and waterfall share zoom+pan by construction. |

## Hard-constraint audit

- Real data only — the buffer stores exactly the engine RSSI it is handed; NaN /
  link-down clears to honest empty. The screenshot gate is a display harness over
  the production `pushDbfs()` path, not a production mock.
- Active parameters all via named tokens; only the files listed above touched.
- No `git add/commit/push`. Red-line wording kept neutral; no competition-style
  framing.
- Left the two untracked isolated files (`cpp/tests/ui_diag_freeze.cpp`,
  `cpp/scratch/regen_tool_doc*`) untouched.
