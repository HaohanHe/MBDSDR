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
    emit sourceChanged(source_->name(), source_->isConnected());

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
    if (demodMode_ == "AM")  demod_ = std::make_unique<DemodAM>(48000, bandwidth_);
    else if (demodMode_ == "WFM") demod_ = std::make_unique<DemodWFM>();
    else if (demodMode_ == "USB") demod_ = std::make_unique<DemodSSB>(DemodSSB::Sideband::USB, 48000, bandwidth_);
    else if (demodMode_ == "LSB") demod_ = std::make_unique<DemodSSB>(DemodSSB::Sideband::LSB, 48000, bandwidth_);
    else if (demodMode_ == "CW") demod_ = std::make_unique<DemodSSB>(DemodSSB::Sideband::LSB, 48000, bandwidth_);
    else demod_ = std::make_unique<DemodNFM>(48000, bandwidth_);
    frontend_.reset();
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
    if (demod_) demod_->setBandwidth(hz);
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

    while (running_.load()) {
        int n = fftSize_.load();
        if (static_cast<int>(iq.size()) != n) iq.resize(static_cast<size_t>(n));

        QMutexLocker lk(&sourceMutex_);

        if (needDemodReset_.load()) { rebuildDemod(); needDemodReset_.store(false); }

        std::size_t got = source_->readIQ(iq);
        if (got == 0) {
            lk.unlock();
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            continue;
        }

        frontend_.process(iq);

        // Record raw IQ if recording
        if (recorder_.isRecording()) recorder_.writeIQ(iq);

        // Spectrum
        SpectrumFrame frame;
        frame.dbfs.resize(static_cast<std::size_t>(n));
        powerSpectrumDbfs(iq, frame.dbfs);
        frame.centerFreqHz = source_->centerFreq();
        frame.sampleRateHz = source_->sampleRate();
        frame.fftSize = n;
        frame.isTestSignal = !source_->isConnected();
        frame.sourceName = source_->name();
        emit spectrumReady(frame);

        // Demod chain
        auto audio = demod_->process(iq);
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

        audioOut_->write(out, demod_->outputSampleRate());

        // Gated recording
        gatedRec_.feed(out, gate);

        // CW decode
        if (demodMode_ == "CW") {
            cwDecoder_.feed(out);
            QString text = cwDecoder_.takeText();
            if (!text.isEmpty()) emit cwDecoded(text, cwDecoder_.wpm());
        }

        // ADS-B decode
        if (demodMode_ == "ADS-B") {
            adsbDecoder_.feed(iq);
            for (const auto& ac : adsbDecoder_.takeNewAircraft())
                emit adsbAircraft(ac);
        }

        lk.unlock();
        std::this_thread::sleep_for(std::chrono::milliseconds(33));
    }
}

} // namespace dsp
} // namespace mbdsdr
