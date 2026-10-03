// SPDX-License-Identifier: MIT
// Offscreen geometry / data / interaction tests for the unified SpectrumDisplay.
//
// Acceptance:
//  (a) spectrum, frequency strip and waterfall share the same left/right edge
//      (one frequency->x mapping by construction).
//  (b) default layout leaves BOTH the trace and the waterfall substantial.
//  (c) dragging the divider is clamped to the min/max trace heights.
//  (d) one real SpectrumFrame drives the waterfall history (no synthetic data).
//  (e) wheel zoom / Shift-drag pan move the visible window while the left/right
//      edges stay pixel-equal between trace and waterfall (lockstep).
#include <QtTest/QtTest>
#include <QApplication>
#include <QSettings>
#include <QVariant>
#include <QWheelEvent>
#include <QDir>
#include <QFile>
#include <QTemporaryFile>
#include <cmath>

#include "ui/spectrum_display.h"
#include "core/spectrum_frame.h"
#include "core/tokens.h"

using namespace mbdsdr;

class TestSpectrumDisplay : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void cleanupTestCase();
    void panelsShareEdges();
    void defaultLayoutProportions();
    void dividerDragIsClamped();
    void realFrameDrivesHistory();
    void zoomAndPanStayAligned();
    void ssbBoxEdgesAreAligned();
    void maturedPeakMarkerGeometry();
    void noiseFloorBaselineGeometry();
    void cursorReadoutIsRealData();
    void traceWaterfallBinCentreAlign();
    void freqTickDecimalsAdaptive();
    void defaultShareIsOneToOne();
    void reRenderHistoryOnPaletteSwitch();
    void loadColormapFileRoundTrip();
    void downscaleUsesBlockMaxDecimation();
    void specFractionRoundTripPersists();
    void specFractionInvalidFallsBackToDefault();

private:
    // Save/restore the QSettings keys this suite may touch, so tests never leak
    // a polluted view/specFraction (which would re-trigger the original
    // "huge spectrum, tiny waterfall" bug on the next real launch).
    struct SavedKey { QString key; bool had = false; QVariant val; };
    QList<SavedKey> saved_;
};

static SpectrumFrame makeFrame(int bins, int peakBin, float peakDb, float floorDb) {
    SpectrumFrame fr;
    fr.sampleRateHz = 2.4e6;
    fr.centerFreqHz = 98.5e6;
    fr.fftSize = bins;
    fr.dbfs.assign(bins, floorDb);
    if (peakBin >= 0 && peakBin < bins) fr.dbfs[peakBin] = peakDb;
    fr.sourceName = "test";
    fr.isTestSignal = true;
    return fr;
}

void TestSpectrumDisplay::initTestCase() {
    // Record the original value of every key this suite could write, then clear
    // them so tests start from a deterministic, default-proportion state.
    const QStringList keys = {
        tokens::kSettingsKeySpecFraction,
        tokens::kSettingsKeyScrollSpeed,
        tokens::kSettingsKeyPalette,
        QStringLiteral("rx/peakThresholdDb"),
    };
    QSettings s("MBDSDR", "MBDSDR");
    saved_.clear();
    for (const QString& k : keys) {
        SavedKey sk;
        sk.key = k;
        sk.had = s.contains(k);
        if (sk.had) sk.val = s.value(k);
        s.remove(k);
        saved_.append(sk);
    }
    s.sync();
}

void TestSpectrumDisplay::cleanupTestCase() {
    // Restore exactly what we found: real values back, absent keys removed.
    QSettings s("MBDSDR", "MBDSDR");
    for (const SavedKey& sk : saved_) {
        if (sk.had) s.setValue(sk.key, sk.val);
        else        s.remove(sk.key);
    }
    s.sync();
}

void TestSpectrumDisplay::panelsShareEdges() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setSpectrum(makeFrame(512, 256, 0.0f, -100.0f));

    QCOMPARE(w.spectrumRect().left(),   w.waterfallRect().left());
    QCOMPARE(w.spectrumRect().right(),  w.waterfallRect().right());
    QCOMPARE(w.freqStripRect().left(), w.spectrumRect().left());
    QCOMPARE(w.freqStripRect().right(), w.spectrumRect().right());
    // The data width must be identical across all three.
    QCOMPARE(w.spectrumRect().width(), w.waterfallRect().width());
}

void TestSpectrumDisplay::defaultLayoutProportions() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setSpectrum(makeFrame(512, 256, 0.0f, -100.0f));

    const int H = 700;
    // Both panels must be substantial -- the waterfall is NOT a tiny strip.
    QVERIFY2(w.waterfallRect().height() >= tokens::scaled(tokens::kWfAreaMinH),
             "waterfall must have at least its reserved minimum height");
    QVERIFY2(w.waterfallRect().height() >= 0.35 * H,
             "waterfall must occupy a substantial share of the canvas");
    QVERIFY2(w.spectrumRect().height() >= 0.30 * H,
             "spectrum trace must not be squeezed away");
    // The strip sits strictly between the trace and the waterfall.
    QVERIFY(w.freqStripRect().top() > w.spectrumRect().bottom());
    QVERIFY(w.waterfallRect().top() > w.freqStripRect().bottom());
}

void TestSpectrumDisplay::dividerDragIsClamped() {
    QSettings("MBDSDR", "MBDSDR").remove(tokens::kSettingsKeySpecFraction);
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setSpectrum(makeFrame(512, 256, 0.0f, -100.0f));

    const int cx = w.width() / 2;
    const QPoint dividerPos(cx, w.dividerY());

    QTest::mousePress(&w, Qt::LeftButton, Qt::NoModifier, dividerPos);
    // Drag all the way up -- trace should shrink to its minimum, not vanish.
    QTest::mouseMove(&w, QPoint(cx, 0), Qt::LeftButton);
    QTest::mouseMove(&w, QPoint(cx, 0), Qt::LeftButton);
    QVERIFY(w.spectrumRect().height() >= tokens::scaled(tokens::kSpecAreaMinH));
    QVERIFY(w.waterfallRect().height() >= tokens::scaled(tokens::kWfAreaMinH));
    // Drag all the way down -- trace should grow, but leave room for waterfall.
    QTest::mouseMove(&w, QPoint(cx, w.height()), Qt::LeftButton);
    QTest::mouseMove(&w, QPoint(cx, w.height()), Qt::LeftButton);
    QVERIFY(w.spectrumRect().height() >= tokens::scaled(tokens::kSpecAreaMinH));
    QVERIFY(w.waterfallRect().height() >= tokens::scaled(tokens::kWfAreaMinH));
    QTest::mouseRelease(&w, Qt::LeftButton, Qt::NoModifier, QPoint(cx, w.height()));
}

void TestSpectrumDisplay::realFrameDrivesHistory() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();

    const int bins = 256;
    w.setSpectrum(makeFrame(bins, 64, 0.0f, -100.0f));
    QVERIFY(w.hasFrame());
    QCOMPARE(w.history().width(), bins);

    // Second frame moves the peak to a different column; the top row must change.
    w.setSpectrum(makeFrame(bins, 192, 0.0f, -100.0f));
    const QRgb row0_b = w.history().pixel(192, 0);
    const QRgb row1_old = w.history().pixel(64, 1);   // previous top row scrolled down

    const int lumNew = qRed(row0_b) + qGreen(row0_b) + qBlue(row0_b);
    const int lumOld = qRed(row1_old) + qGreen(row1_old) + qBlue(row1_old);
    QVERIFY2(lumNew > 100, "new top row should show the new peak bright");
    QVERIFY2(lumOld > 100, "old peak should have scrolled down into row 1");
    // Frame 2 has NO peak at bin 64, so the fresh top row there must be dark
    // (proving the history reflects the latest frame, not a stale image).
    const QRgb row0_col64 = w.history().pixel(64, 0);
    const int lumCol64 = qRed(row0_col64) + qGreen(row0_col64) + qBlue(row0_col64);
    QVERIFY2(lumCol64 < 50, "top row at the old peak column should be dark now");
}

void TestSpectrumDisplay::zoomAndPanStayAligned() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setSpectrum(makeFrame(512, 256, 0.0f, -100.0f));

    const int leftBefore = w.spectrumRect().left();
    const int rightBefore = w.spectrumRect().right();
    const double lo0 = w.visLoHz();
    const double hi0 = w.visHiHz();

    // Ctrl+wheel = zoom around the cursor (plain wheel now step-tunes the VFO).
    QWheelEvent ev(QPointF(500,300), QPointF(500,300), QPoint(120,0),
                   QPoint(0,120), Qt::NoButton, Qt::ControlModifier,
                   Qt::NoScrollPhase, false);
    w.wheelEvent(&ev);
    QVERIFY(w.visLoHz() != lo0 || w.visHiHz() != hi0);
    // Edges must remain identical after zoom.
    QCOMPARE(w.spectrumRect().left(), w.waterfallRect().left());
    QCOMPARE(w.spectrumRect().right(), w.waterfallRect().right());
    QCOMPARE(w.spectrumRect().left(), leftBefore);
    QCOMPARE(w.spectrumRect().right(), rightBefore);

    // Shift+drag = pan the visible window. Press carries the Shift modifier;
    // the move only needs the held button (panning mode is sticky from press).
    const double lo1 = w.visLoHz();
    const QPoint panFrom(500, 300);
    const QPoint panTo(400, 300);
    QTest::mousePress(&w, Qt::LeftButton, Qt::ShiftModifier, panFrom);
    QTest::mouseMove(&w, panTo, Qt::LeftButton);
    QTest::mouseRelease(&w, Qt::LeftButton, Qt::ShiftModifier, panTo);
    QVERIFY(w.visLoHz() != lo1);
    QCOMPARE(w.spectrumRect().left(), w.waterfallRect().left());
    QCOMPARE(w.spectrumRect().right(), w.waterfallRect().right());
}

// SSB sideband alignment: the dial/tuning line must sit on the edge the
// demodulator actually uses, and the bandwidth box must extend into the
// demodulated sideband -- never mirrored to the wrong side.
void TestSpectrumDisplay::ssbBoxEdgesAreAligned() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setSpectrum(makeFrame(512, 256, 0.0f, -100.0f));   // fs=2.4MHz, f0=98.5MHz

    const double dial = 98.5e6;
    const double bw = 2700.0;

    auto mk = [&](int id, const char* mode) {
        mbdsdr::dsp::VfoMarker m;
        m.id = id; m.freqHz = dial; m.bandwidthHz = bw;
        m.mode = QLatin1String(mode);
        return m;
    };

    int bx0, bx1, vx;

    // USB: box [dial, dial+bw] -> tuning line on the LEFT edge, box to the RIGHT.
    w.vfoBoxGeometryFor(mk(1, "USB"), bx0, bx1, vx);
    QCOMPARE(vx, bx0);
    QVERIFY2(bx1 > bx0, "USB box must extend to the right of the dial line");

    // LSB: box [dial-bw, dial] -> tuning line on the RIGHT edge, box to the LEFT.
    w.vfoBoxGeometryFor(mk(2, "LSB"), bx0, bx1, vx);
    QCOMPARE(vx, bx1);
    QVERIFY2(bx0 < bx1, "LSB box must extend to the left of the dial line");

    // CW is demodulated on the LSB side in vfo_manager -> painted like LSB.
    w.vfoBoxGeometryFor(mk(3, "CW"), bx0, bx1, vx);
    QCOMPARE(vx, bx1);
    QVERIFY2(bx0 < bx1, "CW box must sit to the left of the dial line (LSB side)");

    // Symmetric mode (NFM) keeps the centered box: tuning line at the midpoint.
    w.vfoBoxGeometryFor(mk(4, "NFM"), bx0, bx1, vx);
    QVERIFY2(std::abs(vx - (bx0 + bx1) / 2) <= 1,
             "NFM tuning line must be centered between the band edges");

    // Sideband placement must actually be on OPPOSITE sides of the shared dial.
    int u0, u1, uv, l0, l1, lv;
    w.vfoBoxGeometryFor(mk(1, "USB"), u0, u1, uv);
    w.vfoBoxGeometryFor(mk(2, "LSB"), l0, l1, lv);
    QCOMPARE(uv, lv);                       // same dial frequency -> same x
    QVERIFY2(u1 > lv && l0 < uv,
             "USB box right of dial, LSB box left of dial (not mirrored)");
}

// (a) Matured peak markers land exactly on the trace: triangle apex x tracks the
//     peak frequency and its y sits on the peak's dBFS -- no synthetic offset.
void TestSpectrumDisplay::maturedPeakMarkerGeometry() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();

    const int bins = 512;
    // Same frame (one carrier at bin 128) repeated until the peak matures.
    for (int i = 0; i < tokens::kPeakMinSeenFrames; ++i)
        w.setSpectrum(makeFrame(bins, 128, 0.0f, -100.0f));

    QVERIFY2(!w.maturedPeaks().isEmpty(), "single carrier must mature into a peak");
    const mbdsdr::dsp::PeakInfo pk = w.maturedPeaks().first();

    // The tracked peak is the carrier we injected (bin 128 of 512, fs 2.4 MHz,
    // f0 98.5 MHz): bandLo + (bin+0.5)*binHz.
    const double fs = 2.4e6, f0 = 98.5e6;
    const double expectF = (f0 - fs / 2.0) + (128 + 0.5) * (fs / bins);
    QVERIFY2(std::abs(pk.freqHz - expectF) < fs / bins,
             "tracked peak frequency must match the injected carrier bin");
    QCOMPARE(pk.dbfs, 0.0f);

    // Triangle apex sits exactly on the trace: x = frequency->x, y = dBFS->y.
    const int ax = w.xForFrequency(pk.freqHz);
    const int ay = w.yForDbfs(pk.dbfs);
    QVERIFY2(ax >= w.spectrumRect().left() && ax <= w.spectrumRect().right(),
             "peak marker x must lie inside the trace horizontal extent");
    // dbfs=0 == dbCeil maps to the very top row (paintEvent's own dbToY, which
    // can sit one pixel above the top edge by construction) -- allow that slack.
    QVERIFY2(ay >= w.spectrumRect().top() - 2 && ay <= w.spectrumRect().bottom(),
             "peak marker apex y must lie on the trace");
}

// (b) Noise-floor baseline: default NaN suppresses it; after injection the line
//     y must equal dbToY(nf) on the trace axis.
void TestSpectrumDisplay::noiseFloorBaselineGeometry() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setSpectrum(makeFrame(512, 256, 0.0f, -100.0f));

    QVERIFY2(std::isnan(w.noiseFloorDb()), "default noise floor must be NaN (off)");
    w.setNoiseFloorDb(-80.0f);
    QCOMPARE(w.noiseFloorDb(), -80.0f);
    // The baseline y is exactly the trace mapping of the injected value, and it
    // sits between the trace top and bottom (i.e. on the visible dB axis).
    const int yNf = w.yForDbfs(-80.0f);
    QVERIFY2(yNf > w.spectrumRect().top() && yNf < w.spectrumRect().bottom(),
             "noise-floor baseline must plot within the trace vertical range");
}

// (c) Hover read-out is derived from the real frame (freq mapping + the bin's
//     real dBFS), with SNR = dBFS - injected noise floor.
void TestSpectrumDisplay::cursorReadoutIsRealData() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    // Center bin (256 of 512) is the 0 dB carrier; floor -100 dBFS.
    w.setSpectrum(makeFrame(512, 256, 0.0f, -100.0f));
    w.setNoiseFloorDb(-100.0f);

    // Hover exactly on the carrier bin (bin 256 of 512): bandLo + (256.5)*binHz.
    const double fs = 2.4e6, f0 = 98.5e6;
    const int bins = 512;
    const double carrierF = (f0 - fs / 2.0) + (256 + 0.5) * (fs / bins);
    const QPoint atCarrier(w.xForFrequency(carrierF),
                           w.spectrumRect().center().y());
    const QString ro = w.cursorReadoutText(atCarrier);
    QVERIFY2(!ro.isEmpty(), "read-out must be non-empty over the trace");
    // Line 1 = the frequency under the cursor (within one bin of the carrier).
    const QString fLine = ro.section(QLatin1Char('\n'), 0, 0);
    QVERIFY2(fLine.endsWith("MHz"), "line 1 must report frequency in MHz");
    const double mhz = fLine.left(fLine.indexOf(' ')).toDouble();
    QVERIFY2(std::abs(mhz - carrierF / 1e6) < (fs / bins) / 1e6 + 0.001,
             "cursor frequency must map to the carrier bin");
    QVERIFY2(ro.contains("0.0 dBFS"), "line 2 must be the real carrier bin dBFS");
    QVERIFY2(ro.contains("SNR 100.0 dB"), "SNR = carrier dBFS - injected noise floor");

    // Outside the plot the read-out must be suppressed (no invented numbers).
    QVERIFY(w.cursorReadoutText(QPoint(2, 2)).isEmpty());
}

// W2a contract: the trace and the waterfall share the SAME bin-centre mapping, so
// a peak bin's trace vertex and its waterfall colour column centre agree within
// ~1px -- at BOTH zoom=1 and zoom=16 (the pre-fix floor/ceil drift grew to ~4px
// at zoom=16). Uses the exposed waterfallSourceRect() rather than pixel reads.
void TestSpectrumDisplay::traceWaterfallBinCentreAlign() {
    const int bins = 512;
    const double fs = 2.4e6, f0 = 98.5e6;
    for (double zoom : {1.0, 16.0}) {
        ui::SpectrumDisplay w;
        w.resize(1000, 700);
        w.recomputeGeometry();
        w.setSpectrum(makeFrame(bins, 256, 0.0f, -100.0f));
        w.setZoomFactor(zoom);

        // Peak injected at bin 256 -> bin-centre frequency.
        const double peakF = (f0 - fs / 2.0) + (256 + 0.5) * (fs / bins);
        const int traceX = w.xForFrequency(peakF);

        // Waterfall column 256 centre, mapped through the floating source rect.
        const QRectF src = w.waterfallSourceRect();
        const QRect falls = w.waterfallRect();
        QVERIFY2(src.width() > 0, "waterfall source width must be positive");
        const double colX = falls.left() +
            falls.width() * ((256 + 0.5) - src.left()) / src.width();
        QVERIFY2(std::abs(traceX - colX) <= 1.0,
                 qPrintable(QString("zoom=%1: trace bin x=%2 vs waterfall col x=%3")
                            .arg(zoom).arg(traceX).arg(colX)));
    }
}

// W2a: strip label decimals adapt to the nice step (0/1/2 dp), not a hard 3 dp.
void TestSpectrumDisplay::freqTickDecimalsAdaptive() {
    QCOMPARE(ui::SpectrumDisplay::freqTickDecimals(5e6), 0);
    QCOMPARE(ui::SpectrumDisplay::freqTickDecimals(10e6), 0);
    QCOMPARE(ui::SpectrumDisplay::freqTickDecimals(1e6), 1);
    QCOMPARE(ui::SpectrumDisplay::freqTickDecimals(2e6), 1);
    QCOMPARE(ui::SpectrumDisplay::freqTickDecimals(2.5e5), 2);
    QCOMPARE(ui::SpectrumDisplay::freqTickDecimals(5e5), 2);
}

// W2a: default traceShare_ = 0.5 -> trace and waterfall split 1:1 (the reported
// "trace occupies too much" defect is a Flutter-only flex issue; desktop default
// must stay balanced). Runs with the persisted key ABSENT so it proves the
// no-key path falls back to tokens::kDefaultSpecFraction (not a leftover value
// written by an earlier divider-drag test).
void TestSpectrumDisplay::defaultShareIsOneToOne() {
    QSettings("MBDSDR", "MBDSDR").remove(tokens::kSettingsKeySpecFraction);
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setSpectrum(makeFrame(512, 256, 0.0f, -100.0f));
    QCOMPARE(w.traceShareFraction(), tokens::kDefaultSpecFraction);
    const int th = w.spectrumRect().height();
    const int fh = w.waterfallRect().height();
    QVERIFY2(std::abs(th - fh) <= 2,
             qPrintable(QString("default 1:1 split: trace=%1 falls=%2")
                        .arg(th).arg(fh)));
}

// G2 (clean-room SDR++ waterfall): the ring stores RAW dB rows, so swapping the
// palette or the dB range RE-COLOURS the whole existing history (not just future
// rows) without dropping a frame. Pin the range (auto off), push known frames,
// then swap to a grayscale ramp and assert the same raw pixel moves to the new
// ramp's colour while the history depth is unchanged.
void TestSpectrumDisplay::reRenderHistoryOnPaletteSwitch() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setAutoRangeOn(false);
    w.setDbRange(-100.0f, 0.0f);

    const int bins = 128;
    w.setSpectrum(makeFrame(bins, 40, -10.0f, -100.0f));  // peak bin40 @ -10 dB
    w.setSpectrum(makeFrame(bins, 40, -10.0f, -100.0f)); // second history row
    QCOMPARE(w.history().width(), bins);
    QCOMPARE(w.history().height(), tokens::kWaterfallHistoryLines);

    // Row 0 at the peak column: under the built-in classic ramp, -10 dB over a
    // [-100,0] range => t=0.9 => a warm/bright colour (non-trivial luminance).
    const QRgb before = w.history().pixel(40, 0);
    const int lumBefore = qRed(before) + qGreen(before) + qBlue(before);
    QVERIFY(lumBefore > 100);

    // Swap to an external grayscale ramp black->white. Re-render must recolor
    // the SAME stored raw row: -10 dB (t=0.9) -> near-white (229,229,229).
    QVERIFY(w.loadColormapFromJson(
        QByteArray("{\"name\":\"gray\",\"stops\":[\"#000000\",\"#ffffff\"]}")));
    const QRgb after = w.history().pixel(40, 0);
    QVERIFY2(std::abs(qRed(after) - 229) <= 4 &&
             std::abs(qGreen(after) - 229) <= 4 &&
             std::abs(qBlue(after) - 229) <= 4,
             qPrintable(QString("recoloured peak should be near-white, got %1,%2,%3")
                        .arg(qRed(after)).arg(qGreen(after)).arg(qBlue(after))));
    // The floor column (raw -100 dB) must be pure black under the new ramp.
    const QRgb floorPx = w.history().pixel(0, 0);
    QCOMPARE(qRed(floorPx), 0);
    QCOMPARE(qGreen(floorPx), 0);
    QCOMPARE(qBlue(floorPx), 0);
    // No frame was dropped: depth still the full ring.
    QCOMPARE(w.history().height(), tokens::kWaterfallHistoryLines);

    // A malformed JSON must be HONESTLY REJECTED: the ramp stays the gray one.
    QVERIFY(!w.loadColormapFromJson(QByteArray("{\"stops\":[\"#abc\"]}")));
    QCOMPARE(w.history().pixel(40, 0), after);

    // Narrow the dB range to [-50,0]: the same -10 dB raw value is now t=0.8 ->
    // 204 on the gray ramp, proving the history re-coloured to the new range.
    w.setDbRange(-50.0f, 0.0f);
    const QRgb ranged = w.history().pixel(40, 0);
    QVERIFY2(std::abs(qRed(ranged) - 204) <= 4,
             qPrintable(QString("after range [-50,0], peak should be ~204, got %1")
                        .arg(qRed(ranged))));
}

// L8 file closed loop: loadColormapFromFile() parses a real on-disk JSON,
// re-colours the whole stored raw-dB history (not just future rows), and a
// missing / malformed file is HONESTLY rejected with a non-empty reason while
// the current ramp is left untouched (no crash, no half-ramp).
void TestSpectrumDisplay::loadColormapFileRoundTrip() {
    ui::SpectrumDisplay w;
    w.resize(1000, 700);
    w.recomputeGeometry();
    w.setAutoRangeOn(false);
    w.setDbRange(-100.0f, 0.0f);

    const int bins = 128;
    w.setSpectrum(makeFrame(bins, 40, -10.0f, -100.0f));
    w.setSpectrum(makeFrame(bins, 40, -10.0f, -100.0f));
    const QRgb before = w.history().pixel(40, 0);

    // Write a gray ramp (black->white) to a temp file, then load it from disk.
    QTemporaryFile good;
    QVERIFY(good.open());
    good.write("{\"name\":\"gray\",\"stops\":[\"#000000\",\"#ffffff\"]}");
    good.flush();
    QString err;
    QVERIFY2(w.loadColormapFromFile(good.fileName(), &err),
             qPrintable(QString("valid colormap file must load, err=%1").arg(err)));
    QVERIFY(err.isEmpty());
    const QRgb after = w.history().pixel(40, 0);
    QVERIFY2(std::abs(qRed(after) - 229) <= 4,
             qPrintable(QString("file-loaded gray ramp -> near-white, got %1")
                        .arg(qRed(after))));
    QVERIFY(after != before);   // the history actually changed colour

    // A MISSING file: honest false + reason, ramp left as the gray one.
    err.clear();
    QVERIFY2(!w.loadColormapFromFile(QStringLiteral("/no/such/dir/wf_cmap.json"), &err),
             "missing file must be rejected");
    QVERIFY2(!err.isEmpty(), "missing file must report a reason");
    QCOMPARE(w.history().pixel(40, 0), after);   // ramp unchanged

    // A MALFORMED file on disk: honest false + reason, ramp left untouched.
    QTemporaryFile bad;
    QVERIFY(bad.open());
    bad.write("this is { not valid json stops");
    bad.flush();
    err.clear();
    QVERIFY2(!w.loadColormapFromFile(bad.fileName(), &err),
             "malformed file must be rejected");
    QVERIFY2(!err.isEmpty(), qPrintable(QString("malformed must report reason: %1").arg(err)));
    QCOMPARE(w.history().pixel(40, 0), after);   // still the gray ramp
}

// doZoom peak-hold contract: when the visible source bins collapse onto fewer
// display pixels (zoomed out / large FFT), paintEvent routes EVERY history row
// through the block-MAX decimation (decimateBlockMaxRange) so a narrow CW peak
// stays bright instead of being averaged away. We can observe the branch choice
// the same way paintEvent does -- visible-bin count vs waterfall pixel width --
// and pair it with the pure block-max unit test (decimatePeakSurvivesZoomOut).
void TestSpectrumDisplay::downscaleUsesBlockMaxDecimation() {
    // Large FFT at zoom=1: ~2048 visible bins into a ~900px waterfall -> the
    // block-max (peak-hold) downscale branch is the live one.
    {
        ui::SpectrumDisplay w;
        w.resize(1000, 700);
        w.recomputeGeometry();
        w.setSpectrum(makeFrame(2048, 1000, 0.0f, -100.0f));
        const double srcBins = w.waterfallSourceRect().width();
        const int outPx = w.waterfallRect().width();
        QVERIFY2(outPx > 0, "waterfall must have pixels");
        QVERIFY2(srcBins > outPx + 1.0,
                 qPrintable(QString("high-FFT zoom=1 must hit the block-max "
                                    "downscale branch: srcBins=%1 outPx=%2")
                            .arg(srcBins).arg(outPx)));
    }
    // Small FFT at zoom=1: 128 visible bins into a ~900px waterfall -> each bin
    // maps to >=1 pixel, so the bilinear upscale/1:1 branch (no collapse) is used.
    {
        ui::SpectrumDisplay w;
        w.resize(1000, 700);
        w.recomputeGeometry();
        w.setSpectrum(makeFrame(128, 64, 0.0f, -100.0f));
        const double srcBins = w.waterfallSourceRect().width();
        const int outPx = w.waterfallRect().width();
        QVERIFY2(srcBins <= outPx + 1.0,
                 qPrintable(QString("small-FFT must NOT use downscale: srcBins=%1 outPx=%2")
                            .arg(srcBins).arg(outPx)));
    }
}

// P3: divider placement survives a restart. Drive a real divider drag to a new
// share, release (which persists it), then build a FRESH widget and prove it
// read back the exact stored share -- no pixels inspected.
void TestSpectrumDisplay::specFractionRoundTripPersists() {
    QSettings s("MBDSDR", "MBDSDR");
    s.remove(tokens::kSettingsKeySpecFraction);

    // Instance 1: no key -> honest default, then drag the divider off-centre.
    ui::SpectrumDisplay w1;
    w1.resize(1000, 700);
    w1.recomputeGeometry();
    w1.setSpectrum(makeFrame(512, 256, 0.0f, -100.0f));
    QCOMPARE(w1.traceShareFraction(), tokens::kDefaultSpecFraction);

    const int cx = w1.width() / 2;
    QTest::mousePress(&w1, Qt::LeftButton, Qt::NoModifier, QPoint(cx, w1.dividerY()));
    QTest::mouseMove(&w1, QPoint(cx, 240), Qt::LeftButton);   // drag up: smaller trace share
    QTest::mouseRelease(&w1, Qt::LeftButton, Qt::NoModifier, QPoint(cx, 240));

    // Release must have written the share to QSettings.
    QVERIFY2(s.contains(tokens::kSettingsKeySpecFraction),
             "releasing a divider drag must persist the trace share");
    const double saved = s.value(tokens::kSettingsKeySpecFraction).toDouble();
    QVERIFY2(std::abs(saved - tokens::kDefaultSpecFraction) > 1e-6,
             "drag must move the share off the 0.5 default");
    QVERIFY2(saved > 0.1 && saved < 0.9, "stored share must sit in the legal band");

    // Instance 2 (the "restart"): must read back exactly what was persisted.
    ui::SpectrumDisplay w2;
    w2.resize(1000, 700);
    w2.recomputeGeometry();
    QVERIFY2(std::abs(w2.traceShareFraction() - saved) < 1e-9,
             qPrintable(QString("fresh instance read back %1, expected %2")
                        .arg(w2.traceShareFraction()).arg(saved)));
}

// P3: a missing/non-numeric stored value falls back to the default; a wildly
// out-of-range numeric value is clamped into the legal band (never left raw,
// which would break geometry).
void TestSpectrumDisplay::specFractionInvalidFallsBackToDefault() {
    QSettings s("MBDSDR", "MBDSDR");
    s.remove(tokens::kSettingsKeySpecFraction);

    // Non-numeric garbage => default (the "illegal value" case).
    s.setValue(tokens::kSettingsKeySpecFraction, QStringLiteral("bogus-share"));
    ui::SpectrumDisplay bad;
    bad.resize(1000, 700);
    bad.recomputeGeometry();
    QCOMPARE(bad.traceShareFraction(), tokens::kDefaultSpecFraction);

    // Out-of-range number => clamped into the legal [0.1, 0.9] band (mirrors the
    // local kSpecFracMin/Max in spectrum_display.cpp).
    s.setValue(tokens::kSettingsKeySpecFraction, 5.0);
    ui::SpectrumDisplay hi;
    hi.resize(1000, 700);
    hi.recomputeGeometry();
    QVERIFY2(hi.traceShareFraction() >= 0.1 - 1e-9 &&
             hi.traceShareFraction() <= 0.9 + 1e-9,
             qPrintable(QString("out-of-range share must be clamped, got %1")
                        .arg(hi.traceShareFraction())));
}

QTEST_MAIN(TestSpectrumDisplay)
#include "test_spectrum_display.moc"