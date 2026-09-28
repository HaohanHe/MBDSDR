// SPDX-License-Identifier: MIT
// Verify the waterfall consumes real FFT data (not synthetic animation).
#include <QtTest/QtTest>
#include <QApplication>
#include "ui/waterfall.h"
#include "core/spectrum_frame.h"

class TestWaterfall : public QObject {
    Q_OBJECT
private slots:
    void feedsRealFftData();
    void scrollsRows();
};

static mbdsdr::SpectrumFrame makeFrame(int bins, int peakBin, float peakDb, float floorDb) {
    mbdsdr::SpectrumFrame fr;
    fr.sampleRateHz = 2.4e6;
    fr.centerFreqHz = 98.5e6;
    fr.fftSize = bins;
    fr.dbfs.assign(bins, floorDb);
    if (peakBin >= 0 && peakBin < bins) fr.dbfs[peakBin] = peakDb;
    fr.sourceName = "test";
    fr.isTestSignal = true;
    return fr;
}

void TestWaterfall::feedsRealFftData() {
    mbdsdr::ui::WaterfallWidget w;
    w.resize(800, 300);
    w.show();

    const int bins = 256;
    const int peak = bins / 2;
    w.setSpectrum(makeFrame(bins, peak, 0.0f, -100.0f));

    QVERIFY(w.hasFrame());
    const QImage& hist = w.history();
    QCOMPARE(hist.width(), bins);

    // Peak bin (0 dBFS) should map to a bright color; floor bins (-100 dBFS) to dark.
    const QRgb peakPx = hist.pixel(peak, 0);
    const QRgb edgePx = hist.pixel(0, 0);
    const int peakLum = qRed(peakPx) + qGreen(peakPx) + qBlue(peakPx);
    const int edgeLum = qRed(edgePx) + qGreen(edgePx) + qBlue(edgePx);
    QVERIFY2(peakLum > edgeLum, "peak bin should be brighter than floor");
    QVERIFY2(peakLum > 100, "0 dBFS should render a visibly bright pixel");
    QVERIFY2(edgeLum < 50, "-100 dBFS should render near-black");
}

void TestWaterfall::scrollsRows() {
    mbdsdr::ui::WaterfallWidget w;
    w.resize(800, 300);
    w.show();

    const int bins = 128;
    // Frame 1: peak at bin 32
    w.setSpectrum(makeFrame(bins, 32, 0.0f, -100.0f));
    const QRgb row0peak1 = w.history().pixel(32, 0);

    // Frame 2: peak at bin 96 (different column) — should push row 0 down, new top row has peak at 96
    w.setSpectrum(makeFrame(bins, 96, 0.0f, -100.0f));
    const QRgb row0peak2 = w.history().pixel(96, 0);
    const QRgb row1old  = w.history().pixel(32, 1);  // previous top row now at y=1

    const int lumNew = qRed(row0peak2) + qGreen(row0peak2) + qBlue(row0peak2);
    const int lumOld = qRed(row1old) + qGreen(row1old) + qBlue(row1old);
    QVERIFY2(lumNew > 100, "new top row should show the new peak at bin 96");
    QVERIFY2(lumOld > 100, "old peak should have scrolled down to row 1");
}

QTEST_MAIN(TestWaterfall)
#include "test_waterfall.moc"
