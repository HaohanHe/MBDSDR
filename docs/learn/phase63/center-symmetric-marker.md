# Phase63 — Center-symmetric spectrum mirror auxiliary line

## What landed

A **pure display-layer** teaching aid: once a measurement cursor (A or B) is
placed, the trace paints a quiet **lavender dotted reference line at the mirror
frequency about the tuned centre f0**:

```
f_mirror = 2 * f0 - f_cursor        (f0 = tunedFrequencyHz() = dialFreqHz_)
```

This is the direct on-canvas tool for SDR image/sideband symmetry: a signal at
+Δ off the dial has its image at −Δ on the other side. It reuses the EXISTING
`cursorA_Hz_` / `cursorB_Hz_` positions and the existing tuning frequency — **no
new data structure, no new button, no new widget, no persistence**. A cursor
drag or a retune already calls `update()`, so the mirror line follows for free
on the existing repaint paths.

```
cursorA_Hz_ (teal dashed)  ──┐
                             ├─ paintMirror() : 2*f0 - f ──► 1px lavender dotted line
cursorB_Hz_ (pink dashed)  ──┘
tunedFrequencyHz() = dialFreqHz_  (symmetry axis)
```

## Candidate triage (this round)

| Candidate | Verdict | Evidence |
|-----------|---------|----------|
| **Center-symmetric mirror auxiliary line** | **GAP — landed** | dual cursor A/B already draggable with Δ box (`spectrum_display.cpp:978-1024` `paintCursor`; `Grab` enum `spectrum_display.h:349`); fixed markers draggable (`:1283-1290`, `:1335`). But NO line mirrored a cursor about f0 existed — confirmed by reading `paintEvent` and by grep (no prior `mirror`/`2*f0`). |
| **Waterfall colormap scheme** | **NON-GAP** | `paletteCombo_` already offers exactly 3 ramps `{"经典","单色","Viridis"}` (`spectrum_widget.cpp:364`); built-in ramps `kWaterfallStops`/`…Mono`/`…Viridis` (`tokens.h:522/535/546`), PLUS an external user colormap JSON path (`kSettingsKeyColormapFile`, `loadColormapFromFile`). Richer than expected — nothing to add. |
| **RX panel summary row** | **CLOSED ITEM (record, do not re-evaluate)** | The permanent status strip already summarises the whole receive link: `sbMode_/sbSr_/sbVfo_/sbRds_/sbGain_/sbSdr_/sbRssi_/sbSnr_/sbSquelch_/sbGnss_/sbAudio_` + `sMeter_` (`main_window.cpp:2145-2174`), fed by the real ~1 Hz telemetry readback; the multi-VFO list row is separate. RX state is already summarised — closing per prior rounds. |

## API (display layer only)

- `cpp/src/ui/spectrum_display.h:158` — `double mirrorOfCursorHz(int which) const`
  (`which` 1=A, 2=B). PURE geometry, the exact mapping `paintEvent` draws through;
  offscreen-test seam so position can be asserted without inspecting pixels.
  Returns NaN when the cursor is unplaced OR f0 is not finite (the line is then
  honestly not painted).
- `cpp/src/ui/spectrum_display.cpp:196` — implementation:
  `2.0 * dialFreqHz_ - (which==2 ? cursorB_Hz_ : cursorA_Hz_)`, with the NaN guard.
- No new member state — reads `cursorA_Hz_`/`cursorB_Hz_` (`spectrum_display.h:371-372`)
  and `dialFreqHz_` directly.

## Drawing

`cpp/src/ui/spectrum_display.cpp:1003-1023`, painted immediately AFTER the dual-cursor
Δ read-out box, on the SAME `trace` rect and through the SAME `xForFreq(f, fLo, span)`
mapper every other vertical line uses:

- `paintMirror(cursorA_Hz_)` then `paintMirror(cursorB_Hz_)` — one dotted line per
  **placed** cursor;
- **honest empty state**: an unplaced cursor (NaN) — or an out-of-window mirror —
  culls to nothing; with no cursors there are zero mirror lines;
- follows a drag for free (cursor mouse-move updates `cursorA_Hz_` then `update()`,
  `:1333-1337`) and a retune for free (`tuneAndCenter` sets `dialFreqHz_` + `update()`,
  `:685-691`);
- visual separation from every other vertical element on the trace:
  - cursor A/B = teal/pink **dashed** (`kCursorA/BColor`);
  - fixed markers = accent solid / amber dashed-selected;
  - bookmarks = green dotted at 0.45;
  - peaks = accent triangles;
  - VFO tuning centre = solid blue.
  The mirror is a quiet **lavender dotted** line at low alpha, so it reads as a
  *derived reference* of the active cursor, never as a live signal / saved
  bookmark / user-placed marker.
- new named tokens (`tokens.h:188-190`): `kMirrorLineColor="#b48fd6"`,
  `kMirrorLineAlpha=0.35`, `kMirrorLineWidth=1`; dotted style is `Qt::DotLine`.

## Tests (extended existing target — no new CMake target)

`cpp/tests/test_dual_cursor.cpp` (existing `dual_cursor` target already links
`mbdsdr_core`; reused the offscreen `feedFrame`/`sendMouse` harness shape):

- `mirrorPositionAboutTunedFreq` — f0=98.5 MHz; A at +300 kHz, B at −200 kHz ⇒
  mirror A = 98.2 MHz, mirror B = 98.7 MHz; both agree with `xForFrequency`.
- `mirrorFollowsDrag` — a real mouse press-drag-release on cursor A moves it up;
  the mirror recomputes to `2*f0 - newCursorA` and lands on the OPPOSITE side of f0.
- `mirrorRecomputesOnRetune` — `tuneAndCenter(99.0e6)` holds the cursor but moves
  the mirror to `2*newF0 - cursorA`.
- `mirrorHonestEmptyState` — fresh cursors ⇒ mirror NaN (line not painted); after
  `clearCursors()` the mirror returns to NaN.

**Real ctest count:** `dual_cursor` 8/8 passed (6 slots + init/cleanup);
regression `fixed_marker` 4/4 passed.

## Screenshots

`ui_shot_narrow` gained an opt-in `MBD_CURSORSHOT=1` gate (`cpp/tests/ui_screenshot_narrow.cpp:192`):
feeds a real 2.4 MHz frame (so `visibleWindow()` has a real span — without a frame
the mirror x would collapse to span=1 Hz and cull off-canvas) then places
`cursorA=98.8e6`, `cursorB=98.3e6`. Captured offscreen with `MBD_TAB=频谱`:

- `mirror-marker-960.png` (960×760), `mirror-marker-1920.png` (1920×900).
- Verdict (Read on the exact trace rect): pink dashed B, teal dashed A, solid blue
  tuning line, and TWO faint lavender dotted mirrors (98.2 left, 98.7 right) —
  symmetric about 98.5, spaced apart (~30–40 px) from the cursors/tuning line and
  from the dB-grid gutter and the "A"/"B"/Δ labels. **0 clipping, 0 overlapping
  text** at both widths.

## Hard-constraint audit

- Real data only — mirrors derive from the real placed cursors + real tuning
  frequency; the screenshot gate feeds a labelled offline test frame
  (`isTestSignal=true`), no mock inside the app.
- Honest empty state — no cursor / non-finite f0 ⇒ no line.
- No new storage, button, widget, or tool; new values go through named tokens.
- No bare colors/pixels at the call site.
- No `git add/commit/push`.

## Note

The untracked `cpp/tests/ui_diag_freeze.cpp` and `cpp/scratch/regen_tool_doc*`
remain untouched and uncommitted (isolation files, out of scope). The working
tree's other in-flight files were not reverted.
