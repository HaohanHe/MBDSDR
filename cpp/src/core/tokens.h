// SPDX-License-Identifier: MIT
// MBDSDR C++ -- THE single source of design tokens.
//
// Per cpp/DESIGN_RULES.md section 3: every color / size / radius / spacing /
// font size lives here. Business code (main_window.cpp, spectrum_widget.cpp,
// ...) must reference these named constants -- no raw #hex, no raw pixels.
//
// Exception (per DESIGN_RULES.md): algorithm/physical constants that change
// behavior (FFT size, filter taps, demod bandwidth, dB range) live next to
// the algorithm with a comment citing the reference.
#pragma once

#include <QString>
#include <QStringList>

namespace mbdsdr {
namespace tokens {

// =====================================================================
// Colors (DESIGN_RULES.md section 2 -- overrides old Python tokens.py)
// =====================================================================

// Backgrounds (two depths)
inline constexpr const char* kBg0   = "#000000";   // window base
inline constexpr const char* kBg1   = "#080a0c";   // raised/near-bg layer

// Cards
inline constexpr const char* kCard1 = "#1f1f1f";   // deepest card
inline constexpr const char* kCard2 = "#2b2c2f";   // mid card

// Divider (low-alpha white)
inline constexpr const char* kDivider = "rgba(255, 255, 255, 0.08)";

// Text -- solid colors, not alpha-over-white
inline constexpr const char* kTextPrimary   = "#ffffff";
inline constexpr const char* kTextSecondary = "#939393";
inline constexpr const char* kTextWeak      = "#6b727c";
inline constexpr const char* kTextWeaker    = "#4f5359";

// Accent / status
inline constexpr const char* kAccent   = "#919cac";  // cool blue-grey
inline constexpr const char* kSuccess  = "#5fd08a";
inline constexpr const char* kWarning  = "#e0b35a";

// TEST-SIGNAL watermark (still a warning red; kept as named token)
inline constexpr const char* kTestWarn = "#e0b35a";  // use warning amber, not alarming red

// =====================================================================
// Corner radii (DESIGN_RULES.md section 2)
// =====================================================================
inline constexpr int kRadiusCard    = 13;   // cards (NOT 12)
inline constexpr int kRadiusPillSm  = 42;  // pill button small
inline constexpr int kRadiusPillLg = 84;  // pill button large
inline constexpr const char* kRadiusCircle = "50%";

// =====================================================================
// Font sizes (QSS uses pt; px * 0.75 = pt)
//   DESIGN_RULES.md: 大数字 42/39/48px; 次级 20/24px; 正文 13-14px; 辅助 11-12px
// =====================================================================
inline constexpr double kFontHeroPt    = 31.5;  // 42 px
inline constexpr double kFontTitleLgPt = 29.25; // 39 px
inline constexpr double kFontTitlePt   = 36.0;  // 48 px
inline constexpr double kFontSecSmPt   = 15.0;  // 20 px
inline constexpr double kFontSecLgPt   = 18.0;  // 24 px
inline constexpr double kFontBodyPt   = 10.5;  // 14 px
inline constexpr double kFontAuxPt     = 8.5;   // 11-12 px

// =====================================================================
// Sizes (px)
// =====================================================================
inline constexpr int kTopbarH         = 56;
inline constexpr int kDockH           = 64;
inline constexpr int kSplitterHandle  = 4;
inline constexpr int kTouchMin        = 44;   // min touch target (DESIGN_RULES §4)

// Spectrum plot margins (px) -- kept here so paintEvent has no magic numbers
inline constexpr int kPlotMarginL = 50;
inline constexpr int kPlotMarginR = 12;
inline constexpr int kPlotMarginT = 24;
inline constexpr int kPlotMarginB = 28;

// =====================================================================
// Ratios (three-pane parallel view)
// =====================================================================
inline constexpr double kRatioLeft   = 0.19;
inline constexpr double kRatioCenter  = 0.62;
inline constexpr double kRatioRight  = 0.19;

// =====================================================================
// Dark QSS generator -- the ONLY place stylesheet strings are assembled.
// Business code calls tokens::buildDarkQss(); it must NOT concatenate QSS.
// =====================================================================
inline QString buildDarkQss() {
    return QStringLiteral(R"(
/* ===== MBDSDR dark QSS (generated from tokens.h; do not hand-edit) ===== */
QMainWindow, QWidget {
    background-color: %1;
    color: %2;
    font-family: "MiSans", "Inter", "PingFang SC", "Microsoft YaHei", sans-serif;
}
QWidget#topBar {
    background-color: %1;
    border: none;
    border-bottom: 1px solid %3;
}
QFrame#bottomDock {
    background-color: %4;
    border: none;
    border-top: 1px solid %3;
}
QFrame#card {
    background-color: %5;
    border: none;
    border-radius: %6px;
}
QLabel { color: %2; }
QLabel#sectionTitle {
    color: %7;
    font-size: %8pt;
    font-weight: 600;
}
QLabel#windowTitle {
    color: %7;
    font-size: %16pt;
    font-weight: 600;
}
QLabel#statusBanner, QLabel#testBanner {
    color: %17;
    font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
    font-weight: 600;
}
QLabel#monoInfo {
    color: %18;
    font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
    font-size: %10pt;
}
QLabel#dockHint {
    color: %9;
    font-size: %10pt;
}
QPushButton {
    background-color: %4;
    color: %7;
    border: none;
    border-radius: %11px;
    padding: 8px 18px;
    font-size: %12pt;
    font-weight: 500;
    min-height: %13px;
}
QPushButton:hover   { background-color: %5; }
QPushButton:pressed { background-color: %4; }
QPushButton:disabled {
    color: %9;
    background-color: %1;
}
QSplitter::handle { background-color: %1; }
QSplitter::handle:horizontal { width: %14px; }
QSplitter::handle:vertical   { height: %14px; }
QStatusBar {
    background: %4;
    color: %9;
    border-top: 1px solid %3;
}
QComboBox {
    background-color: %5;
    color: %7;
    border: none;
    border-radius: %6px;
    padding: 4px 10px;
    min-height: %13px;
}
QComboBox QAbstractItemView {
    background-color: %4;
    color: %7;
    selection-background-color: %15;
    selection-color: %1;
    border: none;
}
)")
        .arg(QString::fromUtf8(kBg0),
             QString::fromUtf8(kTextSecondary),
             QString::fromUtf8(kDivider),
             QString::fromUtf8(kCard1),
             QString::fromUtf8(kCard2),
             QString::number(kRadiusCard),
             QString::fromUtf8(kTextPrimary),
             QString::number(kFontSecLgPt),
             QString::fromUtf8(kTextWeaker),
             QString::number(kFontAuxPt),
             QString::number(kRadiusPillSm),
             QString::number(kFontBodyPt),
             QString::number(kTouchMin),
             QString::number(kSplitterHandle),
             QString::fromUtf8(kAccent),
             QString::number(kFontSecLgPt),
             QString::fromUtf8(kTestWarn),
             QString::fromUtf8(kTextWeak));
}

} // namespace tokens
} // namespace mbdsdr
