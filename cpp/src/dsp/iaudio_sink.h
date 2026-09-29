// SPDX-License-Identifier: GPL-3.0-or-later
//
// Abstract audio sink for the demodulator output. The DSP engine only knows
// about this interface; concrete backends decide how the 48 kHz mono Float32
// stream is rendered:
//   * QtAudioSink  -- real QAudioSink playback (format negotiation + resample)
//   * MemoryAudioSink -- in-memory capture for unit tests / headless boxes
//
// Contract:
//   * write() feeds 48 kHz mono Float32 samples in [-1, 1] and MUST be called
//     from the DSP worker thread.
//   * There is deliberately NO way to conjure a device: outputDevices() returns
//     the REAL hardware descriptions only -- an empty list on headless boxes,
//     never a fabricated name.
#pragma once

#include <QString>
#include <QStringList>

#include <cstddef>
#include <vector>

namespace mbdsdr {
namespace dsp {

class IAudioSink {
public:
    virtual ~IAudioSink() = default;

    /// Feed a block of 48 kHz mono Float32 samples (range [-1,1]).
    /// Must be called from the DSP worker thread.
    virtual void write(const std::vector<float>& audio) = 0;

    /// Master volume 0.0..1.0.
    virtual void setVolume(float v) = 0;
    /// Hardware/mute switch (applied on the render path).
    virtual void setMuted(bool m) = 0;
    /// True when the backend can actually render samples. A headless box with
    /// no audio device reports false -- it must never pretend to play.
    virtual bool isAvailable() const = 0;

    /// Real output device descriptions (for a combo box). The caller prepends
    /// its own "system default" row. Honest empty QStringList when there is no
    /// hardware -- no fake names.
    virtual QStringList outputDevices() const = 0;
    /// Description of the bound device, or "default"/backend name.
    virtual QString currentDeviceName() const = 0;
};

} // namespace dsp
} // namespace mbdsdr
