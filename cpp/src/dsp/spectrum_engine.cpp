// SPDX-License-Identifier: MIT
#include "spectrum_engine.h"
#include "rtl_sdr_source.h"
#include "rtl_tcp_source.h"
#include "test_signal.h"
#include "power_spectrum.h"
#include "noise_blanker.h"

#include <QDebug>
#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <algorithm>
#include <cmath>
#include <chrono>
#include <thread>

namespace mbdsdr {
namespace dsp {

// Capture-band edge roll-off for the in-band IF-offset move. A VFO may ride on
// the channelizer NCO (tuner left parked) only while it sits inside the central
// fraction of the band; once it approaches the tuner edge (where the passband
// rolls off) we genuinely retune the source to the target so the VFO lands back
// near capture center (offset ~0).
namespace {
constexpr double kVfoEdgeFraction = 0.85;
}

SpectrumEngine::SpectrumEngine(QObject* parent) : QThread(parent) {
    auto rtl = std::make_unique<RtlSdrSource>();
    if (rtl->start()) {
        source_ = std::move(rtl);
    } else {
        source_ = std::make_unique<TestSignalSource>();
        source_->start();
    }
    // NOTE: do NOT emit sourceChanged() here. The engine is being constructed
    // before MainWindow's connect() calls exist, so the signal would be lost.
    // The initial state is emitted once in run(), after connections are wired.

    audioOut_ = new AudioOutput(this);
    audioSink_ = audioOut_;   // default: real device playback
    gatedRec_.setOutputDir("recordings");
    telemetryClock_.start();
    // Single default VFO at the source center, NFM 12.5 kHz -- identical to
    // the legacy single-channel receiver on first boot.
    vfoManager_.initDefault(source_->sampleRate(), source_->centerFreq(),
                            demodMode_, bandwidth_);
    rebuildDemod();
}

SpectrumEngine::~SpectrumEngine() {
    shutdown();
    wait();
    if (recorder_.isRecording()) recorder_.stop();
    if (wavWriter_.isRecording()) wavWriter_.stop();
    gatedRec_.flush();
    if (source_) source_->stop();
}

void SpectrumEngine::rebuildDemod() {
    // Per-channel channelizer/demod/resampler are owned and rebuilt lazily by
    // vfoManager_ (one channel per VFO, mirroring the legacy single-channel
    // chain). Here we only reset the SHARED downstream blocks and re-sync the
    // cached mode used for the test-signal generator + recording template.
    vfoManager_.sourceRateChanged();   // input rate may have changed -> all channels rebuild

    frontend_.reset();
    squelch_.reset();
    agc_.reset();
    cwDecoder_.reset();
    adsbDecoder_.reset();

    // Keep the cached mode/bandwidth in lock-step with the selected VFO so the
    // legacy demodMode()/bandwidth() getters and expandRecTemplate() are correct.
    if (const VfoChannel* sel = vfoManager_.selected()) {
        demodMode_ = sel->mode;
        bandwidth_ = sel->bandwidthHz;
    }

    if (auto* ts = dynamic_cast<TestSignalSource*>(source_.get())) {
        if (demodMode_ == "AM") ts->setModulation("am");
        else if (demodMode_ == "NFM" || demodMode_ == "WFM") ts->setModulation("fm");
        else ts->setModulation("tone");
    }
}

void SpectrumEngine::setFftSize(int n) {
    if (n == 1024 || n == 2048 || n == 4096) fftSize_.store(n);
}
void SpectrumEngine::shutdown() { running_.store(false); }

double SpectrumEngine::centerFreq() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return source_ ? source_->centerFreq() : 0.0;
}

void SpectrumEngine::onSetCenterFreq(double f) {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setCenterFreq(f);
    // Tuning the receiver moves the SELECTED VFO with it (kept at offset 0),
    // exactly like the legacy single-channel receiver. Other VFOs keep their
    // absolute frequencies and just see a different relative offset.
    if (VfoChannel* sel = vfoManager_.selected()) {
        sel->freqHz = f;
    }
    emit vfoListChanged();
}
void SpectrumEngine::onSetSampleRate(double r) {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setSampleRate(r);
    // Sample rate feeds every channelizer; rebuild all channels on next loop.
    vfoManager_.sourceRateChanged();
    needDemodReset_.store(true);
}
void SpectrumEngine::onSetGain(double g) {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setGain(g);
}
void SpectrumEngine::setDemodMode(const QString& m) {
    QMutexLocker lk(&sourceMutex_);
    // Drive the SELECTED VFO's mode (which also resets its bandwidth to the
    // mode default), keeping the legacy demodMode_/bandwidth_ caches in sync.
    vfoManager_.setMode(vfoManager_.selectedId(), m);
    if (const VfoChannel* sel = vfoManager_.selected()) {
        demodMode_ = sel->mode;
        bandwidth_ = sel->bandwidthHz;
    }
    needDemodReset_.store(true);
    emit vfoListChanged();
}
void SpectrumEngine::setSquelchThreshold(float db) { squelch_.setThresholdDb(db); }
void SpectrumEngine::setSquelchEnabled(bool e) { squelch_.setEnabled(e); }

double SpectrumEngine::scanBand(double lowHz, double highHz, double stepHz) {
    double peakDb = -200.0;
    for (double f = lowHz; f <= highHz; f += stepHz) {
        std::vector<std::complex<float>> iq(1024);
        {
            QMutexLocker lk(&sourceMutex_);
            source_->setCenterFreq(f);
            source_->readIQ(iq);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        double rms = 0;
        for (auto c : iq) rms += std::norm(c);
        rms = 10 * std::log10(rms / iq.size() + 1e-10);
        if (rms > peakDb) peakDb = rms;
    }
    return peakDb;
}

bool SpectrumEngine::tryConnectRtl() {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->stop();
    auto rtl = std::make_unique<RtlSdrSource>();
    // Replay cached front-end options onto the fresh device BEFORE start().
    // start() applies stored members to the hardware in the correct order.
    rtl->setDirectSampling(cachedDirectSampling_);
    rtl->setOffsetTuning(cachedOffsetTuning_);
    rtl->setRtlAgc(cachedRtlAgc_);
    rtl->setTunerAgc(cachedTunerAgc_);
    rtl->setBiasTee(cachedBiasTee_);
    rtl->setPpm(cachedPpm_);
    if (rtl->start()) {
        source_ = std::move(rtl);
        emit sourceChanged("RTL-SDR", true);
        return true;
    }
    source_ = std::make_unique<TestSignalSource>();
    source_->start();
    emit sourceChanged("Test Signal", false);
    return false;
}

void SpectrumEngine::disconnectSource() {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->stop();
    source_ = std::make_unique<TestSignalSource>();
    source_->start();
    emit sourceChanged("Test Signal", false);
}

bool SpectrumEngine::connectRtlTcp(const QString& host, quint16 port) {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->stop();
    auto tcp = std::make_unique<RtlTcpSource>(host, port);
    if (tcp->start()) {
        source_ = std::move(tcp);
        emit sourceChanged(QString("rtl_tcp %1:%2").arg(host).arg(port), true);
        return true;
    }
    // Honest failure: fall back to test signal, no fake IQ over the wire.
    source_ = std::make_unique<TestSignalSource>();
    source_->start();
    emit sourceChanged("Test Signal", false);
    return false;
}

void SpectrumEngine::setMuted(bool m) {
    if (audioSink_) audioSink_->setMuted(m);
}

void SpectrumEngine::setTestAudioSink(std::unique_ptr<IAudioSink> sink) {
    // Swap the DSP write path. The settings-dialog handle (audioOut_) and its
    // Qt device hot-swap are untouched; only where run() writes demod audio
    // changes. nullptr restores the real device sink.
    testSink_ = std::move(sink);
    audioSink_ = testSink_ ? testSink_.get() : static_cast<IAudioSink*>(audioOut_);
}

void SpectrumEngine::setBandwidth(double hz) {
    QMutexLocker lk(&sourceMutex_);
    bandwidth_ = hz;
    vfoManager_.setBandwidth(vfoManager_.selectedId(), hz);
    // The channel filter cutoff depends on bandwidth; rebuild on next loop.
    needDemodReset_.store(true);
    emit vfoListChanged();
}

QString SpectrumEngine::expandRecTemplate() const {
    // Called with sourceMutex_ held (source_/VFO state stable).
    const QString stamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    // Tag the recording with the SELECTED VFO's absolute frequency + mode.
    double freqHz = source_ ? source_->centerFreq() : 0.0;
    QString mode = demodMode_;
    if (const VfoChannel* sel = vfoManager_.selected()) {
        freqHz = sel->freqHz;
        mode = sel->mode;
    }
    const double freqMhz = freqHz / 1e6;
    const QString freqStr = QString::number(freqMhz, 'f', 3);
    return QString(recTemplate_)
        .replace("{time}", stamp)
        .replace("{freq}", freqStr)
        .replace("{mode}", mode);
}

bool SpectrumEngine::hasData() const {
    // A live producer exists iff source_ is set AND it is either real hardware
    // or the offline test-signal source (which synthesizes its own IQ).
    if (!source_) return false;
    if (source_->isConnected()) return true;
    return dynamic_cast<TestSignalSource*>(source_.get()) != nullptr;
}

bool SpectrumEngine::startRecording() {
    if (recorder_.isRecording() || wavWriter_.isRecording()) return false;
    QMutexLocker lk(&sourceMutex_);
    if (!hasData()) return false;

    QDir().mkpath("recordings");
    const QString base = "recordings/" + expandRecTemplate();

    if (recTarget_ == RecTarget::BasebandIQ) {
        if (!recorder_.startWithBase(base, source_->sampleRate(),
                                     source_->centerFreq(), source_->gain(),
                                     source_->name())) {
            return false;
        }
        recCurrentPath_ = recorder_.currentFilePath();
        emit recordingStateChanged(true, recCurrentPath_);
    } else {
        const int channels = recStereo_ ? 2 : 1;
        if (!wavWriter_.start(base + ".wav", 48000.0, channels)) {
            return false;
        }
        recCurrentPath_ = wavWriter_.currentFilePath();
        // Capture the readback metadata for the sidecar JSON written at stop.
        // center/gain come from the live source (hardware actual values), not the
        // UI requests. mode = selected VFO demod.
        wavSidecarPath_ = base + ".json";
        wavSidecarCenterHz_ = source_->centerFreq();
        wavSidecarGainDb_ = source_->gain();
        wavSidecarHardware_ = source_->name();
        wavSidecarMode_ = demodMode_;
        emit recordingStateChanged(true, recCurrentPath_);
    }
    // Start the wall-clock used for the 1 Hz REC progress tick.
    recClock_.restart();
    lastRecSecond_ = -1;
    return true;
}

void SpectrumEngine::stopRecording() {
    if (recorder_.isRecording()) {
        const QString path = recorder_.currentFilePath();
        recorder_.stop();
        emit recordingStateChanged(false, path);
    } else if (wavWriter_.isRecording()) {
        const QString path = wavWriter_.currentFilePath();
        wavWriter_.stop();
        // Write the sidecar metadata JSON now that the WAV is finalized. Fields
        // mirror the SigMF recorder so demod-audio captures carry the same proof.
        if (!wavSidecarPath_.isEmpty()) {
            QJsonObject meta;
            meta["type"] = "mbdsdr-audio-recording";
            meta["sample_rate"] = 48000.0;
            meta["center_freq"] = wavSidecarCenterHz_;
            meta["gain"] = wavSidecarGainDb_;
            meta["hardware"] = wavSidecarHardware_;
            meta["mode"] = wavSidecarMode_;
            meta["datetime"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
            QFile f(wavSidecarPath_);
            if (f.open(QIODevice::WriteOnly)) {
                f.write(QJsonDocument(meta).toJson(QJsonDocument::Indented));
            }
            wavSidecarPath_.clear();
        }
        emit recordingStateChanged(false, path);
    }
}
void SpectrumEngine::setGatedRecordingEnabled(bool e) {
    gatedRec_.setEnabled(e);
}

// ---- Multi-VFO management --------------------------------------------------
void SpectrumEngine::vfoAdd() {
    QMutexLocker lk(&sourceMutex_);
    const double center = source_ ? source_->centerFreq() : 0.0;
    const int id = vfoManager_.addVfo(center);
    if (const VfoChannel* sel = vfoManager_.selected()) {
        demodMode_ = sel->mode;
        bandwidth_ = sel->bandwidthHz;
    }
    emit vfoListChanged();
    (void)id;
}

void SpectrumEngine::vfoRemove(int id) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.removeVfo(id);
    if (const VfoChannel* sel = vfoManager_.selected()) {
        demodMode_ = sel->mode;
        bandwidth_ = sel->bandwidthHz;
    }
    emit vfoListChanged();
}

void SpectrumEngine::vfoSelect(int id) {
    QMutexLocker lk(&sourceMutex_);
    if (vfoManager_.selectVfo(id)) {
        if (const VfoChannel* sel = vfoManager_.selected()) {
            demodMode_ = sel->mode;
            bandwidth_ = sel->bandwidthHz;
        }
        emit vfoListChanged();
    }
}

void SpectrumEngine::vfoSetFreq(int id, double hz) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.setFreq(id, hz);
    emit vfoListChanged();
}

bool SpectrumEngine::vfoSetOffset(int id, double targetHz) {
    QMutexLocker lk(&sourceMutex_);
    if (!source_) return false;
    const double center = source_->centerFreq();
    const double sr = source_->sampleRate();
    // Usable half-bandwidth: central fraction of the capture before the tuner's
    // edge roll-off. In-band moves stay inside +/-this and never touch the tuner.
    const double usableHalf = sr * 0.5 * kVfoEdgeFraction;

    bool retuned = false;
    if (std::abs(targetHz - center) > usableHalf) {
        // The target would fall outside the usable capture band. Genuinely move
        // the source tuner to the target so this VFO lands back near offset 0.
        // Every OTHER VFO keeps its absolute freqHz; their channelizer NCO
        // offsets (= freqHz - new center) are recomputed continuously by
        // VfoManager::process() on the next run() frame.
        source_->setCenterFreq(targetHz);
        retuned = true;
    }
    // In-band move (or just after a retune): only record this VFO's absolute
    // frequency. The channelizer NCO offset is applied by process() as
    // (freqHz - sourceCenterHz). This is the SDR++ in-band IF-offset path: the
    // RTL tuner is NOT retuned while the VFO stays inside the capture band.
    vfoManager_.setFreq(id, targetHz);
    emit vfoListChanged();
    return retuned;
}

void SpectrumEngine::vfoSetBandwidth(int id, double hz) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.setBandwidth(id, hz);
    if (vfoManager_.selectedId() == id) bandwidth_ = hz;
    emit vfoListChanged();
}

void SpectrumEngine::vfoSetMode(int id, const QString& mode) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.setMode(id, mode);
    if (vfoManager_.selectedId() == id) {
        demodMode_ = mode;
        if (const VfoChannel* sel = vfoManager_.selected()) bandwidth_ = sel->bandwidthHz;
    }
    emit vfoListChanged();
}

void SpectrumEngine::vfoSetColor(int id, const QColor& c) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.setColor(id, c);
    emit vfoListChanged();
}

void SpectrumEngine::setAnrEnabled(bool on) {
    QMutexLocker lk(&sourceMutex_);
    anr_.setEnabled(on);
}

void SpectrumEngine::setAnrStrength(float s) {
    QMutexLocker lk(&sourceMutex_);
    anr_.setStrength(s);
}

QVector<VfoMarker> SpectrumEngine::vfoMarkers() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    QVector<VfoMarker> out = vfoManager_.markers();
    // Backfill the IF-offset bookkeeping against the CURRENT source capture
    // center. This is the snapshot the UI draws: referenceHz = capture center,
    // centerOffsetHz = this VFO's signed IF offset (freqHz - capture center).
    const double center = source_ ? source_->centerFreq() : 0.0;
    for (auto& m : out) {
        m.referenceHz = center;
        m.centerOffsetHz = m.freqHz - center;
    }
    return out;
}

int SpectrumEngine::selectedVfoId() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return vfoManager_.selectedId();
}

void SpectrumEngine::setDirectSampling(int mode) {
    cachedDirectSampling_ = mode;
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setDirectSampling(mode);
}
void SpectrumEngine::setOffsetTuning(bool on) {
    cachedOffsetTuning_ = on;
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setOffsetTuning(on);
}
void SpectrumEngine::setRtlAgc(bool on) {
    cachedRtlAgc_ = on;
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setRtlAgc(on);
}
void SpectrumEngine::setTunerAgc(bool on) {
    cachedTunerAgc_ = on;
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setTunerAgc(on);
}
void SpectrumEngine::setBiasTee(bool on) {
    cachedBiasTee_ = on;
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setBiasTee(on);
}
void SpectrumEngine::setPpm(double ppm) {
    cachedPpm_ = ppm;
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setPpm(ppm);
}

void SpectrumEngine::setWindowType(int w) {
    powerSpectrum_.setWindow(static_cast<PowerSpectrum::Window>(
        std::clamp(w, 0, 2)));
}
void SpectrumEngine::setAverageMode(int a) {
    powerSpectrum_.setAverage(static_cast<PowerSpectrum::Average>(
        std::clamp(a, 0, 2)));
}
void SpectrumEngine::setNoiseBlanker(bool on) {
    noiseBlanker_.setEnabled(on);
}

bool SpectrumEngine::noiseBlankerEnabled() const { return noiseBlanker_.enabled(); }
int SpectrumEngine::windowType() const { return static_cast<int>(powerSpectrum_.window()); }
int SpectrumEngine::averageMode() const { return static_cast<int>(powerSpectrum_.average()); }

void SpectrumEngine::run() {
    std::vector<std::complex<float>> iq;
    iq.resize(static_cast<std::size_t>(fftSize_.load()));

    // Connections now exist on the UI side; report the real source state.
    {
        QMutexLocker lk(&sourceMutex_);
        emit sourceChanged(source_->name(), source_->isConnected());
    }

    while (running_.load()) {
        int n = fftSize_.load();

        QMutexLocker lk(&sourceMutex_);

        if (needDemodReset_.load()) { rebuildDemod(); needDemodReset_.store(false); }

        const bool real = source_->isConnected();
        const double sr = source_->sampleRate();
        const int D = std::max(1, vfoManager_.maxDecimation());

        // Real sources: blocking reads pace to wall-clock. Synthetic sources have
        // no hardware clock, but we still read ~25 ms of simulated time per loop
        // (aligned to a whole number of decimation branches) so the offline
        // demod chain advances at near wall-clock speed and loops like the
        // Costas/timing PLL converge within a few seconds instead of needing
        // tens of seconds of wall-clock for ~1.3 s of simulated audio. The 33 ms
        // sleep below still paces the loop. The spectrum FFT always uses only
        // the first n samples (unchanged).
        std::size_t wantN;
        {
            long want = static_cast<long>(std::round(sr * 0.025));
            want = ((want + D - 1) / D) * D;
            wantN = static_cast<std::size_t>(std::max<long>(want, n));
        }        if (iq.size() != wantN) iq.resize(wantN);

        std::size_t got = source_->readIQ(iq);
        if (got == 0) {
            lk.unlock();
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
            continue;
        }
        if (got < iq.size()) iq.resize(got);

        noiseBlanker_.process(iq);
        frontend_.process(iq);

        // Record raw IQ if recording
        if (recorder_.isRecording()) recorder_.writeIQ(iq);

        // Spectrum (computed from an FFT-sized window of the block).
        std::size_t specN = std::min<std::size_t>(n, iq.size());
        std::vector<std::complex<float>> spec(iq.begin(), iq.begin() + specN);
        SpectrumFrame frame;
        frame.dbfs.resize(specN);
        powerSpectrum_.process(spec, frame.dbfs);
        frame.centerFreqHz = source_->centerFreq();
        frame.sampleRateHz = source_->sampleRate();
        frame.fftSize = static_cast<int>(specN);
        frame.isTestSignal = !real;
        frame.sourceName = source_->name();
        emit spectrumReady(frame);

        // Fan the source IQ out to every VFO channel (each with its own
        // channelizer + demod + resampler state), then take the SELECTED VFO's
        // 48 kHz audio into the shared downstream.
        const double centerNow = source_->centerFreq();
        const std::vector<float>& raw = vfoManager_.process(iq, sr, centerNow);
        const VfoChannel* sel = vfoManager_.selected();
        const QString selMode = sel ? sel->mode : demodMode_;
        const bool digital = VfoChannel::modeIsDigital(selMode);

        // Keep the offline test source emitting the right kind of IQ for the
        // selected VFO (am/fm/tone/bpsk/qpsk). Real hardware ignores this.
        if (auto* ts = dynamic_cast<TestSignalSource*>(source_.get())) {
            QString want;
            if (selMode == "AM") want = "am";
            else if (selMode == "NFM" || selMode == "WFM") want = "fm";
            else if (selMode == "BPSK") want = "bpsk";
            else if (selMode == "QPSK") want = "qpsk";
            else want = "tone";
            if (want != ts->modulation()) ts->setModulation(want);
        }

        // RSSI is always reported from raw capture energy.
        double rssi = 0;
        for (auto c : iq) rssi += std::norm(c);
        rssi = 10 * std::log10(rssi / iq.size() + 1e-10);
        emit rssiLevel(static_cast<float>(rssi));

        // Real measured noise floor -> SNR. The median per-bin level of the power
        // spectrum is the noise density (peaks don't lift the median); Parseval
        // scales it to the total-power domain used by RSSI. A slow exponential
        // average tracks the floor so transients don't move it. All values come
        // from the real frame.dbfs / real IQ -- nothing fabricated.
        if (!frame.dbfs.empty()) {
            std::vector<float> sorted = frame.dbfs;
            std::sort(sorted.begin(), sorted.end());
            const float medDb = sorted[sorted.size() / 2];
            const double medLin = std::pow(10.0, medDb / 10.0);
            // Per-bin scaled power summed over all bins ~= total capture power.
            const double noiseTotalLin = medLin * static_cast<double>(sorted.size());
            const double noiseTotalDb = 10.0 * std::log10(noiseTotalLin + 1e-12);
            if (!noiseFloorInit_) {
                noiseFloorTrackDb_ = noiseTotalDb;
                noiseFloorInit_ = true;
            } else {
                noiseFloorTrackDb_ = 0.98 * noiseFloorTrackDb_ + 0.02 * noiseTotalDb;
            }
            emit snrLevel(static_cast<float>(rssi - noiseFloorTrackDb_));
        }

        // ~1 Hz readback of the ACTUAL source state to the status bar. These are
        // the hardware readback values (gain is rounded by the driver), not the
        // UI spinbox requests. The test source reports connected=false and the UI
        // tags it "非硬件" -- never presented as real hardware.
        const qint64 nowMs = telemetryClock_.elapsed();
        if (lastTelemetryMs_ < 0 || nowMs - lastTelemetryMs_ >= 1000) {
            lastTelemetryMs_ = nowMs;
            emit sourceTelemetry(source_->name(), source_->isConnected(),
                                 source_->centerFreq(), source_->sampleRate(),
                                 source_->gain());
        }

        if (digital) {
            // ---- Digital VFO: no analog audio; push constellation symbols ----
            if (sel && !sel->recoveredSymbols.empty())
                emit constellationSymbols(sel->recoveredSymbols, source_->isConnected());
            if (!wasDigital_) wasDigital_ = true;   // entering digital (panel setMode in UI)
            std::vector<float> silence;
            silence.resize(raw.size(), 0.0f);
            audioSink_->write(silence);
            emit squelchState(true);
            emit audioLevel(-60.0f);
            // Gated/wav recording: keep the audio stream continuous but silent.
            if (wavWriter_.isRecording()) wavWriter_.write(silence);
            lk.unlock();
            if (!real) std::this_thread::sleep_for(std::chrono::milliseconds(33));
            continue;
        }

        // Leaving digital mode: tell the panel to return to its empty state once.
        if (wasDigital_) {
            wasDigital_ = false;
            emit constellationCleared();
        }

        // ANR sits AFTER the per-VFO resampler and BEFORE squelch/AGC/gated
        // recorder. Disabled by default -> identity (bit-exact legacy chain).
        const std::vector<float> audio = anr_.process(raw);

        // Squelch decision FIRST (updates smoothing/hangover, does not mute),
        // then AGC always sees the REAL audio (it tracks the noise floor while
        // closed instead of decaying away on zero blocks). We mute only the
        // speaker path. The gated recorder receives the real, un-muted audio so
        // its pre-roll captures the true signal onset.
        const float rms = rmsDbfs(audio);
        const bool gate = squelch_.decide(audio, rms);
        auto leveled = agc_.process(audio);
        std::vector<float> out = leveled;
        if (!gate) std::fill(out.begin(), out.end(), 0.0f);
        emit squelchState(gate);
        emit audioLevel(agc_.currentLevelDb());

        audioSink_->write(out);

        // Gated recording: label the segment with the selected VFO, and feed
        // the REAL (un-muted) audio so pre-roll captures the onset.
        gatedRec_.setContext(selMode, sel ? sel->freqHz : centerNow);
        gatedRec_.feed(leveled, gate);

        // Continuous audio (WAV) recording for the main record button. When
        // recIgnoreSquelch_ is false we normally only capture while the gate is
        // open -- BUT skipping the closed-gate frames would shrink the WAV
        // shorter than the wall-clock recording time. Instead, write a silent
        // frame of the same length as `out` so the 48 kHz sample stream stays
        // continuous and the file duration matches the time spent recording.
        // (Baseband IQ recording above is unaffected: it always writes real IQ.)
        if (wavWriter_.isRecording()) {
            if (recIgnoreSquelch_ || gate) {
                wavWriter_.write(out);
            } else {
                std::vector<float> silence(out.size(), 0.0f);
                wavWriter_.write(silence);
            }
        }

        // 1 Hz REC progress tick: elapsed wall-clock seconds + current file
        // size, so the UI can render "● REC: name (MM:SS, NN KB)".
        if (wavWriter_.isRecording() || recorder_.isRecording()) {
            const int secs = static_cast<int>(recClock_.elapsed() / 1000);
            if (secs != lastRecSecond_) {
                lastRecSecond_ = secs;
                emit recordingProgress(recCurrentPath_, secs,
                                       QFileInfo(recCurrentPath_).size());
            }
        }

        // CW decode (follows the selected VFO mode)
        if (selMode == "CW") {
            cwDecoder_.feed(out);
            QString text = cwDecoder_.takeText();
            if (!text.isEmpty()) emit cwDecoded(text, cwDecoder_.wpm());
        }

        // ADS-B decode (operates on full-rate raw IQ)
        if (selMode == "ADS-B") {
            adsbDecoder_.feed(iq);
            for (const auto& ac : adsbDecoder_.takeNewAircraft())
                emit adsbAircraft(ac);
        }

        // RDS (WFM only): pull the selected channel's accumulated PS/PTY/RadioText.
        // The channel's RdsDecoder is already being fed inside vfoManager_.process();
        // we only diff against the last pushed snapshot so the UI sees an event
        // when the station name / PTY / RadioText changes, not a per-block flood.
        if (selMode == "WFM" && sel && sel->rds) {
            RdsInfo rds = sel->rds->info();
            const bool locked = rds.haveAny;
            const bool changed =
                locked != lastRdsLocked_ ||
                (locked && (rds.programService != lastRdsPs_ ||
                            rds.pty != lastRdsPty_ ||
                            rds.radioText != lastRdsRt_));
            if (changed) {
                lastRdsPs_    = rds.programService;
                lastRdsPty_   = rds.pty;
                lastRdsRt_    = rds.radioText;
                lastRdsLocked_ = locked;
                emit rdsUpdated(rds.programService, rds.pty, rds.radioText, locked);
            }
        }

        lk.unlock();
        // Synthetic sources have no hardware clock; pace them manually. Real
        // blocking reads already run at wall-clock speed and must not sleep.
        if (!real) std::this_thread::sleep_for(std::chrono::milliseconds(33));
    }
}

} // namespace dsp
} // namespace mbdsdr
