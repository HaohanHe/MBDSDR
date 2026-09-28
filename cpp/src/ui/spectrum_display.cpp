// SPDX-License-Identifier: GPL-3.0-or-later
#include "spectrum_display.h"

#include <QPainter>
#include <QPen>
#include <QPainterPath>
#include <QPolygonF>
#include <QMouseEvent>
#include <QWheelEvent>
#include <QSettings>
#include <QFontMetrics>
#include <QResizeEvent>
#include <cmath>
#include <cstring>
#include <algorithm>

#include "core/tokens.h"

namespace mbdsdr {
namespace ui {

namespace {
// Internal history buffer resolution (NOT a UI size): the image is scaled to
// whatever waterfallRect currently measures. The held time window = rows *
// smoothed frame period.
constexpr int kDepthRows = 512;
constexpr float kDbMin = -100.0f;   // waterfall palette input range
constexpr float kDbMax = 0.0f;
} // namespace

SpectrumDisplay::SpectrumDisplay(QWidget* parent)
    : QWidget(parent)
{
    setAutoFillBackground(true);
    // Reserve a sensible minimum so the trace, strip and waterfall all fit.
    setMinimumSize(tokens::scaled(tokens::kSpectrumMinW),
                   tokens::scaled(tokens::kSpectrumMinH));
    buildLut();

    // Restore the persisted trace/waterfall split share (0.5 = roughly equal).
    QSettings s("MBDSDR", "MBDSDR");
    double saved = s.value(tokens::kSettingsKeySpecFraction,
                           tokens::kDefaultSpecFraction).toDouble();
    fraction_ = std::clamp(saved, 0.1, 0.9);

    // Persisted waterfall scroll speed / palette.
    int spd = s.value(tokens::kSettingsKeyScrollSpeed, 1).toInt();
    setScrollSpeed(spd);
    int pal = s.value(tokens::kSettingsKeyPalette, 0).toInt();
    setPalette(pal);

    hoverPos_ = QPoint(-1, -1);
    lastPanPos_ = QPoint(-1, -1);
}

// --------------------------------------------------------------------------
// Geometry -- the single source of truth for all three panels.
// --------------------------------------------------------------------------
void SpectrumDisplay::recomputeGeometry() {
    const int w = width();
    const int h = height();

    g_.x0 = tokens::scaled(tokens::kDispLeftInset);
    g_.x1 = w - tokens::scaled(tokens::kDispRightInset);
    g_.dataWidth = g_.x1 - g_.x0;

    g_.contentTop = tokens::scaled(tokens::kDispTopInset);
    g_.contentBottom = h - tokens::scaled(tokens::kDispBottomInset);

    g_.freqH = tokens::scaled(tokens::kDispFreqStripH);
    g_.gap   = tokens::scaled(tokens::kDispAreaGap);

    // Vertical space shared by the trace and the waterfall; everything else
    // (top/bottom insets, the strip, the gap, the 1px divider) is removed.
    g_.panelsH = g_.contentBottom - g_.contentTop - g_.freqH - g_.gap - 1;
    if (g_.panelsH < 1) g_.panelsH = 1;

    const int minTrace = tokens::scaled(tokens::kSpecAreaMinH);
    const int minWf    = tokens::scaled(tokens::kWfAreaMinH);
    const int maxTrace = std::max(minTrace, g_.panelsH - minWf);

    g_.traceH = static_cast<int>(std::round(g_.panelsH * fraction_));
    g_.traceH = std::clamp(g_.traceH, minTrace, maxTrace);
    g_.wfH = g_.panelsH - g_.traceH;

    g_.spectrum = QRect(g_.x0, g_.contentTop, g_.dataWidth, g_.traceH);
    g_.freqStrip = QRect(g_.x0, g_.contentTop + g_.traceH + g_.gap,
                         g_.dataWidth, g_.freqH);
    g_.dividerY = g_.freqStrip.bottom() + 1;
    const int hitHalf = tokens::scaled(tokens::kDividerHitHalfH);
    g_.dividerHit = QRect(0, g_.dividerY - hitHalf, w, hitHalf * 2 + 1);
    g_.waterfall = QRect(g_.x0, g_.dividerY + 1, g_.dataWidth, g_.wfH);
}

void SpectrumDisplay::resizeEvent(QResizeEvent*) {
    recomputeGeometry();
    update();
}

// --------------------------------------------------------------------------
// Visible window / zoom / pan
// --------------------------------------------------------------------------
void SpectrumDisplay::visibleRange(double& fLo, double& fHi, double& spanVis) const {
    const double fs = frame_.sampleRateHz;
    spanVis = (fs > 0.0) ? fs / zoomFactor_ : 1.0;
    fLo = viewCenterHz_ - spanVis / 2.0;
    fHi = viewCenterHz_ + spanVis / 2.0;
}

double SpectrumDisplay::visLoHz() const {
    double fLo, fHi, spanVis; visibleRange(fLo, fHi, spanVis); return fLo;
}
double SpectrumDisplay::visHiHz() const {
    double fLo, fHi, spanVis; visibleRange(fLo, fHi, spanVis); return fHi;
}

void SpectrumDisplay::emitVisibleRange() {
    double fLo, fHi, spanVis;
    visibleRange(fLo, fHi, spanVis);
    emit visibleRangeChanged(fLo, fHi);
}

void SpectrumDisplay::setZoomFactor(double z) {
    zoomFactor_ = std::clamp(z, tokens::kZoomMin, tokens::kZoomMax);
    if (frame_.sampleRateHz > 0.0) viewCenterHz_ = frame_.centerFreqHz;
    update();
    emitVisibleRange();
}

void SpectrumDisplay::resetZoom() {
    zoomFactor_ = tokens::kZoomMin;
    viewCenterHz_ = frame_.centerFreqHz;
    update();
    emitVisibleRange();
}

void SpectrumDisplay::tuneAndCenter(double hz) {
    double fLo, fHi, spanVis;
    visibleRange(fLo, fHi, spanVis);
    if (hz < fLo || hz > fHi) viewCenterHz_ = hz;
    vfoFreq_ = hz;
    emit frequencyChanged(hz);
    update();
    emitVisibleRange();
}

void SpectrumDisplay::setVfoMarkers(const QVector<mbdsdr::dsp::VfoMarker>& markers) {
    markers_ = markers;
    // Mirror the selected marker into the legacy single-VFO state so any
    // back-compat path reading vfoFreq_/bwHz_ still sees something sane.
    for (const auto& m : markers_) {
        if (m.selected) { vfoFreq_ = m.freqHz; bwHz_ = m.bandwidthHz; break; }
    }
    update();
}

int SpectrumDisplay::hitVfoMarker(double x, double fLo, double spanVis) const {
    const int tol = tokens::scaled(tokens::kBandEdgeHitTol);
    // Prefer the selected marker, then the topmost (last drawn = list tail).
    for (int pass = 0; pass < 2; ++pass) {
        for (int i = markers_.size() - 1; i >= 0; --i) {
            const auto& m = markers_[i];
            if (pass == 0 && !m.selected) continue;
            if (pass == 1 && m.selected) continue;
            const double half = m.bandwidthHz / 2.0;
            const int bx0 = xOfFreq(m.freqHz - half, fLo, spanVis);
            const int bx1 = xOfFreq(m.freqHz + half, fLo, spanVis);
            if (x >= bx0 - tol && x <= bx1 + tol) return i;
        }
    }
    return -1;
}

void SpectrumDisplay::setDbRange(float minDb, float maxDb) {
    if (maxDb <= minDb) maxDb = minDb + 1.0f;
    dbMin_ = minDb;
    dbMax_ = maxDb;
    update();
}

void SpectrumDisplay::setMaxHoldEnabled(bool on) {
    maxHoldEnabled_ = on;
    if (!on) maxHold_.clear();
    update();
}

// --------------------------------------------------------------------------
// Waterfall history (ported from the old WaterfallWidget)
// --------------------------------------------------------------------------
void SpectrumDisplay::buildLut() {
    lut_.resize(256);
    struct RgbStop { float t; int r, g, b; };
    const bool mono = (palette_ == 1);
    const auto& stops = mono ? tokens::kWaterfallStopsMono : tokens::kWaterfallStops;
    const int nStops = mono
        ? static_cast<int>(sizeof(tokens::kWaterfallStopsMono)/sizeof(tokens::kWaterfallStopsMono[0]))
        : static_cast<int>(sizeof(tokens::kWaterfallStops)/sizeof(tokens::kWaterfallStops[0]));
    QVector<RgbStop> rgb(nStops);
    for (int i = 0; i < nStops; ++i) {
        QColor c(QString::fromUtf8(stops[i].hex));
        rgb[i] = {stops[i].t, c.red(), c.green(), c.blue()};
    }
    for (int i = 0; i < 256; ++i) {
        const float t = i / 255.0f;
        int s = 0;
        while (s < nStops - 2 && rgb[s + 1].t < t) ++s;
        const RgbStop& a = rgb[s];
        const RgbStop& b = rgb[s + 1];
        const float f = (b.t > a.t) ? (t - a.t) / (b.t - a.t) : 0.0f;
        const int r = static_cast<int>(a.r + (b.r - a.r) * f);
        const int g = static_cast<int>(a.g + (b.g - a.g) * f);
        const int bl = static_cast<int>(a.b + (b.b - a.b) * f);
        lut_[i] = qRgb(r, g, bl);
    }
}

QRgb SpectrumDisplay::colorForDb(float db) const {
    float t = (db - kDbMin) / (kDbMax - kDbMin);
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    return lut_[static_cast<int>(t * 255.0f)];
}

void SpectrumDisplay::rebuildImage(int bins) {
    history_ = QImage(bins, kDepthRows, QImage::Format_RGB32);
    history_.fill(qRgb(0, 0, 0));
    bins_ = bins;
}

void SpectrumDisplay::setScrollSpeed(int n) {
    scrollEvery_ = (n >= 1 && n <= 4) ? n : 1;
}

void SpectrumDisplay::setPalette(int p) {
    palette_ = (p == 1) ? 1 : 0;
    buildLut();
    update();
}

// --------------------------------------------------------------------------
// Frame intake: ONE real frame drives both the trace and the waterfall.
// --------------------------------------------------------------------------
void SpectrumDisplay::setSpectrum(const SpectrumFrame& frame) {
    const bool firstFrame = (frame_.sampleRateHz <= 0.0);
    frame_ = frame;

    // Max-hold envelope.
    if (maxHoldEnabled_) {
        if (static_cast<int>(maxHold_.size()) != static_cast<int>(frame.dbfs.size()))
            maxHold_.assign(frame.dbfs.size(), -1000.0f);
        for (std::size_t i = 0; i < frame.dbfs.size(); ++i)
            if (frame.dbfs[i] > maxHold_[i]) maxHold_[i] = frame.dbfs[i];
    }

    // --- Waterfall history -------------------------------------------------
    const int bins = static_cast<int>(frame.dbfs.size());
    if (bins >= 2) {
        if (bins != bins_ || history_.isNull()) rebuildImage(bins);
        frameF0_ = frame.centerFreqHz;
        frameFs_ = frame.sampleRateHz;

        frameMod_ = (frameMod_ + 1) % scrollEvery_;
        if (frameMod_ == 0) {
            const std::size_t rowBytes = static_cast<std::size_t>(history_.bytesPerLine());
            std::memmove(history_.scanLine(1), history_.constScanLine(0),
                         static_cast<std::size_t>(kDepthRows - 1) * rowBytes);
            QRgb* top = reinterpret_cast<QRgb*>(history_.scanLine(0));
            for (int i = 0; i < bins; ++i) top[i] = colorForDb(frame.dbfs[i]);

            // Time bookkeeping from the real frame count + smoothed period.
            if (frameCount_ == 0) {
                frameClock_.start();
                lastElapsedMs_ = 0;
                frameIntervalMs_ = 0.0;
            } else {
                const qint64 now = frameClock_.elapsed();
                const double dt = double(now - lastElapsedMs_);
                if (dt > 0.0) {
                    frameIntervalMs_ = (frameIntervalMs_ <= 0.0)
                        ? dt : 0.9 * frameIntervalMs_ + 0.1 * dt;
                }
                lastElapsedMs_ = now;
            }
            ++frameCount_;
        }
    }
    haveFrame_ = true;

    // On the very first frame, anchor the view center to f0.
    if (firstFrame) {
        viewCenterHz_ = frame.centerFreqHz;
        vfoFreq_ = frame.centerFreqHz;
        emitVisibleRange();
    }

    detectPeaks();
    update();
}

// --------------------------------------------------------------------------
// Peak detection + cross-frame tracking (moved from the old container).
// --------------------------------------------------------------------------
void SpectrumDisplay::detectPeaks() {
    const QList<dsp::PeakInfo> raw = dsp::detectPeaks(frame_.dbfs, frame_.sampleRateHz,
                                  frame_.centerFreqHz, peakThresholdDb_,
                                  tokens::kPeakAbsFloorDbfs);

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
    tracked_.erase(std::remove_if(tracked_.begin(), tracked_.end(),
        [](const TrackedPeak& t) { return t.missFrames > tokens::kPeakMaxMissFrames; }),
        tracked_.end());

    peaks_.clear();
    peakIds_.clear();
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

    // Throttle: only notify the container when the rounded peak set changes.
    QString sig;
    for (const auto& pk : peaks_)
        sig += QString::number(pk.freqHz / 1e6, 'f', 3) + "|";
    if (sig == lastPeakSignature_) return;
    lastPeakSignature_ = sig;

    highlightedPeak_ = -1;
    emit peaksUpdated(peaks_, peakIds_);
}

void SpectrumDisplay::setHighlightedPeak(int row) {
    highlightedPeak_ = (row >= 0 && row < peaks_.size()) ? row : -1;
    update();
}

// --------------------------------------------------------------------------
// Painting
// --------------------------------------------------------------------------
void SpectrumDisplay::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, false);
    p.fillRect(rect(), QColor(QString::fromUtf8(tokens::kSpectrumBg)));

    if (g_.dataWidth <= 10 || g_.traceH <= 10 || g_.wfH <= 4) return;

    const QRectF sp = g_.spectrum;      // spectrum trace area
    const QRectF strip = g_.freqStrip;  // shared frequency strip
    const QRectF wf = g_.waterfall;     // waterfall area

    double fLo, fHi, spanVis;
    visibleRange(fLo, fHi, spanVis);
    const double fs = frame_.sampleRateHz;
    const double f0 = frame_.centerFreqHz;
    const float yMin = dbMin_;
    const float yMax = dbMax_;

    const int spL = static_cast<int>(sp.left());
    const int spR = static_cast<int>(sp.right());
    const int spT = static_cast<int>(sp.top());
    const int spB = static_cast<int>(sp.bottom());
    const double spH = sp.height();
    const double plotW = g_.dataWidth;

    auto yOfDb = [&](float v) -> int {
        if (v < yMin) v = yMin;
        if (v > yMax) v = yMax;
        return spT + static_cast<int>(spH * (1.0 - (v - yMin) / (yMax - yMin)));
    };

    // --- dB grid + left labels --------------------------------------------
    QPen gridPen(QColor(QString::fromUtf8(tokens::kCardEdge)), 1, Qt::DotLine);
    const int step = tokens::kDbGridStep;
    for (int db = (std::ceil(yMin / step) * step); db <= static_cast<int>(yMax); db += step) {
        const int y = yOfDb(static_cast<float>(db));
        p.setPen(gridPen);
        p.drawLine(spL, y, spR, y);
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        p.drawText(0, y - tokens::scaled(tokens::kDbLabelOffsetY),
                   spL - tokens::scaled(tokens::kDbLabelPadR),
                   tokens::scaled(tokens::kDbLabelH),
                   Qt::AlignRight | Qt::AlignVCenter,
                   QString::number(db));
    }

    // --- Frequency ticks in the shared strip (protrude up into the trace) ---
    {
        // Pick a "nice" tick step (~5 intervals across the visible span).
        double rawStep = spanVis / 5.0;
        if (rawStep <= 0) rawStep = 1.0;
        double mag = std::pow(10.0, std::floor(std::log10(rawStep)));
        double res = rawStep / mag;
        double nice = (res < 1.5) ? mag : (res < 3.5) ? 2.0 * mag
                    : (res < 7.5) ? 5.0 * mag : 10.0 * mag;

        QFont f = p.font(); f.setPointSize(tokens::kFontAuxPt); p.setFont(f);
        const int labelH = tokens::scaled(tokens::kFreqLabelH);
        const int labelW = tokens::scaled(tokens::kFreqLabelW);
        const int tickUp = tokens::scaled(tokens::kDispTickProtrusion);
        double start = std::ceil(fLo / nice) * nice;
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaTertiary)));
        for (double fr = start; fr <= fHi + nice * 0.001; fr += nice) {
            const int x = xOfFreq(fr, fLo, spanVis);
            if (x < spL || x > spR) continue;
            // Small tick that pokes up into the trace a touch.
            p.drawLine(x, static_cast<int>(strip.top()), x,
                       static_cast<int>(strip.top()) - tickUp);
            p.drawLine(x, static_cast<int>(strip.bottom()), x,
                       static_cast<int>(strip.bottom()) - tickUp + 1);
            p.drawText(x - labelW / 2,
                       static_cast<int>(strip.top()) + tokens::scaled(tokens::kFreqLabelOffsetY),
                       labelW, labelH, Qt::AlignCenter,
                       QString("%1M").arg(fr / 1e6, 0, 'f', 1));
        }
    }

    // --- Spectrum trace (+ subtle under-fill) and max-hold ------------------
    const std::size_t n = frame_.dbfs.size();
    if (n >= 2 && fs > 0.0) {
        const double fLowEdge = f0 - fs / 2.0;
        const double iLoF = (fLo - fLowEdge) / fs * (n - 1);
        const double iHiF = (fHi - fLowEdge) / fs * (n - 1);
        int iLo = std::max(0, std::min(static_cast<int>(n) - 1,
                                       static_cast<int>(std::floor(iLoF))));
        int iHi = std::max(0, std::min(static_cast<int>(n) - 1,
                                       static_cast<int>(std::ceil(iHiF))));

        if (maxHoldEnabled_ && static_cast<int>(maxHold_.size()) == static_cast<int>(n)) {
            QPen holdPen(QColor(QString::fromUtf8(tokens::kTextSecondary)), 1, Qt::DotLine);
            p.setPen(holdPen);
            QPainterPath hpath;
            for (int i = iLo; i <= iHi; ++i) {
                const double fi = fLowEdge + fs * i / (n - 1);
                const int x = xOfFreq(fi, fLo, spanVis);
                const int y = yOfDb(maxHold_[i]);
                if (i == iLo) hpath.moveTo(x, y); else hpath.lineTo(x, y);
            }
            p.drawPath(hpath);
        }

        // Under-fill (shadow) down to the trace baseline, SDR++ drawFFT style.
        {
            QColor fill(tokens::kAccent); fill.setAlphaF(0.07);
            p.setPen(Qt::NoPen);
            QPainterPath area;
            area.moveTo(xOfFreq(fLo, fLo, spanVis), spB);
            for (int i = iLo; i <= iHi; ++i) {
                const double fi = fLowEdge + fs * i / (n - 1);
                area.lineTo(xOfFreq(fi, fLo, spanVis), yOfDb(frame_.dbfs[i]));
            }
            area.lineTo(xOfFreq(fHi, fLo, spanVis), spB);
            area.closeSubpath();
            p.fillPath(area, fill);
        }

        QPen tracePen(QColor(QString::fromUtf8(tokens::kAccent)), 1);
        p.setPen(tracePen);
        QPainterPath path;
        for (int i = iLo; i <= iHi; ++i) {
            const double fi = fLowEdge + fs * i / (n - 1);
            const int x = xOfFreq(fi, fLo, spanVis);
            const int y = yOfDb(frame_.dbfs[i]);
            if (i == iLo) path.moveTo(x, y); else path.lineTo(x, y);
        }
        p.drawPath(path);
    }

    // --- VFO band boxes (multi-VFO, drawn over spectrum + waterfall) -------
    if (!markers_.isEmpty()) {
        const int bandTop = spT;
        const int bandBottom = static_cast<int>(wf.bottom());
        QFont lblFont = p.font();
        lblFont.setPointSize(tokens::kFontAuxPt);
        p.setFont(lblFont);
        const int lblH = tokens::scaled(tokens::kVfoBoxLabelH);
        for (const auto& m : markers_) {
            if (m.freqHz < fLo - m.bandwidthHz || m.freqHz > fHi + m.bandwidthHz) continue;
            const double half = m.bandwidthHz / 2.0;
            const int bx0 = xOfFreq(m.freqHz - half, fLo, spanVis);
            const int bx1 = xOfFreq(m.freqHz + half, fLo, spanVis);
            const int vx = xOfFreq(m.freqHz, fLo, spanVis);
            QColor c = m.color.isValid() ? m.color : QColor(QString::fromUtf8(tokens::kAccent));

            // Translucent fill over the whole data column (trace + waterfall).
            QColor fill = c;
            fill.setAlphaF(m.selected ? tokens::kVfoBoxSelFillAlpha
                                      : tokens::kVfoBoxFillAlpha);
            p.fillRect(QRect(bx0, bandTop, bx1 - bx0, bandBottom - bandTop), fill);

            // Edge lines + center line.
            QColor edge = c;
            edge.setAlphaF(m.selected ? tokens::kVfoBoxSelEdgeAlpha
                                     : tokens::kVfoBoxEdgeAlpha);
            QPen ep(edge);
            ep.setWidthF(m.selected ? tokens::kVfoBoxSelLineWidth
                                    : tokens::kVfoBoxLineWidth);
            p.setPen(ep);
            p.drawLine(bx0, bandTop, bx0, bandBottom);
            p.drawLine(bx1, bandTop, bx1, bandBottom);
            QColor cc = c; cc.setAlphaF(tokens::kVfoBoxCenterAlpha);
            QPen cp(cc);
            cp.setWidthF(m.selected ? tokens::kVfoBoxSelLineWidth
                                    : tokens::kVfoBoxLineWidth);
            p.setPen(cp);
            p.drawLine(vx, bandTop, vx, bandBottom);

            // Name label pinned to the top of the trace.
            QColor tc = c; tc.setAlphaF(tokens::kVfoBoxLabelAlpha);
            p.setPen(tc);
            const QString lbl = m.name.isEmpty() ? QString::number(m.freqHz, 'f', 0)
                                                : m.name;
            p.drawText(QRect(bx0, spT, std::max(20, bx1 - bx0), lblH),
                       Qt::AlignHCenter | Qt::AlignVCenter, lbl);
        }
    } else if (f0 >= fLo && f0 <= fHi) {
        // Legacy single-VFO box (kept for back-compat tests / before markers arrive).
        const int vfoX = xOfFreq(vfoFreq_, fLo, spanVis);
        const double halfHz = bwHz_ / 2.0;
        const int bx0 = xOfFreq(vfoFreq_ - halfHz, fLo, spanVis);
        const int bx1 = xOfFreq(vfoFreq_ + halfHz, fLo, spanVis);
        QColor fill(tokens::kAccent); fill.setAlphaF(0.08);
        p.fillRect(QRect(bx0, spT, bx1 - bx0, spB - spT), fill);
        QColor edgeC(tokens::kAccent); edgeC.setAlphaF(0.6);
        QPen edge(edgeC); edge.setWidthF(1.0);
        p.setPen(edge);
        p.drawLine(bx0, spT, bx0, spB);
        p.drawLine(bx1, spT, bx1, spB);
        QPen vfoPen(QColor(QString::fromUtf8(tokens::kAccent)));
        vfoPen.setWidthF(tokens::kVfoLineWidth);
        p.setPen(vfoPen);
        p.drawLine(vfoX, spT, vfoX, spB);
        p.setBrush(QColor(QString::fromUtf8(tokens::kAccent)));
        p.setPen(Qt::NoPen);
        const int hh = tokens::scaled(tokens::kVfoHandleHalfW);
        const int hhH = tokens::scaled(tokens::kVfoHandleH);
        p.drawPolygon(QPolygonF({QPointF(bx0-hh, spT), QPointF(bx0+hh, spT),
                                 QPointF(bx0, spT + hhH)}));
        p.drawPolygon(QPolygonF({QPointF(bx1-hh, spT), QPointF(bx1+hh, spT),
                                 QPointF(bx1, spT + hhH)}));
    }

    // --- Peak markers (triangles along the trace top) ----------------------
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
            const int x = xOfFreq(pk.freqHz, fLo, spanVis);
            QPolygon tri;
            tri << QPoint(x - mhw, spT) << QPoint(x + mhw, spT) << QPoint(x, spT + mh);
            p.drawPolygon(tri);
        }
        p.setPen(QPen());
        p.setBrush(Qt::NoBrush);
    }

    // --- Crosshair readout (trace area only) -------------------------------
    if (hoverPos_.x() >= spL && hoverPos_.x() <= spR &&
        hoverPos_.y() >= spT && hoverPos_.y() <= spB) {
        p.setPen(QPen(QColor(QString::fromUtf8(tokens::kAccent)), 1, Qt::DashLine));
        p.drawLine(hoverPos_.x(), spT, hoverPos_.x(), spB);
        p.drawLine(spL, hoverPos_.y(), spR, hoverPos_.y());
        const double frac = (hoverPos_.x() - spL) / plotW;
        const double freq = fLo + frac * spanVis;
        const double dbFrac = (hoverPos_.y() - spT) / spH;
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

    // --- Divider hairline ---------------------------------------------------
    {
        const bool hot = dividerHover_ || dividerDragging_;
        p.setPen(QPen(hot ? QColor(tokens::splitterHandleRgba())
                          : QColor(QString::fromUtf8(tokens::kCardEdge)),
                      hot ? 2 : 1));
        p.drawLine(0, g_.dividerY, width(), g_.dividerY);
    }

    // --- Waterfall: crop history columns to the visible window -------------
    if (haveFrame_ && !history_.isNull() && bins_ > 1 && frameFs_ > 0.0) {
        p.setRenderHint(QPainter::SmoothPixmapTransform, false);
        const double fLowEdge = frameF0_ - frameFs_ / 2.0;
        double iLoF = (fLo - fLowEdge) / frameFs_ * (bins_ - 1);
        double iHiF = (fHi - fLowEdge) / frameFs_ * (bins_ - 1);
        int iLo = std::max(0, std::min(bins_ - 1, static_cast<int>(std::floor(iLoF))));
        int iHi = std::max(0, std::min(bins_ - 1, static_cast<int>(std::ceil(iHiF))));
        if (iHi > iLo) {
            p.drawImage(wf, history_, QRectF(iLo, 0, iHi - iLo + 1, kDepthRows));
        } else {
            p.drawImage(wf, history_);
        }
    } else {
        p.setPen(QColor(tokens::textRgba(tokens::kTextAlphaSecondary)));
        p.drawText(wf, Qt::AlignCenter, QStringLiteral("等待频谱数据"));
    }
}

// --------------------------------------------------------------------------
// Mouse / wheel interaction
// --------------------------------------------------------------------------
void SpectrumDisplay::mousePressEvent(QMouseEvent* e) {
    const QPoint pos = e->position().toPoint();
    const int ex = pos.x();
    const int ey = pos.y();

    // Divider drag takes priority.
    if (g_.dividerHit.contains(pos)) {
        dividerDragging_ = true;
        setCursor(Qt::SizeVerCursor);
        e->accept();
        return;
    }

    if (ex < g_.x0 || ex > g_.x1 || ey < g_.contentTop || ey > g_.waterfall.bottom()) {
        e->accept();
        return;
    }

    dragging_ = true;
    panning_ = (e->modifiers() & Qt::ShiftModifier);
    lastPanPos_ = pos;
    dragVfoId_ = -1;

    double fLo, fHi, spanVis; visibleRange(fLo, fHi, spanVis);

    // Multi-VFO: hit-test band boxes first.
    if (!markers_.isEmpty()) {
        const int hit = hitVfoMarker(ex, fLo, spanVis);
        if (hit >= 0) {
            const auto& m = markers_[hit];
            const double half = m.bandwidthHz / 2.0;
            const int bx0 = xOfFreq(m.freqHz - half, fLo, spanVis);
            const int bx1 = xOfFreq(m.freqHz + half, fLo, spanVis);
            const int tol = tokens::scaled(tokens::kBandEdgeHitTol);
            dragVfoId_ = m.id;
            if (std::abs(ex - bx0) <= tol) dragMode_ = DragMode::BandL;
            else if (std::abs(ex - bx1) <= tol) dragMode_ = DragMode::BandR;
            else dragMode_ = DragMode::Tune;
            if (!m.selected) emit vfoMarkerSelected(m.id);
            if (dragMode_ == DragMode::Tune) mouseMoveEvent(e);
            e->accept();
            return;
        }
        // Missed every box: panning only (don't retune by clicking empty space).
        dragMode_ = panning_ ? DragMode::Pan : DragMode::None;
        e->accept();
        return;
    }

    // Legacy single-VFO drag.
    const int cx = xOfFreq(vfoFreq_, fLo, spanVis);
    const int halfW = static_cast<int>(g_.dataWidth * (bwHz_ / 2.0) / spanVis);
    const int tol = tokens::scaled(tokens::kBandEdgeHitTol);
    if (std::abs(ex - (cx - halfW)) <= tol) dragMode_ = DragMode::BandL;
    else if (std::abs(ex - (cx + halfW)) <= tol) dragMode_ = DragMode::BandR;
    else if (panning_) dragMode_ = DragMode::Pan;
    else dragMode_ = DragMode::Tune;

    if (dragMode_ == DragMode::Tune) mouseMoveEvent(e);
}

void SpectrumDisplay::mouseMoveEvent(QMouseEvent* e) {
    const QPoint pos = e->position().toPoint();
    hoverPos_ = pos;

    // Hover highlight over the divider.
    if (!dividerDragging_) {
        const bool overDivider = g_.dividerHit.contains(pos);
        if (overDivider != dividerHover_) {
            dividerHover_ = overDivider;
            setCursor(overDivider ? Qt::SizeVerCursor : QCursor());
        }
    }

    if (dividerDragging_) {
        // New trace height = divider Y minus the strip/gap above it.
        int newTrace = pos.y() - g_.contentTop - g_.gap - g_.freqH;
        const int minTrace = tokens::scaled(tokens::kSpecAreaMinH);
        const int minWf    = tokens::scaled(tokens::kWfAreaMinH);
        newTrace = std::clamp(newTrace, minTrace, g_.panelsH - minWf);
        if (g_.panelsH > 0) fraction_ = static_cast<double>(newTrace) / g_.panelsH;
        recomputeGeometry();
        update();
        e->accept();
        return;
    }

    if (!dragging_ || frame_.sampleRateHz <= 0) { update(); return; }

    double fLo, fHi, spanVis; visibleRange(fLo, fHi, spanVis);

    // Multi-VFO box drag: retune / resize the hit marker.
    if (dragVfoId_ >= 0) {
        int idx = -1;
        for (int i = 0; i < markers_.size(); ++i)
            if (markers_[i].id == dragVfoId_) { idx = i; break; }
        if (idx < 0) { dragging_ = false; return; }
        auto& m = markers_[idx];
        const double frac = (pos.x() - g_.x0) / static_cast<double>(g_.dataWidth);
        const double edgeF = fLo + frac * spanVis;
        if (dragMode_ == DragMode::BandL || dragMode_ == DragMode::BandR) {
            double half = std::abs(edgeF - m.freqHz);
            half = std::clamp(half * 2.0,
                              static_cast<double>(tokens::kVfoMinBandwidthHz),
                              static_cast<double>(tokens::kVfoMaxBandwidthHz));
            m.bandwidthHz = half;
            emit vfoMarkerBandwidthChanged(m.id, half);
        } else if (dragMode_ == DragMode::Tune) {
            double freq = edgeF;
            if (stepHz_ > 0) freq = std::round(freq / stepHz_) * stepHz_;
            m.freqHz = freq;
            emit vfoMarkerCenterTuned(m.id, freq);
        }
        update();
        return;
    }

    if (dragMode_ == DragMode::BandL || dragMode_ == DragMode::BandR) {
        const double frac = (pos.x() - g_.x0) / static_cast<double>(g_.dataWidth);
        const double edgeF = fLo + frac * spanVis;
        double half = std::abs(edgeF - vfoFreq_);
        half = std::clamp(half * 2.0, 100.0, 500000.0);
        bwHz_ = half;
        emit bandwidthChanged(bwHz_);
        update();
        return;
    }

    if (dragMode_ == DragMode::Pan) {
        const double fs = frame_.sampleRateHz;
        const double span = fs / zoomFactor_;
        const double dx = pos.x() - lastPanPos_.x();
        viewCenterHz_ -= (dx / static_cast<double>(g_.dataWidth)) * span;
        lastPanPos_ = pos;
        update();
        emitVisibleRange();
        return;
    }

    // Plain drag: retune f0 to the cursor frequency.
    const double frac = (pos.x() - g_.x0) / static_cast<double>(g_.dataWidth);
    double freq = fLo + frac * spanVis;
    if (stepHz_ > 0) freq = std::round(freq / stepHz_) * stepHz_;
    vfoFreq_ = freq;
    viewCenterHz_ = freq;
    emit frequencyChanged(freq);
    update();
}

void SpectrumDisplay::mouseReleaseEvent(QMouseEvent*) {
    if (dividerDragging_) {
        dividerDragging_ = false;
        unsetCursor();
        // Persist the new split share.
        QSettings("MBDSDR", "MBDSDR").setValue(tokens::kSettingsKeySpecFraction, fraction_);
        emit viewChanged();
    }
    dragging_ = false;
    panning_ = false;
    dragMode_ = DragMode::None;
    dragVfoId_ = -1;
}

void SpectrumDisplay::mouseDoubleClickEvent(QMouseEvent*) {
    resetZoom();
}

void SpectrumDisplay::wheelEvent(QWheelEvent* e) {
    if (frame_.sampleRateHz <= 0.0) { e->ignore(); return; }
    const double steps = e->angleDelta().y() / 120.0;
    if (g_.dataWidth <= 10) { e->accept(); return; }

    const double fs = frame_.sampleRateHz;
    const double xRatio = (e->position().x() - g_.x0) / static_cast<double>(g_.dataWidth);

    const double spanBefore = fs / zoomFactor_;
    const double fCursor = viewCenterHz_ + (xRatio - 0.5) * spanBefore;

    const double newZoom = std::clamp(zoomFactor_ * std::pow(2.0, steps),
                                      tokens::kZoomMin, tokens::kZoomMax);
    if (newZoom == zoomFactor_) { e->accept(); return; }
    zoomFactor_ = newZoom;

    const double spanAfter = fs / zoomFactor_;
    viewCenterHz_ = fCursor - (xRatio - 0.5) * spanAfter;

    update();
    emitVisibleRange();
    e->accept();
}

void SpectrumDisplay::leaveEvent(QEvent*) {
    hoverPos_ = QPoint(-1, -1);
    update();
}

} // namespace ui
} // namespace mbdsdr
