// SPDX-License-Identifier: MIT
#include "dsp/digital_demod.h"

#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

namespace {
constexpr float kPi   = 3.14159265358979323846f;
constexpr float k2Pi  = 6.28318530717958647692f;

// Second-order PLL coefficient approximation.
// omega_n in rad/symbol, zeta ~ 0.707 (critically damped-ish).
void pllCoeffs(float omegaN, float zeta, float& kp, float& ki) {
    kp = 2.0f * zeta * omegaN;
    ki = omegaN * omegaN;
}
} // namespace

DigitalDemod::DigitalDemod(const DigitalDemodConfig& cfg) : cfg_(cfg) {
    reset();
}

void DigitalDemod::reset() {
    spsNom_ = static_cast<float>(cfg_.sampleRateHz / cfg_.symbolRateBd);
    spsEst_ = spsNom_;
    tau_    = 0.0f;

    agcGain_  = 1.0f;
    ncoPhase_ = 0.0f;
    carFreq_  = 0.0f;

    // Map normalized loop bandwidth (cycles/symbol) -> rad/symbol gains.
    float wnCar = k2Pi * cfg_.carrierBw;
    pllCoeffs(wnCar, 0.707f, kpCar_, kiCar_);

    xPrev_ = xCur_ = {0.f, 0.f};
    yMid_ = {0.f, 0.f};
    haveMid_ = false;
    yPrev_ = aPrev_ = {1.f, 0.f};
    havePrev_ = false;
    timingErrOut_ = 0.0f;

    decisions_.clear();
    recovered_.clear();
    bits_.clear();

    st_ = DigitalLockStatus{};
    evmAcc_ = 0.0f;
    evmN_   = 0;
    carSmE_ = 1.0f;
    timSmE_ = 1.0f;
    carGood_ = 0;
    timGood_ = 0;
}

std::vector<std::complex<float>> DigitalDemod::idealPoints(DigMode m) {
    if (m == DigMode::BPSK) {
        return {{-1.f, 0.f}, {1.f, 0.f}};
    }
    const float u = 1.0f / std::sqrt(2.0f);
    return {{ u,  u}, {-u,  u}, {-u, -u}, { u, -u}};
}

float DigitalDemod::angleNorm(float rad) {
    while (rad >  kPi) rad -= k2Pi;
    while (rad < -kPi) rad += k2Pi;
    return rad;
}

std::vector<std::complex<float>> DigitalDemod::diffEncode(const std::vector<int>& bits,
                                                          DigMode m) {
    std::vector<std::complex<float>> out;
    out.reserve(bits.size() / (m == DigMode::QPSK ? 2u : 1u) + 1);
    std::complex<float> phaseRef = {1.f, 0.f};
    out.push_back(phaseRef);  // reference symbol (no bit)

    if (m == DigMode::BPSK) {
        for (int b : bits) {
            // bit 0 -> phase unchanged, bit 1 -> 180 deg flip
            float ang = (b != 0) ? kPi : 0.0f;
            phaseRef *= std::polar(1.0f, ang);
            out.push_back(phaseRef);
        }
    } else {
        for (size_t i = 0; i + 1 < bits.size(); i += 2) {
            int iBit = bits[i];
            int qBit = bits[i + 1];
            // Gray-coded phase increments:
            //   (0,0) -> 0 deg, (0,1) -> +90, (1,1) -> 180, (1,0) -> -90
            float ang = 0.0f;
            if (iBit == 0 && qBit == 1)      ang =  kPi / 2.0f;
            else if (iBit == 1 && qBit == 1) ang =  kPi;
            else if (iBit == 1 && qBit == 0) ang = -kPi / 2.0f;
            phaseRef *= std::polar(1.0f, ang);
            out.push_back(phaseRef);
        }
    }
    return out;
}

std::vector<int> DigitalDemod::diffDecode(const std::vector<std::complex<float>>& decisions,
                                          DigMode m) {
    std::vector<int> bits;
    if (decisions.size() < 2) return bits;

    if (m == DigMode::BPSK) {
        bits.reserve(decisions.size() - 1);
        for (size_t k = 1; k < decisions.size(); ++k) {
            float d = angleNorm(std::arg(decisions[k] * std::conj(decisions[k - 1])));
            bits.push_back(std::abs(d) > kPi / 2.0f ? 1 : 0);
        }
    } else {
        bits.reserve((decisions.size() - 1) * 2);
        for (size_t k = 1; k < decisions.size(); ++k) {
            float d = angleNorm(std::arg(decisions[k] * std::conj(decisions[k - 1])));
            // Map phase increment back to Gray (iBit, qBit).
            int iBit = 0, qBit = 0;
            if (d >  kPi / 4.0f && d <  3 * kPi / 4.0f) {       qBit = 1;
            } else if (std::abs(d) > 3 * kPi / 4.0f) {          iBit = 1; qBit = 1;
            } else if (d < -kPi / 4.0f && d > -3 * kPi / 4.0f) { iBit = 1;
            }
            bits.push_back(iBit);
            bits.push_back(qBit);
        }
    }
    return bits;
}

std::complex<float> DigitalDemod::decide(std::complex<float> y) const {
    if (cfg_.mode == DigMode::BPSK) {
        return {y.real() >= 0 ? 1.f : -1.f, 0.f};
    }
    // QPSK: nearest of 4 unit points (signs of I/Q, normalized to unit radius).
    float u = 1.0f / std::sqrt(2.0f);
    return {y.real() >= 0 ? u : -u, y.imag() >= 0 ? u : -u};
}

void DigitalDemod::process(const std::vector<std::complex<float>>& in) {
    for (std::complex<float> raw : in) {
        // --- 1. AGC: aim unit average power ---
        float p = raw.real() * raw.real() + raw.imag() * raw.imag();
        if (p < 1e-6f) p = 1e-6f;
        float target = 1.0f;
        agcGain_ += cfg_.agcTau * (target / std::sqrt(p) - agcGain_);
        if (agcGain_ < 0.01f) agcGain_ = 0.01f;
        if (agcGain_ > 100.0f) agcGain_ = 100.0f;
        std::complex<float> x = raw * agcGain_;

        // --- 2. Carrier NCO rotation (multiply by exp(-j phase)) ---
        // NOTE: cos/sin MUST be recomputed per sample -- ncoPhase_ advances
        // every sample and the loop tracks time-varying frequency offsets.
        float cp = std::cos(ncoPhase_);
        float sp = std::sin(ncoPhase_);
        std::complex<float> rot(cp, -sp);
        std::complex<float> xr = x * rot;

        // shift interpolation history
        xPrev_ = xCur_;
        xCur_  = xr;

        // --- 3. Timing strobe (Gardner TED) ---
        // tau_ is the fractional position into the current symbol interval
        // [0,1). Each input sample advances it by 1/spsEst. We record the
        // interpolated sample at the midpoint (tau crosses 0.5) and at the
        // strobe (tau crosses 1.0). The Gardner error
        //   e = Re[(y_k - y_{k-1}) * conj(y_{k-1/2})]
        // is insensitive to carrier phase (after Costas recovery) and nudges
        // the strobe phase toward the eye center. Unlike a free-running MM TED
        // it converges regardless of the channelizer's arbitrary decimation phase.
        tau_ += 1.0f / spsEst_;

        // Midpoint sample: first time tau reaches/passes 0.5 in this interval.
        if (!haveMid_ && tau_ >= 0.5f) {
            float mfrac = tau_ - 0.5f;
            yMid_ = (1.0f - mfrac) * xPrev_ + mfrac * xCur_;
            haveMid_ = true;
        }

        if (tau_ >= 1.0f) {
            tau_ -= 1.0f;
            float frac = tau_;
            std::complex<float> y = (1.0f - frac) * xPrev_ + frac * xCur_;

            // Gardner phase nudge (uses the previous strobe + this midpoint).
            if (haveMid_ && havePrev_) {
                float eTim = std::real((y - yPrev_) * std::conj(yMid_));
                timingErrOut_ = cfg_.timingBw * eTim;
            } else {
                timingErrOut_ = 0.0f;
            }
            acceptSymbol(y);

            // Apply the nudge AFTER acceptSymbol so acceptSymbol's own EVM uses
            // the un-nagged strobe sample, and clamp the phase to the interval.
            tau_ += timingErrOut_;
            if (tau_ >  0.5f) tau_ =  0.5f;
            if (tau_ < -0.5f) tau_ = -0.5f;
            haveMid_ = false;
        }

        // advance NCO: loop state is in rad/symbol -> per-sample step = rad/symbol / SPS.
        ncoPhase_ += carFreq_ / spsEst_;
        if (ncoPhase_ >  kPi) ncoPhase_ -= k2Pi;
        if (ncoPhase_ < -kPi) ncoPhase_ += k2Pi;
    }
}

void DigitalDemod::acceptSymbol(std::complex<float> y) {
    recovered_.push_back(y);
    std::complex<float> a = decide(y);
    decisions_.push_back(a);
    st_.symbolsProcessed++;

    // --- EVM on the recovered (pre-decision) sample vs its decision ---
    std::complex<float> err = y - a;
    float e2 = err.real() * err.real() + err.imag() * err.imag();
    evmAcc_ += e2;
    evmN_++;
    if (evmN_ >= 256) {
        st_.evmPercent = 100.0f * std::sqrt(evmAcc_ / static_cast<float>(evmN_));
        evmAcc_ = 0.0f;
        evmN_   = 0;
    }

    if (!havePrev_) {
        yPrev_    = y;
        aPrev_    = a;
        havePrev_ = true;
        return;
    }

    // --- Costas loop error (decision-directed): imag(conj(a) * y) ---
    float eCar = std::imag(std::conj(a) * y);
    carFreq_  += kiCar_ * eCar;
    ncoPhase_ += kpCar_ * eCar;
    // clamp frequency excursion to +/- half symbol rate (+/-1200 Hz @2400Bd)
    if (carFreq_ >  3.1f) carFreq_ =  3.1f;
    if (carFreq_ < -3.1f) carFreq_ = -3.1f;

    // --- Gardner timing error (computed in process(); magnitude for lock) ---
    float eTim = timingErrOut_;   // already scaled by timingBw in process()

    // --- Lock detection (smoothed error) ---
    carSmE_ += 0.02f * (std::abs(eCar) - carSmE_);
    timSmE_ += 0.02f * (std::abs(eTim) - timSmE_);
    if (carSmE_ < 0.25f) carGood_++; else carGood_ = 0;
    if (timSmE_ < 0.35f) timGood_++; else timGood_ = 0;
    st_.carrierLocked = carGood_ > 60;
    st_.symbolLocked  = timGood_ > 60;
    st_.freqOffsetHz  = carFreq_ * static_cast<float>(cfg_.symbolRateBd) / k2Pi;

    // --- Differential decode (relative phase, ambiguity-immune) ---
    float d = angleNorm(std::arg(a * std::conj(aPrev_)));
    if (cfg_.mode == DigMode::BPSK) {
        bits_.push_back(std::abs(d) > kPi / 2.0f ? 1 : 0);
    } else {
        int iBit = 0, qBit = 0;
        if (d >  kPi / 4.0f && d <  3 * kPi / 4.0f)       { qBit = 1; }
        else if (std::abs(d) > 3 * kPi / 4.0f)             { iBit = 1; qBit = 1; }
        else if (d < -kPi / 4.0f && d > -3 * kPi / 4.0f)   { iBit = 1; }
        bits_.push_back(iBit);
        bits_.push_back(qBit);
    }

    yPrev_ = y;
    aPrev_ = a;
}

std::vector<std::complex<float>> DigitalDemod::takeDecisions() {
    std::vector<std::complex<float>> out;
    out.swap(decisions_);
    return out;
}
std::vector<std::complex<float>> DigitalDemod::takeRecovered() {
    std::vector<std::complex<float>> out;
    out.swap(recovered_);
    return out;
}
std::vector<int> DigitalDemod::takeDecodedBits() {
    std::vector<int> out;
    out.swap(bits_);
    return out;
}

} // namespace dsp
} // namespace mbdsdr
