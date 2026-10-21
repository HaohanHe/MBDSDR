// SPDX-License-Identifier: MIT
// Band-preset table: well-known public frequency allocations, each with a sane
// mode + default channel bandwidth. Pure data + lookup, no Qt, no DSP state,
// fully unit-testable. Independent: allocations are public ITU/IARU band plans,
// not copied from any SDR++ source file.
#ifndef MBDSDR_BAND_PRESET_H
#define MBDSDR_BAND_PRESET_H

#include <cctype>
#include <optional>
#include <string>
#include <vector>

namespace mbdsdr {

struct BandPreset {
    const char* name;   // display name (UTF-8)
    double lowHz;       // inclusive band edge
    double highHz;      // inclusive band edge
    const char* mode;   // suggested demod mode (AM/WFM/NFM/USB...)
    double bwHz;        // suggested channel bandwidth
};

// The preset table, in display order. These are public allocations:
//   FM broadcast 87.5-108 MHz, airband 118-136 MHz, MW broadcast 531-1602 kHz,
//   20m / 2m / 70cm amateur bands.
inline const std::vector<BandPreset>& bandPresets() {
    static const std::vector<BandPreset> table = {
        {"FM广播",   87.5e6,  108.0e6,  "WFM",  120000.0},
        {"航空",     118.0e6, 136.0e6,  "AM",   8330.0},
        {"中波广播", 531.0e3, 1602.0e3, "AM",   9000.0},
        {"20m 业余", 14.0e6,  14.35e6,  "USB",  2700.0},
        {"2m 业余",  144.0e6, 148.0e6,  "NFM",  12500.0},
        {"70cm 业余",430.0e6, 440.0e6,  "NFM",  12500.0},
    };
    return table;
}

// Find the preset whose [lowHz, highHz] contains hz. Returns nullopt when the
// frequency lies in no known band (honest: no guessed default).
inline std::optional<BandPreset> bandPresetForFrequency(double hz) {
    for (const auto& p : bandPresets())
        if (hz >= p.lowHz && hz <= p.highHz) return p;
    return std::nullopt;
}

// Find a preset by a short alias. Aliases cover the common spoken names
// (航空 / airband, FM广播 / fm, 中波 / mw, 20m / 20米, 2m / 2米,
// 70cm / 70厘米). Case-insensitive. Honest: nullopt when unknown.
inline std::optional<BandPreset> bandPresetByName(const std::string& alias) {
    static const struct { const char* key; const char* band; } aliases[] = {
        {"fm广播", "FM广播"}, {"fm", "FM广播"}, {"广播", "FM广播"},
        {"航空", "航空"}, {"airband", "航空"}, {"air", "航空"},
        {"中波", "中波广播"}, {"mw", "中波广播"}, {"am广播", "中波广播"},
        {"20米", "20m 业余"}, {"20m", "20m 业余"},
        {"2米", "2m 业余"}, {"2m", "2m 业余"},
        {"70厘米", "70cm 业余"}, {"70cm", "70cm 业余"}, {"430", "70cm 业余"},
    };
    std::string a;
    a.reserve(alias.size());
    for (char c : alias) a.push_back((char)tolower((unsigned char)c));
    for (const auto& al : aliases) {
        std::string k = al.key;
        std::string kl;
        kl.reserve(k.size());
        for (char c : k) kl.push_back((char)tolower((unsigned char)c));
        if (a.find(kl) != std::string::npos) {
            for (const auto& p : bandPresets())
                if (std::string(p.name) == al.band) return p;
        }
    }
    return std::nullopt;
}

} // namespace mbdsdr

#endif // MBDSDR_BAND_PRESET_H
