// SPDX-License-Identifier: MIT
#include "sat_task_planner.h"

#include "dsp/tle_client.h"
#include "core/sat_capture.h"

namespace mbdsdr {
namespace ai {

// ---------------------------------------------------------------------------
// Pure seams (no disk / no network) -- see header for the honesty contract.
// ---------------------------------------------------------------------------
bool entriesAreFresh(const QList<dsp::TleEntry>& entries, const QDateTime& nowUtc) {
    if (entries.isEmpty()) return false;
    for (const dsp::TleEntry& e : entries) {
        const QDateTime epoch = dsp::TleClient::parseTleEpoch(e.line1);
        if (!epoch.isValid()) return false;
        const double days = dsp::TleClient::daysSinceEpoch(epoch, nowUtc.toUTC());
        if (days < 0.0 || days > kMaxTleAgeDays) return false;
    }
    return true;
}

SatTaskResult planCaptureFromEntries(const QList<dsp::TleEntry>& entries,
                                     const QString& satName,
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
    if (entries.isEmpty()) {
        out.error = QString::fromUtf8("无可用 TLE");
        return out;
    }

    // Match by case-insensitive substring.
    QList<dsp::TleEntry> matched;
    for (const auto& e : entries)
        if (e.name.contains(satName, Qt::CaseInsensitive))
            matched.append(e);
    if (matched.isEmpty()) {
        out.error = QString::fromUtf8("TLE 中找不到卫星「%1」").arg(satName);
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

SatTaskResult planSatelliteCapture(const QString& satName,
                                   double stationLatDeg, double stationLonDeg,
                                   const QDateTime& nowUtc, int hoursAhead) {
    // Step 1: cheap input validation (no disk / no SGP4 work).
    SatTaskResult out;
    if (!qIsFinite(stationLatDeg) || !qIsFinite(stationLonDeg)) {
        out.error = QString::fromUtf8("站点坐标无效");
        return out;
    }
    if (satName.trimmed().isEmpty()) {
        out.error = QString::fromUtf8("未选择卫星");
        return out;
    }

    // Step 2: try the fresh on-disk cache first.
    dsp::TleClient client;
    const dsp::TleCache cache = client.cachedTle();
    if (cache.valid && !cache.entries.isEmpty() &&
        entriesAreFresh(cache.entries, nowUtc.toUTC())) {
        return planCaptureFromEntries(cache.entries, satName, stationLatDeg,
                                      stationLonDeg, nowUtc, hoursAhead);
    }

    // Step 3: no fresh cache. builtinTle() is the 2006 offline demo snapshot --
    // we still propagate it so the UI has a reference number to show, but the
    // result is explicitly tagged demo-only and MUST NOT be treated as a real
    // upcoming pass. ok=false so callers (main_window.cpp AI plan path) route
    // into the honest-failure UI branch; warning carries the operator-facing
    // notice verbatim.
    const QList<dsp::TleEntry> builtin = dsp::TleClient::builtinTle();
    if (builtin.isEmpty()) {
        out.error = QString::fromUtf8("无新鲜 TLE 缓存，且离线演示 TLE 为空");
        out.warning = out.error;
        return out;
    }
    SatTaskResult demo = planCaptureFromEntries(builtin, satName, stationLatDeg,
                                                stationLonDeg, nowUtc, hoursAhead);
    demo.demo = true;
    demo.ok = false;
    demo.warning = QString::fromUtf8(
        "演示数据：内置 TLE 历元为 2006 年、已过期，结果仅为离线演示，"
        "不代表真实过境，不能用于真实捕获");
    // If the pure seam already failed (e.g. satellite not in builtin list, no
    // pass in window), preserve its error but still surface the demo warning so
    // the operator understands WHY no fresh result was produced.
    if (!demo.ok && demo.error.isEmpty()) demo.error = demo.warning;
    if (demo.error.isEmpty()) demo.error = demo.warning;
    return demo;
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

SatListResult listCachedSatellites(const QDateTime& nowUtc) {
    // On-disk cache ONLY. The stale offline builtinTle() (epoch 2006) is
    // deliberately NOT listed -- it is demo data and must not be presented as a
    // real satellite catalog.
    dsp::TleClient client;
    const dsp::TleCache cache = client.cachedTle();
    if (!cache.valid || cache.entries.isEmpty()) {
        SatListResult out;
        out.error = QString::fromUtf8("无 TLE 缓存（尚未从 celestrak 拉取；predict_passes 同样依赖此缓存）");
        out.source = QString::fromUtf8("none");
        return out;
    }
    SatListResult out = listSatellitesFromEntries(cache.entries, nowUtc);
    out.fetchedAt = cache.fetchedAt.toUTC();
    return out;
}

SatListResult listSatellitesFromEntries(const QList<dsp::TleEntry>& entries,
                                        const QDateTime& nowUtc) {
    SatListResult out;
    out.ok = true;
    out.source = QString::fromUtf8("cached_tle");
    for (const dsp::TleEntry& e : entries) {
        SatListEntry se;
        se.name = e.name;
        se.catalogNumber = dsp::TleClient::catalogNumber(e);
        se.epochUtc = dsp::TleClient::parseTleEpoch(e.line1).toUTC();
        if (se.epochUtc.isValid()) {
            se.ageDays = dsp::TleClient::daysSinceEpoch(se.epochUtc, nowUtc.toUTC());
            se.fresh = se.ageDays >= 0.0 && se.ageDays <= kMaxTleAgeDays;
        }
        se.downlinkHz = dsp::TleClient::downlinkHzFor(e.name);
        out.sats.append(se);
    }
    return out;
}

} // namespace ai
} // namespace mbdsdr
