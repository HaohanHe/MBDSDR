// SPDX-License-Identifier: MIT
//
// MapProjection -- equirectangular (plate carree) forward/inverse projection
// with a view center and zoom, deliberately decoupled from any QWidget so the
// maths can be unit-tested headlessly.
//
// Plate carree: x = lon, y = lat (no cone/cosine correction), which makes the
// transform a pure affine map and therefore exactly reversible:
//
//   px = cx + (lon - centerLon) * scale
//   py = cy - (lat - centerLat) * scale
//
//   lon = centerLon + (px - cx) / scale
//   lat = centerLat - (py - cy) / scale
//
// where (cx, cy) is the canvas centre in pixels and scale is pixels-per-degree.
//
// The implementation is header-only (inline) on purpose: this lets the widget
// link without requiring map_projection.cpp in the consuming target's source
// list (the offline core must build with only the pre-existing sources wired
// in). A matching .cpp translation unit ships for symmetry and future growth.
#pragma once

#include <QPointF>
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace ui {

class MapProjection {
public:
    MapProjection() = default;

    // ---- canvas geometry (widget size in px) -----------------------------
    // The projection needs to know the canvas size only to place the centre and
    // to define the base scale at zoom=1 (whole world width = 360 deg fits).
    // It never touches a QWidget directly, so headless tests can set any size.
    void setSize(double w, double h) {
        width_  = std::max(1.0, w);
        height_ = std::max(1.0, h);
    }
    double width()  const { return width_; }
    double height() const { return height_; }
    QPointF centerPx() const { return QPointF(width_ / 2.0, height_ / 2.0); }

    // ---- view state ------------------------------------------------------
    void setCenter(double lat, double lon) {
        centerLat_ = clampLat(lat);
        centerLon_ = normalizeLon(lon);
    }
    double centerLat() const { return centerLat_; }
    double centerLon() const { return centerLon_; }

    // zoom: 1.0 = the full 360 deg of longitude fits the canvas width.
    void setZoom(double z) { zoom_ = std::clamp(z, kZoomMin, kZoomMax); }
    double zoom() const { return zoom_; }

    // pixels per degree (both axes; plate carree has no latitude correction).
    double scale() const { return zoom_ * (width_ / 360.0); }

    // Visible degree spans (what the canvas currently shows).
    double lonSpan() const { return width_  / scale(); }
    double latSpan() const { return height_ / scale(); }

    // ---- forward / inverse ----------------------------------------------
    QPointF project(double lat, double lon) const {
        const QPointF c = centerPx();
        const double s = scale();
        return QPointF(c.x() + (lon - centerLon_) * s,
                       c.y() - (lat - centerLat_) * s);
    }
    void unproject(const QPointF& px, double& lat, double& lon) const {
        const QPointF c = centerPx();
        const double s = scale();
        lon = centerLon_ + (px.x() - c.x()) / s;
        lat = centerLat_ - (px.y() - c.y()) / s;
    }

    // ---- interaction -----------------------------------------------------
    // Drag by (dx,dy) screen px. Pulling the map right reveals more westerly
    // longitude; pulling down reveals more northerly latitude.
    void panByPixels(double dx, double dy) {
        const double s = scale();
        setCenter(centerLat_ + dy / s, centerLon_ - dx / s);
    }

    // Multiplicative zoom about a screen-space cursor point so the geographic
    // point under the cursor stays put. factor > 1 zooms in.
    void zoomAbout(const QPointF& cursor, double factor) {
        const double oldScale = scale();
        const double newZoom = std::clamp(zoom_ * factor, kZoomMin, kZoomMax);
        const double newScale = newZoom * (width_ / 360.0);
        if (newScale == oldScale) return;
        const QPointF c = centerPx();
        const double k = (1.0 / oldScale - 1.0 / newScale);
        const double newLon = centerLon_ + (cursor.x() - c.x()) * k;
        const double newLat = centerLat_ + (c.y() - cursor.y()) * k;
        zoom_ = newZoom;
        setCenter(newLat, newLon);
    }

    // ---- adaptive graticule step ----------------------------------------
    // Pick a "nice" degree step (1/2/5/10/20/30/60) so the visible span holds
    // roughly 6-10 lines.
    double suggestGridStep() const {
        const double target = lonSpan() / 8.0;
        const double steps[] = {1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0};
        double best = 60.0;
        for (double s : steps) { best = s; if (s >= target) break; }
        return best;
    }

    // ---- persistence -----------------------------------------------------
    // Pack/unpack the mutable view state for QSettings round-trips.
    struct ViewState { double lat; double lon; double zoom; };
    ViewState viewState() const { return {centerLat_, centerLon_, zoom_}; }
    void setViewState(const ViewState& v) { setZoom(v.zoom); setCenter(v.lat, v.lon); }

    static constexpr double kZoomMin = 1.0;
    static constexpr double kZoomMax = 40.0;

private:
    static double clampLat(double lat) { return std::clamp(lat, -85.0, 85.0); }
    static double normalizeLon(double lon) {
        double l = std::fmod(lon + 180.0, 360.0);
        if (l < 0) l += 360.0;
        return l - 180.0;
    }

    double width_  = 800.0;
    double height_ = 480.0;
    double centerLat_ = 0.0;
    double centerLon_ = 0.0;
    double zoom_ = 1.0;
};

} // namespace ui
} // namespace mbdsdr
