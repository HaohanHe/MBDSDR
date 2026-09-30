// SPDX-License-Identifier: MIT
// MBDSDR C++ -- THE single source of design tokens.
// Per DESIGN_RULES.md section 3: business code references named constants
// only -- no raw #hex, no raw pixels. All sizes scale with DPI.
#pragma once

#include <QString>
#include <QColor>
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
// Clean, confident system blue: bright enough to read on #080a0c without
// being neon. Hover lifts lighter, pressed drops ~20% luminance.
inline constexpr const char* kAccent       = "#7CC4FF";
inline constexpr const char* kAccentHover  = "#9FD4FF";
inline constexpr const char* kAccentPress  = "#5AA8F0";
inline QString splitterHandleRgba() { return QString("rgba(124, 196, 255, 0.35)"); }
inline constexpr const char* kSuccess     = "#5fd08a";
inline constexpr const char* kWarning      = "#e0b35a";
inline constexpr const char* kDanger       = "#e74c3c";

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
inline constexpr int kRadiusSplitter   = 10;
inline constexpr int kRadiusSmall      = 4;
inline constexpr const char* kRadiusCircle = "50%";

// =====================================================================
// Spacing rhythm (base px, scaled at runtime): S=4 M=8 L=16
// =====================================================================
inline constexpr int kSpacingS = 4;
inline constexpr int kSpacingM = 8;
inline constexpr int kSpacingL = 16;

// =====================================================================
// Sizes (base px, multiply by scaled() at runtime)
// =====================================================================
inline constexpr int kTopbarH          = 56;
inline constexpr int kSplitterWidth    = 8;
inline constexpr int kTouchMin         = 44;

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
// Legacy alias kept so existing code compiles.
inline constexpr double kFontPanelTitlePt = kFontTitlePt;

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
    border: none;
    border-radius: %radSmall%px;
}
QTabBar::tab:selected { color: %accent%; font-weight: 600; }
QTabBar::tab:hover { color: %textPri%; }
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
QPushButton:disabled { color: rgba(255,255,255,0.3); background-color: transparent; }
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
QComboBox QAbstractItemView {
    background-color: %card1%;
    color: %textPri%;
    selection-background-color: %accent%;
    selection-color: #06121c;
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
    background-color: %accent%;
    color: #06121c;
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
    border: 1px solid %accent%;
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
        .replace(QStringLiteral("%accentP%"), accentP)
        .replace(QStringLiteral("%font%"), QString::fromUtf8(kFontFamily))
        .replace(QStringLiteral("%mono%"), QString::fromUtf8(kFontMono))
        .replace(QStringLiteral("%fontTitle%"), QString::number(kFontTitlePt))
        .replace(QStringLiteral("%fontBody%"), QString::number(kFontBodyPt))
        .replace(QStringLiteral("%fontAux%"), QString::number(kFontAuxPt))
        .replace(QStringLiteral("%touch%"), S(kTouchMin))
        .replace(QStringLiteral("%ctlH%"), S(26))
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

} // namespace tokens
} // namespace mbdsdr
