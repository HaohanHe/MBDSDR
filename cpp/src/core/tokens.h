// SPDX-License-Identifier: MIT
// MBDSDR C++ -- THE single source of design tokens.
//
// Values extracted from Figma frames (284_566 / 284_410 / 284_456 / 284_75)
// and the accompanying SCSS. SCSS values override DESIGN_RULES.md summaries.
// Per cpp/DESIGN_RULES.md section 3: business code references these named
// constants only -- no raw #hex, no raw pixels.
#pragma once

#include <QString>

namespace mbdsdr {
namespace tokens {

// =====================================================================
// Colors -- backgrounds
// =====================================================================
inline constexpr const char* kBgMain    = "#080a0c";   // window background (284_566)
inline constexpr const char* kBgBar      = "#000000";   // top bar / bottom dock
inline constexpr const char* kCard1      = "#1f1f1f";   // panel card
inline constexpr const char* kCard2      = "#2b2c2f";   // secondary card
inline constexpr const char* kCard3      = "#35373c";   // tertiary card
inline constexpr const char* kCard4      = "#36383a";   // quaternary card

// Edge light on floating cards (simulates SCMS gradient overlay)
inline constexpr const char* kCardEdge  = "rgba(255, 255, 255, 0.08)";

// =====================================================================
// Colors -- text (white with opacity levels, per SCSS)
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
// Colors -- accents / status
// =====================================================================
inline constexpr const char* kAccent      = "#919cac";
inline constexpr const char* kSplitterHandle = "#d9d9d9"; // base color
inline QString splitterHandleRgba() { return QString("rgba(217, 217, 217, 0.3)"); }
inline constexpr const char* kSuccess     = "#5fd08a";
inline constexpr const char* kWarning      = "#e0b35a";
inline constexpr const char* kTestWarn    = "#e0b35a";
inline constexpr const char* kNowPlayingCoverFrom = "#6a6a6a";
inline constexpr const char* kNowPlayingCoverTo   = "#2c2c2c";

// =====================================================================
// Corner radii (SCSS authoritative)
// =====================================================================
inline constexpr int kRadiusPanel     = 24;   // main three-column cards
inline constexpr int kRadiusDockIcon   = 13;   // bottom dock app icons
inline constexpr int kRadiusSearch     = 8;    // search bar / floating card
inline constexpr int kRadiusAlbumSm   = 3;    // NowPlaying small cover
inline constexpr int kRadiusAlbumLg    = 12;   // right-panel large cover
inline constexpr int kRadiusBattery   = 8;    // battery bar
inline constexpr int kRadiusSplitter   = 10;   // splitter handle pill
inline constexpr const char* kRadiusCircle = "50%";

// =====================================================================
// Sizes (px)
// =====================================================================
inline constexpr int kTopbarH          = 56;
inline constexpr int kDockH            = 64;
inline constexpr int kSplitterWidth    = 8;    // NOT 4 -- SCSS is 8
inline constexpr int kTouchMin         = 44;

// Top bar padding (SCSS: 23px 40px 25px 36px)
inline constexpr int kTopbarPadTop    = 23;
inline constexpr int kTopbarPadRight  = 40;
inline constexpr int kTopbarPadBottom = 25;
inline constexpr int kTopbarPadLeft   = 36;

// Bottom dock padding (SCSS: 14px 40px)
inline constexpr int kDockPadY        = 14;
inline constexpr int kDockPadX       = 40;

// Bottom dock element sizes (SCSS)
inline constexpr int kHomeBtnSize     = 72;
inline constexpr int kVolBtnSize     = 72;
inline constexpr int kDockIconSize   = 63;
inline constexpr int kDockIconGap    = 54;
inline constexpr int kNowPlayingW    = 277;
inline constexpr int kNowPlayingCover = 67;
inline constexpr int kNowPlayingBtn   = 56;
inline constexpr int kBatteryW       = 136;
inline constexpr int kBatteryH      = 8;
inline constexpr int kSystemIconSize  = 48;
inline constexpr int kSystemIconAreaW = 160;

// Panel content padding (SCSS: ~53 left, 40 top)
inline constexpr int kPanelPadLeft   = 53;
inline constexpr int kPanelPadTop    = 40;

// Spectrum plot margins (kept here to avoid magic numbers in paintEvent)
inline constexpr int kPlotMarginL = 50;
inline constexpr int kPlotMarginR = 12;
inline constexpr int kPlotMarginT = 24;
inline constexpr int kPlotMarginB = 28;

// =====================================================================
// Ratios (three-column parallel view)
// =====================================================================
inline constexpr double kRatioLeft   = 0.19;
inline constexpr double kRatioCenter = 0.62;
inline constexpr double kRatioRight  = 0.19;

// =====================================================================
// Fonts
// =====================================================================
inline constexpr const char* kFontFamily =
    "MiSans, \"PingFang SC\", \"Hiragino Sans GB\", \"Microsoft YaHei\", SimHei, Arial, Helvetica, sans-serif";
inline constexpr const char* kFontMono =
    "\"JetBrains Mono\", \"Fira Code\", \"Inter\", \"PingFang SC\", monospace";

// Font sizes in pt (px * 0.75 = pt), per SCSS / Figma
inline constexpr double kFontClockPt      = 29.25; // 39 px (top-left clock)
inline constexpr double kFontMileagePt     = 15.0;  // 20 px
inline constexpr double kFontPanelTitlePt  = 27.0;  // 36 px (panel title, opacity 0.67)
inline constexpr double kFontTempPt        = 31.5;  // 42 px (temperature)
inline constexpr double kFontSongPt       = 18.0;  // 24 px (NowPlaying title)
inline constexpr double kFontBodyPt       = 10.5;  // 14 px
inline constexpr double kFontAuxPt        = 8.5;   // 11-12 px

// =====================================================================
// Dark QSS generator -- the ONLY place stylesheet strings are assembled.
// =====================================================================
inline QString buildDarkQss() {
    return QStringLiteral(R"(
/* ===== MBDSDR dark QSS (Figma 284_566 + SCSS) ===== */
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
QFrame#bottomDock {
    background-color: %4;
    border: none;
    border-top: 1px solid %5;
}
QFrame#panelCard {
    background-color: %6;
    border: 1px solid %5;
    border-radius: %7px;
}
QLabel { color: %2; background: transparent; }
QLabel#clockLabel {
    color: %8; font-size: %9pt; font-weight: 600;
}
QLabel#mileageLabel {
    color: %8; font-size: %10pt; font-weight: 500;
}
QLabel#mileageSub {
    color: %11; font-size: %10pt;
}
QLabel#panelTitle {
    color: %12; font-size: %13pt; font-weight: 600;
}
QLabel#tempLabel {
    color: %8; font-size: %14pt; font-weight: 500;
    font-family: %15;
}
QLabel#tempArrow {
    color: %16; font-size: %14pt;
}
QLabel#songTitle {
    color: %17; font-size: %18pt; font-weight: 500;
}
QLabel#dockHint {
    color: %19; font-size: %20pt;
}
QLabel#statusBanner, QLabel#testBanner {
    color: %21;
    font-family: %15;
    font-weight: 600;
}
QLabel#monoInfo {
    color: %22;
    font-family: %15;
    font-size: %20pt;
}
QPushButton {
    background-color: %23;
    color: %8;
    border: none;
    border-radius: %24px;
    font-size: %10pt;
    min-height: %25px;
}
QPushButton:hover  { background-color: %6; }
QPushButton:pressed{ background-color: %4; }
QPushButton#homeBtn, QPushButton#volBtn {
    background-color: %23;
    border-radius: %26px;
    min-width: %25px; min-height: %25px;
}
QPushButton#dockIcon {
    background-color: %27;
    border-radius: %28px;
    min-width: %29px; min-height: %29px;
}
QPushButton#dockIconSelected {
    background-color: %30;
    border-radius: %28px;
    min-width: %29px; min-height: %29px;
}
QPushButton#playBtn, QPushButton#nextBtn {
    background-color: transparent;
    border-radius: %26px;
    min-width: %31px; min-height: %31px;
}
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal {
    width: %32px;
    background: %33;
    border-radius: %34px;
    margin: 8px 2px;
}
QSplitter::handle:vertical {
    height: %32px;
    background: %33;
    border-radius: %34px;
}
QStatusBar {
    background: %4;
    color: %19;
    border-top: 1px solid %5;
}
QComboBox {
    background-color: %6;
    color: %8;
    border: none;
    border-radius: %35px;
    padding: 4px 10px;
    min-height: %25px;
}
QComboBox QAbstractItemView {
    background-color: %23;
    color: %8;
    selection-background-color: %36;
    selection-color: %4;
    border: none;
}
)")
        .arg(QString::fromUtf8(kBgMain),
             textRgba(kTextAlphaSecondary),
             QString::fromUtf8(kFontFamily),
             QString::fromUtf8(kBgBar),
             QString::fromUtf8(kCardEdge),
             QString::fromUtf8(kCard1),
             QString::number(kRadiusPanel),
             QString::fromUtf8(kTextWhite),
             QString::number(kFontClockPt),
             QString::number(kFontMileagePt),
             textRgba(kTextAlphaTertiary),
             textRgba(kTextAlphaTertiary2),
             QString::number(kFontPanelTitlePt),
             QString::number(kFontTempPt),
             QString::fromUtf8(kFontMono),
             textRgba(kTextAlphaQuaternary),
             textRgba(kTextAlphaSecondary),
             QString::number(kFontSongPt),
             textRgba(kTextAlphaDisabled),
             QString::number(kFontAuxPt),
             QString::fromUtf8(kTestWarn),
             textRgba(kTextAlphaTertiary),
             QString::fromUtf8(kCard2),
             QString::number(kRadiusSearch),
             QString::number(kTouchMin),
             QString::number(kRadiusSplitter),
             textRgba(kTextAlphaFaint),
             QString::number(kRadiusDockIcon),
             QString::number(kDockIconSize),
             QString::fromUtf8(kCard3),
             QString::number(kNowPlayingBtn),
             QString::number(kSplitterWidth),
             splitterHandleRgba(),
             QString::number(kRadiusSplitter),
             QString::number(kRadiusSearch),
             QString::fromUtf8(kAccent));
}

} // namespace tokens
} // namespace mbdsdr
