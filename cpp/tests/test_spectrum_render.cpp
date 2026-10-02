// SPDX-License-Identifier: MIT
// Pure waterfall-render arithmetic tests. These used to be inlined inside the
// painting / colormap bodies: the doZoom block-max decimation, the 256-entry
// LUT build, the dB->LUT index (range re-mapping), the external colormap JSON
// parser (valid / invalid / honest fallback) and the "nice" frequency ticks
// that follow zoom and pan. Assert them directly with no QWidget.
#include <QtTest/QtTest>
#include <cmath>
#include <vector>

#include "ui/spectrum_render.h"

using namespace mbdsdr::ui;

class TestSpectrumRender : public QObject {
    Q_OBJECT
private slots:
    void decimateTakesBlockMax();
    void decimatePeakSurvivesZoomOut();
    void lutBuiltBetweenStops();
    void lutIndexFollowsRange();
    void jsonParsesEvenlySpacedStops();
    void jsonParsesExplicitStops();
    void jsonRejectsMalformedAndKeepsFallback();
    void ticksShrinkOnZoomIn();
    void ticksFollowPan();
};

// ---- doZoom peak-hold decimation -------------------------------------------
void TestSpectrumRender::decimateTakesBlockMax() {
    // 8 bins -> 4 outputs: each output is the MAX of its two-source block.
    const std::vector<float> in = {1.f, 5.f, 2.f, 8.f, 3.f, 9.f, 4.f, 7.f};
    std::vector<float> out(4, 0.f);
    decimateBlockMax(in.data(), static_cast<int>(in.size()), out.data(), 4);
    QCOMPARE(out[0], 5.f);   // max(1,5)
    QCOMPARE(out[1], 8.f);   // max(2,8)
    QCOMPARE(out[2], 9.f);   // max(3,9)
    QCOMPARE(out[3], 7.f);   // max(4,7)
}

void TestSpectrumRender::decimatePeakSurvivesZoomOut() {
    // 16 bins of -100 dB with a single 0 dB peak at index 7. 4 outputs = 4 bins
    // each: index 7 lives in the second block, so that output MUST be the full 0
    // dB peak. A mean/average downsample would have blurred it to ~-75 dB.
    std::vector<float> in(16, -100.f);
    in[7] = 0.f;
    std::vector<float> out(4, 0.f);
    decimateBlockMax(in.data(), 16, out.data(), 4);
    QCOMPARE(out[0], -100.f);
    QCOMPARE(out[1], 0.f);        // the narrow peak survived the zoom-out
    QCOMPARE(out[2], -100.f);
    QCOMPARE(out[3], -100.f);
}

// ---- 256-entry LUT ---------------------------------------------------------
void TestSpectrumRender::lutBuiltBetweenStops() {
    const ColorStop stops[] = {
        {0.0, Rgb8{0, 0, 0}},
        {1.0, Rgb8{255, 255, 255}},
    };
    const std::array<Rgb8, 256> lut = buildLut256(stops, 2);
    QCOMPARE(lut[0].r, 0);   QCOMPARE(lut[0].g, 0);   QCOMPARE(lut[0].b, 0);
    QCOMPARE(lut[255].r, 255); QCOMPARE(lut[255].g, 255); QCOMPARE(lut[255].b, 255);
    QCOMPARE(lut[127].r, 127);   // mid ramp
}

// dB -> LUT index must track the waterfall range (re-render contract).
void TestSpectrumRender::lutIndexFollowsRange() {
    // Range [-100, 0]: 0 dB -> top, -100 dB -> bottom, -50 dB -> mid.
    QCOMPARE(lutIndexForDb(0.f,   -100.f, 0.f), 255);
    QCOMPARE(lutIndexForDb(-100.f,-100.f, 0.f), 0);
    QCOMPARE(lutIndexForDb(-50.f, -100.f, 0.f), 127);
    // Re-render under a NARROWED range [-50, 0]: the same -50 dB is now the
    // floor (index 0) and an old -100 dB value clamps to the floor too -- the
    // history must be re-coloured, not left at the old indices.
    QCOMPARE(lutIndexForDb(-50.f, -50.f, 0.f), 0);
    QCOMPARE(lutIndexForDb(-100.f,-50.f, 0.f), 0);
    QCOMPARE(lutIndexForDb(-25.f, -50.f, 0.f), 127);
}

// ---- external colormap JSON ------------------------------------------------
void TestSpectrumRender::jsonParsesEvenlySpacedStops() {
    const QByteArray js =
        "{\"name\":\"demo\",\"stops\":[\"#000000\",\"#ff0000\",\"#00ff00\"]}";
    ParsedColormap cm;
    QVERIFY(parseColormapJson(js, &cm));
    QCOMPARE(static_cast<int>(cm.stops.size()), 3);
    QCOMPARE(cm.stops[0].t, 0.0);
    QCOMPARE(cm.stops[1].t, 0.5);   // evenly spaced 0..1
    QCOMPARE(cm.stops[2].t, 1.0);
    QCOMPARE(cm.stops[2].c.g, 255);
    QCOMPARE(cm.name, std::string("demo"));
}

void TestSpectrumRender::jsonParsesExplicitStops() {
    const QByteArray js =
        "{\"stops\":[{\"t\":0.8,\"c\":\"#ff0000\"},{\"t\":0.2,\"c\":\"#00ff00\"}]}";
    ParsedColormap cm;
    QVERIFY(parseColormapJson(js, &cm));
    QCOMPARE(static_cast<int>(cm.stops.size()), 2);
    // Explicit-but-unsorted positions are sorted ascending.
    QCOMPARE(cm.stops[0].t, 0.2);
    QCOMPARE(cm.stops[1].t, 0.8);
}

void TestSpectrumRender::jsonRejectsMalformedAndKeepsFallback() {
    // Seed *out so we can prove a rejection leaves it untouched (honest fallback).
    ParsedColormap good;
    QVERIFY(parseColormapJson("{\"stops\":[\"#000000\",\"#ffffff\"]}", &good));
    ParsedColormap out = good;

    QVERIFY(!parseColormapJson("this is not json", &out));
    QVERIFY(!parseColormapJson("[1,2,3]", &out));                    // not object
    QVERIFY(!parseColormapJson("{\"stops\":[\"#000000\"]}", &out));  // <2 stops
    QVERIFY(!parseColormapJson("{\"stops\":[\"#123\",\"#ff0000\"]}", &out)); // bad hex
    QVERIFY(!parseColormapJson("{\"stops\":[{\"c\":\"#ff0000\"},{\"c\":\"#00ff00\"}]}",
                               &out));                               // object w/o t
    // *out is STILL the good map after every rejection (caller keeps the default).
    QCOMPARE(static_cast<int>(out.stops.size()), 2);
    QCOMPARE(out.stops[1].c.r, 255);
}

// ---- frequency ticks follow zoom/pan ---------------------------------------
void TestSpectrumRender::ticksShrinkOnZoomIn() {
    // Zoomed OUT (full 2.4 MHz span): coarse 500 kHz step.
    const double wide = niceStepForSpan(2.4e6, 5);
    QCOMPARE(wide, 500000.0);
    // Zoomed IN x16 (150 kHz span): fine 50 kHz step.
    const double narrow = niceStepForSpan(2.4e6 / 16.0, 5);
    QCOMPARE(narrow, 50000.0);
    QVERIFY(narrow < wide);
}

void TestSpectrumRender::ticksFollowPan() {
    // Visible window [97.3, 99.7] MHz, 500 kHz step -> ticks on the 500 kHz grid.
    const auto t1 = freqTicksNice(97.3e6, 99.7e6, 5);
    QVERIFY(!t1.empty());
    for (std::size_t i = 1; i < t1.size(); ++i) QVERIFY(t1[i] > t1[i - 1]);
    QVERIFY(t1.front() >= 97.3e6 - 500000.0);
    QVERIFY(t1.back() <= 99.7e6 + 500000.0);
    // Pan the window right by a full 500 kHz step to [97.8, 100.3] MHz: the first
    // visible grid line slides with the window (97.0 MHz -> 97.5 MHz) instead of
    // staying glued to the old anchor.
    const auto t2 = freqTicksNice(97.8e6, 100.3e6, 5);
    QVERIFY(!t2.empty());
    QVERIFY2(std::abs(t2.front() - t1.front()) > 1.0,
             qPrintable(QString("panning a full step must move the first tick: %1 vs %2")
                        .arg(t1.front()).arg(t2.front())));
}

QTEST_MAIN(TestSpectrumRender)
#include "test_spectrum_render.moc"
