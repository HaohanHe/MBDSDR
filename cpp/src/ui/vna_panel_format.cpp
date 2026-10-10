// SPDX-License-Identifier: MIT
#include "vna_panel_format.h"

#include <cmath>

namespace mbdsdr {
namespace ui {

QString vnaPortHintText() {
    // Honest identification hint: NanoVNA H/H4 enumerate as STM32 CDC.
    return QString::fromUtf8(
        "USB 串口：VID:PID 0483:5740（STM32 CDC）；未检测到设备时保持空态");
}

QString vnaStatusText(bool connected, const QString& model,
                      const QString& version, const QStringList& cal) {
    if (!connected)
        return QString::fromUtf8("未连接 NanoVNA");
    QString s = model.isEmpty() ? QStringLiteral("NanoVNA") : model;
    if (!version.isEmpty())
        s += QStringLiteral(" · ") + version;
    if (cal.isEmpty())
        s += QString::fromUtf8(" · 未校准");
    else
        s += QStringLiteral(" · cal: ") + cal.join(QStringLiteral(" "));
    return s;
}

QString vnaReadoutText(double minVswr, long freqAtMinHz, double midGainDb) {
    if (!std::isfinite(minVswr))
        return QString::fromUtf8("无数据");
    QString f = freqAtMinHz > 0
        ? QString::fromUtf8(" @ %1 MHz").arg(freqAtMinHz / 1e6, 0, 'f', 3)
        : QString();
    QString g = std::isfinite(midGainDb)
        ? QStringLiteral(" · S21 %1 dB").arg(midGainDb, 0, 'f', 1)
        : QString();
    return QString::fromUtf8("最小 VSWR %1%2%3").arg(minVswr, 0, 'f', 2).arg(f, g);
}

} // namespace ui
} // namespace mbdsdr
