// SPDX-License-Identifier: MIT
//
// Pure, widget-free waterfall rendering arithmetic.
// ============================================================================
// The canvas (spectrum_display.cpp) used to bake these rules inline: the
// doZoom peak-hold decimation, the 256-entry colormap build, the dB->LUT index,
// the external colormap JSON parser and the "nice" frequency-tick layout. They
// are now header-only functions of plain numbers (plus Qt Core's QJsonDocument,
// which is NOT a widget), unit-tested directly in test_spectrum_render.cpp, and
// the painting / event code delegates to them. One source of truth; the widget
// stays thin.
//
// Clean-room provenance: the SDR++ waterfall mechanism (GPLv3, core/src/gui/
// widgets/waterfall.cpp:65-90 doZoom block-max, :600-631 updateWaterfallFb
// re-render, colormaps.cpp:12-47 external JSON) was studied only for its
// algorithm shape in docs/learn/phase12/sdrpp-waterfall.md and is reimplemented
// here from scratch -- no GPL code was copied.
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <string>
#include <vector>

#include <QByteArray>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QJsonValue>
#include <QString>

namespace mbdsdr {
namespace ui {

// An 8-bit per-channel colour, widget-free (the widget packs these into QRgb).
struct Rgb8 {
    uint8_t r = 0, g = 0, b = 0;
};

// A colormap control point: position t in [0,1] and its colour.
struct ColorStop {
    double t = 0.0;
    Rgb8   c{};
};

// ---------------------------------------------------------------------------
// #rrggbb parsing (shared by the built-in stops and the external JSON file).
// Accepts an optional leading '#', exactly six hex digits. Any other shape is
// rejected -- the caller falls back to a built-in palette rather than inventing
// a colour.
// ---------------------------------------------------------------------------
inline bool parseHexColor(const QString& s, Rgb8* out) {
    if (!out) return false;
    QString str = s.trimmed();
    if (str.startsWith(QLatin1Char('#'))) str = str.mid(1);
    if (str.length() != 6) return false;
    bool ok1 = false, ok2 = false, ok3 = false;
    const unsigned r = str.mid(0, 2).toUInt(&ok1, 16);
    const unsigned g = str.mid(2, 2).toUInt(&ok2, 16);
    const unsigned b = str.mid(4, 2).toUInt(&ok3, 16);
    if (!ok1 || !ok2 || !ok3) return false;
    out->r = static_cast<uint8_t>(r);
    out->g = static_cast<uint8_t>(g);
    out->b = static_cast<uint8_t>(b);
    return true;
}

// ---------------------------------------------------------------------------
// doZoom peak-hold decimation (SDR++ waterfall.cpp:65-90, GPLv3 mechanism only).
// Down-sampling a wide FFT row to fewer display pixels by taking the block MAX
// -- not the block mean -- so a narrow CW peak stays bright when the view is
// zoomed out, instead of being averaged into the neighbours and vanishing.
//
// Decimates input src[srcBegin, srcEnd) into dst[0, dstN). Output pixel j
// covers a proportional slice of the input range; non-finite bins are ignored
// (an empty slice yields -inf, which colourForDb then clamps to the floor).
// ---------------------------------------------------------------------------
inline void decimateBlockMaxRange(const float* src, int srcBegin, int srcEnd,
                                  float* dst, int dstN) {
    if (dstN <= 0 || !dst) return;
    if (srcBegin < 0) srcBegin = 0;
    const int srcN = srcEnd - srcBegin;
    const float negInf = -std::numeric_limits<float>::infinity();
    if (srcN <= 0) {
        for (int j = 0; j < dstN; ++j) dst[j] = negInf;
        return;
    }
    const double scale = static_cast<double>(srcN) / static_cast<double>(dstN);
    for (int j = 0; j < dstN; ++j) {
        int a = srcBegin + static_cast<int>(std::floor(static_cast<double>(j) * scale));
        int b = srcBegin + static_cast<int>(std::floor(static_cast<double>(j + 1) * scale));
        if (b <= a) b = a + 1;          // guarantee at least one source bin
        if (a < srcBegin) a = srcBegin;
        if (b > srcEnd)   b = srcEnd;
        if (a >= b) b = std::min(a + 1, srcEnd);
        float mx = negInf;
        for (int k = a; k < b; ++k) {
            const float v = src[k];
            if (std::isfinite(v) && v > mx) mx = v;
        }
        dst[j] = std::isfinite(mx) ? mx : negInf;
    }
}

// Whole-array convenience: decimate src[0, srcN) into dst[0, dstN).
inline void decimateBlockMax(const float* src, int srcN, float* dst, int dstN) {
    decimateBlockMaxRange(src, 0, srcN, dst, dstN);
}

// ---------------------------------------------------------------------------
// 256-entry LUT build by piecewise-linear interpolation between control stops.
// Mirrors spectrum_display's long-standing rebuildColormap() math (segment
// search on the sorted t grid, linear R/G/B interp), extracted so it can be
// asserted on known stops without a QWidget. Fewer than two stops yields a
// black LUT (a degenerate map never invents colours).
// ---------------------------------------------------------------------------
inline std::array<Rgb8, 256> buildLut256(const ColorStop* stops, int n) {
    std::array<Rgb8, 256> lut{};
    if (!stops || n < 2) return lut;
    std::vector<ColorStop> s(stops, stops + n);
    std::sort(s.begin(), s.end(),
              [](const ColorStop& a, const ColorStop& b) { return a.t < b.t; });
    for (int i = 0; i < 256; ++i) {
        const float t = i / 255.0f;
        int seg = 0;
        while (seg < static_cast<int>(s.size()) - 2 && t > s[seg + 1].t) ++seg;
        const ColorStop& A = s[seg];
        const ColorStop& B = s[seg + 1];
        const float span = static_cast<float>(B.t - A.t);
        const float u = (span > 0.0f) ? static_cast<float>(t - A.t) / span : 0.0f;
        lut[i].r = static_cast<uint8_t>(A.c.r + std::lround(u * (B.c.r - A.c.r)));
        lut[i].g = static_cast<uint8_t>(A.c.g + std::lround(u * (B.c.g - A.c.g)));
        lut[i].b = static_cast<uint8_t>(A.c.b + std::lround(u * (B.c.b - A.c.b)));
    }
    return lut;
}

// dB -> LUT index in [0,255] for the given waterfall dB range. Out-of-range dB
// clamps to the floor/ceiling colour; a degenerate range collapses to index 0.
inline int lutIndexForDb(float db, float floorDb, float ceilDb) {
    const float span = ceilDb - floorDb;
    if (span <= 0.0f) return 0;
    float t = (db - floorDb) / span;
    t = std::min(std::max(t, 0.0f), 1.0f);
    const int idx = static_cast<int>(t * 255.0f);
    return std::min(idx, 255);
}

// ---------------------------------------------------------------------------
// External colormap JSON (SDR++ colormaps.cpp:12-47 shape, clean-room).
// Accepted document, TWO equivalent roots:
//   { "name": "optional",
//     "stops": [ "#rrggbb", ... ] }                       // evenly spaced 0..1
//   { "stops": [ {"t":0.0,"c":"#rrggbb"}, ... ] }          // explicit positions
// or a BARE top-level array with the same element shapes:
//   [ "#rrggbb", ... ]                                      // evenly spaced 0..1
//   [ {"t":0.0,"c":"#rrggbb"}, ... ]                        // explicit positions
// On a well-formed map (>=2 valid stops) the stops are written to *out and the
// function returns true. On ANY malformed input it returns false and leaves
// *out untouched -- the caller keeps the built-in default instead of painting a
// half-parsed ramp. When errorOut is non-null it receives a short, human-readable
// reason so the UI can tell the user exactly why the file was rejected (honest
// reporting, never a silent no-op).
// ---------------------------------------------------------------------------
struct ParsedColormap {
    std::string          name;
    std::vector<ColorStop> stops;
};

inline bool parseColormapJson(const QByteArray& json, ParsedColormap* out,
                              QString* errorOut = nullptr) {
    if (!out) {
        if (errorOut) errorOut->assign(QStringLiteral("空输出"));
        return false;
    }
    auto fail = [&](const QString& msg) {
        if (errorOut) errorOut->assign(msg);
        return false;
    };

    QJsonParseError pe;
    const QJsonDocument doc = QJsonDocument::fromJson(json, &pe);
    if (pe.error != QJsonParseError::NoError)
        return fail(QStringLiteral("不是合法 JSON：%1").arg(pe.errorString()));

    QJsonArray arr;
    if (doc.isObject()) {
        const QJsonObject root = doc.object();
        arr = root.value(QLatin1String("stops")).toArray();
        if (root.value(QLatin1String("name")).isString())
            out->name = root.value(QLatin1String("name")).toString().toStdString();
    } else if (doc.isArray()) {
        arr = doc.array();
    } else {
        return fail(QStringLiteral("色板根必须是 {\"stops\":[...]} 对象或 [...] 数组"));
    }
    if (arr.size() < 2)
        return fail(QStringLiteral("至少需要 2 个色标颜色（当前 %1 个）").arg(arr.size()));

    std::vector<ColorStop> parsed;
    parsed.reserve(arr.size());
    bool explicitT = false;
    for (int i = 0; i < arr.size(); ++i) {
        const QJsonValue el = arr.at(i);
        ColorStop cs;
        if (el.isString()) {
            Rgb8 c;
            if (!parseHexColor(el.toString(), &c))
                return fail(QStringLiteral("第 %1 个颜色不是 #rrggbb 形式：%2")
                                .arg(i + 1).arg(el.toString()));
            cs.c = c;
        } else if (el.isObject()) {
            const QJsonObject o = el.toObject();
            const QJsonValue cv = o.value(QLatin1String("c")).isString()
                                      ? o.value(QLatin1String("c"))
                                      : o.value(QLatin1String("color"));
            if (!cv.isString())
                return fail(QStringLiteral("第 %1 个色标缺少 \"c\":\"#rrggbb\"").arg(i + 1));
            Rgb8 c;
            if (!parseHexColor(cv.toString(), &c))
                return fail(QStringLiteral("第 %1 个颜色不是 #rrggbb 形式：%2")
                                .arg(i + 1).arg(cv.toString()));
            cs.c = c;
            const QJsonValue tv = o.value(QLatin1String("t"));
            if (!tv.isDouble())
                return fail(QStringLiteral("第 %1 个色标缺少数值型 \"t\"（0..1）").arg(i + 1));
            cs.t = std::min(std::max(tv.toDouble(0.0), 0.0), 1.0);
            explicitT = true;
        } else {
            return fail(QStringLiteral("第 %1 个色标既不是颜色字符串也不是对象").arg(i + 1));
        }
        parsed.push_back(cs);
    }

    if (!explicitT) {
        // String-only list: space the controls evenly from 0 to 1.
        const double denom = static_cast<double>(parsed.size() - 1);
        for (std::size_t i = 0; i < parsed.size(); ++i)
            parsed[i].t = static_cast<double>(i) / denom;
    } else {
        std::sort(parsed.begin(), parsed.end(),
                  [](const ColorStop& a, const ColorStop& b) { return a.t < b.t; });
    }

    out->stops = std::move(parsed);
    if (errorOut) errorOut->clear();
    return true;
}

// ---------------------------------------------------------------------------
// Frequency-strip ticks: pick a "nice" step so ~targetCount ticks land across
// the visible window, then list the tick frequencies ascending. The step is
// one of {1,2,2.5,5,10}*10^k, exactly mirroring the strip's long-standing
// paintEvent math -- extracted so the zoom/pan linkage (a wide span -> coarse
// ticks, a zoomed-in span -> fine ticks) can be asserted without a widget.
// ---------------------------------------------------------------------------
inline double niceStepForSpan(double spanHz, int targetCount = 5) {
    if (!(spanHz > 0.0) || targetCount <= 0) return 1.0;
    const double raw = spanHz / static_cast<double>(targetCount);
    const double mag = std::pow(10.0, std::floor(std::log10(raw)));
    double step = mag;
    for (const double m : {2.0, 2.5, 5.0, 10.0})
        if (mag * m >= raw) { step = mag * m; break; }
    return step;
}

inline std::vector<double> freqTicksNice(double loHz, double hiHz,
                                         int targetCount = 5) {
    std::vector<double> ticks;
    if (!(hiHz > loHz) || targetCount <= 0) return ticks;
    const double step = niceStepForSpan(hiHz - loHz, targetCount);
    for (double f = std::floor(loHz / step) * step; f <= hiHz; f += step)
        ticks.push_back(f);
    return ticks;
}

} // namespace ui
} // namespace mbdsdr
