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
    // Try RTL-SDR first; if it fails (no device / no lib), fall back.
    auto rtl = std::make_unique<RtlSdrSource>();
    if (rtl->start()) {
        source_ = std::move(rtl);
        qInfo() << "[SpectrumEngine] using hardware source:" << source_->name();
    } else {
        qInfo() << "[SpectrumEngine] RTL-SDR unavailable; falling back to Test Signal";
        source_ = std::make_unique<TestSignalSource>();
        source_->start();
    }
    emit sourceChanged(source_->name(), source_->isConnected());
}

SpectrumEngine::~SpectrumEngine() {
    shutdown();
    wait();
    if (source_) source_->stop();
}

void SpectrumEngine::setFftSize(int n) {
    if (n == 1024 || n == 2048 || n == 4096) fftSize_.store(n);
}

void SpectrumEngine::shutdown() { running_.store(false); }

void SpectrumEngine::onSetCenterFreq(double f) {
    if (source_) source_->setCenterFreq(f);
}
void SpectrumEngine::onSetSampleRate(double r) {
    if (source_) source_->setSampleRate(r);
}
void SpectrumEngine::onSetGain(double g) {
    if (source_) source_->setGain(g);
}

void SpectrumEngine::run() {
    std::vector<std::complex<float>> iq;
    iq.resize(static_cast<std::size_t>(fftSize_.load()));

    while (running_.load()) {
        int n = fftSize_.load();
        if (static_cast<int>(iq.size()) != n) iq.resize(static_cast<std::size_t>(n));

        std::size_t got = source_->readIQ(iq);
        if (got == 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            continue;
        }

        SpectrumFrame frame;
        frame.dbfs.resize(static_cast<std::size_t>(n));
        powerSpectrumDbfs(iq, frame.dbfs);
        frame.centerFreqHz = source_->centerFreq();
        frame.sampleRateHz = source_->sampleRate();
        frame.fftSize = n;
        frame.isTestSignal = !source_->isConnected();
        frame.sourceName = source_->name();

        emit spectrumReady(frame);
        std::this_thread::sleep_for(std::chrono::milliseconds(33));
    }
}

} // namespace dsp
} // namespace mbdsdr
