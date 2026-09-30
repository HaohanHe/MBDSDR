// SPDX-License-Identifier: MIT
#include "device_capabilities.h"

namespace mbdsdr {
namespace dsp {

namespace {
// Real RTL2832U sample-rate accept rule (repos/librtlsdr/src/librtlsdr.c:1100):
//   invalid if rate<=225k OR rate>3.2M OR (rate>300k AND rate<=900k).
// So the usable windows are (225k,300k] and (900k,3.2M]. We expose the
// practical low-band floor (250k) and the 3.2M ceiling as the range endpoints.
constexpr double kRtlsdrSrMinHz = 250000.0;
constexpr double kRtlsdrSrMaxHz = 3200000.0;

// Tunable range per tuner, from the C driver (docs/learn/librtlsdr.md:52-55):
//   E4000  default spec   64   – 1700 MHz   (tuner_e4k.c:351-352)
//   R820T/R828D           24   – 1766 MHz   (tuner_r82xx.c:1168)
//   FC0012/FC0013         22   – 948.6 MHz   (Fitipower published range)
// Unknown / FC2580: no driver-known table entry -> 0 (honest "未知").
struct TunerRange { double minHz; double maxHz; };
constexpr TunerRange kKnownRanges[] = {
    // index == static_cast<int>(RtlTuner)
    {0.0,        0.0},        // Unknown
    {64.0e6,     1700.0e6},    // E4000
    {22.0e6,     948.6e6},     // FC0012
    {22.0e6,     948.6e6},     // FC0013
    {0.0,        0.0},         // FC2580 (CMMB TV tuner; no table entry here)
    {24.0e6,     1766.0e6},    // R820T
    {24.0e6,     1766.0e6},    // R828D
};

// Real RTL2832U PLL-friendly rates the driver accepts. These are the standard
// clock configurations used by GQRX/SDR++ for an RTL device; every entry is in
// a valid window (<=300k low band, or >900k high band) per the rule above.
// buildSampleRateOptions filters these to the connected device's live range.
constexpr double kCandidateRatesHz[] = {
    250000.0,
    1024000.0,
    1536000.0,
    1792000.0,
    1920000.0,
    2048000.0,
    2400000.0,
    2560000.0,
    2800000.0,
    3200000.0,
};

constexpr int knownRangeIndex(int raw) {
    return (raw >= 0 && raw <= 6) ? raw : 0;
}
} // namespace

QString rtlTunerName(RtlTuner t) {
    switch (t) {
    case RtlTuner::E4000:  return QStringLiteral("E4000");
    case RtlTuner::FC0012: return QStringLiteral("FC0012");
    case RtlTuner::FC0013: return QStringLiteral("FC0013");
    case RtlTuner::FC2580: return QStringLiteral("FC2580");
    case RtlTuner::R820T: return QStringLiteral("R820T");
    case RtlTuner::R828D: return QStringLiteral("R828D");
    case RtlTuner::Unknown:
    default:               return QStringLiteral("未知调谐器");
    }
}

DeviceCapabilities noDeviceCapabilities() {
    DeviceCapabilities c;
    c.connected = false;
    c.deviceName = QStringLiteral("RTL-SDR 未连接");
    c.provenance = QStringLiteral("无真实设备连接");
    return c;
}

DeviceCapabilities rtlCapabilitiesFromHandshake(int tunerTypeRaw, int gainCount,
                                                const QString& endpoint) {
    DeviceCapabilities c;
    c.connected = true;
    const auto tuner = static_cast<RtlTuner>(tunerTypeRaw);
    const QString tunerName = rtlTunerName(tuner);
    c.deviceName = QStringLiteral("RTL-SDR · %1").arg(tunerName);

    const TunerRange range = kKnownRanges[knownRangeIndex(tunerTypeRaw)];
    c.tunableMinHz = range.minHz;
    c.tunableMaxHz = range.maxHz;
    c.sampleRateMinHz = kRtlsdrSrMinHz;
    c.sampleRateMaxHz = kRtlsdrSrMaxHz;

    const bool tunerKnown = (range.minHz > 0.0);
    c.provenance = QStringLiteral(
        "设备身份来自 rtl_tcp 握手（RTL0，tuner_type=%1%2）；"
        "调谐范围%3；采样率范围按 RTL2832U 规则（>225k、≤3.2M、避开 300k–900k 死区）")
        .arg(tunerTypeRaw)
        .arg(tunerKnown ? QString() : QStringLiteral("（未识别）"))
        .arg(tunerKnown
             ? QStringLiteral("按 librtlsdr 驱动常量表")
             : QStringLiteral("驱动常量表无此型号，标注为未知"));
    if (!endpoint.isEmpty())
        c.provenance += QStringLiteral(" · %1").arg(endpoint);
    if (gainCount > 0)
        c.provenance += QStringLiteral(" · 增益档位 %1").arg(gainCount);
    return c;
}

QList<double> buildSampleRateOptions(const DeviceCapabilities& caps) {
    QList<double> out;
    if (!caps.connected) return out;                 // honest empty state
    if (!(caps.sampleRateMaxHz > caps.sampleRateMinHz)) return out;
    for (const double r : kCandidateRatesHz) {
        if (r < caps.sampleRateMinHz - 1.0) continue;
        if (r > caps.sampleRateMaxHz + 1.0) continue;
        out.push_back(r);
    }
    return out;
}

} // namespace dsp
} // namespace mbdsdr
