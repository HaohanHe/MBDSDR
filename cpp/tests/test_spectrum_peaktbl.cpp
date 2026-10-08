// SPDX-License-Identifier: MIT
// Offscreen tests for the auto peak table panel (SpectrumWidget):
//  (a) two deterministic carriers mature into two table rows, loudest first.
//  (b) a single click on a row forwards a real peakTuned(freqHz) carrying the
//      exact injected carrier frequency (the "click a peak to tune" path).
//  (c) a pure-noise band shows the honest "no signal" row -- never a blank grid
//      and never an invented carrier.
//
// Drives the container with synthetic frames; clicks the real QTableWidget
// viewport so the production cellClicked -> peakTuned path is exercised.
#include <QtTest/QtTest>
#include <QApplication>
#include <QSignalSpy>
#include <QTableWidget>
#include <QModelIndex>
#include <vector>
#include <cmath>

#include "ui/spectrum_widget.h"
#include "ui/spectrum_display.h"
#include "core/spectrum_frame.h"
#include "core/tokens.h"

using namespace mbdsdr;

class TestSpectrumPeakTable : public QObject {
    Q_OBJECT
private slots:
    void twoCarriersPopulateRows();
    void clickRowEmitsPeakTuned();
    void emptyBandShowsHonestHint();
};

static constexpr int kBins = 512;
static constexpr double kFs = 2.4e6;
static constexpr double kF0 = 98.5e6;

static SpectrumFrame makeCarrierFrame(int bins, const QList<int>& atBins,
                                      const QList<float>& ampsDb, float floorDb) {
    SpectrumFrame fr;
    fr.sampleRateHz = kFs;
    fr.centerFreqHz = kF0;
    fr.fftSize = bins;
    fr.dbfs.assign(bins, floorDb);
    const int n = qMin(atBins.size(), ampsDb.size());
    for (int i = 0; i < n; ++i) {
        const int b = atBins[i];
        const float db = ampsDb[i];
        if (b < 2 || b >= bins - 2) continue;
        fr.dbfs[b] = db;
        fr.dbfs[b - 1] = db - 4.0f;
        fr.dbfs[b + 1] = db - 4.0f;
        fr.dbfs[b - 2] = db - 10.0f;
        fr.dbfs[b + 2] = db - 10.0f;
    }
    fr.sourceName = "test";
    fr.isTestSignal = true;
    return fr;
}

static void feedMatured(ui::SpectrumWidget& w, const SpectrumFrame& fr) {
    // A carrier must persist kPeakMinSeenFrames to mature into a table row.
    for (int i = 0; i < tokens::kPeakMinSeenFrames; ++i) w.setSpectrum(fr);
}

void TestSpectrumPeakTable::twoCarriersPopulateRows() {
    ui::SpectrumWidget w;
    w.resize(900, 700);
    w.show();
    feedMatured(w, makeCarrierFrame(kBins, {200, 300}, {-20.0f, -30.0f}, -100.0f));

    auto* tbl = w.findChild<QTableWidget*>("peakTable");
    QVERIFY2(tbl, "peak table must exist as a findable child");
    QCOMPARE(tbl->rowCount(), 2);

    // Row 0 = the louder carrier (bin 200 @ -20 dBFS).
    auto* fItem = tbl->item(0, 1);
    QVERIFY(fItem);
    bool ok = false;
    const double mhz = fItem->data(Qt::UserRole).toDouble(&ok);
    QVERIFY2(ok, "frequency cell must carry the carrier MHz in UserRole");
    const double binHz = kFs / (kBins - 1);
    const double expectHz = (kF0 - kFs / 2.0) + 200.0 * binHz;
    QVERIFY2(std::abs(mhz * 1e6 - expectHz) < binHz,
             qPrintable(QString("row 0 frequency must be the injected carrier, got %1 MHz")
                        .arg(mhz)));
    // Δ column (index 3) shows the signed offset in kHz.
    auto* dItem = tbl->item(0, 3);
    QVERIFY(dItem);
    QVERIFY2(!dItem->text().isEmpty(), "Δ(kHz) cell must be populated");
}

void TestSpectrumPeakTable::clickRowEmitsPeakTuned() {
    ui::SpectrumWidget w;
    w.resize(900, 700);
    w.show();
    feedMatured(w, makeCarrierFrame(kBins, {200, 300}, {-20.0f, -30.0f}, -100.0f));

    auto* tbl = w.findChild<QTableWidget*>("peakTable");
    QVERIFY(tbl);
    QCOMPARE(tbl->rowCount(), 2);

    QSignalSpy spy(&w, &ui::SpectrumWidget::peakTuned);
    // Single-click row 0 (the louder carrier at bin 200) on the viewport.
    const QModelIndex idx = tbl->model()->index(0, 1);
    const QRect rc = tbl->visualRect(idx);
    QTest::mouseClick(tbl->viewport(), Qt::LeftButton, Qt::NoModifier, rc.center());

    QVERIFY2(!spy.isEmpty(), "clicking a peak row must emit peakTuned");
    const double tunedHz = spy.takeFirst().at(0).toDouble();
    const double binHz = kFs / (kBins - 1);
    const double expectHz = (kF0 - kFs / 2.0) + 200.0 * binHz;
    QVERIFY2(std::abs(tunedHz - expectHz) < binHz,
             qPrintable(QString("peakTuned must carry the row's carrier frequency, "
                                "got %1 expected ~%2").arg(tunedHz).arg(expectHz)));
}

void TestSpectrumPeakTable::emptyBandShowsHonestHint() {
    ui::SpectrumWidget w;
    w.resize(900, 700);
    w.show();
    // Flat noise floor at the absolute detection gate: no carrier above it.
    for (int i = 0; i < 5; ++i)
        w.setSpectrum(makeCarrierFrame(kBins, {}, {}, -100.0f));

    auto* tbl = w.findChild<QTableWidget*>("peakTable");
    QVERIFY(tbl);
    QCOMPARE(tbl->rowCount(), 1);   // the single spanned hint row
    auto* it = tbl->item(0, 0);
    QVERIFY(it);
    QVERIFY2(it->text().contains(QStringLiteral("无信号峰值")),
             qPrintable(QString("empty band must show the honest no-signal hint, "
                                "got '%1'").arg(it->text())));
}

QTEST_MAIN(TestSpectrumPeakTable)
#include "test_spectrum_peaktbl.moc"
