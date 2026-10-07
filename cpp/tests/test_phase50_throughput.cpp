// SPDX-License-Identifier: MIT
//
// *** SYNTHETIC DETERMINISTIC IQ -- NOT HARDWARE / 非硬件合成 ***
//
// Phase50 channelizer buffering / on-demand demodulation throughput baseline.
// Feeds deterministic wideband FM IQ straight into VfoManager::process
// (channelizer + analog demod per ACTIVE VFO; idle/orphan VFOs cost ~zero
// CPU by design) and measures the realtime multiple = samples-fed / wall time.
// Scenarios: 1 selected VFO (the Phase50 baseline), then 4 and 9 VFOs with the
// extras user-armed (parallel monitoring) -- the #50 question is whether
// demodulation CPU scales linearly with the number of ACTIVE channels.
#include <chrono>
#include <complex>
#include <cstdio>
#include <vector>
#include <cmath>

#include "dsp/vfo_manager.h"

using namespace mbdsdr::dsp;

namespace {

// Deterministic wideband IQ, one second at fs. Two FM carriers:
//   A at +100 kHz modulated by 1.0 kHz tone (deviation 3 kHz)
//   B at -300 kHz modulated by 2.0 kHz tone (deviation 3 kHz)
std::vector<std::complex<float>> makeSecondIq(double fs) {
    const int n = static_cast<int>(fs);
    std::vector<std::complex<float>> out(static_cast<std::size_t>(n));
    double phA = 0.0, phB = 0.0;
    const double offA = 100e3, offB = -300e3, fdev = 3e3;
    const double toneA = 1000.0, toneB = 2000.0;
    for (int i = 0; i < n; ++i) {
        const double t = static_cast<double>(i);
        phA += 2.0 * M_PI * fdev / fs * std::cos(2.0 * M_PI * toneA * t / fs);
        phB += 2.0 * M_PI * fdev / fs * std::cos(2.0 * M_PI * toneB * t / fs);
        const double cA = 2.0 * M_PI * offA * t / fs + phA;
        const double cB = 2.0 * M_PI * offB * t / fs + phB;
        out[static_cast<std::size_t>(i)] =
            std::complex<float>(static_cast<float>(std::cos(cA) + std::cos(cB)),
                                static_cast<float>(std::sin(cA) + std::sin(cB)));
    }
    return out;
}

// Feed `seconds` of one-second blocks through the VFO manager and return the
// realtime multiple (1.0 = real time, >1 = faster than real time).
double runSeconds(VfoManager& vm, const std::vector<std::complex<float>>& oneSec,
                  int seconds) {
    const auto t0 = std::chrono::steady_clock::now();
    for (int k = 0; k < seconds; ++k)
        vm.process(oneSec, 2.4e6, 100.0e6);
    const auto t1 = std::chrono::steady_clock::now();
    const double wall =
        std::chrono::duration<double>(t1 - t0).count();
    return static_cast<double>(seconds) / wall;
}

} // namespace

int main() {
    const double fs = 2.4e6;
    const std::vector<std::complex<float>> oneSec = makeSecondIq(fs);
    const int seconds = 10;

    // Scenario 1: single selected NFM VFO -- the Phase50 on-demand baseline.
    {
        VfoManager vm;
        vm.initDefault(fs, 100.0e6, "NFM", 12500.0);
        const double rt = runSeconds(vm, oneSec, seconds);
        std::printf("scenario1 single-vfo    : %6.2fx realtime (10s in %.3fs)\n", rt, seconds / rt);
    }
    // Scenario 2: 4 active VFOs (selected + 3 armed), spread across the band.
    {
        VfoManager vm;
        vm.initDefault(fs, 100.0e6, "NFM", 12500.0);
        const int a = vm.addVfo(100.1e6);   // +100 kHz (carrier A)
        const int b = vm.addVfo(99.7e6);    // -300 kHz (carrier B)
        const int c = vm.addVfo(99.9e6);    // idle spot, still demodulated
        vm.setArmed(a, true);
        vm.setArmed(b, true);
        vm.setArmed(c, true);
        const double rt = runSeconds(vm, oneSec, seconds);
        std::printf("scenario2 4-vfo-armed   : %6.2fx realtime (10s in %.3fs)\n", rt, seconds / rt);
    }
    // Scenario 3: 9 active VFOs (selected + 8 armed).
    {
        VfoManager vm;
        vm.initDefault(fs, 100.0e6, "NFM", 12500.0);
        for (int k = 0; k < 8; ++k) {
            const double off = 100e3 * ((k % 2 == 0) ? (k / 2 + 1) : -(k / 2 + 1));
            const int id = vm.addVfo(100.0e6 + off);
            vm.setArmed(id, true);
        }
        const double rt = runSeconds(vm, oneSec, seconds);
        std::printf("scenario3 9-vfo-armed   : %6.2fx realtime (10s in %.3fs)\n", rt, seconds / rt);
    }
    // Scenario 4: 1 selected + 8 idle VFOs (none armed) -- on-demand cost proof.
    {
        VfoManager vm;
        vm.initDefault(fs, 100.0e6, "NFM", 12500.0);
        for (int k = 0; k < 8; ++k) {
            const double off = 100e3 * ((k % 2 == 0) ? (k / 2 + 1) : -(k / 2 + 1));
            vm.addVfo(100.0e6 + off);      // NOT armed -> idle, ~zero CPU
        }
        const double rt = runSeconds(vm, oneSec, seconds);
        std::printf("scenario4 1-of-9-armed  : %6.2fx realtime (10s in %.3fs)\n", rt, seconds / rt);
    }
    return 0;
}
