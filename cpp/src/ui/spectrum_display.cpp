// SPDX-License-Identifier: MIT
#include "spectrum_display.h"

#include "core/tokens.h"

#include <QPainter>
#include <QMouseEvent>
#include <QWheelEvent>
#include <QToolTip>
#include <QRectF>
#include <algorithm>
#include <cmath>
#include <limits>

namespace mbdsdr {
namespace ui {

namespace {
// Clamp helper kept local so behaviour is obvious at every call site.
inline double clampd(double v, double lo, double hi) {
    return std::min(std::max(v, lo), hi);
}

// --- dB axis auto-range tuning (instrument "auto scale") -------------------
// Sliding window of real per-frame peaks (frame.dbfs max). 24 frames ~ 0.5 s
// at the typical host rate, so a momentary blip cannot move the scale.
constexpr int   kAutoWindowFrames   = 24;
// Snap the desired ceiling to this coarse "sensitivity step" (dB), matching
// the dB-grid cadence so ticks land on round numbers.
constexpr float kAutoCeilSnapDb    = 10.0f;
// Where the sliding peak should sit on the plot (0..1 from the bottom). We
// aim for the upper ~80%: strong traces stop slamming the top border while
// weak frames still get the scale pulled in around them.
constexpr float kAutoTargetFrac    = 0.80f;
// Anti-clip headroom: never let the target ceiling sit within this many dB of
// the measured peak (which would fold the trace over the top edge).
constexpr float kAutoPeakHeadroomDb = 4.0f;
// Per-frame easing cap. Combined with a ~0.5s window this lands inside the
// kAnimMedium1 (220 ms) feel: the scale glides instead of jumping.
constexpr float kAutoEasePerFrameDb = 1.5f;
} // namespace

SpectrumDisplay::SpectrumDisplay(QWidget* parent)
    : QWidget(parent)
{
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    setMouseTracking(true);
    setAutoFillBackground(true);
    traceShare_ = tokens::kDefaultSpecFraction;
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------
void SpectrumDisplay::recomputeGeometry() {
    using namespace tokens;
    lay_.plotX0  = scaled(kDispLeftInset);
    lay_.plotX1  = width() - scaled(kDispRightInset);
    lay_.plotW   = lay_.plotX1 - lay_.plotX0;
    lay_.topPad  = scaled(kDispTopInset);
    lay_.botPad  = height() - scaled(kDispBottomInset);
    lay_.stripH  = scaled(kDispFreqStripH);
    lay_.gapPx   = scaled(kDispAreaGap);
    lay_.splitGap= scaled(kDispAreaGap);

    const int fixedV = lay_.stripH + lay_.gapPx + lay_.splitGap;
    int pool = lay_.botPad - lay_.topPad - fixedV;
    if (pool < 0) pool = 0;

    const int minTrace = scaled(kSpecAreaMinH);
    const int minFalls = scaled(kWfAreaMinH);

    int traceH = static_cast<int>(pool * traceShare_);
    traceH = static_cast<int>(clampd(traceH, minTrace, pool - minFalls));
    if (traceH < 0) traceH = 0;
    int fallsH = pool - traceH;
    if (fallsH < 0) fallsH = 0;
    lay_.traceH = traceH;
    lay_.fallsH = fallsH;

    const int traceTop = lay_.topPad;
    const int stripTop = traceTop + traceH + lay_.gapPx;
    const int splitY   = stripTop + lay_.stripH + lay_.splitGap;

    lay_.traceRect = QRect(lay_.plotX0, traceTop, lay_.plotW, traceH);
    lay_.stripRect = QRect(lay_.plotX0, stripTop, lay_.plotW, lay_.stripH);
    lay_.splitY    = splitY;
    lay_.fallsRect = QRect(lay_.plotX0, splitY, lay_.plotW, fallsH);

    const int half = scaled(kDividerHitHalfH);
    lay_.splitZone = QRect(lay_.plotX0, splitY - half, lay_.plotW, 2 * half);
}

void SpectrumDisplay::resizeEvent(QResizeEvent*) {
    recomputeGeometry();
    update();
}

// ---------------------------------------------------------------------------
// Visible window
// ---------------------------------------------------------------------------
void SpectrumDisplay::visibleWindow(double& fLo, double& fHi, double& spanHz) const {
    if (frameFsHz_ <= 0.0 || !haveFrame_) {
        fLo = viewCenterHz_;
        fHi = viewCenterHz_;
        spanHz = 1.0;
        return;
    }
    spanHz = frameFsHz_ / zoomFactor_;
    fLo = viewCenterHz_ - spanHz / 2.0;
    fHi = viewCenterHz_ + spanHz / 2.0;
}

double SpectrumDisplay::visLoHz() const {
    double lo, hi, sp; visibleWindow(lo, hi, sp); return lo;
}
double SpectrumDisplay::visHiHz() const {
    double lo, hi, sp; visibleWindow(lo, hi, sp); return hi;
}

int SpectrumDisplay::dbToY(float db) const {
    const double dbSpan = dbCeilDb_ - dbFloorDb_;
    const float t = (db - dbFloorDb_) / (dbSpan > 0 ? dbSpan : 1.0f);
    return lay_.traceRect.bottom()
           - static_cast<int>(std::clamp(static_cast<double>(t), 0.0, 1.0)
                              * lay_.traceRect.height());
}

int SpectrumDisplay::yForDbfs(float db) const { return dbToY(db); }

int SpectrumDisplay::xForFrequency(double f) const {
    double lo, hi, span; visibleWindow(lo, hi, span);
    return xForFreq(f, lo, span);
}

QString SpectrumDisplay::cursorReadoutText(const QPoint& pos) const {
    if (!haveFrame_ || bins_ <= 0 || frame_.dbfs.empty()) return QString();
    if (!(lay_.traceRect.contains(pos) || lay_.fallsRect.contains(pos))) return QString();
    double fLo, fHi, span; visibleWindow(fLo, fHi, span);
    const double f = freqForX(pos.x(), fLo, span);
    const double bandLo = frameF0Hz_ - frameFsHz_ / 2.0;
    const double binHz = (bins_ > 0) ? frameFsHz_ / bins_ : 0.0;
    int idx = (binHz > 0.0) ? static_cast<int>((f - bandLo) / binHz) : 0;
    idx = std::clamp(idx, 0, bins_ - 1);
    const float db = frame_.dbfs[idx];
    QString s = QString("%1 MHz").arg(f / 1e6, 0, 'f', 3);
    s += QLatin1Char('\n');
    s += QString("%1 dBFS").arg(db, 0, 'f', 1);
    if (std::isfinite(noiseFloorDb_))
        s += QString("   SNR %1 dB").arg(db - noiseFloorDb_, 0, 'f', 1);
    return s;
}

void SpectrumDisplay::setNoiseFloorDb(float db) {
    noiseFloorDb_ = db;
    update();
}

void SpectrumDisplay::publishVisibleRange() {
    double lo, hi, sp; visibleWindow(lo, hi, sp);
    emit visibleRangeChanged(lo, hi);
}

// ---------------------------------------------------------------------------
// Waterfall ring buffer
// ---------------------------------------------------------------------------
void SpectrumDisplay::allocateRing(int bins) {
    bins_ = bins;
    ringDepth_ = tokens::kWaterfallHistoryLines;
    ringRows_.assign(ringDepth_, QImage(bins, 1, QImage::Format_ARGB32));
    ringHead_ = 0;
    ringCount_ = 0;
    for (QImage& r : ringRows_) r.fill(qRgb(0, 0, 0));
    maxHold_.assign(bins, -std::numeric_limits<float>::max());
    materialiseHistory();
}

void SpectrumDisplay::pushHistoryRow() {
    if (bins_ <= 0 || ringRows_.empty()) return;
    QImage& row = ringRows_[ringHead_];
    if (row.width() != bins_) row = QImage(bins_, 1, QImage::Format_ARGB32);
    auto* line = reinterpret_cast<QRgb*>(row.bits());
    const int n = std::min(bins_, static_cast<int>(frame_.dbfs.size()));
    for (int i = 0; i < bins_; ++i) {
        const float db = (i < n) ? frame_.dbfs[i] : dbFloorDb_;
        line[i] = colourForDb(db);
    }
    ringHead_ = (ringHead_ + 1) % ringDepth_;
    if (ringCount_ < ringDepth_) ++ringCount_;
    materialiseHistory();
}

void SpectrumDisplay::materialiseHistory() {
    if (bins_ <= 0 || ringDepth_ <= 0) {
        history_ = QImage();
        return;
    }
    if (history_.width() != bins_ || history_.height() != ringDepth_)
        history_ = QImage(bins_, ringDepth_, QImage::Format_ARGB32);
    QPainter c(&history_);
    c.setCompositionMode(QPainter::CompositionMode_Source);
    for (int logical = 0; logical < ringDepth_; ++logical) {
        // Logical row 0 is the newest push, which lives one slot behind head.
        const int phys = (ringHead_ - 1 - logical + ringDepth_) % ringDepth_;
        c.drawImage(0, logical, ringRows_[phys]);
    }
    c.end();
}

void SpectrumDisplay::rebuildColormap() {
    using namespace tokens;
    const WaterfallStop* stops = kWaterfallStops;
    int n = static_cast<int>(std::size(kWaterfallStops));
    if (paletteIndex_ == 1) { stops = kWaterfallStopsMono; n = static_cast<int>(std::size(kWaterfallStopsMono)); }
    else if (paletteIndex_ == 2) { stops = kWaterfallStopsViridis; n = static_cast<int>(std::size(kWaterfallStopsViridis)); }

    for (int i = 0; i < 256; ++i) {
        const float t = i / 255.0f;
        int seg = 0;
        while (seg < n - 2 && t > stops[seg + 1].t) ++seg;
        const QColor a(stops[seg].hex);
        const QColor b(stops[seg + 1].hex);
        const float span = stops[seg + 1].t - stops[seg].t;
        const float u = (span > 0.0f) ? (t - stops[seg].t) / span : 0.0f;
        const int r = a.red()   + static_cast<int>(u * (b.red()   - a.red()));
        const int g = a.green() + static_cast<int>(u * (b.green() - a.green()));
        const int bl= a.blue()  + static_cast<int>(u * (b.blue()  - a.blue()));
        lut_[i] = qRgb(r, g, bl);
    }
}

QRgb SpectrumDisplay::colourForDb(float db) const {
    const float span = dbCeilDb_ - dbFloorDb_;
    if (span <= 0.0f) return lut_[0];
    float t = (db - dbFloorDb_) / span;
    t = static_cast<float>(clampd(t, 0.0f, 1.0f));
    const int idx = static_cast<int>(t * 255.0f);
    return lut_[std::clamp(idx, 0, 255)];
}

// ---------------------------------------------------------------------------
// Frame intake
// ---------------------------------------------------------------------------
void SpectrumDisplay::setSpectrum(const SpectrumFrame& frame) {
    frame_ = frame;
    frameF0Hz_ = frame.centerFreqHz;
    frameFsHz_ = frame.sampleRateHz;

    if (!haveFrame_) {
        haveFrame_ = true;
        viewCenterHz_ = frameF0Hz_;
        dialFreqHz_   = frameF0Hz_;
        allocateRing(static_cast<int>(frame.dbfs.size()));
        rebuildColormap();
    }

    const int bins = static_cast<int>(frame.dbfs.size());
    if (bins != bins_) allocateRing(bins);

    // ---- dB axis auto-range: purely from the real sliding peak of dbfs ----
    // No synthetic peak is invented; a test-signal frame drives this exactly
    // like a hardware frame. The windowed peak decides the *target* ceiling;
    // the on-screen ceiling then eases toward it by a bounded step so the grid
    // and trace glide rather than jump.
    {
        float peak = -std::numeric_limits<float>::max();
        for (const float v : frame_.dbfs) if (std::isfinite(v) && v > peak) peak = v;
        peakWindow_.push_back(peak);
        if (static_cast<int>(peakWindow_.size()) > kAutoWindowFrames)
            peakWindow_.erase(peakWindow_.begin());

        float goal = manualCeilDb_;
        if (autoRangeOn_ && !peakWindow_.empty()) {
            float pwin = -std::numeric_limits<float>::max();
            for (const float v : peakWindow_) pwin = std::max(pwin, v);
            if (std::isfinite(pwin)) {
                // Place the peak at kAutoTargetFrac of the span above the floor.
                float ceilDes = manualFloorDb_ + (pwin - manualFloorDb_) / kAutoTargetFrac;
                // Snap to a coarse sensitivity step for calm, round-number ticks.
                ceilDes = std::round(ceilDes / kAutoCeilSnapDb) * kAutoCeilSnapDb;
                // Anti-clip: keep at least headroom above the real peak.
                ceilDes = std::max(ceilDes, pwin + kAutoPeakHeadroomDb);
                // Bounds: never above the manual top, never tighter than the
                // spinbox's lowest allowed ceiling, and never above the floor.
                ceilDes = static_cast<float>(clampd(ceilDes,
                                                    tokens::kDbSpinUpperMin,
                                                    tokens::kDbUpperDefault));
                ceilDes = std::max(ceilDes, manualFloorDb_ + kAutoCeilSnapDb);
                ceilTargetDb_ = ceilDes;
                goal = ceilTargetDb_;
            }
        }
        // Ease the on-screen ceiling toward the goal, one bounded step/frame.
        const float diff = goal - dbCeilDb_;
        const float step = static_cast<float>(clampd(diff, -kAutoEasePerFrameDb,
                                                     kAutoEasePerFrameDb));
        dbCeilDb_ += step;
        dbCeilDb_ = static_cast<float>(clampd(dbCeilDb_, manualFloorDb_,
                                               tokens::kDbUpperDefault));
        dbFloorDb_ = manualFloorDb_;   // floor is the stable manual reference
    }

    if (maxHoldOn_) {
        if (static_cast<int>(maxHold_.size()) != bins)
            maxHold_.assign(bins, -std::numeric_limits<float>::max());
        for (int i = 0; i < bins && i < static_cast<int>(frame.dbfs.size()); ++i)
            if (frame.dbfs[i] > maxHold_[i]) maxHold_[i] = frame.dbfs[i];
    }

    // ---- Persistence (余晖): decay the ghost envelope, refresh on fresh rise --
    if (persistMode_ > 0) {
        const float decay = (persistMode_ == 2) ? tokens::kPersistDecayHigh
                                                : tokens::kPersistDecayLow;
        if (static_cast<int>(persist_.size()) != bins)
            persist_.assign(bins, -std::numeric_limits<float>::max());
        for (int i = 0; i < bins && i < static_cast<int>(frame.dbfs.size()); ++i) {
            const float fresh = frame.dbfs[i];
            const float aged  = persist_[i] * decay;   // -inf * 0 stays -inf
            persist_[i] = std::isfinite(fresh) ? std::max(fresh, aged) : aged;
        }
    }

    ++frameMod_;
    if (frameMod_ >= everyNthFrame_) {
        frameMod_ = 0;
        pushHistoryRow();
    }

    rescanPeaks();
    publishVisibleRange();
    update();
}

void SpectrumDisplay::setDbRange(float minDb, float maxDb) {
    manualFloorDb_ = minDb;
    manualCeilDb_  = maxDb;
    if (!autoRangeOn_) {
        dbFloorDb_ = minDb;
        dbCeilDb_  = maxDb;
    }
    update();
}

void SpectrumDisplay::setAutoRangeOn(bool on) {
    autoRangeOn_ = on;
    if (!on) {
        // Release the scale back to the manual bounds immediately (no glide on
        // user override). While off, setDbRange() owns both ends again.
        dbFloorDb_ = manualFloorDb_;
        dbCeilDb_  = manualCeilDb_;
    }
    update();
}

int SpectrumDisplay::waterfallCropLeftBin() const {
    if (frameFsHz_ <= 0.0 || bins_ <= 0 || !haveFrame_) return 0;
    double fLo, fHi, span; visibleWindow(fLo, fHi, span);
    const double bandLo = frameF0Hz_ - frameFsHz_ / 2.0;
    const int binF = static_cast<int>(std::floor((fLo - bandLo) / frameFsHz_ * bins_));
    return std::clamp(binF, 0, bins_);
}

QRectF SpectrumDisplay::waterfallSourceRect() const {
    if (frameFsHz_ <= 0.0 || bins_ <= 0 || !haveFrame_) return QRectF();
    double fLo, fHi, span; visibleWindow(fLo, fHi, span);
    const double bandLo = frameF0Hz_ - frameFsHz_ / 2.0;
    // Bin-centre mapping shared with the trace: history column i sits at image x
    // in [i,i+1), whose centre (i+0.5) is the bin-centre frequency. Map the visible
    // window straight through -- no floor/ceil rounding.
    const double srcL = clampd((fLo - bandLo) / frameFsHz_ * bins_, 0.0,
                               static_cast<double>(bins_));
    const double srcR = clampd((fHi - bandLo) / frameFsHz_ * bins_, 0.0,
                               static_cast<double>(bins_));
    return QRectF(srcL, 0, srcR - srcL, ringDepth_);
}

int SpectrumDisplay::freqTickDecimals(double stepHz) {
    // Nice steps come in {1,2,2.5,5,10}*mag. In MHz: >=5 -> 0 dp, >=1 -> 1 dp,
    // otherwise (0.1/0.2/0.25/0.5 MHz) -> 2 dp. Matches the Flutter strip table.
    const double mhz = stepHz / 1e6;
    if (mhz >= 5.0) return 0;
    if (mhz >= 1.0) return 1;
    return 2;
}

void SpectrumDisplay::setZoomFactor(double z) {
    zoomFactor_ = clampd(z, tokens::kZoomMin, tokens::kZoomMax);
    publishVisibleRange();
    update();
}

void SpectrumDisplay::resetZoom() {
    zoomFactor_ = 1.0;
    viewCenterHz_ = frameF0Hz_;
    publishVisibleRange();
    update();
    emit viewChanged();
}

void SpectrumDisplay::setMaxHoldEnabled(bool on) {
    maxHoldOn_ = on;
    if (!on) maxHold_.clear();
    update();
}

void SpectrumDisplay::setPersistenceMode(int mode) {
    persistMode_ = std::clamp(mode, 0, 2);
    if (persistMode_ == 0) persist_.clear();
    update();
}

void SpectrumDisplay::setFixedMarkers(const QVector<SpectrumDisplay::FixedMarker>& m) {
    fixedMarkers_ = m;
    update();
}

void SpectrumDisplay::addFixedMarker(double freqHz, const QString& name) {
    fixedMarkers_.push_back(FixedMarker{freqHz, name});
    update();
}

void SpectrumDisplay::removeFixedMarker(int index) {
    if (index >= 0 && index < fixedMarkers_.size()) {
        fixedMarkers_.removeAt(index);
        update();
    }
}

void SpectrumDisplay::setScrollSpeed(int linesPerFrame) {
    everyNthFrame_ = (linesPerFrame == 1 || linesPerFrame == 2 || linesPerFrame == 4)
                     ? linesPerFrame : 1;
}

void SpectrumDisplay::setPalette(int p) {
    paletteIndex_ = std::clamp(p, 0, 2);
    rebuildColormap();
    update();
}

void SpectrumDisplay::setHighlightedPeak(int row) {
    highlightedPeak_ = row;
    update();
}

void SpectrumDisplay::tuneAndCenter(double hz) {
    viewCenterHz_ = hz;
    dialFreqHz_   = hz;
    publishVisibleRange();
    update();
    emit viewChanged();
}

void SpectrumDisplay::setVfoMarkers(const QVector<dsp::VfoMarker>& markers) {
    markers_ = markers;
    update();
}

// ---------------------------------------------------------------------------
// VFO band-box geometry
// ---------------------------------------------------------------------------
void SpectrumDisplay::markerBox(const dsp::VfoMarker& m, double fLo, double spanHz,
                                int& bx0, int& bx1, int& vx) const {
    const double dial = m.freqHz;
    double loF, hiF;
    const QString mode = m.mode;
    if (mode == QLatin1String("USB")) {
        loF = dial;                 hiF = dial + m.bandwidthHz;   // dial on left edge
    } else if (mode == QLatin1String("LSB") || mode == QLatin1String("CW")) {
        loF = dial - m.bandwidthHz; hiF = dial;                 // dial on right edge
    } else {
        loF = dial - m.bandwidthHz / 2.0; hiF = dial + m.bandwidthHz / 2.0;
    }
    bx0 = xForFreq(loF, fLo, spanHz);
    bx1 = xForFreq(hiF, fLo, spanHz);
    vx  = xForFreq(dial, fLo, spanHz);
    if (bx0 > bx1) std::swap(bx0, bx1);
}

void SpectrumDisplay::vfoBoxGeometryFor(const dsp::VfoMarker& m,
                                        int& bx0, int& bx1, int& vx) const {
    double fLo, fHi, span;
    visibleWindow(fLo, fHi, span);
    markerBox(m, fLo, span, bx0, bx1, vx);
}

int SpectrumDisplay::findMarkerAt(int x, double fLo, double spanHz) const {
    const int tol = tokens::scaled(tokens::kBandEdgeHitTol);
    for (int i = 0; i < markers_.size(); ++i) {
        int bx0, bx1, vx;
        markerBox(markers_[i], fLo, spanHz, bx0, bx1, vx);
        if (x >= bx0 - tol && x <= bx1 + tol) return i;
    }
    return -1;
}

// ---------------------------------------------------------------------------
// Peak tracking
// ---------------------------------------------------------------------------
void SpectrumDisplay::rescanPeaks() {
    if (!haveFrame_ || frame_.dbfs.empty()) return;
    const auto fresh = dsp::detectPeaks(frame_.dbfs, frameFsHz_, frameF0Hz_,
                                        peakThresholdDb_, tokens::kPeakAbsFloorDbfs);
    for (auto& t : tracked_) t.matched = false;

    const double binHz = (bins_ > 0) ? frameFsHz_ / bins_ : 0.0;
    const double matchHz = tokens::kPeakMatchBins * binHz;

    for (const auto& f : fresh) {
        int best = -1;
        double bestD = matchHz;
        for (int i = 0; i < tracked_.size(); ++i) {
            const double d = std::abs(tracked_[i].freqHz - f.freqHz);
            if (d < bestD) { bestD = d; best = i; }
        }
        if (best >= 0) {
            auto& t = tracked_[best];
            t.freqHz = f.freqHz; t.dbfs = f.dbfs; t.bandwidthHz = f.bandwidthHz;
            ++t.seen; t.missed = 0; t.matched = true;
        } else {
            TrackedBlip t;
            t.id = nextBlipId_++;
            t.freqHz = f.freqHz; t.dbfs = f.dbfs; t.bandwidthHz = f.bandwidthHz;
            t.seen = 1; t.missed = 0; t.matched = true;
            tracked_.append(t);
        }
    }

    QList<TrackedBlip> kept;
    for (auto& t : tracked_) {
        if (!t.matched) ++t.missed;
        if (t.missed < tokens::kPeakMaxMissFrames) kept.append(t);
    }
    tracked_ = kept;

    peaks_.clear();
    peakIds_.clear();
    QString sig;
    for (const auto& t : tracked_) {
        if (t.seen >= tokens::kPeakMinSeenFrames) {
            dsp::PeakInfo p; p.freqHz = t.freqHz; p.dbfs = t.dbfs; p.bandwidthHz = t.bandwidthHz;
            peaks_.append(p);
            peakIds_.append(t.id);
            sig += QString::number(static_cast<int>(t.freqHz / 1000.0)) + QLatin1Char(',');
        }
    }
    if (sig != lastPeakSig_) {
        lastPeakSig_ = sig;
        emit peaksUpdated(peaks_, peakIds_);
    }
}

// ---------------------------------------------------------------------------
// Painting
// ---------------------------------------------------------------------------
void SpectrumDisplay::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.fillRect(rect(), QColor(tokens::kSpectrumBg));
    if (lay_.plotW <= 0) return;

    double fLo, fHi, span;
    visibleWindow(fLo, fHi, span);

    const QRect trace = lay_.traceRect;
    const QRect strip = lay_.stripRect;
    const QRect falls = lay_.fallsRect;

    // --- trace background + dB grid --------------------------------------
    p.fillRect(trace, QColor(tokens::kSpectrumBg));
    QPen gridPen(tokens::rgbaA(tokens::kTextAlphaFaint), 1);
    p.setPen(gridPen);
    for (float db = std::ceil(dbFloorDb_ / tokens::kDbGridStep) * tokens::kDbGridStep;
         db <= dbCeilDb_; db += tokens::kDbGridStep) {
        const int y = dbToY(db);
        p.drawLine(trace.left(), y, trace.right(), y);
        p.drawText(trace.left() + tokens::scaled(tokens::kDbLabelPadR),
                   y + tokens::scaled(tokens::kDbLabelOffsetY),
                   QString::number(static_cast<int>(db)));
    }

    // --- spectrum polyline -------------------------------------------------
    const int bins = bins_;
    if (haveFrame_ && bins > 0 && !frame_.dbfs.empty()) {
        const double bandLo = frameF0Hz_ - frameFsHz_ / 2.0;
        const double binHz = frameFsHz_ / bins;
        QPolygonF line;
        QPolygonF holdLine;
        QPolygonF ghostLine;
        for (int i = 0; i < bins; ++i) {
            const double f = bandLo + (i + 0.5) * binHz;
            const int x = xForFreq(f, fLo, span);
            if (x < trace.left() - 2 || x > trace.right() + 2) continue;
            line << QPointF(x, dbToY(frame_.dbfs[i]));
            if (maxHoldOn_ && i < static_cast<int>(maxHold_.size()))
                holdLine << QPointF(x, dbToY(maxHold_[i]));
            if (persistMode_ > 0 && i < static_cast<int>(persist_.size()) &&
                std::isfinite(persist_[i]))
                ghostLine << QPointF(x, dbToY(persist_[i]));
        }
        // Ghost (余晖) underlay: calm accent, drawn BEFORE the live trace.
        if (persistMode_ > 0 && !ghostLine.isEmpty()) {
            const float ga = (persistMode_ == 2) ? tokens::kPersistAlphaHigh
                                                 : tokens::kPersistAlphaLow;
            QColor ghost = QColor(tokens::kAccent); ghost.setAlphaF(ga);
            p.setPen(QPen(ghost, 1.0));
            p.drawPolyline(ghostLine);
        }
        p.setPen(QPen(tokens::rgbaA(tokens::kTextAlphaPrimary), 1.2));
        if (!line.isEmpty()) p.drawPolyline(line);
        if (maxHoldOn_ && !holdLine.isEmpty()) {
            p.setPen(QPen(tokens::rgbaA(tokens::kTextAlphaTertiary2), 1.0));
            p.drawPolyline(holdLine);
        }
    }

    // --- Fixed user markers: vertical named lines on the trace ---------------
    // Three-layer visual separation: auto peaks = accent triangles (peak table);
    // VFO = colored band boxes / solid edges; fixed markers = thin quiet lines,
    // SELECTED = amber dashed + handle dot (kFixedMarkerSelColor). Never blends
    // with the VFO band fill.
    for (int i = 0; i < fixedMarkers_.size(); ++i) {
        const auto& fm = fixedMarkers_[i];
        const int x = xForFreq(fm.freqHz, fLo, span);
        if (x < trace.left() || x > trace.right()) continue;
        const bool sel = (i == selectedFixedIdx_);
        QPen linePen;
        if (sel) {
            QColor sc = QColor(tokens::kFixedMarkerSelColor);
            linePen = QPen(sc, tokens::kFixedMarkerLineWidth + 1, Qt::DashLine);
        } else {
            QColor lc = QColor(tokens::kAccent);
            lc.setAlphaF(tokens::kVfoBoxSelEdgeAlpha);
            linePen = QPen(lc, tokens::kFixedMarkerLineWidth);
        }
        p.setPen(linePen);
        p.drawLine(x, trace.top(), x, trace.bottom());
        // Handle dot at the trace top for the selected marker (touch target cue).
        if (sel) {
            QColor hc = QColor(tokens::kFixedMarkerSelColor);
            p.setPen(Qt::NoPen); p.setBrush(hc);
            const int r = tokens::scaled(3);
            p.drawEllipse(QPointF(x, trace.top()), r, r);
        }
        if (!fm.name.isEmpty()) {
            p.setPen(QPen(tokens::rgbaA(sel ? tokens::kTextAlphaPrimary
                                           : tokens::kTextAlphaTertiary2), 1));
            p.drawText(x + tokens::scaled(2), trace.top() + tokens::scaled(12),
                       QString("%1 %2").arg(fm.name).arg(fm.freqHz / 1e6, 0, 'f', 3));
        }
    }

    // --- waterfall (crop the history snapshot to the visible window) --------
    // Bin-centre mapping SHARED with the trace above: history column i sits at
    // image x in [i, i+1), whose centre (i+0.5) is the bin-centre frequency
    // bandLo+(i+0.5)*binHz. The visible window fLo..fHi maps straight through
    // (f-bandLo)/binHz into image coordinates -- NO floor/ceil rounding -- so the
    // waterfall column centres land EXACTLY on the trace bin x positions. The old
    // floor/ceil introduced up to a ~4px offset at zoom=16 (the reported
    // "trace peak sits right of the waterfall colour" defect).
    if (!history_.isNull() && falls.height() > 0 && bins > 0) {
        const double bandLo = frameF0Hz_ - frameFsHz_ / 2.0;
        const double srcL = clampd((fLo - bandLo) / frameFsHz_ * bins, 0.0,
                                    static_cast<double>(bins_));
        const double srcR = clampd((fHi - bandLo) / frameFsHz_ * bins, 0.0,
                                    static_cast<double>(bins_));
        if (srcR > srcL + 0.5)
            p.drawImage(falls, history_, QRectF(srcL, 0, srcR - srcL, ringDepth_));
        p.setPen(QPen(tokens::cardEdge(), 1));
        p.drawRect(falls);
    }

    // --- VFO band boxes on trace + waterfall --------------------------------
    for (const auto& m : markers_) {
        int bx0, bx1, vx;
        markerBox(m, fLo, span, bx0, bx1, vx);
        const bool sel = m.selected;
        const double fillA = sel ? tokens::kVfoBoxSelFillAlpha : tokens::kVfoBoxFillAlpha;
        const double edgeA = sel ? tokens::kVfoBoxSelEdgeAlpha : tokens::kVfoBoxEdgeAlpha;
        QColor mc = m.color.isValid() ? m.color : QColor(tokens::kAccent);
        QColor fill = mc; fill.setAlphaF(fillA);
        p.fillRect(QRect(bx0, trace.top(), bx1 - bx0, trace.height()), fill);
        p.fillRect(QRect(bx0, falls.top(), bx1 - bx0, falls.height()), fill);
        QColor edge = mc; edge.setAlphaF(edgeA);
        p.setPen(QPen(edge, sel ? tokens::kVfoBoxSelLineWidth : tokens::kVfoBoxLineWidth));
        p.drawLine(bx0, trace.top(), bx0, trace.bottom());
        p.drawLine(bx1, trace.top(), bx1, trace.bottom());
        p.drawLine(bx0, falls.top(), bx0, falls.bottom());
        p.drawLine(bx1, falls.top(), bx1, falls.bottom());
        QColor ctr = mc; ctr.setAlphaF(tokens::kVfoBoxCenterAlpha);
        p.setPen(QPen(ctr, tokens::kVfoBoxLineWidth));
        p.drawLine(vx, trace.top(), vx, trace.bottom());
        p.drawLine(vx, falls.top(), vx, falls.bottom());
    }

    // --- real measured noise-floor baseline (dashed) -----------------------
    // Drawn above the translucent VFO band boxes so the annotation stays crisp.
    if (std::isfinite(noiseFloorDb_)) {
        const int yNf = dbToY(noiseFloorDb_);
        QColor nf(tokens::kNoiseFloorColor);
        QColor nfLine = nf; nfLine.setAlphaF(tokens::kNoiseFloorLineAlpha);
        QPen nfPen(nfLine, 1, Qt::DashLine);
        p.setPen(nfPen);
        p.drawLine(trace.left(), yNf, trace.right(), yNf);
        QColor nfLab = nf; nfLab.setAlphaF(tokens::kNoiseFloorLabelAlpha);
        p.setPen(nfLab);
        p.drawText(trace.left() + tokens::scaled(tokens::kDbLabelPadR),
                   yNf - tokens::scaled(2),
                   QStringLiteral("NF"));
    }

    // --- matured peak markers: triangle on the trace + thin drop line ------
    for (int i = 0; i < peaks_.size(); ++i) {
        const mbdsdr::dsp::PeakInfo& pk = peaks_[i];
        const int px = xForFreq(pk.freqHz, fLo, span);
        if (px < trace.left() || px > trace.right()) continue;
        const int apexY = dbToY(pk.dbfs);
        const bool hi = (i == highlightedPeak_);
        const int halfW = tokens::scaled(hi ? tokens::kPeakMarkerHiHalfW
                                            : tokens::kPeakMarkerHalfW);
        const int triH = tokens::scaled(hi ? tokens::kPeakMarkerHiH
                                           : tokens::kPeakMarkerH);
        // Thin drop line from the summit down to the trace baseline.
        QColor drop = tokens::rgbaA(tokens::kPeakMarkerLineAlpha);
        p.setPen(QPen(drop, 1));
        p.drawLine(px, apexY, px, trace.bottom());
        // Downward-pointing triangle: apex (point) rests on the trace summit.
        QPolygon tri;
        tri << QPoint(px, apexY)
            << QPoint(px - halfW, apexY - triH)
            << QPoint(px + halfW, apexY - triH);
        if (hi) {
            QColor hiFill(QString::fromUtf8(tokens::kAccent));
            hiFill.setAlphaF(tokens::kPeakMarkerHiAlpha);
            p.setPen(Qt::NoPen);
            p.setBrush(hiFill);
        } else {
            p.setPen(Qt::NoPen);
            p.setBrush(tokens::rgbaA(tokens::kPeakMarkerFillAlpha));
        }
        p.drawPolygon(tri);
    }

    // --- frequency strip ----------------------------------------------------
    p.fillRect(strip, QColor(tokens::kSpectrumBg).darker(120));
    const double raw = span / tokens::kWaterfallFreqTicks;
    const double mag = std::pow(10.0, std::floor(std::log10(raw)));
    double nice = mag;
    for (double f : {2.0, 2.5, 5.0, 10.0}) if (mag * f >= raw) { nice = mag * f; break; }
    // Adaptive label precision by step (0/1/2 dp) -- the old hard-coded 3 dp drew
    // a redundant ".000" on every tick. Ticks hang from the strip bottom (shared
    // convention with the waterfall side).
    const int decimals = freqTickDecimals(nice);
    p.setPen(QPen(tokens::rgbaA(tokens::kTextAlphaTertiary), 1));
    const int half = tokens::scaled(tokens::kFreqLabelHalfW);
    for (double f = std::floor(fLo / nice) * nice; f <= fHi; f += nice) {
        const int x = xForFreq(f, fLo, span);
        if (x < strip.left() || x > strip.right()) continue;
        p.drawLine(x, strip.bottom(), x, strip.bottom() - tokens::scaled(tokens::kWaterfallTickH));
        const QString lbl = QString::number(f / 1e6, 'f', decimals);
        p.drawText(QRect(x - half, strip.top(), half * 2, strip.height()),
                   Qt::AlignHCenter | Qt::AlignVCenter, lbl);
    }

    // Centre VFO highlight: a longer accent tick hanging from the strip bottom and
    // a bold accent label at the visible-window centre (matches Flutter's
    // _FreqStripPainter centre mark). View centre == viewCenterHz_ by construction.
    {
        const int cx = lay_.plotX0 + lay_.plotW / 2;
        QColor accent(QString::fromUtf8(tokens::kAccent));
        p.setPen(QPen(accent, 1.2));
        p.drawLine(cx, strip.bottom(),
                   cx, strip.bottom() - tokens::scaled(tokens::kWaterfallTickH) * 2);
        const QFont savedFont = p.font();
        QFont bold = savedFont; bold.setBold(true); p.setFont(bold);
        p.drawText(QRect(cx - half, strip.top(), half * 2, strip.height()),
                   Qt::AlignHCenter | Qt::AlignVCenter,
                   QString::number(viewCenterHz_ / 1e6, 'f', decimals));
        p.setFont(savedFont);
    }

    // --- divider hairline ---------------------------------------------------
    p.setPen(QPen(tokens::rgbaA(dividerHot_ ? tokens::kTextAlphaPrimary
                                            : tokens::kTextAlphaTertiary), 1));
    p.drawLine(lay_.plotX0, lay_.splitY, lay_.plotX1, lay_.splitY);

    // --- hover measurement cursor (hairline + read-out box) -----------------
    if (cursorActive_ && grab_ == Grab::None) {
        const QString readout = cursorReadoutText(cursorPos_);
        if (!readout.isEmpty()) {
            const int cx = cursorPos_.x();
            QColor lineCol(QString::fromUtf8(tokens::kCursorLineColor));
            lineCol.setAlphaF(tokens::kCursorLineAlpha);
            p.setPen(QPen(lineCol, 1));
            p.drawLine(cx, trace.top(), cx, falls.bottom());

            const int boxW = tokens::scaled(tokens::kCursorReadoutW);
            const int boxH = tokens::scaled(tokens::kCursorReadoutH);
            const int gap  = tokens::scaled(tokens::kCursorReadoutGap);
            const bool rightSide = (cx + gap + boxW <= width());
            int boxX = rightSide ? cx + gap : cx - gap - boxW;
            int boxY = cursorPos_.y() - boxH / 2;
            boxY = static_cast<int>(clampd(boxY, 0.0, height() - boxH));
            QRect box(boxX, boxY, boxW, boxH);

            QColor bg = tokens::rgbaA(tokens::kCursorReadoutBgAlpha, 0, 0, 0);
            p.setPen(Qt::NoPen);
            p.setBrush(bg);
            p.drawRoundedRect(box, tokens::scaled(tokens::kRadiusSmall),
                              tokens::scaled(tokens::kRadiusSmall));
            p.setPen(QPen(tokens::rgbaA(tokens::kCursorReadoutEdge), 1));
            p.setBrush(Qt::NoBrush);
            p.drawRoundedRect(box, tokens::scaled(tokens::kRadiusSmall),
                              tokens::scaled(tokens::kRadiusSmall));

            const int pad = tokens::scaled(tokens::kCursorReadoutPad);
            p.setPen(tokens::rgbaA(tokens::kCursorReadoutText));
            p.drawText(box.adjusted(pad, pad, -pad, -pad),
                       Qt::AlignLeading | Qt::AlignVCenter | Qt::TextWordWrap,
                       readout);
        }
    }
}

// ---------------------------------------------------------------------------
// Pointer interaction
// ---------------------------------------------------------------------------
void SpectrumDisplay::mousePressEvent(QMouseEvent* e) {
    if (e->button() != Qt::LeftButton) return;
    const QPoint pos = e->pos();
    double fLo, fHi, span;
    visibleWindow(fLo, fHi, span);

    if (lay_.splitZone.contains(pos)) {
        grab_ = Grab::Divider;
        e->accept();
        return;
    }
    if (lay_.stripRect.contains(pos)) {
        grab_ = Grab::Pan;
        panRefX_ = pos.x();
        e->accept();
        return;
    }
    if (lay_.traceRect.contains(pos) || lay_.fallsRect.contains(pos)) {
        if (e->modifiers() & Qt::ShiftModifier) {
            grab_ = Grab::Pan;
            panRefX_ = pos.x();
            return;
        }
        // Fixed markers take priority: a click/tap near a line selects+drags it.
        const int hitTol = tokens::scaled(tokens::kTouchMinDim) / 2;
        int hitFixed = -1;
        for (int i = 0; i < fixedMarkers_.size(); ++i) {
            const int x = xForFreq(fixedMarkers_[i].freqHz, fLo, span);
            if (std::abs(pos.x() - x) <= hitTol) { hitFixed = i; break; }
        }
        if (hitFixed >= 0) {
            selectedFixedIdx_ = hitFixed;
            dragFixedIdx_ = hitFixed;
            grab_ = Grab::FixedMarker;
            downFixedFreqHz_ = fixedMarkers_[hitFixed].freqHz;
            update();
            e->accept();
            return;
        }
        // Click on empty plot deselects the current fixed marker.
        selectedFixedIdx_ = -1;
        if (!markers_.isEmpty()) {
            const int idx = findMarkerAt(pos.x(), fLo, span);
            int selId = -1;
            for (int i = 0; i < markers_.size(); ++i) if (markers_[i].selected) selId = markers_[i].id;
            if (idx >= 0) {
                const dsp::VfoMarker& m = markers_[idx];
                int bx0, bx1, vx;
                markerBox(m, fLo, span, bx0, bx1, vx);
                const int tol = tokens::scaled(tokens::kBandEdgeHitTol);
                grabVfoId_ = m.id;
                if (std::abs(pos.x() - bx0) <= tol)      grab_ = Grab::VfoEdgeL;
                else if (std::abs(pos.x() - bx1) <= tol) grab_ = Grab::VfoEdgeR;
                else {
                    grab_ = Grab::VfoBody;
                    downPos_ = pos;
                    downFreqHz_ = freqForX(pos.x(), fLo, span);
                    emit vfoMarkerSelected(m.id);
                }
            } else {
                // Blank spectrum: a click retunes the selected VFO to this frequency.
                grab_ = Grab::VfoBody;
                grabVfoId_ = selId;
                downPos_ = pos;
                downFreqHz_ = freqForX(pos.x(), fLo, span);
            }
        } else {
            grab_ = Grab::Tune;
            downPos_ = pos;
            downFreqHz_ = dialFreqHz_;
        }
    }
}

void SpectrumDisplay::mouseMoveEvent(QMouseEvent* e) {
    const QPoint pos = e->pos();
    double fLo, fHi, span;
    visibleWindow(fLo, fHi, span);

    dividerHot_ = lay_.splitZone.contains(pos);

    switch (grab_) {
    case Grab::Divider: {
        int traceH = pos.y() - lay_.topPad - lay_.gapPx - lay_.stripH;
        const int fixedV = lay_.stripH + lay_.gapPx + lay_.splitGap;
        const int pool = lay_.botPad - lay_.topPad - fixedV;
        const int minTrace = tokens::scaled(tokens::kSpecAreaMinH);
        const int minFalls = tokens::scaled(tokens::kWfAreaMinH);
        traceH = static_cast<int>(clampd(traceH, minTrace, pool - minFalls));
        traceShare_ = (pool > 0) ? static_cast<double>(traceH) / pool : 0.5;
        recomputeGeometry();
        update();
        break;
    }
    case Grab::Pan: {
        const int dx = pos.x() - panRefX_;
        panRefX_ = pos.x();
        viewCenterHz_ -= dx / static_cast<double>(lay_.plotW) * span;
        publishVisibleRange();
        update();
        break;
    }
    case Grab::Tune: {
        const int dx = pos.x() - downPos_.x();
        const double newFreq = downFreqHz_ + dx / static_cast<double>(lay_.plotW) * span;
        dialFreqHz_ = newFreq;
        emit frequencyChanged(newFreq);
        update();
        break;
    }
    case Grab::VfoBody: {
        const int dx = pos.x() - downPos_.x();
        const double newFreq = downFreqHz_ + dx / static_cast<double>(lay_.plotW) * span;
        if (grabVfoId_ >= 0) emit vfoMarkerCenterTuned(grabVfoId_, newFreq);
        update();
        break;
    }
    case Grab::VfoEdgeL:
    case Grab::VfoEdgeR: {
        for (int i = 0; i < markers_.size(); ++i) {
            if (markers_[i].id != grabVfoId_) continue;
            const double edgeFreq = freqForX(pos.x(), fLo, span);
            double bw = std::abs(markers_[i].freqHz - edgeFreq);
            bw = clampd(bw, tokens::kVfoMinBandwidthHz, tokens::kVfoMaxBandwidthHz);
            emit vfoMarkerBandwidthChanged(grabVfoId_, bw);
        }
        update();
        break;
    }
    case Grab::FixedMarker: {
        if (dragFixedIdx_ >= 0 && dragFixedIdx_ < fixedMarkers_.size()) {
            fixedMarkers_[dragFixedIdx_].freqHz = freqForX(pos.x(), fLo, span);
            update();
        }
        break;
    }
    default: {
        // No grab: this is the hover measurement cursor. Only track it over the
        // trace / waterfall data areas; divider/VFO drags suppress it via the
        // paintEvent (grab_ == None) gate.
        const bool inPlot = lay_.traceRect.contains(pos) || lay_.fallsRect.contains(pos);
        const bool was = cursorActive_;
        cursorActive_ = inPlot;
        cursorPos_ = pos;
        if (inPlot != was || inPlot) update();
        break;
    }
    }
}

void SpectrumDisplay::mouseReleaseEvent(QMouseEvent* e) {
    if (grab_ == Grab::FixedMarker) {
        if (dragFixedIdx_ >= 0) {
            dragFixedIdx_ = -1;
            emit fixedMarkersEdited();   // persist the dragged marker
        }
    } else if (grab_ == Grab::VfoBody && grabVfoId_ >= 0) {
        double fLo, fHi, span;
        visibleWindow(fLo, fHi, span);
        // A click (little or no drag) settles the selected VFO on the pointer.
        const double target = ((e->pos() - downPos_).manhattanLength() < 3)
                              ? freqForX(e->pos().x(), fLo, span)
                              : downFreqHz_ + (e->pos().x() - downPos_.x()) /
                                                static_cast<double>(lay_.plotW) * span;
        emit vfoMarkerCenterTuned(grabVfoId_, target);
        emit viewChanged();
    } else if (grab_ == Grab::Tune || grab_ == Grab::Pan || grab_ == Grab::Divider) {
        emit viewChanged();
    }
    grab_ = Grab::None;
    grabVfoId_ = -1;
}

void SpectrumDisplay::mouseDoubleClickEvent(QMouseEvent* e) {
    // Double-click re-centres the view on the clicked frequency.
    double fLo, fHi, span;
    visibleWindow(fLo, fHi, span);
    viewCenterHz_ = freqForX(e->pos().x(), fLo, span);
    publishVisibleRange();
    update();
}

void SpectrumDisplay::keyPressEvent(QKeyEvent* e) {
    if (selectedFixedIdx_ >= 0 && selectedFixedIdx_ < fixedMarkers_.size()) {
        double fLo, fHi, span;
        visibleWindow(fLo, fHi, span);
        const double step = span / tokens::kFixedMarkerKeyStepDiv;  // 0.5% of view
        if (e->key() == Qt::Key_Left) {
            fixedMarkers_[selectedFixedIdx_].freqHz -= step;
            update(); emit fixedMarkersEdited(); e->accept(); return;
        }
        if (e->key() == Qt::Key_Right) {
            fixedMarkers_[selectedFixedIdx_].freqHz += step;
            update(); emit fixedMarkersEdited(); e->accept(); return;
        }
        if (e->key() == Qt::Key_Delete || e->key() == Qt::Key_Backspace) {
            fixedMarkers_.removeAt(selectedFixedIdx_);
            selectedFixedIdx_ = -1;
            update(); emit fixedMarkersEdited(); e->accept(); return;
        }
    }
    QWidget::keyPressEvent(e);
}

void SpectrumDisplay::wheelEvent(QWheelEvent* e) {
    const int dy = e->angleDelta().y();
    if (dy == 0) return;
    double fLo, fHi, span;
    visibleWindow(fLo, fHi, span);

    if (e->modifiers() & Qt::ControlModifier) {
        // Zoom about the cursor frequency.
        const double cursorFreq = freqForX(e->position().x(), fLo, span);
        const double factor = (dy > 0) ? 1.2 : (1.0 / 1.2);
        const double newZoom = clampd(zoomFactor_ * factor, tokens::kZoomMin, tokens::kZoomMax);
        const double newSpan = frameFsHz_ / newZoom;
        const double frac = (e->position().x() - lay_.plotX0) / static_cast<double>(lay_.plotW);
        viewCenterHz_ = cursorFreq - frac * newSpan + newSpan / 2.0;
        zoomFactor_ = newZoom;
        publishVisibleRange();
        update();
        emit viewChanged();
    } else {
        // Plain wheel step-tunes the dial by the configured step.
        const double dir = (dy > 0) ? 1.0 : -1.0;
        const double newFreq = dialFreqHz_ + dir * tuneStepHz_;
        dialFreqHz_ = newFreq;
        emit frequencyChanged(newFreq);
        update();
    }
    e->accept();
}

void SpectrumDisplay::leaveEvent(QEvent*) {
    dividerHot_ = false;
    cursorActive_ = false;
    QToolTip::hideText();
    update();
}

} // namespace ui
} // namespace mbdsdr
