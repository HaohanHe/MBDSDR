// SPDX-License-Identifier: MIT
//
// Desktop keyboard-shortcut catalog: the single declarative source of truth
// for BOTH the "快捷键" dialog (ShortcutsDialog renders from here) and the
// MainWindow wiring. The numeric tuning / zoom / gain arithmetic that used to
// live inline inside MainWindow lambdas is factored into the inline pure helpers
// below so it can be unit-tested with no QApplication / no radio hardware.
//
// Upstream references (mechanism only, clean-room re-implementation; SDR++ is
// ImGui/Apache-2.0-ish, GQRX is Qt/GPLv3 -- we copy neither code nor strings):
//   * SDR++ core/src/gui/main_window.cpp:580-597: wheel steps the selected VFO
//     by snapInterval (Shift x10 / Alt x0.1); no VFO => view zoom.
//   * GQRX src/qtgui/dockaudio.cpp:71-72: Key_Plus / Key_Minus step gain;
//     dockaudio.cpp:69-70: Key_R record, Key_M mute.
#pragma once

#include <QtCore/QString>

#include <algorithm>
#include <cmath>
#include <vector>

namespace mbdsdr {
namespace ui {

// One dialog row. `sequence` is human-readable (also QKeySequence-ish text).
struct ShortcutRow {
    const char* sequence;   // e.g. "← / →"
    const char* description;
};

// Full catalog; order == dialog row order. Kept in sync with the MainWindow
// wiring block (P3 added + / - and PgUp / PgDown on top of the previously
// undocumented Ctrl+Tab / Ctrl+1..9 VFO switching).
inline const std::vector<ShortcutRow>& shortcutCatalog() {
    static const std::vector<ShortcutRow> rows = {
        {"← / →",                       "中心频率 ± 步进"},
        {"Shift+← / →",                 "中心频率 ± 步进/10（细调）"},
        {"↑ / ↓",                       "带宽 ×2 / ÷2"},
        {"+ / -",                       "增益 加一档 / 减一档"},
        {"PgUp / PgDown",               "调频步进 档位 增大 / 减小"},
        {"Space",                       "静音切换"},
        {"Ctrl+R",                      "录制 / 停止"},
        {"Ctrl+Tab / Ctrl+Shift+Tab",   "切换 下一个 / 上一个 VFO"},
        {"Ctrl+1 … Ctrl+9",             "跳转到第 N 个 VFO"},
    };
    return rows;
}

// ---- Pure tuning arithmetic (unit-tested; no Qt state touched) ------------

// Frequency nudge. dir: +1 up / -1 down. fine => step/10 (SDR++ Alt-wheel
// mechanism, bound here to Shift+arrows).
inline double nudgeFreqHz(double currentHz, double stepHz, int dir, bool fine) {
    const double s = fine ? stepHz / 10.0 : stepHz;
    return currentHz + (dir >= 0 ? s : -s);
}

// Bandwidth nudge by factor (2.0 / 0.5), clamped to [loHz, hiHz].
inline double nudgeBandwidthHz(double bwHz, double factor, double loHz, double hiHz) {
    return std::clamp(bwHz * factor, loHz, hiHz);
}

// Wrap-step through `n` combo entries. dir: +1 next / -1 prev. Inputs already
// out of range are clamped into [0,n) first so a stale index never throws.
inline int cycleStepIndex(int cur, int n, int dir) {
    if (n <= 0) return 0;
    cur = std::clamp(cur, 0, n - 1);
    return ((cur + dir) % n + n) % n;
}

// Gain step.
//   * Discrete table (real RTL gain table, ascending or not -- sorted here):
//     move to the nearest legal level strictly above/below `currentDb`
//     (0.5 dB tolerance against the readback round), pinned at the ends.
//   * Empty table (rtl_tcp / offline test / file): continuous control,
//     +/-continuousStepDb clamped to [minDb, maxDb].
inline double stepGainDb(const std::vector<double>& table, double currentDb,
                         int dir, double minDb, double maxDb,
                         double continuousStepDb) {
    if (table.empty()) {
        return std::clamp(currentDb + (dir >= 0 ? continuousStepDb : -continuousStepDb),
                          minDb, maxDb);
    }
    std::vector<double> t = table;
    std::sort(t.begin(), t.end());
    if (dir >= 0) {
        for (double g : t) {
            if (g > currentDb + 0.5) return g;
        }
        return t.back();
    }
    for (auto it = t.rbegin(); it != t.rend(); ++it) {
        if (*it < currentDb - 0.5) return *it;
    }
    return t.front();
}

} // namespace ui
} // namespace mbdsdr
