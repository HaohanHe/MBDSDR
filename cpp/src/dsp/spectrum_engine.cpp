// SPDX-License-Identifier: MIT
#include "spectrum_engine.h"
#include "rtl_sdr_source.h"
#include "test_signal.h"
#include "power_spectrum.h"

#include <QDebug>
#include <chrono>
#include <thread>

namespace mbdsdr {
namespace dsp {

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
    gatedRec_.setOutputDir("recordings");
    rebuildDemod();
}

SpectrumEngine::~SpectrumEngine() {
    shutdown();
    wait();
    if (recorder_.isRecording()) recorder_.stop();
    gatedRec_.flush();
    if (source_) source_->stop();
}

void SpectrumEngine::rebuildDemod() {
    // SDR++ style channelization: the wide source IQ is shifted to baseband,
    // band-limited by a channel filter and decimated to a per-mode IF rate,
    // THEN demodulated. Demods must never see the full-rate source block.
    const double sr = source_ ? source_->sampleRate() : 2.4e6;

    double ifTarget = 48000.0;   // narrowband modes
    double chBw = bandwidth_;
    if (demodMode_ == "WFM") {
        ifTarget = 240000.0;     // keeps +/-75 kHz deviation
        chBw = 200000.0;
    }
    if (sr < ifTarget) ifTarget = sr;

    channelizer_.configure(sr, ifTarget, chBw, 31);
    const double ifRate = channelizer_.effectiveOutputRateHz();

    if (demodMode_ == "AM")  demod_ = std::make_unique<DemodAM>(ifRate, bandwidth_);
    else if (demodMode_ == "WFM") demod_ = std::make_unique<DemodWFM>(ifRate, chBw);
    else if (demodMode_ == "USB") demod_ = std::make_unique<DemodSSB>(DemodSSB::Sideband::USB, ifRate, bandwidth_);
    else if (demodMode_ == "LSB") demod_ = std::make_unique<DemodSSB>(DemodSSB::Sideband::LSB, ifRate, bandwidth_);
    else if (demodMode_ == "CW") demod_ = std::make_unique<DemodSSB>(DemodSSB::Sideband::LSB, ifRate, bandwidth_);
    else demod_ = std::make_unique<DemodNFM>(ifRate, bandwidth_);

    // Final audio is always presented at a fixed rate.
    audioRes_.configure(ifRate, 48000.0, 31);

    frontend_.reset();
    channelizer_.reset();
    audioRes_.reset();
    squelch_.reset();
    agc_.reset();
    cwDecoder_.reset();
    adsbDecoder_.reset();

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

void SpectrumEngine::onSetCenterFreq(double f) {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setCenterFreq(f);
}
void SpectrumEngine::onSetSampleRate(double r) {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setSampleRate(r);
}
void SpectrumEngine::onSetGain(double g) {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->setGain(g);
}
void SpectrumEngine::setDemodMode(const QString& m) {
    QMutexLocker lk(&sourceMutex_);
    demodMode_ = m;
    if (m == "AM") bandwidth_ = 8000;
    else if (m == "NFM") bandwidth_ = 12500;
    else if (m == "WFM") bandwidth_ = 200000;
    else if (m == "CW") bandwidth_ = 500;
    else bandwidth_ = 2400;
    needDemodReset_.store(true);
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

void SpectrumEngine::setMuted(bool m) {
    if (audioOut_) audioOut_->setMuted(m);
}

void SpectrumEngine::setBandwidth(double hz) {
    bandwidth_ = hz;
    // The channel filter cutoff depends on bandwidth; rebuild the whole chain.
    needDemodReset_.store(true);
}

void SpectrumEngine::startRecording() {
    if (recorder_.isRecording()) return;
    if (recorder_.start("recordings", source_->sampleRate(),
                        source_->centerFreq(), source_->gain(),
                        source_->name())) {
        emit recordingStateChanged(true, recorder_.currentFilePath());
    }
}
void SpectrumEngine::stopRecording() {
    if (!recorder_.isRecording()) return;
    recorder_.stop();
    emit recordingStateChanged(false, "");
}
void SpectrumEngine::setGatedRecordingEnabled(bool e) {
    gatedRec_.setEnabled(e);
}

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
        const int D = std::max(1, channelizer_.decimation());

        // Real sources: read ~25 ms per iteration (rounded to a whole number
        // of decimation branches) so the blocking read paces the loop to real
        // time and the audio sink gets ~25 ms buffers. Synthetic sources have
        // no clock; read one FFT and sleep instead.
        std::size_t wantN;
        if (real) {
            long want = static_cast<long>(std::round(sr * 0.025));
            want = ((want + D - 1) / D) * D;
            wantN = static_cast<std::size_t>(std::max<long>(want, n));
        } else {
            wantN = static_cast<std::size_t>(n);
        }
        if (iq.size() != wantN) iq.resize(wantN);

        std::size_t got = source_->readIQ(iq);
        if (got == 0) {
            lk.unlock();
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
            continue;
        }
        if (got < iq.size()) iq.resize(got);

        frontend_.process(iq);

        // Record raw IQ if recording
        if (recorder_.isRecording()) recorder_.writeIQ(iq);

        // Spectrum (computed from an FFT-sized window of the block).
        std::size_t specN = std::min<std::size_t>(n, iq.size());
        std::vector<std::complex<float>> spec(iq.begin(), iq.begin() + specN);
        SpectrumFrame frame;
        frame.dbfs.resize(specN);
        powerSpectrumDbfs(spec, frame.dbfs);
        frame.centerFreqHz = source_->centerFreq();
        frame.sampleRateHz = source_->sampleRate();
        frame.fftSize = static_cast<int>(specN);
        frame.isTestSignal = !real;
        frame.sourceName = source_->name();
        emit spectrumReady(frame);

        // Channelize (VFO currently tracks source center -> offset 0), then
        // demodulate at the true IF rate, then resample audio to a fixed rate.
        auto baseband = channelizer_.process(iq);
        std::vector<float> audio;
        if (!baseband.empty()) {
            auto aif = demod_->process(baseband);
            audio = audioRes_.process(aif);
        }

        float rms = rmsDbfs(audio);
        bool gate = squelch_.open();
        auto gated = squelch_.apply(audio, rms);
        emit squelchState(gate);
        auto out = agc_.process(gated);
        emit audioLevel(agc_.currentLevelDb());

        double rssi = 0;
        for (auto c : iq) rssi += std::norm(c);
        rssi = 10 * std::log10(rssi / iq.size() + 1e-10);
        emit rssiLevel(static_cast<float>(rssi));

        audioOut_->write(out, 48000.0);

        // Gated recording
        gatedRec_.feed(out, gate);

        // CW decode
        if (demodMode_ == "CW") {
            cwDecoder_.feed(out);
            QString text = cwDecoder_.takeText();
            if (!text.isEmpty()) emit cwDecoded(text, cwDecoder_.wpm());
        }

        // ADS-B decode (operates on full-rate raw IQ)
        if (demodMode_ == "ADS-B") {
            adsbDecoder_.feed(iq);
            for (const auto& ac : adsbDecoder_.takeNewAircraft())
                emit adsbAircraft(ac);
        }

        lk.unlock();
        // Synthetic sources have no hardware clock; pace them manually. Real
        // blocking reads already run at wall-clock speed and must not sleep.
        if (!real) std::this_thread::sleep_for(std::chrono::milliseconds(33));
    }
}

} // namespace dsp
} // namespace mbdsdr
