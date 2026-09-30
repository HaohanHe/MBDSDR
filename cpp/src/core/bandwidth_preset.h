// SPDX-License-Identifier: MIT
//
// Per-mode default IF / channel bandwidth presets.
//
// This header is deliberately UI-free (only QString) so the decision logic can
// be unit-tested offscreen without a MainWindow or a radio:
//
//   * defaultBandwidthHzForMode(mode) -- the recommended channel bandwidth when
//     the user picks a demodulation mode from the combo.  It maps by the MODE
//     NAME (AM/NFM/WFM/USB/LSB/CW/BPSK/QPSK/ADS-B), NEVER by a station name.
//     Unknown modes fall back to the narrow-FM default.  Every value is a named
//     constant -- no scattered magic numbers.
//
//   * bandwidthOnModeSwitch(oldMode, newMode, currentBwHz) -- the coverage
//     rule applied when the user switches mode: adopt the new mode's default
//     ONLY while the user's current bandwidth was itself the old mode's default
//     (i.e. they never manually tuned it).  A manually-set bandwidth is
//     preserved across the switch instead of being stomped.
//
// The engine's VfoManager::defaultBandwidthForMode() delegates here so the
// engine, the UI combo and the tests all share ONE table.
#pragma once

#include <QString>
#include <QtGlobal>

#include <cmath>

namespace mbdsdr {
namespace core {

// ---- Named preset bandwidths (Hz) -------------------------------------
// Chosen to match the real demodulator channel needs:
//   NFM  -> 12.5 kHz  (narrow FM voice / repeater channel spacing)
//   WFM  -> 200 kHz   (FM broadcast: +/-75 kHz deviation + 19 kHz pilot +
//                      stereo MPX; the engine's WFM path hard-targets a
//                      240 kHz IF and 200 kHz channel, so we match it rather
//                      than the 60 kHz "narrow WFM" option -- 60 kHz would
//                      clip the stereo composite and is only used for the
//                      narrower NOAA APT downlink, which is a different band).
//   AM   -> 9 kHz     (standard AM broadcast / air bandwidth)
//   USB/LSB -> 2.4 kHz (SSB voice)
//   CW   -> 500 Hz    (narrow CW filter)
//   BPSK/QPSK -> 12 kHz (2400-baud digital main-lobe; matches the digital
//                        channelizer's fixed 12 kHz channel)
//   ADS-B -> 2 MHz    (wideband 1090 MHz Mode S squitter capture)
inline constexpr double kBwNfmHz       = 12500.0;
inline constexpr double kBwWfmHz       = 200000.0;
inline constexpr double kBwAmHz        = 9000.0;
inline constexpr double kBwSsbHz      = 2400.0;
inline constexpr double kBwCwHz       = 500.0;
inline constexpr double kBwDigitalHz  = 12000.0;
inline constexpr double kBwAdsbHz     = 2000000.0;
// Unknown / unlisted mode: conservative narrow-FM voice default.
inline constexpr double kBwFallbackHz = 12500.0;

// Recommended channel bandwidth (Hz) for a demodulation mode.
inline double defaultBandwidthHzForMode(const QString& mode) {
    if (mode == QLatin1String("NFM"))   return kBwNfmHz;
    if (mode == QLatin1String("WFM"))   return kBwWfmHz;
    if (mode == QLatin1String("AM"))    return kBwAmHz;
    if (mode == QLatin1String("USB") ||
        mode == QLatin1String("LSB"))   return kBwSsbHz;
    if (mode == QLatin1String("CW"))    return kBwCwHz;
    if (mode == QLatin1String("BPSK") ||
        mode == QLatin1String("QPSK"))  return kBwDigitalHz;
    if (mode == QLatin1String("ADS-B")) return kBwAdsbHz;
    return kBwFallbackHz;
}

// Coverage rule when the user switches from `oldMode` to `newMode` while the
// live channel bandwidth is `currentBwHz`:
//
//   * If `currentBwHz` equals `oldMode`'s own default, the user has never
//     manually tuned the bandwidth -- adopt `newMode`'s default (natural walk
//     between modes).
//   * Otherwise the user deliberately set a custom bandwidth; PRESERVE it
//     instead of stomping it with the new mode's preset.
//
// Pure, side-effect free, fully unit-testable.
inline double bandwidthOnModeSwitch(const QString& oldMode,
                                    const QString& newMode,
                                    double currentBwHz) {
    const double oldDefault = defaultBandwidthHzForMode(oldMode);
    const double newDefault = defaultBandwidthHzForMode(newMode);
    // More than 1 Hz away from the old mode's default counts as a manual
    // override (presets are all >= 500 Hz, so 1 Hz is a negligible epsilon).
    const bool userTouched = std::abs(currentBwHz - oldDefault) > 1.0;
    return userTouched ? currentBwHz : newDefault;
}

} // namespace core
} // namespace mbdsdr
