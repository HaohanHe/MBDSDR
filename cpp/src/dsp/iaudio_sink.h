// SPDX-License-Identifier: GPL-3.0-or-later
//
// Abstract audio sink for the demodulator output. The DSP engine only knows
// about this interface; concrete backends decide how the 48 kHz Float32
// stream is rendered:
//   * QtAudioSink  -- real QAudioSink playback (format negotiation + resample)
//   * MemoryAudioSink -- in-memory capture for unit tests / headless boxes
//
// Contract:
//   * write() feeds 48 kHz mono Float32 samples in [-1, 1] and MUST be called
//     from the DSP worker thread.
//   * writeStereo() feeds 48 kHz stereo Float32 samples in [-1, 1]. left/right
//     MUST be equal length; frame i pairs left[i] with right[i]. It ships a
//     default downmix to mono so backends that cannot (or choose not to) do
//     real stereo keep working unchanged.
//   * There is deliberately NO way to conjure a device: outputDevices() returns
//     the REAL hardware descriptions only -- an empty list on headless boxes,
//     never a fabricated name.
#pragma once

#include <QString>
#include <QStringList>

#include <algorithm>
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

    /// Feed a block of 48 kHz stereo Float32 samples (range [-1,1]).
    /// left/right MUST be equal length; frame i pairs left[i] with right[i].
    /// Must be called from the DSP worker thread.
    ///
    /// Default implementation downmixes to mono as (L+R)/2 and forwards to
    /// write(), so any backend that does not override this keeps working with
    /// no extra code. Backends that can render real stereo override this.
    virtual void writeStereo(const std::vector<float>& left,
                             const std::vector<float>& right) {
        const std::size_t n = std::min(left.size(), right.size());
        std::vector<float> mono;
        mono.reserve(n);
        for (std::size_t i = 0; i < n; ++i)
            mono.push_back((left[i] + right[i]) * 0.5f);
        write(mono);
    }

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
