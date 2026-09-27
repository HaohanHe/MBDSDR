// SPDX-License-Identifier: MIT
#include "spectrum_engine.h"

#include <QDebug>
#include <chrono>
#include <thread>

namespace mbdsdr {
namespace dsp {

SpectrumEngine::SpectrumEngine(QObject* parent)
    : QThread(parent), generator_(2.4e6, 98.5e6) {}

SpectrumEngine::~SpectrumEngine() {
    shutdown();
    wait();
}

void SpectrumEngine::setFftSize(int n) {
    // Only accept power-of-two FFT sizes; engine applies on next loop.
    if (n == 1024 || n == 2048 || n == 4096) {
        fftSize_.store(n);
    }
}

void SpectrumEngine::shutdown() {
    running_.store(false);
}

void SpectrumEngine::run() {
    // Working buffers (thread-local to this engine loop)
    std::vector<std::complex<float>> iq;
    iq.resize(static_cast<std::size_t>(fftSize_.load()));

    while (running_.load()) {
        int n = fftSize_.load();
        if (static_cast<int>(iq.size()) != n) iq.resize(static_cast<std::size_t>(n));

        // 1. Pull one frame of IQ from the (offline) test generator.
        //    *** TEST SIGNAL -- NOT HARDWARE ***
        generator_.next(iq, static_cast<std::size_t>(n));

        // 2. Compute power spectrum (Hann window + FFT + fftshift + dBFS).
        SpectrumFrame frame;
        frame.dbfs.resize(static_cast<std::size_t>(n));
        powerSpectrumDbfs(iq, frame.dbfs);
        frame.centerFreqHz = generator_.centerFreq();
        frame.sampleRateHz  = generator_.sampleRate();
        frame.fftSize       = n;
        frame.isTestSignal  = true;   // Phase 1: always test data

        // 3. Emit to UI (queued connection to the GUI thread).
        emit spectrumReady(frame);

        // ~30 fps pacing (engine loop pace, independent of UI repaint).
        std::this_thread::sleep_for(std::chrono::milliseconds(33));
    }
}

} // namespace dsp
} // namespace mbdsdr
