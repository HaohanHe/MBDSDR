// SPDX-License-Identifier: MIT
#include "sat_task_planner.h"

#include "dsp/tle_client.h"
#include "core/sat_capture.h"

namespace mbdsdr {
namespace ai {

SatTaskResult planSatelliteCapture(const QString& satName,
                                   double stationLatDeg, double stationLonDeg,
                                   const QDateTime& nowUtc, int hoursAhead) {
    SatTaskResult out;
    if (!qIsFinite(stationLatDeg) || !qIsFinite(stationLonDeg)) {
        out.error = QString::fromUtf8("站点坐标无效");
        return out;
    }
    if (satName.trimmed().isEmpty()) {
        out.error = QString::fromUtf8("未选择卫星");
        return out;
    }

    const QList<dsp::TleEntry> entries = dsp::TleClient::builtinTle();
    if (entries.isEmpty()) {
        out.error = QString::fromUtf8("无可用 TLE（离线缓存为空）");
        return out;
    }

    // Match by case-insensitive substring.
    QList<dsp::TleEntry> matched;
    for (const auto& e : entries)
        if (e.name.contains(satName, Qt::CaseInsensitive))
            matched.append(e);
    if (matched.isEmpty()) {
        out.error = QString::fromUtf8("离线 TLE 中找不到卫星「%1」").arg(satName);
        return out;
    }

    dsp::TleClient client;
    const QList<dsp::SatPass> passes =
        client.computePasses(matched, stationLatDeg, stationLonDeg,
                             nowUtc.toUTC(), hoursAhead);
    if (passes.isEmpty()) {
        out.error = QString::fromUtf8("未来 %1 小时内「%2」没有可见过境").arg(hoursAhead).arg(satName);
        return out;
    }

    const dsp::SatPass p = passes.first();
    out.satName = p.name;
    out.aosUtc = p.aos;
    out.maxElDeg = p.maxEl;

    // Downlink carrier: from the real TLE name (0 = unknown, never invented).
    const double f0 = dsp::TleClient::downlinkHzFor(p.name);
    if (f0 <= 0.0) {
        out.error = QString::fromUtf8("「%1」下行频率未知，不自动选频").arg(p.name);
        return out;
    }
    out.dopplerAtPeakHz = p.dopplerAtPeakHz;
    const double targetHz = core::captureTargetHz(f0, p.dopplerAtPeakHz);
    if (targetHz <= 0.0) {
        out.error = QString::fromUtf8("捕获频率计算无效");
        return out;
    }
    out.captureFreqHz = targetHz;

    const core::SatChannelMode ch = core::recommendSatelliteMode(targetHz);
    out.mode = ch.mode;

    out.plan = planTargetCapture(targetHz, ch.mode);
    out.plan.name = QString::fromUtf8("卫星捕获：%1").arg(p.name);
    out.ok = true;
    return out;
}

// ---------------------------------------------------------------------------
// Read-only pass prediction (LLM predict_passes tool). See header for the
// honesty contract: fresh cache only, never the stale builtin demo TLE.
// ---------------------------------------------------------------------------
SatPassListResult predictPassesFromEntries(const QList<dsp::TleEntry>& entries,
                                           const QString& satName,
                                           double stationLatDeg, double stationLonDeg,
                                           const QDateTime& nowUtc, int hoursAhead) {
    SatPassListResult out;
    if (!qIsFinite(stationLatDeg) || !qIsFinite(stationLonDeg)) {
        out.error = QString::fromUtf8("站点坐标无效");
        return out;
    }
    if (satName.trimmed().isEmpty()) {
        out.error = QString::fromUtf8("未指定卫星");
        return out;
    }

    // Match by case-insensitive substring against the supplied (fresh) entries.
    QList<dsp::TleEntry> matched;
    for (const auto& e : entries)
        if (e.name.contains(satName, Qt::CaseInsensitive))
            matched.append(e);
    if (matched.isEmpty()) {
        out.error = QString::fromUtf8("TLE 缓存中找不到卫星「%1」").arg(satName);
        return out;
    }

    dsp::TleClient client;
    const QList<dsp::SatPass> passes =
        client.computePasses(matched, stationLatDeg, stationLonDeg,
                             nowUtc.toUTC(), hoursAhead);
    if (passes.isEmpty()) {
        out.error = QString::fromUtf8("未来 %1 小时内「%2」没有可见过境").arg(hoursAhead).arg(satName);
        return out;
    }

    for (const dsp::SatPass& p : passes) {
        SatPassEntry e;
        e.name = p.name;
        e.catalogNumber = dsp::TleClient::catalogNumber(p.tle);
        e.aosUtc = p.aos;
        e.azAos = p.azAos;
        e.losUtc = p.los;
        e.azLos = p.azLos;
        e.maxEl = p.maxEl;
        out.passes.append(e);
    }
    out.ok = true;
    out.source = QString::fromUtf8("cached_tle");
    return out;
}

SatPassListResult predictSatellitePasses(const QString& satName,
                                         double stationLatDeg, double stationLonDeg,
                                         const QDateTime& nowUtc, int hoursAhead) {
    // Validate cheap inputs first so the model gets an honest reason without
    // touching disk.
    SatPassListResult bad;
    if (!qIsFinite(stationLatDeg) || !qIsFinite(stationLonDeg) ||
        stationLatDeg < -90.0 || stationLatDeg > 90.0 ||
        stationLonDeg < -180.0 || stationLonDeg > 180.0) {
        bad.error = QString::fromUtf8("站点坐标无效");
        return bad;
    }
    if (satName.trimmed().isEmpty()) {
        bad.error = QString::fromUtf8("未指定卫星");
        return bad;
    }

    // Fresh on-disk cache ONLY. No builtinTle() fallback -- that 2006 snapshot
    // is offline demo data and must never be reported as a real upcoming pass.
    dsp::TleClient client;
    const dsp::TleCache cache = client.cachedTle();
    if (!cache.valid || cache.entries.isEmpty()) {
        bad.error = QString::fromUtf8("无新鲜 TLE");
        bad.source = QString::fromUtf8("none");
        return bad;
    }
    return predictPassesFromEntries(cache.entries, satName, stationLatDeg,
                                    stationLonDeg, nowUtc, hoursAhead);
}

} // namespace ai
} // namespace mbdsdr
