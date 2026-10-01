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

} // namespace ai
} // namespace mbdsdr
