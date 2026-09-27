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
    rebuildDemod();
}

SpectrumEngine::~SpectrumEngine() {
    shutdown();
    wait();
    if (source_) source_->stop();
}

void SpectrumEngine::rebuildDemod() {
    if (demodMode_ == "AM")  demod_ = std::make_unique<DemodAM>();
    else if (demodMode_ == "WFM") demod_ = std::make_unique<DemodWFM>();
    else if (demodMode_ == "USB") demod_ = std::make_unique<DemodSSB>(DemodSSB::Sideband::USB);
    else if (demodMode_ == "LSB") demod_ = std::make_unique<DemodSSB>(DemodSSB::Sideband::LSB);
    else demod_ = std::make_unique<DemodNFM>();  // default NFM
    frontend_.reset();
    squelch_.reset();
    agc_.reset();

    // Tell test source which modulation to emit
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

void SpectrumEngine::onSetCenterFreq(double f) { if (source_) source_->setCenterFreq(f); }
void SpectrumEngine::onSetSampleRate(double r) { if (source_) source_->setSampleRate(r); }
void SpectrumEngine::onSetGain(double g) { if (source_) source_->setGain(g); }
void SpectrumEngine::setDemodMode(const QString& m) {
    demodMode_ = m;
    needDemodReset_ = true;
}
void SpectrumEngine::setSquelchThreshold(float db) { squelch_.setThresholdDb(db); }
void SpectrumEngine::setSquelchEnabled(bool e) { squelch_.setEnabled(e); }

void SpectrumEngine::run() {
    std::vector<std::complex<float>> iq;
    iq.resize(static_cast<std::size_t>(fftSize_.load()));

    while (running_.load()) {
        int n = fftSize_.load();
        if (static_cast<int>(iq.size()) != n) iq.resize(static_cast<std::size_t>(n));

        if (needDemodReset_) { rebuildDemod(); needDemodReset_ = false; }

        std::size_t got = source_->readIQ(iq);
        if (got == 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            continue;
        }

        // 1. IQ front-end correction
        frontend_.process(iq);

        // 2. Spectrum
        SpectrumFrame frame;
        frame.dbfs.resize(static_cast<std::size_t>(n));
        powerSpectrumDbfs(iq, frame.dbfs);
        frame.centerFreqHz = source_->centerFreq();
        frame.sampleRateHz = source_->sampleRate();
        frame.fftSize = n;
        frame.isTestSignal = !source_->isConnected();
        frame.sourceName = source_->name();
        emit spectrumReady(frame);

        // 3. Demod -> squelch -> AGC -> audio
        auto audio = demod_->process(iq);
        float rms = rmsDbfs(audio);
        auto gated = squelch_.apply(audio, rms);
        emit squelchState(squelch_.open());
        auto out = agc_.process(gated);
        emit audioLevel(agc_.currentLevelDb());
        audioOut_->write(out, demod_->outputSampleRate());

        std::this_thread::sleep_for(std::chrono::milliseconds(33));
    }
}

} // namespace dsp
} // namespace mbdsdr
