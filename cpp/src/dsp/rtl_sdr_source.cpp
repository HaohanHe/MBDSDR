// SPDX-License-Identifier: MIT
#include "rtl_sdr_source.h"
#include "rtl_sdr_ops.h"

#include <QDebug>
#include <cstring>

#ifdef HAVE_RTLSDR
#include <rtl-sdr.h>
#endif

namespace mbdsdr {
namespace dsp {

RtlSdrSource::RtlSdrSource() = default;

RtlSdrSource::~RtlSdrSource() {
    if (running_) stop();
}

// =====================================================================
// Common (device-independent) setters: always persist to members, and
// push to hardware immediately only when the device is already open.
// Before start() (or when no ops table is bound) the values are simply
// remembered and applied by start() once the device is up.
// =====================================================================

void RtlSdrSource::setGain(double gainDb) {
    // Snap the continuous UI request to the nearest legal discrete step when a
    // table is known. With no table (stub / could not read it) this is an
    // honest passthrough -- we never invent a step. The snapped value is what
    // we store AND push to hardware, so gain() reports the real applied level.
    const double snapped = gainTable_.snap(gainDb);
    gainDb_ = snapped;
    gainTable_.setActualGainDb(snapped);
    const RtlLibOps* ops = rtlLibOps();
    // Manual tuner gain is ignored while tuner AGC is on; just keep the value.
    if (dev_ && ops && ops->setTunerGain && !tunerAgc_)
        ops->setTunerGain(dev_, static_cast<int>(snapped * 10.0));  // tenths of dB
}

void RtlSdrSource::setDirectSampling(int mode) {
    directSampling_ = mode;
    const RtlLibOps* ops = rtlLibOps();
    if (dev_ && ops && ops->setDirectSampling) ops->setDirectSampling(dev_, mode);
}

void RtlSdrSource::setOffsetTuning(bool on) {
    offsetTuning_ = on;
    const RtlLibOps* ops = rtlLibOps();
    if (dev_ && ops && ops->setOffsetTuning) ops->setOffsetTuning(dev_, on ? 1 : 0);
}

void RtlSdrSource::setRtlAgc(bool on) {
    rtlAgc_ = on;
    const RtlLibOps* ops = rtlLibOps();
    if (dev_ && ops && ops->setAgcMode) ops->setAgcMode(dev_, on ? 1 : 0);  // RTL2832 internal AGC
}

void RtlSdrSource::setTunerAgc(bool on) {
    tunerAgc_ = on;
    const RtlLibOps* ops = rtlLibOps();
    if (dev_ && ops && ops->setTunerGainMode) {
        // mode 0 = automatic tuner AGC, mode 1 = manual gain.
        ops->setTunerGainMode(dev_, on ? 0 : 1);
        // Leaving auto mode: restore the stored manual gain level (already snapped).
        if (!on && ops->setTunerGain)
            ops->setTunerGain(dev_, static_cast<int>(gainDb_ * 10.0));
    }
}

void RtlSdrSource::setBiasTee(bool on) {
    biasTee_ = on;
    const RtlLibOps* ops = rtlLibOps();
    if (dev_ && ops && ops->setBiasTee) ops->setBiasTee(dev_, on ? 1 : 0);
}

void RtlSdrSource::setPpm(double ppm) {
    ppm_ = ppm;
    const RtlLibOps* ops = rtlLibOps();
    if (dev_ && ops && ops->setFreqCorrection)
        ops->setFreqCorrection(dev_, static_cast<int>(ppm));
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

// These two are device-independent: they read the snapped gain table that is
// shared between the bound-ops and stub builds. With no table the reported
// gain is just the stored (passthrough) value and availableGainsDb() is empty.
double RtlSdrSource::gain() const { return gainDb_; }

std::vector<double> RtlSdrSource::availableGainsDb() const {
    return gainTable_.availableGainsDb();
}

namespace {

// librtlsdr returns negative/nonzero codes on failure (e.g. FC0013 tuner does
// not implement offset tuning / bias-tee). Log a warning but keep running --
// the unsupported feature is simply ignored, never a crash.
void checkRtl(const char* what, int rc) {
    if (rc != 0)
        qWarning() << "[RtlSdrSource]" << what << "rc=" << rc
                   << "(tuner may not support it; option ignored)";
}

// Test-installed fake ops table (see rtl_sdr_ops.h). Nullptr in production.
const RtlLibOps* g_testOps = nullptr;

#ifdef HAVE_RTLSDR
// ---- Real librtlsdr bindings (adapt rtlsdr_dev_t* -> opaque void*) ----------
int      advOpen(void** dev, uint32_t index) {
    return rtlsdr_open(reinterpret_cast<rtlsdr_dev_t**>(dev), index);
}
void     advClose(void* dev) { rtlsdr_close(reinterpret_cast<rtlsdr_dev_t*>(dev)); }
int      advSetCenterFreq(void* dev, uint32_t freq) {
    return rtlsdr_set_center_freq(reinterpret_cast<rtlsdr_dev_t*>(dev), freq);
}
uint32_t advGetCenterFreq(void* dev) {
    return rtlsdr_get_center_freq(reinterpret_cast<rtlsdr_dev_t*>(dev));
}
int      advSetSampleRate(void* dev, uint32_t rate) {
    return rtlsdr_set_sample_rate(reinterpret_cast<rtlsdr_dev_t*>(dev), rate);
}
int      advSetTunerBandwidth(void* dev, int bw) {
    return rtlsdr_set_tuner_bandwidth(reinterpret_cast<rtlsdr_dev_t*>(dev), bw);
}
int      advSetTunerGainMode(void* dev, int manual) {
    return rtlsdr_set_tuner_gain_mode(reinterpret_cast<rtlsdr_dev_t*>(dev), manual);
}
int      advSetTunerGain(void* dev, int gain) {
    return rtlsdr_set_tuner_gain(reinterpret_cast<rtlsdr_dev_t*>(dev), gain);
}
int      advGetTunerGains(void* dev, int* table) {
    return rtlsdr_get_tuner_gains(reinterpret_cast<rtlsdr_dev_t*>(dev), table);
}
int      advGetTunerGain(void* dev) {
    return rtlsdr_get_tuner_gain(reinterpret_cast<rtlsdr_dev_t*>(dev));
}
int      advSetAgcMode(void* dev, int on) {
    return rtlsdr_set_agc_mode(reinterpret_cast<rtlsdr_dev_t*>(dev), on);
}
int      advSetDirectSampling(void* dev, int mode) {
    return rtlsdr_set_direct_sampling(reinterpret_cast<rtlsdr_dev_t*>(dev), mode);
}
int      advSetOffsetTuning(void* dev, int on) {
    return rtlsdr_set_offset_tuning(reinterpret_cast<rtlsdr_dev_t*>(dev), on);
}
int      advSetBiasTee(void* dev, int on) {
    return rtlsdr_set_bias_tee(reinterpret_cast<rtlsdr_dev_t*>(dev), on);
}
int      advSetFreqCorrection(void* dev, int ppm) {
    return rtlsdr_set_freq_correction(reinterpret_cast<rtlsdr_dev_t*>(dev), ppm);
}
int      advResetBuffer(void* dev) {
    return rtlsdr_reset_buffer(reinterpret_cast<rtlsdr_dev_t*>(dev));
}
void     advCancelAsync(void* dev) {
    rtlsdr_cancel_async(reinterpret_cast<rtlsdr_dev_t*>(dev));
}
int      advReadSync(void* dev, unsigned char* buf, uint32_t len, uint32_t* nRead) {
    int n = 0;
    const int rc = rtlsdr_read_sync(reinterpret_cast<rtlsdr_dev_t*>(dev), buf,
                                    static_cast<int>(len), &n);
    if (nRead) *nRead = static_cast<uint32_t>(n);
    return rc;
}

const RtlLibOps g_realOps{
    &advOpen,             &advClose,
    &advSetCenterFreq,    &advGetCenterFreq,
    &advSetSampleRate,    &advSetTunerBandwidth,
    &advSetTunerGainMode, &advSetTunerGain,
    &advGetTunerGains,   &advGetTunerGain,
    &advSetAgcMode,       &advSetDirectSampling,
    &advSetOffsetTuning,  &advSetBiasTee,
    &advSetFreqCorrection,&advResetBuffer,
    &advCancelAsync,      &advReadSync
};
#endif  // HAVE_RTLSDR

} // namespace

const RtlLibOps* rtlLibOps() {
    if (g_testOps) return g_testOps;   // test double wins (never set in production)
#ifdef HAVE_RTLSDR
    return &g_realOps;
#else
    return nullptr;                    // no librtlsdr: graceful stub
#endif
}

void rtlSetLibOpsForTesting(const RtlLibOps* ops) { g_testOps = ops; }

bool RtlSdrSource::pushCenterFreq(double freqHz) {
    const RtlLibOps* ops = rtlLibOps();
    tuneConverged_ = false;
    tuneAttemptsLast_ = 0;
    if (!ops || !dev_ || !ops->setCenterFreq || !ops->getCenterFreq) return false;

    // SDR++-style PLL write-loss defence (clean-room re-derivation of
    // main.cpp:344-359): retry the write, and after each write read the
    // frequency the hardware actually parked at. A write that returns "ok"
    // but reads back something else IS the failure we defend against --
    // looping here is what keeps the UI from lying about the tuned frequency.
    const uint32_t target = static_cast<uint32_t>(freqHz);
    int lastRc = -1;
    uint32_t lastReadback = 0;
    for (int attempt = 1; attempt <= kRtlMaxTuneAttempts; ++attempt) {
        tuneAttemptsLast_ = attempt;
        lastRc = ops->setCenterFreq(dev_, target);
        if (lastRc == 0) {
            lastReadback = ops->getCenterFreq(dev_);
            if (lastReadback == target) {
                tuneConverged_ = true;
                if (attempt > 1)
                    qInfo() << "[RtlSdrSource] tune to" << target << "Hz converged after"
                            << (attempt - 1) << "retries";
                return true;
            }
            // rc ok but PLL parked elsewhere -> retry; do NOT mask it as done.
        }
    }
    qWarning() << "[RtlSdrSource] tune to" << target << "Hz FAILED after"
               << kRtlMaxTuneAttempts << "attempts (last rc=" << lastRc
               << "readback=" << lastReadback << "Hz); request kept but hardware"
                  "is NOT at the requested frequency";
    return false;
}

bool RtlSdrSource::start() {
    const RtlLibOps* ops = rtlLibOps();
    if (!ops || !ops->open) {
        qInfo() << "[RtlSdrSource] no librtlsdr ops bound; stub start() returns false";
        return false;
    }

    void* dev = nullptr;
    if (ops->open(&dev, 0) < 0 || !dev) {
        qWarning() << "[RtlSdrSource] open failed (no device? falling back to test signal)";
        dev_ = nullptr;
        return false;
    }
    dev_ = dev;

    // Order matters: center freq / sample rate first, then tuner mode, then
    // the optional front-end features, finally reset the endpoint buffer.
    checkRtl("set_sample_rate", ops->setSampleRate(dev_, static_cast<uint32_t>(fs_)));
    // Center freq goes through the retry+readback path (was a blind single
    // write): if it does not converge here we already warned loudly.
    pushCenterFreq(f0_);
    checkRtl("set_tuner_gain_mode", ops->setTunerGainMode(dev_, tunerAgc_ ? 0 : 1));
    if (!tunerAgc_)
        checkRtl("set_tuner_gain", ops->setTunerGain(dev_, static_cast<int>(gainDb_ * 10.0)));
    checkRtl("set_agc_mode", ops->setAgcMode(dev_, rtlAgc_ ? 1 : 0));
    checkRtl("set_direct_sampling", ops->setDirectSampling(dev_, directSampling_));
    checkRtl("set_offset_tuning", ops->setOffsetTuning(dev_, offsetTuning_ ? 1 : 0));
    checkRtl("set_bias_tee", ops->setBiasTee(dev_, biasTee_ ? 1 : 0));
    // Zero correction is the default and needs no call (FC0012 returns -2
    // here even though nothing is being corrected). Non-zero ppm is applied
    // live by setPpm() and its result is reported honestly.
    if (ppm_ != 0)
        checkRtl("set_freq_correction", ops->setFreqCorrection(dev_, static_cast<int>(ppm_)));
    // Explicit tuner bandwidth = 0: the driver selects the bandwidth matching
    // the sample rate (SDR++ main.cpp:310). Without this call the intent was an
    // implicit librtlsdr default -- fine today, but it would drift the day a
    // non-RTL backend is wired in. One call fixes the contract.
    checkRtl("set_tuner_bandwidth", ops->setTunerBandwidth(dev_, 0));

    // ---- Discrete gain table (learned from the driver) -------------------
    // getTunerGains(dev, NULL) returns the count; a second call with a buffer
    // fills the legal steps in tenths of dB (R82xx 29, E4000 14, ...).
    // We snap the stored continuous gain to the nearest legal step so the UI
    // never shows "20 dB" while the driver actually parked at 19.7 dB.
    // 「真机待验」: the real table comes from hardware; the snap logic itself is
    // unit-tested offline with an injected table.
    if (ops->getTunerGains) {
        const int nGains = ops->getTunerGains(dev_, nullptr);
        if (nGains > 0) {
            std::vector<int> table(static_cast<std::size_t>(nGains), 0);
            const int filled = ops->getTunerGains(dev_, table.data());
            if (filled > 0) {
                table.resize(static_cast<std::size_t>(filled));
                gainTable_.setTable(std::move(table));
                // Re-snap the stored gain now that the table is known, then push
                // the real stepped value (readback below for honest reporting).
                const double snapped = gainTable_.snap(gainDb_);
                gainDb_ = snapped;
                gainTable_.setActualGainDb(snapped);
                if (!tunerAgc_)
                    checkRtl("set_tuner_gain (snap)",
                             ops->setTunerGain(dev_, static_cast<int>(snapped * 10.0)));
                // Read back the level the driver actually accepted.
                if (ops->getTunerGain) {
                    const int actualDb10 = ops->getTunerGain(dev_);
                    if (actualDb10 > 0) {
                        const double actual = actualDb10 / 10.0;
                        gainDb_ = actual;
                        gainTable_.setActualGainDb(actual);
                    }
                }
            } else {
                qWarning() << "[RtlSdrSource] get_tuner_gains filled 0;"
                              "gain table empty (passthrough)";
            }
        } else {
            qWarning() << "[RtlSdrSource] get_tuner_gains returned" << nGains
                       << "; no discrete table (passthrough)";
        }
    }

    ops->resetBuffer(dev_);
    running_ = true;
    readWatchdog_.reset();
    qInfo() << "[RtlSdrSource] opened device 0, freq=" << f0_ << "Hz sr=" << fs_ << "Hz"
            << "gainTableSteps=" << gainTable_.size()
            << "actualGainDb=" << gainTable_.actualGainDb()
            << rtlOptionsSummary();
    return true;
}

void RtlSdrSource::stop() {
    const RtlLibOps* ops = rtlLibOps();
    if (dev_ && ops) {
        if (ops->cancelAsync) ops->cancelAsync(dev_);
        if (ops->close) ops->close(dev_);
        dev_ = nullptr;
    }
    running_ = false;
    readWatchdog_.reset();
}

std::size_t RtlSdrSource::readIQ(std::vector<std::complex<float>>& out) {
    const RtlLibOps* ops = rtlLibOps();
    if (!ops || !ops->readSync || !dev_ || !running_) return 0;
    const std::size_t n = out.size();
    if (n == 0) return 0;

    // rtlsdr_read_sync returns interleaved uint8 I/Q.
    std::vector<unsigned char> raw(n * 2);
    uint32_t nRead = 0;
    int r = ops->readSync(dev_, raw.data(), static_cast<uint32_t>(n * 2), &nRead);
    if (r < 0 || nRead == 0) {
        // Consecutive read failure: feed the watchdog. Once it latches DEAD we
        // close the device so isConnected() flips false honestly (no more
        // silent zero reads with a stale dev_). The engine's own zero-read
        // detector sees the 0 return and falls back / attempts reconnect.
        if (readWatchdog_.onRead(false)) {
            qWarning() << "[RtlSdrSource] read stream dead after"
                       << readWatchdog_.failureCount()
                       << "consecutive failures; closing device (isConnected=false)";
            if (ops->cancelAsync) ops->cancelAsync(dev_);
            if (ops->close) ops->close(dev_);
            dev_ = nullptr;
            running_ = false;
        }
        return 0;
    }

    readWatchdog_.onRead(true);
    const std::size_t samples = static_cast<std::size_t>(nRead) / 2;
    for (std::size_t i = 0; i < samples; ++i) {
        const float I = (static_cast<float>(raw[2*i])     - 127.5f) / 127.5f;
        const float Q = (static_cast<float>(raw[2*i + 1]) - 127.5f) / 127.5f;
        out[i] = std::complex<float>(I, Q);
    }
    return samples;
}

void RtlSdrSource::setCenterFreq(double freqHz) {
    f0_ = freqHz;   // always persist: start() replays it
    if (!dev_) return;   // not open yet: remembered, pushed by start()
    // Runtime tune with retry + readback. On exhaustion pushCenterFreq already
    // warned loudly and tuneConverged_ stays false; the request (f0_) is kept
    // so the next tune / start retries it. Nothing here pretends success.
    pushCenterFreq(freqHz);
}

void RtlSdrSource::setSampleRate(double rateHz) {
    fs_ = rateHz;
    const RtlLibOps* ops = rtlLibOps();
    if (dev_ && ops && ops->setSampleRate)
        ops->setSampleRate(dev_, static_cast<uint32_t>(fs_));
}

QString RtlSdrSource::name() const {
    if (dev_) return QStringLiteral("RTL-SDR");
    return rtlLibOps() ? QStringLiteral("RTL-SDR (no device)")
                       : QStringLiteral("RTL-SDR (unsupported)");
}

bool RtlSdrSource::isConnected() const { return dev_ != nullptr; }

} // namespace dsp
} // namespace mbdsdr
