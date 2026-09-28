// SPDX-License-Identifier: MIT
#include "rtl_sdr_source.h"

#include <QDebug>
#include <cstring>

namespace mbdsdr {
namespace dsp {

RtlSdrSource::RtlSdrSource() = default;

RtlSdrSource::~RtlSdrSource() {
    if (running_) stop();
}

// =====================================================================
// Common (device-independent) setters: always persist to members, and
// push to hardware immediately only when the device is already open.
// Before start() (or when librtlsdr is not compiled in) the values are
// simply remembered and applied by start() once the device is up.
// =====================================================================

void RtlSdrSource::setGain(double gainDb) {
    gainDb_ = gainDb;
#ifdef HAVE_RTLSDR
    // Manual tuner gain is ignored while tuner AGC is on; just keep the value.
    if (dev_ && !tunerAgc_)
        rtlsdr_set_tuner_gain(dev_, static_cast<int>(gainDb_ * 10.0));  // tenths of dB
#endif
}

void RtlSdrSource::setDirectSampling(int mode) {
    directSampling_ = mode;
#ifdef HAVE_RTLSDR
    if (dev_) rtlsdr_set_direct_sampling(dev_, mode);
#endif
}

void RtlSdrSource::setOffsetTuning(bool on) {
    offsetTuning_ = on;
#ifdef HAVE_RTLSDR
    if (dev_) rtlsdr_set_offset_tuning(dev_, on ? 1 : 0);
#endif
}

void RtlSdrSource::setRtlAgc(bool on) {
    rtlAgc_ = on;
#ifdef HAVE_RTLSDR
    if (dev_) rtlsdr_set_agc_mode(dev_, on ? 1 : 0);  // RTL2832 internal AGC
#endif
}

void RtlSdrSource::setTunerAgc(bool on) {
    tunerAgc_ = on;
#ifdef HAVE_RTLSDR
    if (dev_) {
        // mode 0 = automatic tuner AGC, mode 1 = manual gain.
        rtlsdr_set_tuner_gain_mode(dev_, on ? 0 : 1);
        // Leaving auto mode: restore the stored manual gain level.
        if (!on) rtlsdr_set_tuner_gain(dev_, static_cast<int>(gainDb_ * 10.0));
    }
#endif
}

void RtlSdrSource::setBiasTee(bool on) {
    biasTee_ = on;
#ifdef HAVE_RTLSDR
    if (dev_) rtlsdr_set_bias_tee(dev_, on ? 1 : 0);
#endif
}

void RtlSdrSource::setPpm(double ppm) {
    ppm_ = ppm;
#ifdef HAVE_RTLSDR
    if (dev_) rtlsdr_set_freq_correction(dev_, static_cast<int>(ppm));
#endif
}

void RtlSdrSource::setGainStage(int stage, double gainDb) {
    // RTL2832 has no independent LNA/MIX/VGA gain stages (those belong to
    // E4000/R820T tuners exposed via SoapySDR). Map stage 0 to the single
    // tuner gain; ignore any other stage but keep the call for future backends.
    if (stage == 0) {
        setGain(gainDb);
    } else {
        qWarning() << "[RtlSdrSource] setGainStage: stage" << stage
                   << "not supported on RTL2832 (single tuner gain); ignored";
    }
}

QString RtlSdrSource::rtlOptionsSummary() const {
    const char* ds = directSampling_ == 1 ? "I"
                   : directSampling_ == 2 ? "Q" : "off";
    return QStringLiteral("DS=%1 OffT=%2 AGC=%3 TunerAGC=%4 BiasT=%5 PPM=%6")
        .arg(QLatin1String(ds))
        .arg(offsetTuning_ ? QStringLiteral("on") : QStringLiteral("off"))
        .arg(rtlAgc_       ? QStringLiteral("RTL") : QStringLiteral("off"))
        .arg(tunerAgc_     ? QStringLiteral("auto") : QStringLiteral("manual"))
        .arg(biasTee_      ? QStringLiteral("on") : QStringLiteral("off"))
        .arg(ppm_, 0, 'f', 1);
}

#ifdef HAVE_RTLSDR

namespace {
// librtlsdr returns negative/nonzero codes on failure (e.g. FC0013 tuner does
// not implement offset tuning / bias-tee). Log a warning but keep running --
// the unsupported feature is simply ignored, never a crash.
void checkRtl(const char* what, int rc) {
    if (rc != 0)
        qWarning() << "[RtlSdrSource]" << what << "rc=" << rc
                   << "(tuner may not support it; option ignored)";
}
} // namespace

bool RtlSdrSource::start() {
    int r = rtlsdr_open(&dev_, 0);
    if (r < 0) {
        qWarning() << "[RtlSdrSource] rtlsdr_open failed:" << r
                   << "(no device? falling back to test signal)";
        dev_ = nullptr;
        return false;
    }
    // Order matters: center freq / sample rate first, then tuner mode, then
    // the optional front-end features, finally reset the endpoint buffer.
    checkRtl("set_center_freq", rtlsdr_set_center_freq(dev_, static_cast<uint32_t>(f0_)));
    checkRtl("set_sample_rate", rtlsdr_set_sample_rate(dev_, static_cast<uint32_t>(fs_)));
    checkRtl("set_tuner_gain_mode", rtlsdr_set_tuner_gain_mode(dev_, tunerAgc_ ? 0 : 1));
    if (!tunerAgc_)
        checkRtl("set_tuner_gain", rtlsdr_set_tuner_gain(dev_, static_cast<int>(gainDb_ * 10.0)));
    checkRtl("set_agc_mode", rtlsdr_set_agc_mode(dev_, rtlAgc_ ? 1 : 0));
    checkRtl("set_direct_sampling", rtlsdr_set_direct_sampling(dev_, directSampling_));
    checkRtl("set_offset_tuning", rtlsdr_set_offset_tuning(dev_, offsetTuning_ ? 1 : 0));
    checkRtl("set_bias_tee", rtlsdr_set_bias_tee(dev_, biasTee_ ? 1 : 0));
    checkRtl("set_freq_correction", rtlsdr_set_freq_correction(dev_, static_cast<int>(ppm_)));
    rtlsdr_reset_buffer(dev_);
    running_ = true;
    qInfo() << "[RtlSdrSource] opened device 0, freq=" << f0_ << "Hz sr=" << fs_ << "Hz"
            << rtlOptionsSummary();
    return true;
}

void RtlSdrSource::stop() {
    if (dev_) {
        rtlsdr_cancel_async(dev_);
        rtlsdr_close(dev_);
        dev_ = nullptr;
    }
    running_ = false;
}

std::size_t RtlSdrSource::readIQ(std::vector<std::complex<float>>& out) {
    if (!dev_ || !running_) return 0;
    const std::size_t n = out.size();
    if (n == 0) return 0;

    // rtlsdr_read_sync returns interleaved uint8 I/Q.
    std::vector<unsigned char> raw(n * 2);
    int nRead = 0;
    int r = rtlsdr_read_sync(dev_, raw.data(), static_cast<int>(n * 2), &nRead);
    if (r < 0 || nRead <= 0) return 0;

    const std::size_t samples = static_cast<std::size_t>(nRead) / 2;
    for (std::size_t i = 0; i < samples; ++i) {
        const float I = (static_cast<float>(raw[2*i])     - 127.5f) / 127.5f;
        const float Q = (static_cast<float>(raw[2*i + 1]) - 127.5f) / 127.5f;
        out[i] = std::complex<float>(I, Q);
    }
    return samples;
}

void RtlSdrSource::setCenterFreq(double freqHz) {
    f0_ = freqHz;
    if (dev_) rtlsdr_set_center_freq(dev_, static_cast<uint32_t>(f0_));
}
void RtlSdrSource::setSampleRate(double rateHz) {
    fs_ = rateHz;
    if (dev_) rtlsdr_set_sample_rate(dev_, static_cast<uint32_t>(fs_));
}

QString RtlSdrSource::name() const {
    return dev_ ? QStringLiteral("RTL-SDR") : QStringLiteral("RTL-SDR (no device)");
}
bool RtlSdrSource::isConnected() const { return dev_ != nullptr; }

#else  // !HAVE_RTLSDR -- stub

bool RtlSdrSource::start() {
    qInfo() << "[RtlSdrSource] librtlsdr not compiled in; stub start() returns false";
    return false;
}
void RtlSdrSource::stop() {}
std::size_t RtlSdrSource::readIQ(std::vector<std::complex<float>>&) { return 0; }
void RtlSdrSource::setCenterFreq(double) {}
void RtlSdrSource::setSampleRate(double) {}
// setGain and the optional setters are implemented in the common section
// above (they only persist members in stub mode).
QString RtlSdrSource::name() const { return QStringLiteral("RTL-SDR (unsupported)"); }
bool RtlSdrSource::isConnected() const { return false; }

#endif

} // namespace dsp
} // namespace mbdsdr
