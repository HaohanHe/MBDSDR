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

bool RtlSdrSource::start() {
    int r = rtlsdr_open(&dev_, 0);
    if (r < 0) {
        qWarning() << "[RtlSdrSource] rtlsdr_open failed:" << r
                   << "(no device? falling back to test signal)";
        dev_ = nullptr;
        return false;
    }
    rtlsdr_set_center_freq(dev_, static_cast<uint32_t>(f0_));
    rtlsdr_set_sample_rate(dev_, static_cast<uint32_t>(fs_));
    rtlsdr_set_tuner_gain_mode(dev_, 1);  // manual gain
    rtlsdr_set_tuner_gain(dev_, static_cast<int>(gainDb_ * 10.0));  // tenths of dB
    rtlsdr_reset_buffer(dev_);
    running_ = true;
    qInfo() << "[RtlSdrSource] opened device 0, freq=" << f0_ << "Hz sr=" << fs_ << "Hz";
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
    if (dev_) rtlsdr_set_tuner_gain(dev_, static_cast<int>(gainDb_ * 10.0));
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

} // namespace dsp
} // namespace mbdsdr
