// SPDX-License-Identifier: MIT
//
// Unified spectrum-trace / shared frequency-strip / scrolling-waterfall canvas.
//
// One real SpectrumFrame drives both the line trace and the rolling waterfall
// history -- no synthetic data is ever invented. The three painted regions
// share the same left x and one width so the frequency axis aligns across them
// by construction. Vertically, top to bottom:
//
//   top inset
//   |-- spectrum trace   (trace box)
//   1px hairline gap
//   |-- shared tick/label strip (strip box)
//   == draggable divider hairline ==
//   |-- waterfall        (falls box)
//   bottom inset
//
// The waterfall keeps its history in a ring of single-pixel-high rows; the
// public history() snapshot is materialised from that ring with the newest row
// at index 0. All pixel sizes go through the DPI-aware design tokens.
#pragma once

#include <QWidget>
#include <QImage>
#include <QVector>
#include <QElapsedTimer>
#include <QRect>
#include <QRectF>
#include <QList>
#include <array>
#include <cmath>
#include <limits>
#include <vector>

#include "core/spectrum_frame.h"
#include "dsp/peak_detector.h"
#include "dsp/vfo_manager.h"
#include "ui/spectrum_render.h"   // pure waterfall arithmetic (decimate/lut/ticks/json)

namespace mbdsdr {
namespace ui {

// Unified trace + frequency strip + waterfall canvas.
class SpectrumDisplay : public QWidget {
    Q_OBJECT
public:
    explicit SpectrumDisplay(QWidget* parent = nullptr);

    // User-placed fixed marker (persisted by the container). Declared here,
    // outside the slots section, so moc does not choke on a nested type.
    struct FixedMarker { double freqHz = 0; QString name; };

    // One row of the auto peak table (clean-room SDR++ "signal list"). Pure
    // POD read-back of the matured, tracked detection -- no synthetic peaks.
    // deltaHz is the signed offset of the carrier from the current dial/tuned
    // frequency (positive = the peak sits above where the receiver is tuned).
    struct PeakEntry {
        double freqHz = 0.0;   // absolute centre frequency of the carrier
        float  dbfs   = 0.0f;  // carrier power (dBFS)
        double deltaHz = 0.0;  // freqHz - tunedFrequencyHz()
    };

    // ---- Geometry accessors (recomputed in resizeEvent / recomputeGeometry) --
    void recomputeGeometry();
    QRect spectrumRect()   const { return lay_.traceRect; }
    QRect freqStripRect()  const { return lay_.stripRect; }
    QRect waterfallRect()  const { return lay_.fallsRect; }
    QRect dividerHitRect() const { return lay_.splitZone; }
    int   dividerY()       const { return lay_.splitY; }

    // Visible frequency window (zoom / pan state).
    double visLoHz() const;
    double visHiHz() const;
    double zoomFactor() const { return zoomFactor_; }
    // Visible-window centre frequency (Hz) -- used to place fixed markers.
    double viewCenterHz() const { return viewCenterHz_; }

    // ---- Waterfall read-out (tests) --------------------------------------
    const QImage& history() const { return history_; }
    bool hasFrame() const { return haveFrame_; }

public slots:
    // Drive both the trace and the waterfall from the SAME real frame.
    void setSpectrum(const SpectrumFrame& frame);
    void setDbRange(float minDb, float maxDb);
    // Instrument auto-range for the trace (and, by the shared dbToY / colourForDb
    // mapping, the waterfall colour scale). Driven purely by the real sliding
    // peak of frame.dbfs -- no synthetic signals. On = the ceiling eases toward
    // a target that keeps the recent peak in the upper ~80% of the plot without
    // clipping; off = manual setDbRange() bounds are used verbatim.
    void setAutoRangeOn(bool on);
    bool autoRangeOn() const { return autoRangeOn_; }
    void setZoomFactor(double z);
    void resetZoom();
    void setBandwidthHz(double hz) { dialBandwidthHz_ = hz; update(); }
    double bandwidthHz() const { return dialBandwidthHz_; }
    void setStepHz(double hz) { tuneStepHz_ = hz; }
    void setMaxHoldEnabled(bool on);
    void clearMaxHold() { maxHold_.clear(); update(); }
    // Test seam: read back the per-bin peak-hold envelope (dB). Not for
    // production UI use -- lets ctest drive setSpectrum and assert the
    // per-frame kMaxHoldDecayDb decay without painting.
    const std::vector<float>& maxHoldEnvelopeForTest() const { return maxHold_; }

    // ---- Minimum-hold envelope (SDR++ "min hold") -------------------------
    // The per-bin running MINIMUM of every frame (symmetric to the max-hold
    // above). Unlike max-hold this envelope has NO decay: it takes each frame's
    // minimum and holds it, so the trace's quietest floor over the session is
    // visible as a second overlay line. Re-armed fresh on the next enable.
    void setMinHoldEnabled(bool on);
    void clearMinHold() { minHold_.clear(); update(); }
    const std::vector<float>& minHoldEnvelopeForTest() const { return minHold_; }

    // ---- Spectrum persistence (余晖) -------------------------------------
    // mode: 0 = off, 1 = low (short trails), 2 = high (long trails). The ghost
    // envelope decays per-frame by the named tokens (kPersistDecay*); a fresh
    // rise refreshes it. Cleared by clearPersistence().
    void setPersistenceMode(int mode);
    int  persistenceMode() const { return persistMode_; }
    void clearPersistence() { persist_.clear(); update(); }

    // ---- Fixed user markers (竖线+名称+频率) ----------------------------
    // Distinct from the temporary auto peak table and the VFO band boxes: these
    // are user-placed named lines persisted by the container.
    void setFixedMarkers(const QVector<FixedMarker>& m);
    const QVector<FixedMarker>& fixedMarkers() const { return fixedMarkers_; }
    void addFixedMarker(double freqHz, const QString& name);
    void removeFixedMarker(int index);
    void clearFixedMarkers() { fixedMarkers_.clear(); update(); }

    // ---- Spectrum bookmark overlay (read-only reference lines) -----------
    // The container pushes BookmarkManager::frequencies() here. PURE DISPLAY:
    // each saved frequency maps to x through the SAME xForFrequency as the trace
    // and paints one quiet dotted reference line over the trace. No storage in
    // this widget, no interaction, no coupling to the trace data -- an empty list
    // paints nothing (honest empty state). Whole-list replace: the container
    // re-feeds on every bookmark add/edit/delete.
    void setBookmarkHz(const QVector<double>& hz);
    const QVector<double>& bookmarkHz() const { return bookmarkHz_; }

    // ---- Dual measurement cursors (SDR++ Δ markers) ----------------------
    // Two independent draggable vertical lines; the read-out shows |A-B|.
    // NaN = not placed. Pure helper measurementDeltaHz(a,b) is unit-tested.
    static double measurementDeltaHz(double aHz, double bHz);
    void placeCursorA(double hz) { cursorA_Hz_ = hz; update(); }
    void placeCursorB(double hz) { cursorB_Hz_ = hz; update(); }
    void clearCursors() { cursorA_Hz_ = cursorB_Hz_ = std::nan(""); update(); }
    double cursorAHz() const { return cursorA_Hz_; }
    double cursorBHz() const { return cursorB_Hz_; }
    double cursorDeltaHz() const { return measurementDeltaHz(cursorA_Hz_, cursorB_Hz_); }

    // Waterfall controls.
    void setScrollSpeed(int linesPerFrame);   // push a row every N frames (1/2/4)
    void setPalette(int p);                   // 0 classic, 1 monochrome, 2 viridis

    // External user colormap (clean-room SDR++ colormaps). Parses a JSON document
    // of {"name":..,"stops":["#rrggbb",..]} / [{"t":..,"c":"#rrggbb"},..] or a
    // bare top-level array of the same; on success it becomes the active ramp and
    // the ENTIRE waterfall history is re-coloured from the stored raw-dB rows (no
    // frame is dropped). On any malformed input it returns false, the current ramp
    // is left untouched (honest fallback), and -- when errorOut is non-null -- it
    // receives a short human-readable reason the UI can show the user.
    // loadColormapFromFile() reads a .json from disk first.
    bool loadColormapFromJson(const QByteArray& json, QString* errorOut = nullptr);
    bool loadColormapFromFile(const QString& absPath, QString* errorOut = nullptr);

    // Peak handling driven by the container's matured peak table.
    void setHighlightedPeak(int row);         // row index into the matured list
    void setPeakThresholdDb(float db) { peakThresholdDb_ = db; rescanPeaks(); update(); }
    void tuneAndCenter(double hz);

    // After an external tune (arrow-key nudge / engine LO re-tune) decide whether
    // the visible canvas must follow the tuned frequency so the VFO does not walk
    // off-screen: pure followCenterAfterTune() under the hood, only pans the view
    // when the tuned frequency crossed a 10% viewport margin. No-op otherwise.
    void followTunedFrequency(double hz);

    // Injected, real measured noise floor (dBFS on the trace axis). NaN = do not
    // draw the baseline. The engine slowly tracks this; the integration layer
    // forwards it here. Also feeds the cursor SNR read-out.
    void setNoiseFloorDb(float db);
    float noiseFloorDb() const { return noiseFloorDb_; }

    // Multi-VFO band boxes. When non-empty these replace the legacy single box.
    void setVfoMarkers(const QVector<mbdsdr::dsp::VfoMarker>& markers);

    // Band-box pixel geometry for a marker (edge-aligned for SSB). Public so
    // offscreen tests can assert USB/LSB side placement. On return bx0 <= bx1;
    // vx is the dial/tuning line x.
    void vfoBoxGeometryFor(const mbdsdr::dsp::VfoMarker& m,
                           int& bx0, int& bx1, int& vx) const;

    // ---- Offscreen-test-only read-only helpers (do not drive production) ---
    // Exposed purely so the QTest offscreen suite can assert painted marker
    // geometry and the hover read-out text against the real frame data, without
    // inspecting pixels. They duplicate no logic: they reuse the very mappings
    // paintEvent uses.
    const QList<mbdsdr::dsp::PeakInfo>& maturedPeaks() const { return peaks_; }
    // Compact auto-peak-table rows (loudest first, capped at kMaxPeakCount),
    // each annotated with its signed offset from the current tuned frequency.
    // This is the container-facing API the peak table renders from; it is a
    // pure read-back of the matured detection (no peaks are invented).
    std::vector<PeakEntry> peaks() const;
    // Current dial/tuned frequency (Hz) -- the reference for PeakEntry.deltaHz.
    double tunedFrequencyHz() const { return dialFreqHz_; }
    int    yForDbfs(float db) const;                 // trace dBFS -> canvas y
    int    xForFrequency(double f) const;            // absolute Hz -> canvas x
    QString cursorReadoutText(const QPoint& pos) const; // hover F/dBFS/SNR lines

    // ---- Offscreen-test-only read-outs (do not drive production) ----------
    // Current animated trace ceiling / floor the dB grid + trace + waterfall
    // colour map all share. Lets the auto-range suite assert smoothing without
    // inspecting pixels.
    float currentDbCeil() const { return dbCeilDb_; }
    float currentDbFloor() const { return dbFloorDb_; }
    // Persisted trace/waterfall height share (0..1). Lets the persistence suite
    // assert the QSettings round-trip / fallback without inspecting pixels.
    double traceShareFraction() const { return traceShare_; }
    // Ceiling the auto-range is easing toward (0 when off / at rest).
    float autoCeilTarget() const { return autoRangeOn_ ? ceilTargetDb_ : dbCeilDb_; }
    // Source history column the waterfall crop starts at, mirroring paintEvent's
    // binF computation. Exposed so the contract suite can prove the waterfall
    // crop walks the SAME visible window as the trace (no offset / jump).
    int    waterfallCropLeftBin() const;

    // Floating source rectangle into history_ that the waterfall paints into
    // falls, mirroring paintEvent's SHARED bin-centre mapping (no floor/ceil).
    // Lets the offscreen suite prove waterfall column centres land exactly on the
    // trace xForFrequency() positions, without inspecting pixels.
    QRectF waterfallSourceRect() const;
    // Decimals (0/1/2) for a frequency-strip tick label given the nice step, so
    // the suite can assert the adaptive label precision without pixel reads.
    static int freqTickDecimals(double stepHz);

signals:
    void frequencyChanged(double newFreqHz);
    void bandwidthChanged(double newBandwidthHz);
    void visibleRangeChanged(double fLoHz, double fHiHz);
    /// Matured, tracked peak list changed (throttled to a rounded-freq signature).
    void peaksUpdated(QList<mbdsdr::dsp::PeakInfo> peaks, QList<int> ids);
    /// Emitted when a view-changing control settles so the container can persist.
    void viewChanged();

    // Multi-VFO interaction.
    void vfoMarkerSelected(int id);
    void vfoMarkerCenterTuned(int id, double freqHz);
    void vfoMarkerBandwidthChanged(int id, double bwHz);

    // Fixed markers edited (drag / keyboard nudge / delete) -- container persists.
    void fixedMarkersEdited();

protected:
    void resizeEvent(QResizeEvent* event) override;
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void keyPressEvent(QKeyEvent* event) override;
    void mouseDoubleClickEvent(QMouseEvent* event) override;
public:
    void wheelEvent(QWheelEvent* event) override;  // public for offscreen tests
protected:
    void leaveEvent(QEvent* event) override;

private:
    // All painted boxes, derived from one geometry pass. trace/strip/falls share
    // plotX0..plotX0+plotW so the frequency->x mapping is identical everywhere.
    struct CanvasLayout {
        int plotX0 = 0, plotX1 = 0, plotW = 0;
        int topPad = 0, botPad = 0;      // outer insets
        int stripH = 0, gapPx = 0;       // strip height, trace<->strip hairline
        int splitGap = 0;                // strip<->waterfall hairline
        int traceH = 0, fallsH = 0;      // panel heights
        QRect traceRect;                 // line spectrum
        QRect stripRect;                 // shared tick / label strip
        int splitY = 0;                  // divider hairline y
        QRect splitZone;                 // generous hit band around the divider
        QRect fallsRect;                 // scrolling spectrogram
    } lay_;

    // Frequency <-> pixel helpers, shared by trace ticks, strip and waterfall crop.
    void visibleWindow(double& fLo, double& fHi, double& spanHz) const;
    int  dbToY(float db) const;   // trace dBFS -> canvas y (shared by paint + tests)
    int  xForFreq(double f, double fLo, double spanHz) const {
        return lay_.plotX0 + static_cast<int>(lay_.plotW * (f - fLo) / spanHz);
    }
    double freqForX(int x, double fLo, double spanHz) const {
        return fLo + (x - lay_.plotX0) / static_cast<double>(lay_.plotW) * spanHz;
    }
    void publishVisibleRange();

    // Peak detection + cross-frame tracking.
    void rescanPeaks();

    // ---- Waterfall ring-buffer internals ----------------------------------
    void allocateRing(int bins);
    void pushHistoryRow();
    void materialiseHistory();          // ring -> history_ snapshot (row 0 = newest)
    void rebuildColormap();
    QRgb colourForDb(float db) const;

    SpectrumFrame frame_;
    bool haveFrame_ = false;

    // ---- Pointer / interaction state --------------------------------------
    enum class Grab { None, Tune, Pan, Divider, VfoBody, VfoEdgeL, VfoEdgeR, FixedMarker };
    Grab grab_ = Grab::None;
    bool dividerHot_ = false;
    QPoint downPos_;          // press position (widget coords)
    double downFreqHz_ = 0.0; // frequency under the press (tune / pan reference)
    int    panRefX_ = 0;      // last panning pixel x
    int    grabVfoId_ = -1;   // marker being dragged, -1 = legacy/root
    // Fixed-marker interaction: selected index (-1 = none), dragged index, and
    // the frequency the drag started from.
    int selectedFixedIdx_ = -1;
    int dragFixedIdx_ = -1;
    double downFixedFreqHz_ = 0.0;
    // Tooltip cache so setToolTip() only runs when the read-out changes.
    int     tipVfoId_ = -1;
    QString tipText_;

    // Hover measurement cursor (mouseTracking on, no button pressed). Repainted
    // from mouseMoveEvent; suppressed while a divider/VFO/tune grab is active.
    bool    cursorActive_ = false;
    QPoint  cursorPos_;

    // Dual measurement cursors (Hz; NaN = not placed).
    double cursorA_Hz_ = std::nan("");
    double cursorB_Hz_ = std::nan("");
    int    grabCursor_ = 0;   // 0 none, 1 = A, 2 = B

    double dialBandwidthHz_ = 12500.0;
    double tuneStepHz_ = 1000.0;
    double dialFreqHz_ = 0.0;

    // Multi-VFO band boxes. When non-empty these supersede the legacy single box.
    QVector<mbdsdr::dsp::VfoMarker> markers_;
    // Hit-test a marker at data-area pixel x -> index into markers_, or -1.
    int findMarkerAt(int x, double fLo, double spanHz) const;
    // Pixel band-box for a marker accounting for sideband alignment.
    void markerBox(const mbdsdr::dsp::VfoMarker& m, double fLo, double spanHz,
                   int& bx0, int& bx1, int& vx) const;

    float  dbFloorDb_ = -100.0f;
    float  dbCeilDb_ = 0.0f;
    // Manual bounds (last setDbRange). Auto-range only eases dbCeilDb_ toward a
    // data-driven target; the floor stays pinned to the manual value so the
    // noise-floor reference / grid origin never moves unexpectedly.
    float  manualFloorDb_ = -100.0f;
    float  manualCeilDb_  = 0.0f;
    // ---- dB axis auto-range (real frame.dbfs sliding peak) ----------------
    bool   autoRangeOn_ = true;
    std::vector<float> peakWindow_;   // recent per-frame max dBFS (sliding)
    float  ceilTargetDb_ = 0.0f;      // desired ceiling the eased value tracks
    // Real injected noise floor (NaN = baseline suppressed). Also drives the
    // cursor SNR read-out.
    float  noiseFloorDb_ = std::numeric_limits<float>::quiet_NaN();
    double zoomFactor_ = 1.0;
    double viewCenterHz_ = 0.0;

    // Divider position: share of the (trace + waterfall) pool handed to the trace.
    double traceShare_ = 0.5;

    // Peak tracking state.
    QList<mbdsdr::dsp::PeakInfo> peaks_;
    QList<int> peakIds_;
    float peakThresholdDb_ = 15.0f;
    int   highlightedPeak_ = -1;
    QString lastPeakSig_;
    struct TrackedBlip {
        int    id = 0;
        double freqHz = 0;
        float  dbfs = 0;
        double bandwidthHz = 0;
        int    seen = 0;
        int    missed = 0;
        bool   matched = false;
    };
    QList<TrackedBlip> tracked_;
    int nextBlipId_ = 1;

    // Max-hold envelope.
    std::vector<float> maxHold_;
    bool maxHoldOn_ = false;

    // Min-hold envelope (running per-bin minimum, no decay; +inf = "unset").
    std::vector<float> minHold_;
    bool minHoldOn_ = false;

    // Guarantees the FIRST matured detection (even an empty one) is pushed to
    // the container, so the peak table shows its honest empty state on launch
    // instead of a blank un-populated grid. Without this the empty signature ""
    // would equal the initial lastPeakSig_ "" and suppress the very first emit.
    bool peaksAnnounced_ = false;

    // Persistence (余晖) ghost envelope: decays per frame, refreshed by fresh
    // rises. Same bin count as the trace; floor initialises to -inf.
    std::vector<float> persist_;
    int persistMode_ = 0;

    // User fixed markers (persisted by the container).
    QVector<FixedMarker> fixedMarkers_;

    // Spectrum bookmark reference frequencies (Hz), pushed read-only from the
    // container's BookmarkManager. Display-only state -- no persistence here.
    QVector<double> bookmarkHz_;

    // ---- Waterfall ring buffer -------------------------------------------
    // The ring stores RAW dB rows (the SDR++ "source of truth"), not baked
    // pixels: swapping the palette or dragging the dB range then re-colours every
    // historical row from these numbers without re-running the FFT or dropping a
    // frame. history_ is a derived, bin-resolution colour snapshot rebuilt from
    // this ring by materialiseHistory().
    QImage history_;                 // public snapshot: width=bins, row 0 = newest
    std::vector<std::vector<float>> ringDb_;   // depth raw-dB rows (length = bins_)
    QImage fallsPeak_;               // display-res, peak-held downscale cache
    std::vector<float> decScratch_;  // reused decimation output row
    int  ringDepth_ = 0;             // history depth (rows)
    int  ringHead_ = 0;              // next physical slot to overwrite
    int  ringCount_ = 0;             // rows written so far (capped at depth)
    int  bins_ = 0;
    std::array<QRgb, 256> lut_{};
    int  everyNthFrame_ = 1;         // push one row every N frames
    int  frameMod_ = 0;
    int  paletteIndex_ = 0;
    // User-supplied external ramp (overrides the built-in three when set).
    std::vector<ColorStop> customStops_;
    bool hasCustomStops_ = false;
    double frameF0Hz_ = 0.0;         // centre frequency of the last frame
    double frameFsHz_ = 0.0;         // sample rate of the last frame
};

} // namespace ui
} // namespace mbdsdr
