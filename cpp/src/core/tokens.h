// SPDX-License-Identifier: MIT
// MBDSDR C++ -- THE single source of design tokens.
// Per DESIGN_RULES.md section 3: business code references named constants
// only -- no raw #hex, no raw pixels. All sizes scale with DPI.
#pragma once

#include <QString>
#include <QColor>
#include <QEasingCurve>
#include <QGuiApplication>
#include <QScreen>
#include <QtGlobal>
#include <algorithm>

namespace mbdsdr {
namespace tokens {

// =====================================================================
// Runtime scale factor (DPI-aware, clamped)
// =====================================================================
inline double g_userScale = 1.0;
inline void setUserScale(double s) { g_userScale = s; }

inline double scaleFactor() {
    double base = 1.0;
    if (auto* scr = QGuiApplication::primaryScreen()) {
        base = scr->logicalDotsPerInchX() / 96.0;
    }
    double f = base * g_userScale;
    return std::clamp(f, 0.7, 2.5);
}

inline int scaled(int px) {
    return static_cast<int>(px * scaleFactor());
}

// =====================================================================
// Colors -- backgrounds
// =====================================================================
inline constexpr const char* kBgMain    = "#080a0c";
inline constexpr const char* kBgBar      = "#000000";
inline constexpr const char* kSpectrumBg = "#0a0c0e";
// Cards float on the background as subtle white overlays (CarWith 8% surface),
// not solid filled panels. Adjusted for our #080a0c base.
inline constexpr const char* kCard1      = "rgba(255,255,255,0.045)";
inline constexpr const char* kCard2      = "rgba(255,255,255,0.075)";
inline constexpr const char* kCard3      = "rgba(255,255,255,0.10)";

inline constexpr const char* kCardEdge  = "rgba(255, 255, 255, 0.06)";

// =====================================================================
// Colors -- text (warm neutral, not dead gray)
// =====================================================================
inline constexpr const char* kTextWhite = "#ffffff";
inline constexpr const char* kTextPrimary   = "#ECEAE6";   // warm near-white
inline constexpr const char* kTextSecondary = "#B9B6B1";   // warm mid-gray
inline QString textRgba(double a) {
    return QString("rgba(255, 255, 255, %1)").arg(a);
}
// QPainter-safe QColor built numerically. QColor(QString) cannot parse the
// "rgba(...)" CSS functional string in this Qt build (it silently yields an
// invalid / black color), so widget painting must go through these instead of
// QColor(textRgba(...)). The kCard* strings remain for the QSS generator.
inline QColor rgbaA(double a, int r = 255, int g = 255, int b = 255) {
    return QColor(r, g, b, int(std::clamp(a, 0.0, 1.0) * 255.0 + 0.5));
}
inline QColor card1()     { return rgbaA(0.045); }
inline QColor cardEdge()  { return rgbaA(0.06); }
inline constexpr double kTextAlphaPrimary   = 1.0;
inline constexpr double kTextAlphaSecondary = 0.78;
inline constexpr double kTextAlphaTertiary2 = 0.67;
inline constexpr double kTextAlphaTertiary  = 0.5;
inline constexpr double kTextAlphaQuaternary = 0.3;
inline constexpr double kTextAlphaFaint     = 0.23;
inline constexpr double kTextAlphaDisabled  = 0.2;

// =====================================================================
// Colors -- accents
// =====================================================================
// Instrument / trace blue (#7CC4FF): the bright blue's ONLY remaining role.
// It paints oscilloscope-style canvas traces, VFO band boxes, S-meter fill and
// map/sky instrument overlays, where it carries real signal semantics. It is
// NO LONGER the UI interactive accent -- tab selected text, active buttons,
// splitter handles and tooltip borders now use the restrained blue-gray below.
inline constexpr const char* kAccent       = "#7CC4FF";
inline constexpr const char* kAccentHover  = "#9FD4FF";
inline constexpr const char* kAccentPress  = "#5AA8F0";
// Restrained interactive blue-gray (Figma #919cac): the new UI primary for
// selected / active / focus states. Three low-saturation steps -- hover lifts
// lighter, press drops darker -- never the bright instrument blue.
inline constexpr const char* kInteract       = "#919cac";
inline constexpr const char* kInteractHover  = "#aab3c2";
inline constexpr const char* kInteractPress  = "#7c8796";
// Splitter handle: same blue-gray hairline (was bright blue 0.35).
inline QString splitterHandleRgba() { return QString("rgba(145, 156, 172, 0.35)"); }
inline constexpr const char* kSuccess     = "#5fd08a";
inline constexpr const char* kWarning      = "#e0b35a";
inline constexpr const char* kDanger       = "#e74c3c";

// ---- Restrained semantic layers (Figma audit checklist) ---------------
// Hairline divider: 1px white at ~6%. Prefer whitespace+surface contrast;
// when a line is unavoidable use this, never a heavy solid border.
inline constexpr const char* kDivider      = "rgba(255, 255, 255, 0.06)";
// Selected / active fill: LOW-saturation blue-gray (Figma #919cac), NOT the
// bright accent. Use for list/nav selected blocks; keep accent for the
// single interactive highlight only. Alpha is the cross-end contract (Flutter
// selectedFill must match exactly, within rounding).
inline constexpr double kSelectedFillAlpha = 0.16;
inline constexpr const char* kSelectedFill = "rgba(145, 156, 172, 0.16)";
inline constexpr const char* kSelectedText= "#d7dee8";
// Keyboard focus ring: visible, calm blue-gray ring (was bright accent).
inline constexpr const char* kFocusRing   = "rgba(145, 156, 172, 0.45)";
// Disabled control surface (we previously only dimmed disabled text).
inline constexpr const char* kDisabledFill = "rgba(255, 255, 255, 0.04)";

// ---- Spectrum persistence (余晖) ----------------------------------------
// Per-frame retention of the ghost trace: each frame the held envelope decays by
// (1 - kPersistDecay*) toward the floor, while a fresh rise refreshes it back up.
// Low = short trails (fast fade), High = long trails (slow fade). Named so the
// decay is never a magic number in the canvas.
inline constexpr float kPersistDecayOff  = 0.0f;
inline constexpr float kPersistDecayLow  = 0.78f;   // fast fade
inline constexpr float kPersistDecayHigh = 0.93f;   // slow, long trails
// Ghost trace paint alpha (over the live trace).
inline constexpr float kPersistAlphaLow  = 0.35f;
inline constexpr float kPersistAlphaHigh = 0.55f;

// ---- Frontend software decimation (real anti-alias lowpass + integer D) --
// Selectable integer decimation factors applied BEFORE channelization so narrow
// modes (CW/FT8) run on a reduced-rate band with less compute. D=1 = off.
inline constexpr int   kDecimMaxFactor = 8;
// Lowpass corner as a fraction of the DECIMATED Nyquist (avoids aliasing).
inline constexpr double kDecimLpfFrac = 0.80;

// ---- Fixed marker interaction -------------------------------------------
// Keyboard nudge = visible-span / kFixedMarkerKeyStepDiv (one key step = 0.5%
// of the view), so the granularity scales with zoom.
inline constexpr int    kFixedMarkerKeyStepDiv = 200;
// Selected fixed-marker line: amber dashed + handle dot. Unselected = quiet.
inline constexpr const char* kFixedMarkerSelColor = "#e0b35a";
inline constexpr int    kFixedMarkerLineWidth = 1;

// ---- Dual measurement cursors (SDR++ Δ markers) -------------------------
// Two independent vertical measurement lines; the read-out shows |A-B| Hz.
// Distinct from fixed markers (amber) and peaks (accent triangles): cursor A =
// teal, cursor B = pink. Drag tolerance = half the touch minimum.
inline constexpr const char* kCursorAColor = "#5fe0d0";
inline constexpr const char* kCursorBColor = "#ff8fb2";
inline constexpr int    kCursorLineWidth = 1;

// =====================================================================
// Corner radii
// =====================================================================
inline constexpr int kRadiusPanel     = 24;
inline constexpr int kRadiusDockIcon   = 13;
inline constexpr int kRadiusSearch     = 8;
inline constexpr int kRadiusCard       = 10;

// Motion timing (ms) -- Material Motion scaled down for desktop.
inline constexpr int kAnimShort1  = 120;  // micro feedback
inline constexpr int kAnimShort2  = 160;  // button/list press
inline constexpr int kAnimMedium1 = 220;  // tab/panel switch
inline constexpr int kAnimMedium2 = 280;  // dialog
inline constexpr int kAnimLong1   = 350;  // overlay

// Easing curves: standard = fast-out-slow-in (calm entry); exit = quick
// fade-out (exit duration ~= half entry). Named so animation code does not
// scatter raw QEasingCurve values.
inline QEasingCurve easingStandard() { return QEasingCurve(QEasingCurve::OutCubic); }
inline QEasingCurve easingExit()     { return QEasingCurve(QEasingCurve::InCubic); }
inline constexpr int kRadiusSplitter   = 10;
inline constexpr int kRadiusSmall      = 4;
inline constexpr const char* kRadiusCircle = "50%";
// Pill capsule: fully rounded ends (height/2). Search bar / pill button.
inline constexpr int kRadiusPill      = 999;

// =====================================================================
// Spacing rhythm (base px, scaled at runtime): S=4 M=8 L=16 XL=24 XXL=32
// 4pt grid. Page gutters / big block gaps use XL/XXL (Figma 64/69px @1920
// scales down to ~24-32 desktop). Never invent off-grid pixels.
// =====================================================================
inline constexpr int kSpacingS = 4;
inline constexpr int kSpacingM = 8;
inline constexpr int kSpacingL = 16;
inline constexpr int kSpacingXL  = 24;
inline constexpr int kSpacingXXL = 32;

// =====================================================================
// Sizes (base px, multiply by scaled() at runtime)
// =====================================================================
inline constexpr int kTopbarH          = 56;
inline constexpr int kSplitterWidth    = 8;
inline constexpr int kTouchMin         = 44;
// Main-window floor (base px, scaled at runtime): keeps the 3-column layout
// readable on narrow windows -- no horizontal overflow, no crushed center
// trace. 4pt-grid values; the left rail already scrolls and the right rail
// elides, so a minimum size is enough, no extra re-layout.
inline constexpr int kMainMinW         = 960;
inline constexpr int kMainMinH         = 600;
// Logical minimum touch-target dimension (Qt logical px, scaled at runtime).
// Alias of kTouchMin kept under the name the UI audit references; primary
// interactive controls (text-only buttons, list rows) must meet this.
inline constexpr int kTouchMinDim      = kTouchMin;

// Multi-VFO management list panel (left rail). Min height shows ~2 rows at the
// 44px touch row pitch; max keeps the panel compact yet roomy enough for a few.
inline constexpr int kVfoListMinH     = 96;
inline constexpr int kVfoListMaxH     = 168;
// Logical list-row height for touch: every row is a >=44px tap target.
inline constexpr int kVfoRowH         = kTouchMinDim;

inline constexpr int kPanelPadLeft   = 16;
inline constexpr int kPanelPadTop    = 16;

// Hardware ranges (RTL-SDR spec)
inline constexpr double kFreqMinHz = 24e6;
inline constexpr double kFreqMaxHz = 1700e6;
inline constexpr double kFreqStepHz = 100e3;
inline constexpr double kGainMinDb = 0.0;
inline constexpr double kGainMaxDb = 49.6;
inline constexpr double kGainStepDb = 0.6;
inline const std::initializer_list<double> kSampleRatesHz = {1.024e6, 2.048e6, 2.4e6, 3.2e6};

// Continuous-capture auto-segmentation: after this many seconds of wall-clock
// (by sample count) the recorder finalises the current SigMF capture and opens a
// fresh, collision-avoided file. 0 = unlimited (single file). Not a hard-coded
// magic number: the engine reads it from QSettings("rec/max_seg_s") falling back
// to this default, so a user override never has to edit code.
inline constexpr double kRecMaxSegmentSecondsDefault = 300.0;
inline constexpr double kRecMaxSegmentSecondsMin     = 5.0;
inline constexpr double kRecMaxSegmentSecondsMax     = 3600.0;

// RTL-SDR front-end tuning (advanced panel)
inline constexpr double kPpmMin = -100.0;
inline constexpr double kPpmMax = 100.0;
inline constexpr double kPpmStep = 0.1;

// Spectrum dB range (adjustable via spinboxes)
inline constexpr int    kDbSpinLowerMin = -120;   // lower spinbox allowed min
inline constexpr int    kDbSpinLowerMax = -20;    // lower spinbox allowed max
inline constexpr int    kDbSpinUpperMin = -40;    // upper spinbox allowed min
inline constexpr int    kDbSpinUpperMax = 20;     // upper spinbox allowed max
inline constexpr int    kDbLowerDefault = -100;
inline constexpr int    kDbUpperDefault = 0;
inline constexpr int    kDbGridStep = 20;         // dB gridline spacing

// Horizontal zoom (display-only, does not touch the engine)
inline constexpr double kZoomMin = 1.0;
inline constexpr double kZoomMax = 16.0;

// Peak detection (relative to the spectral median / noise floor)
inline constexpr double kPeakThresholdDefault = 15.0; // dB above median
inline constexpr int    kPeakThresholdMin = 5;
inline constexpr int    kPeakThresholdMax = 40;
inline constexpr float  kPeakAbsFloorDbfs = -100.0f;  // absolute dBFS floor
inline constexpr int    kPeakMatchBins = 5;          // cross-frame match radius (bins)
inline constexpr int    kPeakMinSeenFrames = 3;      // must persist N frames to show
inline constexpr int    kPeakMaxMissFrames = 10;     // forget a peak after N misses
inline constexpr int    kPeakTableH = 96;             // compact list height (base px)
inline constexpr int    kPeakMarkerHalfW = 5;         // triangle marker half-width
inline constexpr int    kPeakMarkerH = 6;             // triangle marker height
inline constexpr int    kPeakMarkerHiHalfW = 7;       // selected marker half-width
inline constexpr int    kPeakMarkerHiH = 9;           // selected marker height

// =====================================================================
// Squelch (静噪) -- thresholds in the AUDIO-BLOCK RMS dBFS domain.
//
// The Squelch gate compares each 48 kHz demodulated audio block's RMS
// (dsp::rmsDbfs) against thresholdDb: audio passes (gate OPEN) only when
// smoothed RMS >= thresholdDb. More-negative = more sensitive (opens on
// quieter audio). These bounds mirror the slider range; the auto-threshold
// derives thresholdDb from the SAME-domain tracked noise floor so it never
// mixes the IQ total-power / per-bin canvas domains (see spectrum_engine).
// =====================================================================
inline constexpr int    kSquelchMinDb     = -100;   // slider lower bound (dBFS)
inline constexpr int    kSquelchMaxDb     = -20;    // slider upper bound (dBFS)
inline constexpr int    kSquelchDefaultDb  = -50;    // default threshold (dBFS)
// Auto gate: threshold = tracked audio-RMS noise floor + this margin (dB).
// Positive margin keeps the gate CLOSED on the noise itself yet opens for a
// real signal ~ this many dB above the quiet background.
inline constexpr double kSquelchAutoMarginDb = 8.0;
// Asymmetric noise-floor follower time constants (per 20 ms audio block):
// follow noise DOWN quickly, ignore upward transients (signals).
inline constexpr double kSquelchNfAlphaDown  = 0.20;  // fast track toward quieter
inline constexpr double kSquelchNfAlphaUp   = 0.005; // slow creep toward louder

// =====================================================================
// Demodulator -> audio chain shared constants (dsp/agc.cpp, dsp/spectrum_engine).
// The envelope AGC levels the recovered audio toward kAgcTargetLin, but its
// boost is CEILed at kAgcMaxGainLin so an empty channel's quiet noise floor is
// not amplified to listening volume (the real "sandpaper hiss" fault). A
// well-received voice already sits near target, so its gain (~1..3x) never
// reaches the ceiling; only idle noise is left quiet.
// =====================================================================
inline constexpr float  kAgcTargetLin   = 0.3f;   // audio level AGC drives toward
inline constexpr float kAgcMaxGainLin = 12.0f;  // ~21.6 dB boost ceiling
// Fixed audio render rate every VFO resampler lands on, and the broadcast-FM
// intermediate rate. Shared by the engine + the synthetic e2e harness.
inline constexpr int    kAudioRateHz   = 48000;
inline constexpr int    kWfmIfRateHz   = 240000;
// Typical RTL-SDR capture rate used by the offline synthetic e2e fixture
// (NOT a production default; the device reports its real rate).
inline constexpr double kFixtureSrcRateHz = 2.048e6;

// Waterfall time-axis labels (inside the left margin)
inline constexpr int kTimeLabelW = 44;
inline constexpr int kTimeLabelH = 12;
inline constexpr int kTimeLabelPadY = 2;
inline constexpr int kTimeTickCount = 3;          // interior ticks (excl. top/bottom)

// Waterfall bottom frequency scale (drawn under the plot area)
inline constexpr int kWaterfallBottomPad = 22;     // reserved strip height
inline constexpr int kWaterfallFreqTicks = 5;      // tick labels across the span
inline constexpr int kWaterfallTickH     = 4;      // tick mark length

// Spectrum plot margins
inline constexpr int kPlotMarginL = 50;
inline constexpr int kPlotMarginR = 12;
inline constexpr int kPlotMarginT = 24;
inline constexpr int kPlotMarginB = 28;

// =====================================================================
// Offline GIS map / polar sky / elevation plot -- "默认" instrument theme.
// Restrained, readable, soft: no loud fills, no culture symbols. Every size
// here is base px and goes through scaled(); widgets must not invent pixels.
// =====================================================================
// Subdued map linework on the near-black ocean (kBgMain).
inline constexpr double kCoastlineAlpha  = 0.34;  // coastline vector
inline constexpr double kGraticuleAlpha  = 0.08;  // graticule hairline
inline constexpr double kTickLabelAlpha  = 0.30;  // lat/lon tick captions
inline constexpr double kLeaderLineAlpha = 0.32;  // point -> label leader

// Map edge gutters (base px): faint lat/lon tick labels sit inside, never
// touching the frame or the data.
inline constexpr int kMapGutterL  = 30;
inline constexpr int kMapGutterB  = 18;

// Map data-point markers (base px, scaled()).
inline constexpr int kMapSatDotR    = 3;   // ordinary orbit star
inline constexpr int kMapSatSelR     = 5;   // selected orbit star
inline constexpr int kMapGnssOuterR  = 6;   // GNSS fix outer ring
inline constexpr int kMapGnssInnerR = 2;   // GNSS fix inner dot
inline constexpr int kMapStationHalf = 3;  // station square half-size
inline constexpr int kMapAcHalf      = 4;   // aircraft triangle half

// Collision-free label placement.
inline constexpr int kLabelOffset   = 10;   // gap marker -> label
inline constexpr int kLabelMinInset = 6;    // label min inset from widget edge

// Polar sky chart.
inline constexpr int    kSkyGutter       = 28;  // compassRadius inset for az captions
inline constexpr int    kSkyRingDotR     = 2;   // pass-arc time tick dot
inline constexpr double  kArcWidth       = 1.4;
inline constexpr double  kArcSelWidth   = 2.2;

// =====================================================================
// Sky "now / preview" time scrubber + selected-satellite trajectory.
// The slider offsets the displayed UTC moment relative to wall-now; dragging
// re-propagates ALL visible satellites with the REAL SGP4 propagator
// (tleClient_->propagateAt). It is throttled so a fast drag never issues more
// than kSkyPreviewMaxHz propagations/sec; release returns to live. The
// trajectory overlays the selected satellite's real az/el around the displayed
// moment (past + future window), distinct from the predicted pass arcs.
// =====================================================================
inline constexpr int    kSkyPreviewRangeMin   = 30;   // slider span: ±30 min around now
inline constexpr int    kSkyPreviewThrottleMs = 100;  // drag recompute coalesce = 10 Hz max
inline constexpr int    kSkyTrajectoryWindowMin = 10; // ±10 min around the displayed time
inline constexpr int    kSkyTrajectorySamples  = 61;   // sample points across the 2*window
inline constexpr double  kTrajLineAlpha       = 0.45;  // dashed trajectory overlay alpha
inline constexpr double  kTrajLineWidth      = 1.2;

// =====================================================================
// LEO PNT geometry availability (PREDICTION, not a position fix) + S-meter.
// =====================================================================
// Geometry: elevation-weighted simplified DOP threshold gates.
inline constexpr double  kGeoMinElevationDeg = 5.0;   // mask low-EL multipath
inline constexpr int     kGeoGoodMinVisible  = 6;     // >=6 usable sats for 好
inline constexpr double  kGeoDopGood        = 4.0;   // DOP <=4  => 好
inline constexpr double  kGeoDopFair        = 6.0;   // DOP <=6  => 中
inline constexpr double  kGeoDopPoor        = 8.0;   // DOP <=8  => 差; beyond => 不足
// S-meter: standard S-unit scale. 1 S-unit = 6 dB (SDR++/fldigi convention),
// S0..S9 range; peak-hold decays slowly toward the real level.
inline constexpr double  kSMeterDbPerUnit   = 6.0;
inline constexpr int     kSMeterMaxUnits    = 9;
inline constexpr double  kSMeterPeakDecayDbPerSec = 12.0;

// TLE freshness: orbit elements drift; an epoch older than kTleStaleDays is
// labelled 过期 (still usable, flagged). kTleRefreshAgeHours = cache age after
// which we block-refetch instead of cache-first. Real thresholds, not demo.
inline constexpr double  kTleStaleDays          = 14.0;
inline constexpr double  kTleFreshCacheHours    = 48.0;

// =====================================================================
// Digital constellation panel (星座图). Display-only: it paints ONLY the
// real post-Costas soft symbols fed via feedSymbols(). Zoom and the I/Q
// histogram are pure view transforms over those real points -- no synthesis.
// =====================================================================
inline constexpr double  kCstZoomMin    = 1.0;    // 1:1 (no magnification)
inline constexpr double  kCstZoomMax    = 8.0;    // max magnification
inline constexpr double  kCstZoomStep   = 1.25;   // per wheel notch / button click
inline constexpr int     kCstHistBins   = 24;     // I-histogram bins (real stat)
inline constexpr int     kCstHistStripH = 18;     // bottom histogram strip height (base px, scaled)
inline constexpr double  kCstHistBarAlpha = 0.35; // subdued histogram bars

// Elevation-vs-time plot margins (separate from spectrum kPlotMargin* so the
// spectrum widget is untouched). Enough room for axis titles + tick captions
// on all four sides so nothing is clipped.
inline constexpr int kElevMarginL = 46;
inline constexpr int kElevMarginR = 22;
inline constexpr int kElevMarginT = 26;
inline constexpr int kElevMarginB = 50;

// =====================================================================
// Satellite pass capture + live Doppler auto-compensation
// =====================================================================
// Max frequency correction (Hz) applied to the active VFO per 1 s live tick.
// Real LEO Doppler at 137-437 MHz moves only tens of Hz/s, so a 2 kHz ceiling
// lets the loop track the live f0+fd exactly while still smoothing a one-shot
// jump (capture / AOS) into bounded, radar-style convergence steps -- never a
// tuner slam or a dither.  Named token, read by core/sat_capture.h.
inline constexpr double kDopplerMaxStepHz = 2000.0;

// =====================================================================
// Autonomous multi-step task orchestration (Agent 自主任务)
// =====================================================================
// Hard cap on steps a single task plan may execute, so a buggy/LLM-generated
// plan can never loop forever. Deterministic templates stay well under this.
inline constexpr int    kTaskMaxSteps = 8;
// A tool result longer than this is trimmed for the on-step summary (the full
// text still goes to the conversation context).
inline constexpr int    kTaskSummaryMaxChars = 160;
// Cap on the automatic signal-activity log history (newest-first); a long scan
// session must not grow storage unbounded.
inline constexpr int    kActivityLogMaxEntries = 500;

// Waterfall (spectrogram) widget
// 5-stop dBFS palette, mapped linearly over [-100, 0] dBFS:
//   #000033 (noise floor) -> #0000ff -> #00ffff -> #ffff00 -> #ff0000 (0 dBFS)
inline constexpr const char* kWaterfallColors[5] = {
    "#000033", "#0000ff", "#00ffff", "#ffff00", "#ff0000"
};
inline constexpr int kWaterfallHeight = 220;      // minimum widget height (base px, scaled())
inline constexpr int kWaterfallHistoryLines = 256; // rolling depth, rows

// =====================================================================
// Waterfall palette -- the single source of colors (hex only here)
// =====================================================================
struct WaterfallStop { float t; const char* hex; };
inline constexpr WaterfallStop kWaterfallStops[] = {
    {0.00f, "#000000"},
    {0.12f, "#00005A"},
    {0.25f, "#0028C8"},
    {0.42f, "#00C8EB"},
    {0.58f, "#28DC5A"},
    {0.75f, "#FFEB3C"},
    {0.88f, "#FF7800"},
    {1.00f, "#FF281E"},
};
inline constexpr int kWaterfallMinH = 48;

// Monochrome (blue-scale) alternative palette.
inline constexpr WaterfallStop kWaterfallStopsMono[] = {
    {0.00f, "#000000"},
    {0.30f, "#0a1a2a"},
    {0.60f, "#1a4a6a"},
    {0.85f, "#5aa8d8"},
    {1.00f, "#dcefff"},
};

// Third palette: viridis (perceptually-uniform, color-vision-deficiency safe).
// Dark purple noise floor -> blue -> teal -> green -> bright yellow peak.
// Index 2 in SpectrumDisplay::setPalette(); persisted under kSettingsKeyPalette.
inline constexpr WaterfallStop kWaterfallStopsViridis[] = {
    {0.00f, "#440154"},
    {0.25f, "#3b528b"},
    {0.50f, "#21918c"},
    {0.75f, "#5ec962"},
    {1.00f, "#fde725"},
};

// =====================================================================
// Unified SpectrumDisplay geometry
// The spectrum trace, the shared frequency strip and the waterfall all start
// at the same left x and share one width, so the frequency axes line up by
// construction (not by coincidence). Every size below is base px and goes
// through scaled() at runtime.
// Layout (top -> bottom): top inset | spectrum trace | 1px gap | shared
// frequency strip | draggable divider | waterfall | bottom inset.
// =====================================================================
inline constexpr int kDispLeftInset    = 50;   // dB label gutter (base px)
inline constexpr int kDispRightInset   = 12;
inline constexpr int kDispTopInset     = 9;
inline constexpr int kDispBottomInset  = 8;
inline constexpr int kDispFreqStripH   = 30;   // shared tick + label strip
inline constexpr int kDispAreaGap      = 1;    // hairline between trace and strip
inline constexpr int kDividerHitHalfH  = 4;    // draggable divider hit half-height
inline constexpr int kSpecAreaMinH     = 120;  // min trace height (base px)
inline constexpr int kWfAreaMinH       = 60;   // min waterfall height (base px)
// Default share of the (trace + waterfall) height given to the trace.
// 0.5 => spectrum and waterfall are roughly equal, both substantial.
inline constexpr double kDefaultSpecFraction = 0.5;
inline constexpr const char* kSettingsKeySpecFraction = "view/specFraction";
inline constexpr const char* kSettingsKeyScrollSpeed  = "view/wfScrollSpeed";
inline constexpr const char* kSettingsKeyPalette      = "view/wfPalette";
// Absolute path of a user-supplied external waterfall colormap JSON (loaded via
// the canvas right-click menu). Empty = built-in palette. Honest silent fallback
// on launch if the file vanished / is malformed -- never a crash, never an
// invented ramp.
inline constexpr const char* kSettingsKeyColormapFile = "view/wfColormapFile";

// Small painted details inside the unified canvas (base px, scaled() at runtime).
inline constexpr int kDispTickProtrusion = 3;   // freq tick pokes up/down into panels
inline constexpr int kVfoHandleHalfW     = 4;   // VFO band-edge drag handle half-width
inline constexpr int kVfoHandleH         = 6;   // VFO band-edge drag handle triangle height
inline constexpr int kBandEdgeHitTol     = 12;  // mouse hit tolerance around a VFO edge

// =====================================================================
// Spectrum widget sizes / offsets
// =====================================================================
inline constexpr int kSpectrumMinW     = 480;
inline constexpr int kSpectrumMinH     = 320;
inline constexpr int kSpectrumPad      = 8;
inline constexpr int kSpectrumSpacing = 4;

// dB axis label geometry
inline constexpr int kDbLabelOffsetY = 6;
inline constexpr int kDbLabelPadR     = 4;
inline constexpr int kDbLabelH       = 12;

// Frequency axis label geometry
inline constexpr int kFreqLabelHalfW   = 40;
inline constexpr int kFreqLabelOffsetY = 4;
inline constexpr int kFreqLabelW       = 80;
inline constexpr int kFreqLabelH       = 16;

inline constexpr double kVfoLineWidth  = 1.5;
inline constexpr int    kTooltipOffset = 8;

// ---- Multi-VFO band boxes (translucent overlays) ------------------------
// Each VFO draws a translucent band box on the spectrum AND waterfall data
// areas: colored fill, two edge lines, a center line, and a name label. Alpha
// is a float 0..1 so the per-VFO color shows through without opaque blocks.
inline constexpr double kVfoBoxFillAlpha         = 0.10;  // unselected fill
inline constexpr double kVfoBoxSelFillAlpha     = 0.20;  // selected fill
inline constexpr double kVfoBoxEdgeAlpha         = 0.55;  // unselected edges
inline constexpr double kVfoBoxSelEdgeAlpha      = 0.95;  // selected edges
inline constexpr double kVfoBoxCenterAlpha       = 0.90;  // center tuning line
inline constexpr double kVfoBoxLabelAlpha        = 0.85;  // name label text
inline constexpr int    kVfoBoxLabelH            = 12;    // base px, scaled()
inline constexpr double kVfoBoxLineWidth         = 1.2;   // edge/center line
inline constexpr double kVfoBoxSelLineWidth      = 1.8;   // selected line
inline constexpr int    kVfoMinBandwidthHz       = 100;
inline constexpr int    kVfoMaxBandwidthHz       = 500000;

// Settings dialog
inline constexpr int kSettingsMinW = 420;

// Recording group (left panel)
inline constexpr int kRecComboMinW = 150;
inline constexpr int kRecTemplateMinW = 140;

// Fine tuning step (keyboard nudge)
inline constexpr double kFreqFineStepHz = 10000.0;

// QSS internal padding/margin (base px, scaled at generation time).
// Vertical padding = kSpacingM, horizontal = kSpacingL, per the rhythm.
inline constexpr int kBtnPadV         = kSpacingM;
inline constexpr int kBtnPadH         = kSpacingL;
// Visual (density) control height for buttons/combos/spinboxes. Distinct from
// the 44px touch minimum: visually dense, but small text-only buttons get a
// 44px hit area on top via setMinimumHeight(tokens::scaled(kTouchMin)).
inline constexpr int kControlH        = 26;
inline constexpr int kSplitterMarginV = 8;
inline constexpr int kSplitterMarginH = 2;
inline constexpr int kComboPadV       = kSpacingM;
inline constexpr int kComboPadH       = kSpacingL;
inline constexpr int kGroupMarginTop  = kSpacingL;
inline constexpr int kGroupPadTop      = kSpacingM;

// =====================================================================
// Ratios
// =====================================================================
inline constexpr double kRatioLeft   = 0.22;
inline constexpr double kRatioCenter = 0.56;
inline constexpr double kRatioRight  = 0.22;

// =====================================================================
// Fonts
// =====================================================================
inline constexpr const char* kFontFamily =
    "MiSans, \"PingFang SC\", \"Hiragino Sans GB\", \"Microsoft YaHei\", SimHei, Arial, Helvetica, sans-serif";
inline constexpr const char* kFontMono =
    "\"JetBrains Mono\", \"Fira Code\", \"Inter\", \"PingFang SC\", monospace";

inline constexpr double kFontTitlePt  = 14.0;   // group / panel titles, bold
inline constexpr double kFontBodyPt   = 11.0;   // controls / labels
inline constexpr double kFontAuxPt     = 9.5;   // status / hints / mono info
// Hero number: central frequency / signal readout (Figma 42-48px @1920).
inline constexpr double kFontDisplayPt = 22.0;
// Legacy alias kept so existing code compiles.
inline constexpr double kFontPanelTitlePt = kFontTitlePt;

// Font-weight semantic names (Figma uses only 400/500/600; never 700+).
inline constexpr int kWeightRegular = 400;
inline constexpr int kWeightMedium  = 500;
inline constexpr int kWeightSemi    = 600;

// =====================================================================
// Measurement cursor (hover readout) -- drawn on the unified canvas only,
// never invented pixels: everything here goes through scaled().
// =====================================================================
inline constexpr double kCursorLineAlpha       = 0.28;   // hairline across trace/waterfall
inline constexpr double kCursorReadoutBgAlpha  = 0.85;   // readout box backdrop
inline constexpr double kCursorReadoutEdge     = 0.35;   // readout box edge
inline constexpr double kCursorReadoutText     = 0.92;   // readout text
inline constexpr int    kCursorReadoutW        = 168;    // box width (base px)
inline constexpr int    kCursorReadoutH        = 34;     // box height (base px)
inline constexpr int    kCursorReadoutPad      = 4;      // inner padding
inline constexpr int    kCursorReadoutGap      = 8;      // gap box <-> cursor
inline constexpr const char* kCursorLineColor  = "#B9B6B1"; // warm neutral, not pure white

// Noise floor baseline on the spectrum trace (real engine measurement, passed
// in by the UI; the canvas only draws what it is given -- never estimates).
inline constexpr double kNoiseFloorLineAlpha  = 0.45;   // dashed baseline
inline constexpr double kNoiseFloorLabelAlpha = 0.55;   // "NF" caption next to the line
inline constexpr const char* kNoiseFloorColor = "#e0b35a"; // subtle warm amber, low alpha

// Peak markers on the trace (sizes already defined above: kPeakMarker*).
inline constexpr double kPeakMarkerFillAlpha    = 0.9;   // small triangle
inline constexpr double kPeakMarkerHiAlpha      = 1.0;   // highlighted triangle
inline constexpr double kPeakMarkerLineAlpha    = 0.55;  // drop line marker -> trace

// "非硬件/NOT HARDWARE" honest-data badge (restrained, neutral, small -- the
// Figma principle: status is quiet information, not a sticker).
inline constexpr double kBadgeBgAlpha    = 0.08;   // subtle pill fill
inline constexpr double kBadgeTextAlpha  = 0.62;   // calm tertiary text

// Focus mode: hide the side rails so the spectrum takes the full width
// (CarWith driving-mode analog). Animation uses the existing kAnimMedium1.
inline constexpr const char* kSettingsKeyFocusMode = "view/focusMode";

// =====================================================================
// Dark QSS generator -- simplified, only what we use.
// Sizes are scaled at generation time.
// =====================================================================
inline QString buildDarkQss() {
    double f = scaleFactor();
    auto S = [&](int px) { return QString::number(static_cast<int>(px * f)); };

    const QString bg      = QString::fromUtf8(kBgMain);
    const QString bgBar   = QString::fromUtf8(kBgBar);
    const QString card1   = QString::fromUtf8(kCard1);
    const QString card2   = QString::fromUtf8(kCard2);
    const QString edge    = QString::fromUtf8(kCardEdge);
    const QString textPri = QString::fromUtf8(kTextPrimary);
    const QString textSec = QString::fromUtf8(kTextSecondary);
    const QString accent  = QString::fromUtf8(kAccent);
    const QString accentH = QString::fromUtf8(kAccentHover);
    const QString accentP = QString::fromUtf8(kAccentPress);
    // Restrained interactive blue-gray (UI primary, NOT the instrument blue).
    const QString interact = QString::fromUtf8(kInteract);
    // Audit tokens (Figma checklist): restrained selected fill, visible focus
    // ring, disabled surface, hairline divider.
    const QString selFill = QString::fromUtf8(kSelectedFill);
    const QString selText = QString::fromUtf8(kSelectedText);
    const QString focus   = QString::fromUtf8(kFocusRing);
    const QString disFill = QString::fromUtf8(kDisabledFill);
    const QString divider = QString::fromUtf8(kDivider);

    return QStringLiteral(R"(
QMainWindow, QWidget {
    background-color: %bg%;
    color: %textPri%;
    font-family: %font%;
}
QWidget#topBar {
    background-color: %bgBar%;
    border: none;
    border-bottom: 1px solid %edge%;
}
QFrame#panelCard {
    background-color: %card1%;
    border: 1px solid %edge%;
    border-radius: %radCard%px;
}
QLabel { color: %textPri%; background: transparent; }
QLabel#panelTitle { color: %textPri%; font-size: %fontTitle%pt; font-weight: 600; }
QLabel#dockHint, QLabel#statusHint { color: %textSec%; font-size: %fontAux%pt; }
QLabel#monoInfo { color: %textSec%; font-family: %mono%; font-size: %fontAux%pt; }
QLabel#testBanner {
    color: rgba(255, 255, 255, 0.62);
    font-size: %fontAux%pt;
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: %radSmall%px;
    padding: 1px %padMV%px;
}
QTabBar::tab {
    background: transparent;
    color: %textSec%;
    padding: %padMV%px %padLH%px;
    min-height: %touch%px;
    border: none;
    border-radius: %radSmall%px;
}
QTabBar::tab:selected { color: %interact%; font-weight: 600; }
QTabBar::tab:hover { color: %textPri%; }
/* Non-switchable section header tab (right-panel info-architecture groups):
   dim, centered, no hover feedback, acts as a group label. */
QTabBar::tab:disabled { color: %textSec%; font-weight: 500; background: transparent; }
QPushButton {
    background-color: transparent;
    color: %textPri%;
    border: 1px solid %edge%;
    border-radius: %radCard%px;
    font-size: %fontBody%pt;
    min-height: %ctlH%px;
    padding: %padSV%px %padMV%px;
}
QPushButton:hover  { background-color: %card2%; }
QPushButton:pressed{ background-color: rgba(0,0,0,0.12); border-color: transparent; }
QPushButton:focus  { border: 1px solid %focus%; }
QPushButton:disabled { color: rgba(255,255,255,0.3); background-color: %disFill%; }
QPushButton[recording="true"] { background-color: #c0392b; color: #ffffff; border-color: #e74c3c; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal {
    width: %splW%px;
    background: %splitterRgba%;
    border-radius: %radSpl%px;
    margin: %splMV%px %splMH%px;
}
QStatusBar { background: %bgBar%; color: %textSec%; border-top: 1px solid %edge%; }
QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: rgba(255,255,255,0.03);
    color: %textPri%;
    border: 1px solid transparent;
    border-radius: %radCard%px;
    padding: %padSV%px %padMV%px;
    min-height: %ctlH%px;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    background-color: %card2%;
    border: 1px solid %edge%;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border: 1px solid %focus%;
    background-color: %card2%;
}
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: rgba(255,255,255,0.3);
    background-color: %disFill%;
}
QListWidget:focus, QTableWidget:focus {
    border: 1px solid %focus%;
}
QComboBox QAbstractItemView {
    background-color: %card1%;
    color: %textPri%;
    selection-background-color: %selFill%;
    selection-color: %selText%;
    border: 1px solid %edge%;
    outline: none;
}
QGroupBox {
    border: 1px solid %edge%;
    border-radius: %radCard%px;
    margin-top: %groupTop%px;
    padding-top: %groupPad%px;
    color: %textSec%;
    font-weight: 600;
    font-size: %fontBody%pt;
    background-color: %card1%;
}
QGroupBox::title { subcontrol-origin: margin; left: %groupPad%px; padding: 0 %padSV%px; }
QPlainTextEdit, QTableWidget, QListWidget {
    background-color: %card1%;
    border: 1px solid %edge%;
    border-radius: %radCard%px;
    color: %textPri%;
    gridline-color: rgba(255,255,255,0.05);
}
QTableWidget::item, QListWidget::item { padding: %padSV%px %padMV%px; }
QTableWidget::item:hover, QListWidget::item:hover { background-color: %card2%; }
QTableWidget::item:selected, QListWidget::item:selected {
    background-color: %selFill%;
    color: %selText%;
}
QHeaderView::section {
    background-color: %card2%;
    color: %textSec%;
    border: none;
    border-right: 1px solid %edge%;
    padding: %padSV%px %padMV%px;
}
QCheckBox { color: %textPri%; spacing: %padMV%px; }
QToolTip {
    background-color: %card1%;
    color: %textPri%;
    border: 1px solid %interact%;
    padding: %padSV%px %padMV%px;
}
QScrollBar:vertical {
    background: transparent;
    width: %sbW%px;
    margin: %padSV%px 0;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.15);
    border-radius: %sbR%px;
    min-height: %touch%px;
}
QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.3); }
QScrollBar:horizontal {
    background: transparent;
    height: %sbW%px;
    margin: 0 %padSV%px;
}
QScrollBar::handle:horizontal {
    background: rgba(255,255,255,0.15);
    border-radius: %sbR%px;
    min-width: %touch%px;
}
QScrollBar::handle:horizontal:hover { background: rgba(255,255,255,0.3); }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
)")
        .replace(QStringLiteral("%bg%"), bg)
        .replace(QStringLiteral("%bgBar%"), bgBar)
        .replace(QStringLiteral("%card1%"), card1)
        .replace(QStringLiteral("%card2%"), card2)
        .replace(QStringLiteral("%card3%"), QString::fromUtf8(kCard3))
        .replace(QStringLiteral("%edge%"), edge)
        .replace(QStringLiteral("%textPri%"), textPri)
        .replace(QStringLiteral("%textSec%"), textSec)
        .replace(QStringLiteral("%accent%"), accent)
        .replace(QStringLiteral("%interact%"), interact)
        .replace(QStringLiteral("%accentP%"), accentP)
        .replace(QStringLiteral("%selFill%"), selFill)
        .replace(QStringLiteral("%selText%"), selText)
        .replace(QStringLiteral("%focus%"), focus)
        .replace(QStringLiteral("%disFill%"), disFill)
        .replace(QStringLiteral("%divider%"), divider)
        .replace(QStringLiteral("%font%"), QString::fromUtf8(kFontFamily))
        .replace(QStringLiteral("%mono%"), QString::fromUtf8(kFontMono))
        .replace(QStringLiteral("%fontTitle%"), QString::number(kFontTitlePt))
        .replace(QStringLiteral("%fontBody%"), QString::number(kFontBodyPt))
        .replace(QStringLiteral("%fontAux%"), QString::number(kFontAuxPt))
        .replace(QStringLiteral("%touch%"), S(kTouchMin))
        .replace(QStringLiteral("%ctlH%"), S(kControlH))
        .replace(QStringLiteral("%sbW%"), S(6))
        .replace(QStringLiteral("%sbR%"), S(3))
        .replace(QStringLiteral("%radCard%"), S(kRadiusCard))
        .replace(QStringLiteral("%radSmall%"), S(kRadiusSmall))
        .replace(QStringLiteral("%radSpl%"), S(kRadiusSplitter))
        .replace(QStringLiteral("%splW%"), S(kSplitterWidth))
        .replace(QStringLiteral("%splitterRgba%"), splitterHandleRgba())
        .replace(QStringLiteral("%splMV%"), S(kSplitterMarginV))
        .replace(QStringLiteral("%splMH%"), S(kSplitterMarginH))
        .replace(QStringLiteral("%padSV%"), S(kSpacingS))
        .replace(QStringLiteral("%padMV%"), S(kSpacingM))
        .replace(QStringLiteral("%padLH%"), S(kSpacingL))
        .replace(QStringLiteral("%groupTop%"), S(kGroupMarginTop))
        .replace(QStringLiteral("%groupPad%"), S(kGroupPadTop));
}

// =====================================================================
// AI tool-calling loop (M4) -- function-calling runtime constants.
// Anchor: END of this header (impl-spec §6). No magic numbers at the call
// sites; the loop watchdog, HTTP timeout and thinking budget all live here.
// =====================================================================
// Hard cap on tool-execution rounds in a single chat loop. A model that keeps
// emitting tool_calls can never spin forever: after this many assistant turns
// the loop force-stops with a "轮次用尽" terminal answer. Same order as
// kTaskMaxSteps (the autonomous-task watchdog).
inline constexpr int kAiMaxToolRounds = 8;
// Per-request HTTP timeout (ms), replacing the previous hardcoded 30000 in
// llm_client. The streaming pump uses a chunk-arrival timeout internally.
inline constexpr int kAiRequestTimeoutMs = 30000;
// Default chain-of-thought budget (tokens) interleaved-thinking models are
// allowed to spend. Backend range is 128..32768 (SF chat-completions note).
inline constexpr int kAiThinkingBudgetTokens = 4096;

// ---- Context compaction (ai/ai_context.cpp), Phase31 Wave2 --------------
// The prompt budget is a FRACTION of the model context window, not a hardcoded
// token count (context-compaction.md §6.2; MetaGPT base_llm.py:352 uses 0.8).
// 0.75 reserves the remaining 25% for completion + tool results, so swapping
// in a bigger-window model (32k -> 128k) scales the budget with it instead of
// over-compacting early. estimateTokens() is heuristic, so this is a trigger,
// not an exact wire measurement.
inline constexpr int    kAiDefaultContextWindowTokens = 32768;  // Qwen2.5-7B window
inline constexpr double  kAiContextBudgetRatio          = 0.75;
// A single tool result longer than this (chars) is PRE-truncated to a head+tail
// preview BEFORE the whole-history budget check, so one verbose tool output (an
// IQ snapshot, a decode log) does not by itself force a full LLM summary. This is
// the orthogonal light trim OpenAI SDK does in ToolOutputTrimmer
// (tool_output_trimmer.py:112-114: max_output_chars=500, preview=200).
inline constexpr int    kAiToolOutputMaxChars     = 1200;
inline constexpr int    kAiToolOutputPreviewChars = 400;

// ---- LLM request error recovery (ai/llm_worker.cpp), Phase31 Wave2 -------
// Retry/backoff budget for TRANSIENT upstream failures (429 / 503 / 504 / stream
// read timeout). Mirrors MiMo-Code retry.ts budget bands: rate_limit 5, server 8.
// 400/401/403 are TERMINAL (never retried -- honest PENDING, no mock).
inline constexpr int    kAiMaxTransientRetries    = 3;
inline constexpr int    kAiBackoffBaseMs         = 1000;
inline constexpr int    kAiBackoffMaxMs           = 8000;

// =====================================================================
// Headless ControlHub (无头控制层, cpp/src/control/control_hub.{h,cpp})
//
// A GUI-decoupled command dispatcher: a named command table maps straight onto
// the existing dsp::SpectrumEngine slots (no QWidget, no rewritten DSP). It is a
// general platform capability -- GUI, the AI tool loop and a future remote/JSON
// front-end are all just its clients. Read commands (get_*, list_*, status) are
// always allowed; write commands are gated by a single master switch
// (setWriteEnabled). The numeric bounds below are DERIVED from the hardware
// tokens above (kFreqMinHz..kFreqMaxHz / kGainMinDb..kGainMaxDb /
// kSquelchMinDb..kSquelchMaxDb) so the command layer stays elastic and invents no
// magic numbers of its own.
// =====================================================================
// Default write-gate posture for a freshly constructed ControlHub. true = the
// headless layer may actually drive the receiver out of the box (that is its
// purpose); a caller that only wants to observe state, or a remote/untrusted
// front-end, flips it off via setWriteEnabled(false), after which every write
// command is HONESTLY refused ({ok:false, gated:true}) and the engine is never
// touched.
inline constexpr bool kControlHubWriteEnabledDefault = true;
// Demodulation modes the set_mode / vfo_set_mode commands accept. Mirrors the
// AI tool enum (ai/tool_schema.cpp) so both command surfaces agree; an
// out-of-vocabulary mode is an honest bad-argument error, never a silent guess.
// This is the SINGLE shared source of mode names: the ControlHub validation
// (needMode), the engine's VfoManager rebuild(), the UI combo and (Wave2) the
// agent tool schema all read this list. Modes are generic capability names --
// never a station / repeater call sign.
inline const char* const kControlHubModes[] = {
    "AM", "NFM", "WFM", "USB", "LSB", "CW",
    "POCSAG", "m17", "VOR"
};
inline constexpr int kControlHubModesCount =
    int(sizeof(kControlHubModes) / sizeof(kControlHubModes[0]));
// Minimum non-zero scan step (Hz) for scan_band: guards against a meaningless
// zero/negative sweep. Elastic upper bound is left to the caller.
inline constexpr double kControlHubScanStepMinHz = 1.0;

// =====================================================================
// Loopback HTTP/JSON control endpoint (cpp/src/control/control_http_server.{h,cpp})
//
// A thin local front-end on top of ControlHub: GET /status + the three decoder
// snapshots, POST /command -> execute() (the SAME read/write gate). It binds
// 127.0.0.1 ONLY and has NO authentication by design. The port is a named token
// (never a raw literal at the call site) and is elastic: it can be overridden at
// runtime by the env var or the QSettings key below, so a test can pin/ephemeral-
// port without recompiling and an operator can move it off the default.
// =====================================================================
// Default loopback TCP port. Chosen clear of the common SDR daemons (rtl_tcp
// 1234, SpyServer 1631, SoapyRemote 55130, rigctl 45321) to avoid a clash.
inline constexpr quint16 kControlHttpDefaultPort = 50732;
// Runtime overrides (highest precedence first: env, then QSettings).
inline constexpr const char* kControlHttpPortEnvVar       = "MBDSDR_CONTROL_HTTP_PORT";
inline constexpr const char* kControlHttpPortSettingsKey  = "control/httpPort";

// =====================================================================
// POCSAG / m17 digital data-link air-interface constants.
//
// Shared by dsp/vfo_manager (which wires the front-end per mode), the decoder
// headers (protocol words) and the Wave-2 ControlHub/UI layers. These are the
// PUBLIC standard modulation parameters -- no private station table.
// =====================================================================
// POCSAG (CCIR Radiopaging Code No.1) over-the-air 2-FSK:
//   1200 baud, +/-4.5 kHz peak deviation. The VfoChannel FskDemod is configured
//   from these so the discriminator normalises exactly to the occupied shift.
inline constexpr double kPocsagBaudBd       = 1200.0;
inline constexpr double kPocsagDeviationHz  = 4500.0;   // +/- peak shift

// m17 4FSK symbol rate already lives on the decoder (M17Decoder::kSymbolRateBd
// = 4800 sym/s); the VfoChannel just feeds channelized IQ to its built-in
// 4FSK front-end at the 48 kHz IF. No extra magic here.

// =====================================================================
// VOR (VHF Omnidirectional Range, ICAO Annex 10) navigation receiver.
//
// Shared physics + business thresholds for dsp/vor_receiver.{h,cpp} and the
// Wave-2 UI/ControlHub display (radial readout, lock indicator, station ID).
// The audio fed to the decoder is the post-FM-demod composite baseband: it
// carries BOTH a 30 Hz spatial (variable) AM tone AND a 30 Hz reference hidden
// as frequency modulation of a 9960 Hz subcarrier (deviation +/-480 Hz,
// modulation index 16). The bearing (radial) is the phase difference between
// the two 30 Hz components. A 1020 Hz on/off-keyed Morse carrier announces the
// station identifier. These are the ICAO standard values; no station database
// is baked in -- the decoder only reports what the airwave actually carries.
// =====================================================================
// ICAO Annex 10 modulation constants (Hz).
inline constexpr double kVorReferenceModHz      = 30.0;     // ref / variable tone
inline constexpr double kVorSubcarrierHz        = 9960.0;   // reference subcarrier
inline constexpr double kVorSubcarrierDevHz     = 480.0;    // +/- peak deviation
inline constexpr double kVorMorseToneHz         = 1020.0;   // ID carrier
// Measurement: one radial estimate integrates over an integer number of 30 Hz
// cycles (2.0 s = 60 cycles) so every integer-Hz interferer coherently cancels.
inline constexpr double kVorBlockSeconds        = 2.0;
// The block is split into this many sub-measurements; their radial scatter
// (circular mean-resultant length R in [0,1]) is the honest confidence.
inline constexpr int    kVorSubBlocks           = 10;
// Declare a radial "locked" only when sub-blocks agree this well AND both 30 Hz
// channels are above their noise floor. Below this the decoder reports an
// honest UNLOCKED state (no fabricated bearing).
inline constexpr double kVorQualityLock         = 0.5;
// Display / test tolerance (degrees): how far a reported radial may sit from
// the true bearing before it is considered wrong.
inline constexpr double kVorRadialToleranceDeg  = 5.0;
// Morse sending speed (PARIS, words/min) used only to size on/off segments.
inline constexpr double kVorMorseWpm           = 12.0;

} // namespace tokens
} // namespace mbdsdr
