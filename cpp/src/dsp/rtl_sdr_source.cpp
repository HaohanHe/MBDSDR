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
void RtlSdrSource::setGain(double gainDb) {
    gainDb_ = gainDb;
    // In automatic tuner-gain mode the chip picks its own gain; pushing a
    // manual value would either be rejected or fight the AGC, so only store.
    if (dev_ && !tunerAgc_)
        checkRtl("set_tuner_gain", rtlsdr_set_tuner_gain(dev_, static_cast<int>(gainDb * 10.0)));
}

void RtlSdrSource::setDirectSampling(int mode) {
    directSampling_ = mode;
    if (dev_) checkRtl("set_direct_sampling", rtlsdr_set_direct_sampling(dev_, mode));
}
void RtlSdrSource::setOffsetTuning(bool on) {
    offsetTuning_ = on;
    if (dev_) checkRtl("set_offset_tuning", rtlsdr_set_offset_tuning(dev_, on ? 1 : 0));
}
void RtlSdrSource::setRtlAgc(bool on) {
    rtlAgc_ = on;
    if (dev_) checkRtl("set_agc_mode", rtlsdr_set_agc_mode(dev_, on ? 1 : 0));
}
void RtlSdrSource::setTunerAgc(bool on) {
    tunerAgc_ = on;
    if (dev_) {
        // 0 = automatic tuner gain, 1 = manual. Switching to manual reapplies
        // the stored gain so the slider takes effect immediately.
        checkRtl("set_tuner_gain_mode", rtlsdr_set_tuner_gain_mode(dev_, on ? 0 : 1));
        if (!on)
            checkRtl("set_tuner_gain", rtlsdr_set_tuner_gain(dev_, static_cast<int>(gainDb_ * 10.0)));
    }
}
void RtlSdrSource::setBiasTee(bool on) {
    biasTee_ = on;
    if (dev_) checkRtl("set_bias_tee", rtlsdr_set_bias_tee(dev_, on ? 1 : 0));
}
void RtlSdrSource::setPpm(double ppm) {
    ppm_ = ppm;
    if (dev_) checkRtl("set_freq_correction",
                       rtlsdr_set_freq_correction(dev_, static_cast<int>(ppm)));
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
void RtlSdrSource::setGain(double) {}
QString RtlSdrSource::name() const { return QStringLiteral("RTL-SDR (unsupported)"); }
bool RtlSdrSource::isConnected() const { return false; }

#endif

QString RtlSdrSource::rtlOptionsSummary() const {
    const char* ds = directSampling_ == 1 ? "I" : directSampling_ == 2 ? "Q" : "off";
    return QString("DS=%1 AGC=%2 TunerAGC=%3 BiasT=%4 PPM=%5")
        .arg(QString::fromLatin1(ds))
        .arg(rtlAgc_ ? "on" : "off")
        .arg(tunerAgc_ ? "auto" : "manual")
        .arg(biasTee_ ? "on" : "off")
        .arg(ppm_, 0, 'f', 1);
}

} // namespace dsp
} // namespace mbdsdr
