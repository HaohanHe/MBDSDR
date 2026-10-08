# Phase63 — Spectrum bookmark vertical-line overlay

## What landed

A **pure display-layer** overlay: the saved bookmark frequencies now paint as
quiet dotted reference lines on the spectrum trace, reusing the EXISTING
`ui::BookmarkManager` data verbatim. No new storage, no new persistence, no new
tool, no new widget — the canvas only draws what it is handed.

```
BookmarkManager (existing, QSettings-backed, JSON)
   frequencies()  ──►  MainWindow wiring  ──►  SpectrumDisplay::setBookmarkHz()
                                                       │
                                          paintEvent: f ──xForFreq──► 1px dotted line
```

## API (display layer)

- `cpp/src/ui/spectrum_display.h:138` — `void setBookmarkHz(const QVector<double>& hz)`
  (whole-list replace; Qt6 `QList<double>` == `QVector<double>`, so the manager's
  `frequencies()` binds with zero conversion).
- `cpp/src/ui/spectrum_display.h:139` — `const QVector<double>& bookmarkHz() const`
  read-back (offscreen-test seam, mirrors the existing `fixedMarkers()` getter).
- `cpp/src/ui/spectrum_display.h:406` — private `bookmarkHz_` state.
- `cpp/src/ui/spectrum_display.cpp:506` — setter body: assign + `update()`.

## Drawing

`cpp/src/ui/spectrum_display.cpp:789` — a loop over `bookmarkHz_` painted just
BEFORE the fixed-marker block (so user markers always paint on top of the bookmark
references):

- each frequency maps through the **same** `xForFreq(f, fLo, span)` private helper
  the trace/ticks/waterfall already use — no independent mapping, so the lines can
  never drift off the trace;
- frequencies outside the visible window are culled exactly like fixed markers
  (`x < trace.left() || x > trace.right()`);
- **honest empty state**: an empty `bookmarkHz_` means the loop body never runs —
  zero lines, no placeholder, no invented frequency;
- visual style (deliberately distinct from every other vertical element on the
  trace): quiet green **dotted** line, 1px, alpha 0.45 —
  - fixed user markers = quiet accent solid / amber dashed-selected;
  - measurement cursors = teal / pink dashed;
  - peaks = accent triangles;
  - VFO tuning = solid blue centre line.
- color/alpha/width go through NEW named tokens (`cpp/src/core/tokens.h:160-162`:
  `kBookmarkColor="#5fd08a"`, `kBookmarkLineAlpha=0.45`, `kBookmarkLineWidth=1`);
  the dotted style is `Qt::DotLine` (a pen style, not a magic number).

## Container wiring (MainWindow)

`cpp/src/ui/main_window.cpp`:

- `:1351` — after `bookmarkManager_->load()`, seed the overlay with the persisted
  list: `spectrum_->displayCanvas()->setBookmarkHz(bookmarkManager_->frequencies())`.
- `:1589-1594` — the existing `resyncScanBookmarks` lambda (already called from
  every bookmark add / edit / delete / scan-hit-save site) now ALSO re-pushes the
  frequencies to the canvas. The overlay push sits OUTSIDE the
  `scanBmOnlyChk_->isChecked()` guard, so it mirrors the saved list regardless of
  the scanner "only bookmarks" mode.

### Why no `bookmarksChanged` signal

Recon expected a `bookmarksChanged` signal on `BookmarkManager`. Reading
`bookmark_manager.h` showed it is a plain data class — **no `Q_OBJECT`, no
signals** by design ("data + persistence only, no QWidget"). All mutations already
funnel through the four in-dialogue call sites that invoke `resyncScanBookmarks()`,
so extending that one lambda is the honest minimal wiring — no fake signal, no
new observer. The overlay thus tracks the real list at every mutation point.

## Tests (display-level only)

`cpp/tests/test_spectrum_display.cpp` — three new slots (no new CMake target; the
existing `test_spectrum_display` target already links `mbdsdr_core`):

- `:753 bookmarkOverlayMapsToTraceX` — injected 98.0/99.0 MHz lines map inside the
  trace horizontal extent, x monotonic with frequency, and agree within 1px with a
  fixed marker placed at the same frequency (proves one shared `xForFreq` mapping).
- `:788 bookmarkOverlayHonestEmptyState` — default overlay empty; re-feeding `{}`
  clears a previously fed list.
- `:806 bookmarkOverlayRefeedsUpdate` — a second feed REPLACES the whole list
  (container pushes the full list on every mutation, never appends).

main_window-level wiring is NOT unit-tested here (no MainWindow offscreen harness
in this suite); the wiring itself is three lines mirroring the already-proven
`resyncScanBookmarks` pattern, and the display-level contract is fully covered.

## Screenshots

`ui_shot_narrow` (`cpp/tests/ui_screenshot_narrow.cpp`) gained an opt-in
`MBD_BMKSHOT=1` channel (`:121`) that pushes `{98.0e6, 99.0e6}` to the canvas —
the same injection pattern as the existing `MBD_PEAKSHOT`. Captured offscreen at
640 / 960 / 1920 with `MBD_TAB=频谱` + `MBD_PEAKSHOT=1`:

- **Why pair with PEAKSHOT**: with NO flowing frame, `visibleWindow()` collapses to
  `spanHz = 1 Hz` (spectrum_display.cpp:132) and every bookmark x is culled
  off-canvas — so the lines are only visible once a real frame sets the 2.4 MHz
  window around 98.5 MHz. This is honest geometry, not a workaround.
- Verdict: 0 clipping, 0 overlapping text at all three widths; the two green dotted
  lines land exactly on the 98.00 / 99.00 strip ticks, clearly distinct from the
  solid blue 98.50 tuning line and the white peak triangles.
- Note: the requested 640px shot came out 960px — the app's own
  `kMainMinW = 960` clamps the width (existing behavior, not touched here).

## Why AGC / display modes were NOT gaps

- **AGC**: the envelope AGC (`dsp/agc.cpp`, `kAgcTargetLin` / `kAgcMaxGainLin` in
  tokens.h) already lives in the audio recovery chain; `ComplexCarrierAgc` is
  already in the DSP chain. Nothing in bookmark overlay needs (or touches) gain —
  it is geometry only.
- **Display modes**: `setDbRange()` / `setAutoRangeOn()` already exist and drive the
  trace + waterfall colour scale; max-hold / min-hold / persistence are already
  separate overlays. The bookmark lines deliberately live OUTSIDE the dB/pipeline
  domain (pure frequency → x), so no new display mode or range plumbing is needed.

## Hard-constraint audit

- Real data only — frequencies come from `BookmarkManager`; no mock, no invented
  stations, no TLE/call-signs.
- No new storage (the manager already persists; the canvas holds a transient copy).
- No new tool — `list_bookmarks` / `add_bookmark` / `tune_to_bookmark` already
  exist; tool count unchanged.
- No bare colors/pixels at the call site; new elements go through tokens.
- No `git add/commit/push`.
