// SPDX-License-Identifier: MIT
#include "stream_watchdog.h"

namespace mbdsdr {
namespace dsp {

StreamWatchdog::StreamWatchdog(int maxConsecutiveFailures)
    : maxFail_(maxConsecutiveFailures < 1 ? 1 : maxConsecutiveFailures) {}

bool StreamWatchdog::onRead(bool ok) {
    if (dead_)
        return true;   // latched dead: only reset() can revive

    if (ok) {
        failCount_ = 0;
        return false;
    }

    ++failCount_;
    if (failCount_ >= maxFail_)
        dead_ = true;
    return dead_;
}

void StreamWatchdog::reset() {
    failCount_ = 0;
    dead_      = false;
}

} // namespace dsp
} // namespace mbdsdr
