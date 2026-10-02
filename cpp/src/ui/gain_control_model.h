// SPDX-License-Identifier: MIT
//
// Pure decision: given the ACTUAL discrete tuner-gain table the driver reported
// (ISource::availableGainsDb()) and whether a hardware device is connected,
// decide how the UI gain control should behave and WHY.
//
// Honesty rules (never fabricate a step):
//   * Non-empty table + hardware -> a discrete step combo (one row per legal
//     step). This is the real-device case.
//   * Empty table               -> keep the continuous slider. rtl_tcp and the
//     offline test source cannot read the librtlsdr step array, so we never
//     invent a combo.
//   * No hardware at all        -> the control is disabled with an explicit
//     reason, instead of silently looking usable.
//
// Header-only on purpose: a tiny pure struct so the unit test links only this
// translation-free header + QtCore.
#pragma once

#include <QString>
#include <vector>

namespace mbdsdr {
namespace ui {

struct GainControlModel {
    enum class Mode {
        ContinuousSlider,   // no discrete table known: free slider
        DiscreteCombo       // driver reported real legal steps: step combo
    };

    Mode    mode;
    bool    enabled;         // false when no hardware
    QString reason;          // honest disabled / fallback reason (may be empty)

    static GainControlModel decide(const std::vector<double>& table,
                                   bool hardwareConnected) {
        const bool hasTable = !table.empty();
        GainControlModel m;
        m.mode    = hasTable ? Mode::DiscreteCombo : Mode::ContinuousSlider;
        m.enabled = hardwareConnected;
        if (!hardwareConnected) {
            m.reason = QStringLiteral("无设备：增益档不可用");
        } else if (!hasTable) {
            m.reason = QStringLiteral("未读到离散档位表：连续增益（rtl_tcp/测试信号）");
        } else {
            m.reason.clear();
        }
        return m;
    }
};

} // namespace ui
} // namespace mbdsdr
