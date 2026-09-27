// SPDX-License-Identifier: MIT
// MBDSDR C++ -- THE single source of design tokens.
// Per DESIGN_RULES.md section 3: business code references named constants
// only -- no raw #hex, no raw pixels. All sizes scale with DPI.
#pragma once

#include <QString>
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
inline constexpr const char* kCard1      = "#1f1f1f";
inline constexpr const char* kCard2      = "#2b2c2f";
inline constexpr const char* kCard3      = "#35373c";

inline constexpr const char* kCardEdge  = "rgba(255, 255, 255, 0.08)";

// =====================================================================
// Colors -- text
// =====================================================================
inline constexpr const char* kTextWhite = "#ffffff";
inline QString textRgba(double a) {
    return QString("rgba(255, 255, 255, %1)").arg(a);
}
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
inline constexpr const char* kAccent      = "#919cac";
inline QString splitterHandleRgba() { return QString("rgba(217, 217, 217, 0.3)"); }
inline constexpr const char* kSuccess     = "#5fd08a";
inline constexpr const char* kWarning      = "#e0b35a";

// =====================================================================
// Corner radii
// =====================================================================
inline constexpr int kRadiusPanel     = 24;
inline constexpr int kRadiusDockIcon   = 13;
inline constexpr int kRadiusSearch     = 8;
inline constexpr int kRadiusSplitter   = 10;
inline constexpr int kRadiusSmall      = 4;
inline constexpr const char* kRadiusCircle = "50%";

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
inline constexpr int    kPeakTableH = 96;             // compact list height (base px)
inline constexpr int    kPeakMarkerHalfW = 5;         // triangle marker half-width
inline constexpr int    kPeakMarkerH = 6;             // triangle marker height

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

// Settings dialog
inline constexpr int kSettingsMinW = 420;

// Recording group (left panel)
inline constexpr int kRecComboMinW = 150;
inline constexpr int kRecTemplateMinW = 140;

// Fine tuning step (keyboard nudge)
inline constexpr double kFreqFineStepHz = 10000.0;

// QSS internal padding/margin (base px, scaled at generation time)
inline constexpr int kBtnPadV         = 4;
inline constexpr int kBtnPadH         = 12;
inline constexpr int kSplitterMarginV = 8;
inline constexpr int kSplitterMarginH = 2;
inline constexpr int kComboPadV       = 4;
inline constexpr int kComboPadH       = 10;
inline constexpr int kGroupMarginTop  = 12;
inline constexpr int kGroupPadTop    = 10;

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

inline constexpr double kFontPanelTitlePt  = 18.0;
inline constexpr double kFontBodyPt       = 10.5;
inline constexpr double kFontAuxPt        = 8.5;

// =====================================================================
// Dark QSS generator -- simplified, only what we use.
// Sizes are scaled at generation time.
// =====================================================================
inline QString buildDarkQss() {
    double f = scaleFactor();
    int touchMin = static_cast<int>(kTouchMin * f);
    int radiusPanel = static_cast<int>(kRadiusPanel * f);
    int radiusSearch = static_cast<int>(kRadiusSearch * f);
    int splitterW = static_cast<int>(kSplitterWidth * f);
    int radiusSplitter = static_cast<int>(kRadiusSplitter * f);
    int btnPadV = static_cast<int>(kBtnPadV * f);
    int btnPadH = static_cast<int>(kBtnPadH * f);
    int splitterMarginV = static_cast<int>(kSplitterMarginV * f);
    int splitterMarginH = static_cast<int>(kSplitterMarginH * f);
    int comboPadV = static_cast<int>(kComboPadV * f);
    int comboPadH = static_cast<int>(kComboPadH * f);
    int groupMarginTop = static_cast<int>(kGroupMarginTop * f);
    int groupPadTop = static_cast<int>(kGroupPadTop * f);

    return QStringLiteral(R"(
QMainWindow, QWidget {
    background-color: %1;
    color: %2;
    font-family: %3;
}
QWidget#topBar {
    background-color: %4;
    border: none;
    border-bottom: 1px solid %5;
}
QFrame#panelCard {
    background-color: %6;
    border: 1px solid %5;
    border-radius: %7px;
}
QLabel { color: %2; background: transparent; }
QLabel#panelTitle { color: %8; font-size: %9pt; font-weight: 600; }
QLabel#dockHint { color: %10; font-size: %11pt; }
QLabel#monoInfo { color: %12; font-family: %13; font-size: %11pt; }
QPushButton {
    background-color: %14;
    color: %2;
    border: none;
    border-radius: %15px;
    font-size: %11pt;
    min-height: %16px;
    padding: %21px %22px;
}
QPushButton:hover  { background-color: %6; }
QPushButton:pressed{ background-color: %4; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal {
    width: %17px;
    background: %18;
    border-radius: %19px;
    margin: %23px %24px;
}
QStatusBar { background: %4; color: %10; border-top: 1px solid %5; }
QComboBox {
    background-color: %6;
    color: %2;
    border: none;
    border-radius: %15px;
    padding: %25px %26px;
    min-height: %16px;
}
QComboBox QAbstractItemView {
    background-color: %14;
    color: %2;
    selection-background-color: %20;
    selection-color: %4;
    border: none;
}
QGroupBox {
    border: 1px solid %5;
    border-radius: %15px;
    margin-top: %27px;
    padding-top: %28px;
    color: %8;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPlainTextEdit, QTableWidget {
    background-color: %14;
    border: 1px solid %5;
    border-radius: %15px;
    color: %2;
}
)")
        .arg(QString::fromUtf8(kBgMain),
             textRgba(kTextAlphaSecondary),
             QString::fromUtf8(kFontFamily),
             QString::fromUtf8(kBgBar),
             QString::fromUtf8(kCardEdge),
             QString::fromUtf8(kCard1),
             QString::number(radiusPanel),
             textRgba(kTextAlphaTertiary2),
             QString::number(kFontPanelTitlePt),
             textRgba(kTextAlphaDisabled),
             QString::number(kFontAuxPt),
             textRgba(kTextAlphaTertiary),
             QString::fromUtf8(kFontMono),
             QString::fromUtf8(kCard2),
             QString::number(radiusSearch),
             QString::number(touchMin),
             QString::number(splitterW),
             splitterHandleRgba(),
             QString::number(radiusSplitter),
             QString::fromUtf8(kAccent),
             QString::number(btnPadV),
             QString::number(btnPadH),
             QString::number(splitterMarginV),
             QString::number(splitterMarginH),
             QString::number(comboPadV),
             QString::number(comboPadH),
             QString::number(groupMarginTop),
             QString::number(groupPadTop));
}

} // namespace tokens
} // namespace mbdsdr
