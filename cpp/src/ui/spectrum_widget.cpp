// SPDX-License-Identifier: MIT
#include "spectrum_widget.h"

#include <QPainter>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QComboBox>
#include <QSpinBox>
#include <QLabel>
#include <QPen>
#include <QPainterPath>
#include <QMenu>
#include <QTableWidget>
#include <QHeaderView>
#include <QTableWidgetItem>
#include <QCheckBox>
#include <QPushButton>
#include <QSettings>
#include <algorithm>
#include <cmath>

#include <QMouseEvent>
#include <QWheelEvent>
#include <QPolygonF>
#include <QContextMenuEvent>
#include <QtGlobal>
#include "core/tokens.h"

#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace ui {

SpectrumWidget::SpectrumWidget(QWidget* parent)
    : QWidget(parent)
{
    setMinimumSize(tokens::scaled(tokens::kSpectrumMinW),
                   tokens::scaled(tokens::kSpectrumMinH));
    setAutoFillBackground(true);

    auto* outer = new QVBoxLayout(this);
    outer->setContentsMargins(tokens::scaled(tokens::kSpectrumPad),
                              tokens::scaled(tokens::kSpectrumPad),
                              tokens::scaled(tokens::kSpectrumPad),
                              tokens::scaled(tokens::kSpectrumPad));
    outer->setSpacing(tokens::scaled(tokens::kSpectrumSpacing));

    // Row 1: compact tool strip. Row 2: source banner (test signal / name).
    auto* topRow = new QHBoxLayout();
    topRow->setSpacing(tokens::scaled(tokens::kSpacingS));

    topRow->addWidget(new QLabel("FFT", this));
    fftCombo_ = new QComboBox(this);
    fftCombo_->addItems({"1024", "2048", "4096"});
    fftCombo_->setCurrentIndex(1);
    connect(fftCombo_, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) {
                switch (idx) {
                    case 0: emit fftSizeRequested(1024); break;
                    case 1: emit fftSizeRequested(2048); break;
                    case 2: emit fftSizeRequested(4096); break;
                }
                emit viewChanged();
            });
    topRow->addWidget(fftCombo_);

    topRow->addWidget(new QLabel("窗", this));
    auto* winCombo = new QComboBox(this);
    winCombo->addItems({"Hann", "FlatTop", "Blackman"});
    connect(winCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) { emit windowTypeRequested(idx); });
    topRow->addWidget(winCombo);

    topRow->addWidget(new QLabel("平均", this));
    auto* avgCombo = new QComboBox(this);
    avgCombo->addItems({"Off", "Slow", "Fast"});
    connect(avgCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, [this](int idx) { emit averageModeRequested(idx); });
    topRow->addWidget(avgCombo);

    auto* maxHoldChk = new QCheckBox("Max", this);
    connect(maxHoldChk, &QCheckBox::toggled,
            this, &SpectrumWidget::setMaxHoldEnabled);
    topRow->addWidget(maxHoldChk);
    auto* maxRst = new QPushButton("Rst", this);
    connect(maxRst, &QPushButton::clicked, this, [this]() {
        maxHold_.clear();
        update();
    });
    topRow->addWidget(maxRst);
    topRow->addSpacing(tokens::scaled(tokens::kSpacingM));

    // Adjustable dB range (vertical scale).
    topRow->addWidget(new QLabel("dB", this));
    dbMinSpin_ = new QSpinBox(this);
    dbMinSpin_->setRange(tokens::kDbSpinLowerMin, tokens::kDbSpinLowerMax);
    dbMinSpin_->setValue(tokens::kDbLowerDefault);
    dbMinSpin_->setSuffix("");
    topRow->addWidget(dbMinSpin_);
    dbMaxSpin_ = new QSpinBox(this);
    dbMaxSpin_->setRange(tokens::kDbSpinUpperMin, tokens::kDbSpinUpperMax);
    dbMaxSpin_->setValue(tokens::kDbUpperDefault);
    topRow->addWidget(dbMaxSpin_);
    auto applyDb = [this]() {
        int lo = dbMinSpin_->value();
        int hi = dbMaxSpin_->value();
        if (lo >= hi) {  // keep a sane range; nudge the other bound
            hi = lo + 10;
            if (hi > tokens::kDbSpinUpperMax) { hi = tokens::kDbSpinUpperMax; lo = hi - 10; }
            dbMaxSpin_->blockSignals(true); dbMaxSpin_->setValue(hi); dbMaxSpin_->blockSignals(false);
            dbMinSpin_->blockSignals(true); dbMinSpin_->setValue(lo); dbMinSpin_->blockSignals(false);
        }
        setDbRange(static_cast<float>(lo), static_cast<float>(hi));
    };
    connect(dbMinSpin_, QOverload<int>::of(&QSpinBox::valueChanged), this, applyDb);
    connect(dbMaxSpin_, QOverload<int>::of(&QSpinBox::valueChanged), this, applyDb);
    // Persist dB range immediately whenever it actually settles.
    connect(dbMinSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
            this, &SpectrumWidget::viewChanged);
    connect(dbMaxSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
            this, &SpectrumWidget::viewChanged);

    // Peak detection threshold (dB above the measured noise floor / median).
    topRow->addWidget(new QLabel("门限", this));
    peakThreshSpin_ = new QSpinBox(this);
    peakThreshSpin_->setRange(tokens::kPeakThresholdMin, tokens::kPeakThresholdMax);
    peakThreshSpin_->setSuffix(" dB");
    // Restore a persisted threshold; fall back to the token default.
    {
        QSettings s("MBDSDR", "MBDSDR");
        int saved = s.value("rx/peakThresholdDb",
                            static_cast<int>(tokens::kPeakThresholdDefault)).toInt();
        saved = std::clamp(saved, tokens::kPeakThresholdMin, tokens::kPeakThresholdMax);
        peakThreshSpin_->setValue(saved);
        peakThresholdDb_ = static_cast<float>(saved);
    }
    connect(peakThreshSpin_, QOverload<int>::of(&QSpinBox::valueChanged),
            this, [this](int v) {
        peakThresholdDb_ = static_cast<float>(v);
        // Persist immediately (cheap single-value write; no need for the
        // debounced save timer MainWindow uses for high-frequency events).
        QSettings("MBDSDR", "MBDSDR").setValue("rx/peakThresholdDb", v);
        detectPeaks();          // re-run immediately on threshold change
        update();
    });
    topRow->addWidget(peakThreshSpin_);

    infoLabel_ = new QLabel(this);
    infoLabel_->setObjectName("monoInfo");
    infoLabel_->setText(" ");
    topRow->addWidget(infoLabel_, 1);

    outer->addLayout(topRow);

    // Row 2: source banner + info (kept off the tool strip so controls breathe).
    auto* bannerRow = new QHBoxLayout();
    testLabel_ = new QLabel("", this);
    testLabel_->setObjectName("testBanner");
    bannerRow->addWidget(testLabel_);
    bannerRow->addStretch();
    outer->addLayout(bannerRow);

    // Peak list below the plot: frequency / level / -3 dB bandwidth.
    peakTable_ = new QTableWidget(0, 4, this);
    peakTable_->setHorizontalHeaderLabels({"#", "频率(MHz)", "强度(dBFS)", "带宽(kHz)"});
    peakTable_->horizontalHeader()->setStretchLastSection(true);
    peakTable_->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    peakTable_->verticalHeader()->setVisible(false);
    peakTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    peakTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    peakTable_->setSelectionMode(QAbstractItemView::SingleSelection);
    peakTable_->setFixedHeight(tokens::scaled(tokens::kPeakTableH));
    // Double-click a row -> retune the VFO to that peak, and recenter the view
    // on it if it fell outside the current zoom window.
    connect(peakTable_, &QTableWidget::cellDoubleClicked,
            this, [this](int row, int) {
        auto* item = peakTable_->item(row, 1);
        if (!item) return;
        bool ok = false;
        const double mhz = item->data(Qt::UserRole).toDouble(&ok);
        if (ok) tuneAndCenter(mhz * 1e6);
    });
    // Single click (select row) -> highlight that peak's marker on the plot,
    // but do NOT retune. The highlighted index indexes into peaks_ (which is
    // sorted loudest-first, same order as the table rows).
    connect(peakTable_, &QTableWidget::currentCellChanged,
            this, [this](int row, int, int, int) {
        highlightedPeak_ = (row >= 0 && row < peaks_.size()) ? row : -1;
        update();
    });
    outer->addWidget(peakTable_);

    outer->addStretch();
}

void SpectrumWidget::setDbRange(float minDb, float maxDb) {
    if (maxDb <= minDb) maxDb = minDb + 1.0f;
    dbMin_ = minDb;
    dbMax_ = maxDb;
    update();
}

void SpectrumWidget::setDbSpinValues(int lo, int hi) {
    if (dbMinSpin_) dbMinSpin_->setValue(lo);
    if (dbMaxSpin_) dbMaxSpin_->setValue(hi);
    // Re-apply through the same clamping logic the spinbox callback uses.
    if (hi <= lo) hi = lo + 10;
    setDbRange(static_cast<float>(lo), static_cast<float>(hi));
}

int SpectrumWidget::dbMinValue() const {
    return dbMinSpin_ ? dbMinSpin_->value() : static_cast<int>(dbMin_);
}
int SpectrumWidget::dbMaxValue() const {
    return dbMaxSpin_ ? dbMaxSpin_->value() : static_cast<int>(dbMax_);
}

int SpectrumWidget::fftSizeValue() const {
    static const int kSizes[] = {1024, 2048, 4096};
    const int idx = fftCombo_ ? fftCombo_->currentIndex() : 1;
    return kSizes[(idx >= 0 && idx <= 2) ? idx : 1];
}

void SpectrumWidget::setFftSizeValue(int n) {
    static const int kSizes[] = {1024, 2048, 4096};
    int idx = 1;
    for (int i = 0; i < 3; ++i) if (kSizes[i] == n) idx = i;
    if (fftCombo_) fftCombo_->setCurrentIndex(idx);
}

void SpectrumWidget::setZoomFactor(double z) {
    zoomFactor_ = std::clamp(z, tokens::kZoomMin, tokens::kZoomMax);
    // Restoring a persisted zoom: keep the view centered on the current f0.
    if (frame_.sampleRateHz > 0.0) viewCenterHz_ = frame_.centerFreqHz;
    update();
    emitVisibleRange();
}

void SpectrumWidget::resetZoom() {
    zoomFactor_ = tokens::kZoomMin;
    viewCenterHz_ = frame_.centerFreqHz;
    update();
    emitVisibleRange();
}

void SpectrumWidget::visibleRange(double& fLo, double& fHi, double& spanVis) const {
    const double fs = frame_.sampleRateHz;
    spanVis = fs / zoomFactor_;
    fLo = viewCenterHz_ - spanVis / 2.0;
    fHi = viewCenterHz_ + spanVis / 2.0;
}

void SpectrumWidget::emitVisibleRange() {
    double fLo, fHi, spanVis;
    visibleRange(fLo, fHi, spanVis);
    emit visibleRangeChanged(fLo, fHi);
}

void SpectrumWidget::tuneAndCenter(double hz) {
    double fLo, fHi, spanVis;
    visibleRange(fLo, fHi, spanVis);
    // Only pan if the target is outside the current window; keep zoomFactor_.
    if (hz < fLo || hz > fHi) viewCenterHz_ = hz;
    emit frequencyChanged(hz);
    update();
    emitVisibleRange();
}

void SpectrumWidget::setSpectrum(const SpectrumFrame& frame) {
    // On the very first frame (or if f0 changed while we were NOT panned
    // away), anchor the view center to f0.
    const bool firstFrame = (frame_.sampleRateHz <= 0.0);
    frame_ = frame;
    // Max-hold envelope: per-bin historical peak.
    if (maxHoldEnabled_) {
        if (static_cast<int>(maxHold_.size()) != frame.dbfs.size())
            maxHold_.assign(frame.dbfs.size(), -1000.0f);
        for (std::size_t i = 0; i < frame.dbfs.size(); ++i)
            if (frame.dbfs[i] > maxHold_[i]) maxHold_[i] = frame.dbfs[i];
    }
    const double fs  = frame.sampleRateHz;
    const double f0  = frame.centerFreqHz;
    if (firstFrame) {
        viewCenterHz_ = f0;
        // Push the now-correct visible window to the waterfall so it syncs
        // on startup (the restore-time emit had no f0 yet).
        emitVisibleRange();
    }
    testLabel_->setText(frame.isTestSignal ? "测试信号（非硬件）" : "");
    const QString info = QString("%1  Fs=%2 MHz  F0=%3 MHz  N=%4")
                        .arg(frame.sourceName.isEmpty() ? "?" : frame.sourceName)
                        .arg(fs / 1e6, 0, 'f', 1)
                        .arg(f0 / 1e6, 0, 'f', 1)
                        .arg(frame.fftSize);
    // Elide long source names so they never overflow the right edge.
    QFontMetrics fm(infoLabel_->font());
    infoLabel_->setText(fm.elidedText(info, Qt::ElideRight,
                                      qMax(50, infoLabel_->width())));
    detectPeaks();
    update();
}

void SpectrumWidget::detectPeaks() {
    const QList<dsp::PeakInfo> raw = dsp::detectPeaks(frame_.dbfs, frame_.sampleRateHz,
                                  frame_.centerFreqHz, peakThresholdDb_,
                                  tokens::kPeakAbsFloorDbfs);

    // --- Cross-frame tracking ------------------------------------------------
    const std::size_t n = frame_.dbfs.size();
    const double binHz = (n > 1) ? frame_.sampleRateHz / (n - 1) : 1.0;
    const double matchDist = binHz * tokens::kPeakMatchBins;

    for (auto& t : tracked_) t.matchedThisFrame = false;

    for (const auto& rp : raw) {
        TrackedPeak* best = nullptr;
        double bestD = matchDist;
        for (auto& t : tracked_) {
            const double d = std::abs(rp.freqHz - t.freqHz);
            if (d < bestD) { bestD = d; best = &t; }
        }
        if (best) {
            best->freqHz = rp.freqHz;
            best->dbfs = rp.dbfs;
            best->bandwidthHz = rp.bandwidthHz;
            best->seenFrames++;
            best->missFrames = 0;
            best->matchedThisFrame = true;
        } else {
            tracked_.push_back({nextPeakId_++, rp.freqHz, rp.dbfs,
                                rp.bandwidthHz, 1, 0, true});
        }
    }
    for (auto& t : tracked_)
        if (!t.matchedThisFrame) t.missFrames++;
    // Forget peaks that disappeared more than kPeakMaxMissFrames ago.
    tracked_.erase(std::remove_if(tracked_.begin(), tracked_.end(),
        [](const TrackedPeak& t) { return t.missFrames > tokens::kPeakMaxMissFrames; }),
        tracked_.end());

    // Displayed list = tracked peaks that have persisted long enough, loudest first.
    peaks_.clear();
    peakIds_.clear();
    // Build (peak, id) pairs then sort loudest-first for stable row order.
    QList<QPair<dsp::PeakInfo,int>> mature;
    for (const auto& t : tracked_)
        if (t.seenFrames >= tokens::kPeakMinSeenFrames)
            mature.append({{t.freqHz, t.dbfs, t.bandwidthHz}, t.id});
    std::sort(mature.begin(), mature.end(),
              [](const QPair<dsp::PeakInfo,int>& a, const QPair<dsp::PeakInfo,int>& b) {
                  return a.first.dbfs > b.first.dbfs;
              });
    for (const auto& m : mature) {
        peaks_.append(m.first);
        peakIds_.append(m.second);
    }

    // Throttle table rebuilds: only touch the widget when the set of rounded
    // peak frequencies actually changes (avoids a QTableWidget rebuild every
    // ~30 ms frame and the flicker that would cause).
    QString sig;
    for (const auto& pk : peaks_)
        sig += QString::number(pk.freqHz / 1e6, 'f', 3) + "|";
    if (sig == lastPeakSignature_) return;
    lastPeakSignature_ = sig;

    // The row set changed -> drop any previous row highlight.
    highlightedPeak_ = -1;
    peakTable_->clearSpans();
    peakTable_->setRowCount(0);

    if (peaks_.isEmpty()) {
        // Honest empty state -- no synthetic peaks.
        peakTable_->setRowCount(1);
        peakTable_->setSpan(0, 0, 1, 4);
        auto* it = new QTableWidgetItem(QStringLiteral("未检测到信号"));
        it->setFlags(Qt::NoItemFlags);
        peakTable_->setItem(0, 0, it);
        return;
    }

    for (int k = 0; k < peaks_.size(); ++k) {
        const auto& pk = peaks_[k];
        const int row = peakTable_->rowCount();
        peakTable_->insertRow(row);
        auto* idItem = new QTableWidgetItem(QString::number(peakIds_.value(k, -1)));
        peakTable_->setItem(row, 0, idItem);
        auto* fItem = new QTableWidgetItem(QString("%1").arg(pk.freqHz / 1e6, 0, 'f', 3));
        fItem->setData(Qt::UserRole, pk.freqHz / 1e6);   // absolute MHz for dbl-click
        peakTable_->setItem(row, 1, fItem);
        peakTable_->setItem(row, 2,
            new QTableWidgetItem(QString("%1").arg(pk.dbfs, 0, 'f', 1)));
        peakTable_->setItem(row, 3,
            new QTableWidgetItem(QString("%1").arg(pk.bandwidthHz / 1e3, 0, 'f', 2)));
    }
}

void SpectrumWidget::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, false);

    // Background: kCard1 (from tokens)
    p.fillRect(rect(), QColor(QString::fromUtf8(tokens::kCard1)));

    const int w = width();
    const int h = height();
    const int mL = tokens::kPlotMarginL;
    const int mR = tokens::kPlotMarginR;
    const int mT = tokens::kPlotMarginT;
    const int mB = tokens::kPlotMarginB;
    // Reserve the compact peak list at the bottom of the widget.
    const int tableH = (peakTable_ && peakTable_->isVisible()) ? peakTable_->height() : 0;
    const int plotBottom = h - tableH;
    const int plotW = w - mL - mR;
    const int plotH = plotBottom - mT - mB;
    if (plotW <= 10 || plotH <= 10) return;

    // Grid: kCardEdge
    QPen gridPen(QColor(QString::fromUtf8(tokens::kCardEdge)), 1, Qt::DotLine);
    p.setPen(gridPen);

    // dBFS range -- adjustable via the top-row spinboxes.
    const float yMin = dbMin_;
    const float yMax = dbMax_;
    const int step = tokens::kDbGridStep;
    for (int db = (std::ceil(yMin / step) * step); db <= static_cast<int>(yMax); db += step) {
        int y = mT + static_cast<int>(plotH * (1.0f - (db - yMin) / (yMax - yMin)));
        p.drawLine(mL, y, w - mR, y);
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        p.drawText(0, y - tokens::scaled(tokens::kDbLabelOffsetY),
                   mL - tokens::scaled(tokens::kDbLabelPadR),
                   tokens::scaled(tokens::kDbLabelH),
                   Qt::AlignRight | Qt::AlignVCenter,
                   QString("%1").arg(db));
        p.setPen(gridPen);
    }

    // Visible frequency window (zoom shrinks the span around viewCenterHz_).
    double fLo, fHi, spanVis;
    visibleRange(fLo, fHi, spanVis);
    const double fs = frame_.sampleRateHz;
    const double f0 = frame_.centerFreqHz;

    // Frequency grid -- recomputed for the zoomed-in window.
    for (int i = 0; i <= 4; ++i) {
        int x = mL + static_cast<int>(plotW * i / 4.0);
        p.drawLine(x, mT, x, mT + plotH);
        double freq = fLo + spanVis * i / 4.0;
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        p.drawText(x - tokens::scaled(tokens::kFreqLabelHalfW),
                   mT + plotH + tokens::scaled(tokens::kFreqLabelOffsetY),
                   tokens::scaled(tokens::kFreqLabelW),
                   tokens::scaled(tokens::kFreqLabelH),
                   Qt::AlignCenter,
                   QString("%1M").arg(freq / 1e6, 0, 'f', 1));
        p.setPen(gridPen);
    }

    p.setPen(QPen(QColor(QString::fromUtf8(tokens::kCardEdge)), 1));
    p.drawRect(mL, mT, plotW, plotH);

    // Spectrum trace: kAccent -- draw only the bins inside the visible window.
    const std::size_t n = frame_.dbfs.size();
    if (n >= 2 && fs > 0.0) {
        const double fLowEdge = f0 - fs / 2.0;   // bin 0 frequency (already fft-shifted)
        const double iLoF = (fLo - fLowEdge) / fs * (n - 1);
        const double iHiF = (fHi - fLowEdge) / fs * (n - 1);
        int iLo = static_cast<int>(std::floor(iLoF));
        int iHi = static_cast<int>(std::ceil(iHiF));
        iLo = std::max(0, std::min(static_cast<int>(n) - 1, iLo));
        iHi = std::max(0, std::min(static_cast<int>(n) - 1, iHi));

        // Max-hold envelope: pale, thinner, under the live trace.
        if (maxHoldEnabled_ && maxHold_.size() == n) {
            QPen holdPen(QColor(QString::fromUtf8(tokens::kTextSecondary)), 1, Qt::DotLine);
            p.setPen(holdPen);
            QPainterPath hpath;
            for (int i = iLo; i <= iHi; ++i) {
                const double fi = fLowEdge + fs * i / (n - 1);
                int x = mL + static_cast<int>(plotW * (fi - fLo) / spanVis);
                float v = maxHold_[i];
                if (v < yMin) v = yMin;
                if (v > yMax) v = yMax;
                int y = mT + static_cast<int>(plotH * (1.0f - (v - yMin) / (yMax - yMin)));
                if (i == iLo) hpath.moveTo(x, y);
                else hpath.lineTo(x, y);
            }
            p.drawPath(hpath);
        }

        QPen tracePen(QColor(QString::fromUtf8(tokens::kAccent)), 1);
        p.setPen(tracePen);
        QPainterPath path;
        for (int i = iLo; i <= iHi; ++i) {
            const double fi = fLowEdge + fs * i / (n - 1);
            int x = mL + static_cast<int>(plotW * (fi - fLo) / spanVis);
            float v = frame_.dbfs[i];
            if (v < yMin) v = yMin;
            if (v > yMax) v = yMax;
            int y = mT + static_cast<int>(plotH * (1.0f - (v - yMin) / (yMax - yMin)));
            if (i == iLo) path.moveTo(x, y);
            else path.lineTo(x, y);
        }
        p.drawPath(path);
    }

    // VFO line + demod band box: [f0-bw/2, f0+bw/2].
    if (f0 >= fLo && f0 <= fHi) {
        const int vfoX = mL + static_cast<int>(plotW * (f0 - fLo) / spanVis);
        const double halfHz = bwHz_ / 2.0;
        const int bx0 = mL + static_cast<int>(plotW * (f0 - halfHz - fLo) / spanVis);
        const int bx1 = mL + static_cast<int>(plotW * (f0 + halfHz - fLo) / spanVis);
        // Translucent fill across the demod passband.
        QColor fill(tokens::kAccent); fill.setAlphaF(0.08);
        p.fillRect(QRect(bx0, mT, bx1 - bx0, plotH), fill);
        // Edge lines.
        QColor edgeC(tokens::kAccent); edgeC.setAlphaF(0.6);
        QPen edge(edgeC);
        edge.setWidthF(1.0);
        p.setPen(edge);
        p.drawLine(bx0, mT, bx0, mT + plotH);
        p.drawLine(bx1, mT, bx1, mT + plotH);
        // VFO center line.
        QPen vfoPen(QColor(tokens::kAccent));
        vfoPen.setWidthF(tokens::kVfoLineWidth);
        p.setPen(vfoPen);
        p.drawLine(vfoX, mT, vfoX, mT + plotH);
        // Small draggable handle triangles on the top edge.
        p.setBrush(QColor(tokens::kAccent));
        p.setPen(Qt::NoPen);
        p.drawPolygon(QPolygonF({QPointF(bx0-4, mT), QPointF(bx0+4, mT), QPointF(bx0, mT+6)}));
        p.drawPolygon(QPolygonF({QPointF(bx1-4, mT), QPointF(bx1+4, mT), QPointF(bx1, mT+6)}));
    }

    // Detected peak markers: filled triangles along the top edge, only for
    // peaks inside the current visible window. The row currently selected in
    // the table (highlightedPeak_) is drawn larger, in kAccent.
    {
        p.setPen(Qt::NoPen);
        for (int pi = 0; pi < peaks_.size(); ++pi) {
            const auto& pk = peaks_[pi];
            if (pk.freqHz < fLo || pk.freqHz > fHi) continue;
            const bool selected = (pi == highlightedPeak_);
            const int mhw = tokens::scaled(selected ? tokens::kPeakMarkerHiHalfW
                                                   : tokens::kPeakMarkerHalfW);
            const int mh  = tokens::scaled(selected ? tokens::kPeakMarkerHiH
                                                    : tokens::kPeakMarkerH);
            p.setBrush(QColor(QString::fromUtf8(selected ? tokens::kAccent
                                                         : tokens::kSuccess)));
            const int x = mL + static_cast<int>(plotW * (pk.freqHz - fLo) / spanVis);
            QPolygon tri;
            tri << QPoint(x - mhw, mT) << QPoint(x + mhw, mT) << QPoint(x, mT + mh);
            p.drawPolygon(tri);
        }
        p.setPen(QPen());
        p.setBrush(Qt::NoBrush);
    }

    // Crosshair readout: frequency + dBFS at the cursor, on a translucent card.
    if (hoverPos_.x() >= 0 && hoverPos_.y() >= mT && hoverPos_.y() <= mT + plotH) {
        p.setPen(QPen(QColor(tokens::kAccent), 1, Qt::DashLine));
        p.drawLine(hoverPos_.x(), mT, hoverPos_.x(), mT + plotH);
        p.drawLine(mL, hoverPos_.y(), w - mR, hoverPos_.y());
        const double frac = (hoverPos_.x() - mL) / static_cast<double>(plotW);
        const double freq = fLo + frac * spanVis;
        const double dbFrac = (hoverPos_.y() - mT) / static_cast<double>(plotH);
        const double dbfs = dbMax_ - dbFrac * (dbMax_ - dbMin_);
        const QString txt = QString("%1 MHz  %2 dBFS")
                                .arg(freq / 1e6, 0, 'f', 3).arg(dbfs, 0, 'f', 1);
        QFont f = font(); f.setPointSize(tokens::kFontAuxPt); p.setFont(f);
        QFontMetrics fm(f);
        QRectF box(hoverPos_ + QPointF(tokens::kTooltipOffset, -tokens::kTooltipOffset),
                   QSizeF(fm.horizontalAdvance(txt) + tokens::scaled(tokens::kSpacingM),
                          fm.height() + tokens::scaled(tokens::kSpacingS)));
        p.setPen(Qt::NoPen);
        p.setBrush(QColor(QString::fromUtf8(tokens::kCard2)));
        p.drawRoundedRect(box, tokens::scaled(tokens::kRadiusSmall),
                          tokens::scaled(tokens::kRadiusSmall));
        p.setPen(QPen(QColor(QString::fromUtf8(tokens::kTextPrimary))));
        p.drawText(box.adjusted(tokens::scaled(tokens::kSpacingS), 0, 0, 0),
                   Qt::AlignVCenter, txt);
    }
}

void SpectrumWidget::mousePressEvent(QMouseEvent* e) {
    dragging_ = true;
    panning_ = (e->modifiers() & Qt::ShiftModifier);
    lastPanPos_ = e->pos();

    // Hit-test VFO band edges first: near left/right edge of the demod box.
    const int mL = tokens::kPlotMarginL;
    const int mR = tokens::kPlotMarginR;
    const int plotW = width() - mL - mR;
    double fLo, fHi, spanVis; visibleRange(fLo, fHi, spanVis);
    auto xOf = [&](double f) { return mL + static_cast<int>(plotW * (f - fLo) / spanVis); };
    const int cx = xOf(vfoFreq_);
    const int halfW = static_cast<int>(plotW * (bwHz_ / 2) / spanVis);
    const int tol = tokens::scaled(6);
    const int ex = e->position().x();
    if (std::abs(ex - (cx - halfW)) <= tol) dragMode_ = DragMode::BandL;
    else if (std::abs(ex - (cx + halfW)) <= tol) dragMode_ = DragMode::BandR;
    else if (panning_) dragMode_ = DragMode::Pan;
    else dragMode_ = DragMode::Tune;

    if (dragMode_ == DragMode::Tune) mouseMoveEvent(e);
}

void SpectrumWidget::mouseMoveEvent(QMouseEvent* e) {
    hoverPos_ = e->pos();
    if (!dragging_ || frame_.sampleRateHz <= 0) { update(); return; }
    const int mL = tokens::kPlotMarginL;
    const int mR = tokens::kPlotMarginR;
    const int plotW = width() - mL - mR;
    if (plotW <= 10) { update(); return; }

    if (dragMode_ == DragMode::BandL || dragMode_ == DragMode::BandR) {
        double fLo, fHi, spanVis; visibleRange(fLo, fHi, spanVis);
        const double frac = (e->position().x() - mL) / plotW;
        const double edgeF = fLo + frac * spanVis;
        // Distance from VFO center is the new half-bandwidth.
        double half = std::abs(edgeF - vfoFreq_);
        half = std::clamp(half * 2.0, 100.0, 500000.0);
        bwHz_ = half;
        emit bandwidthChanged(bwHz_);
        update();
        return;
    }

    if (dragMode_ == DragMode::Pan) {
        // Shift+drag: pan the view window without retuning f0.
        const double fs = frame_.sampleRateHz;
        const double spanVis = fs / zoomFactor_;
        const double dx = e->position().x() - lastPanPos_.x();
        viewCenterHz_ -= (dx / plotW) * spanVis;
        lastPanPos_ = e->pos();
        update();
        emitVisibleRange();
        return;
    }

    // Plain drag: retune f0 to the cursor frequency, and re-center the view
    // on the new f0 so the VFO stays framed.
    double fLo, fHi, spanVis;
    visibleRange(fLo, fHi, spanVis);
    const double frac = (e->position().x() - mL) / plotW;
    const double freq = fLo + frac * spanVis;
    vfoFreq_ = freq;
    viewCenterHz_ = freq;
    emit frequencyChanged(freq);
    update();
}

void SpectrumWidget::mouseReleaseEvent(QMouseEvent*) {
    dragging_ = false;
    panning_ = false;
    dragMode_ = DragMode::None;
}

void SpectrumWidget::mouseDoubleClickEvent(QMouseEvent*) {
    resetZoom();
}

void SpectrumWidget::wheelEvent(QWheelEvent* e) {
    if (frame_.sampleRateHz <= 0.0) { e->ignore(); return; }
    // Wheel up = zoom in (shrink the visible span), down = zoom out.
    // Cursor-anchored: the frequency under the mouse stays under the mouse.
    const double steps = e->angleDelta().y() / 120.0;
    const int mL = tokens::kPlotMarginL;
    const int mR = tokens::kPlotMarginR;
    const int plotW = width() - mL - mR;
    if (plotW <= 10) { e->accept(); return; }

    const double fs = frame_.sampleRateHz;
    const double xRatio = (e->position().x() - mL) / static_cast<double>(plotW);

    // Frequency under the cursor BEFORE zoom.
    const double spanBefore = fs / zoomFactor_;
    const double fCursor = viewCenterHz_ + (xRatio - 0.5) * spanBefore;

    const double newZoom = std::clamp(zoomFactor_ * std::pow(2.0, steps),
                                      tokens::kZoomMin, tokens::kZoomMax);
    if (newZoom == zoomFactor_) { e->accept(); return; }
    zoomFactor_ = newZoom;

    // Re-anchor: keep fCursor at the same pixel (xRatio).
    const double spanAfter = fs / zoomFactor_;
    viewCenterHz_ = fCursor - (xRatio - 0.5) * spanAfter;

    update();
    emitVisibleRange();
    e->accept();
}

void SpectrumWidget::contextMenuEvent(QContextMenuEvent* e) {
    QMenu menu(this);
    QAction* reset = menu.addAction("重置 zoom");
    QAction* chosen = menu.exec(e->globalPos());
    if (chosen == reset) resetZoom();
}

void SpectrumWidget::leaveEvent(QEvent*) {
    hoverPos_ = QPoint(-1, -1);
    update();
}

} // namespace ui
} // namespace mbdsdr
