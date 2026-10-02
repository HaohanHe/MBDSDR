// SPDX-License-Identifier: MIT
// librtlsdr device-ops seam for RtlSdrSource.
//
// Every C call RtlSdrSource makes into librtlsdr goes through this table.
//  - PRODUCTION: rtlLibOps() returns the real bindings compiled in only under
//    HAVE_RTLSDR (see rtl_sdr_source.cpp). Without the macro the table is
//    nullptr and the source stays a graceful stub (start() == false).
//  - TESTING: rtlSetLibOpsForTesting() installs an in-memory fake table. The
//    fake is a pure TEST DOUBLE -- function pointers that record a call log and
//    an imaginary PLL state. It is NOT a hardware mock: no USB, no RTL2832, no
//    driver code path, no real device is ever touched; every branch is
//    deterministic.
//
// The handle type is opaque void* on purpose: the real bindings cast it to
// rtlsdr_dev_t*, so the device logic compiles and unit-tests without the
// rtl-sdr.h header present.
#pragma once

#include <cstdint>

namespace mbdsdr {
namespace dsp {

struct RtlLibOps {
    int      (*open)(void** dev, uint32_t index) = nullptr;
    void     (*close)(void* dev) = nullptr;
    int      (*setCenterFreq)(void* dev, uint32_t freqHz) = nullptr;
    uint32_t (*getCenterFreq)(void* dev) = nullptr;
    int      (*setSampleRate)(void* dev, uint32_t rateHz) = nullptr;
    int      (*setTunerBandwidth)(void* dev, int bwHz) = nullptr; // 0 => driver auto
    // tuner gain mode: 0 = automatic tuner AGC, 1 = manual gain (tenths of dB)
    int      (*setTunerGainMode)(void* dev, int manual) = nullptr;
    int      (*setTunerGain)(void* dev, int tenthsDb) = nullptr;
    // nullptr buffer => return the number of legal steps (passthrough if <= 0)
    int      (*getTunerGains)(void* dev, int* table) = nullptr;
    int      (*getTunerGain)(void* dev) = nullptr;
    int      (*setAgcMode)(void* dev, int on) = nullptr;
    int      (*setDirectSampling)(void* dev, int mode) = nullptr;
    int      (*setOffsetTuning)(void* dev, int on) = nullptr;
    int      (*setBiasTee)(void* dev, int on) = nullptr;
    int      (*setFreqCorrection)(void* dev, int ppm) = nullptr;
    int      (*resetBuffer)(void* dev) = nullptr;
    void     (*cancelAsync)(void* dev) = nullptr;
    int      (*readSync)(void* dev, unsigned char* buf, uint32_t len,
                         uint32_t* nRead) = nullptr;
};

// Active ops table: a test-installed fake wins, otherwise the real librtlsdr
// bindings (nullptr when librtlsdr was not compiled in).
const RtlLibOps* rtlLibOps();

// TEST-ONLY injection seam -- never used for production wiring. Install a fake
// table for the duration of a case and restore the previous value afterwards.
void rtlSetLibOpsForTesting(const RtlLibOps* ops);

// Total center-frequency write attempts (first write + retries). Clean-room
// re-derivation of the SDR++ RTL2832 PLL write-loss defence (upstream retries
// up to 10: repos/sdrpp/source_modules/rtl_sdr_source/src/main.cpp:344-359).
// We cap at 5: every attempt is followed by a get_center_freq readback, and an
// exhausted budget is reported loudly -- the source never silently parks at the
// wrong frequency.
inline constexpr int kRtlMaxTuneAttempts = 5;

} // namespace dsp
} // namespace mbdsdr
