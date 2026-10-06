// SPDX-License-Identifier: MIT
// Phase55 block3: abstract control surface for the live Doppler auto-compensation
// loop. Kept in its own dependency-free header so the UI (main_window.h) can
// inherit from it without pulling in the whole engine header chain. The engine
// holds a pointer to this interface; MainWindow implements it and registers
// itself. A headless / no-UI run leaves the pointer null -> callers report an
// honest "not available" instead of fabricating a toggle.
#pragma once

namespace mbdsdr {
namespace dsp {

class DopplerControlSurface {
public:
    virtual ~DopplerControlSurface() = default;
    // Toggle the live compensation. The implementation re-checks preconditions
    // (station + captured pass) and refuses silently if they are not met.
    virtual void setDopplerCompensationEnabled(bool on) = 0;
    virtual bool isDopplerCompensationEnabled() const = 0;
    // True when a station is configured AND a pass is captured (loop can run).
    virtual bool isDopplerCompensationAvailable() const = 0;
};

} // namespace dsp
} // namespace mbdsdr
