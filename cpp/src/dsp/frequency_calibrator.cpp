// SPDX-License-Identifier: MIT
//
// Frequency calibration (晶振 ppm 误差自动测量) -- implementation.
//
// Pure offline logic: a dependency-free radix-2 FFT, Hann window, peak search
// bounded around the expected baseband position, parabolic sub-bin refinement,
// SNR vs the median bin, and multi-block averaging with a consistency check.
// See frequency_calibrator.h for the sign convention and rationale.

#include "dsp/frequency_calibrator.h"

#include "core/tokens.h"   // kPpmMax (search-window bound) / kPeakThreshold*

#include <algorithm>
#include <cmath>
#include <numeric>

#include <QSettings>

namespace mbdsdr {
namespace dsp {

namespace {

constexpr double kPi = 3.14159265358979323846;

int nextPow2(int n) {
    int p = 1;
    while (p < n) p <<= 1;
    return p;
}

// In-place radix-2 Cooley-Tukey FFT (complex float). Length must be a power of
// two. Mechanism-only reimplementation (the same textbook transform used by
// fcch_detector); no external FFT dependency.
void fftRadix2(std::vector<std::complex<float>>& a) {
    const int n = static_cast<int>(a.size());
    for (int i = 1, j = 0; i < n; ++i) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(a[i], a[j]);
    }
    for (int len = 2; len <= n; len <<= 1) {
        const double ang = -2.0 * kPi / len;
        const std::complex<float> wlen(static_cast<float>(std::cos(ang)),
                                       static_cast<float>(std::sin(ang)));
        for (int i = 0; i < n; i += len) {
            std::complex<float> w(1.0f, 0.0f);
            for (int k = 0; k < len / 2; ++k) {
                const std::complex<float> u = a[i + k];
                const std::complex<float> v = a[i + k + len / 2] * w;
                a[i + k] = u + v;
                a[i + k + len / 2] = u - v;
                w *= wlen;
            }
        }
    }
}

// Map an FFT bin index (0..n-1) to a SIGNED bin, DC centred: 0..n/2 stay,
// n/2+1..n-1 wrap to negative.
double signedBin(int k, int n) {
    return (k <= n / 2) ? static_cast<double>(k)
                        : static_cast<double>(k - n);
}

// Choose the FFT length for a block of `blockN` samples.
int autoFftSize(int blockN, const CalibratorConfig& cfg) {
    int fftN = cfg.fftSize;
    if (fftN <= 0) {
        fftN = std::min(blockN, cfg.maxFftSize);
        fftN = nextPow2(fftN);
        if (fftN > blockN || fftN > cfg.maxFftSize) fftN >>= 1;
    }
    return fftN;
}

// Half-width (Hz) of the peak search window around the expected position. It is
// bounded by the correctable ppm range applied to the centre, with a small floor
// so a near-DC reference still has a few bins to search.
double searchHalfHz(double centreHz, double binHz) {
    const double byPpm = std::fabs(centreHz) *
                         std::fabs(tokens::kPpmMax) / 1.0e6;
    return std::max(byPpm, 4.0 * binHz);
}

} // namespace

QString calibrationReferenceName(CalibrationReference ref) {
    switch (ref) {
    case CalibrationReference::HandheldGuided:
        return QString::fromUtf8("手台引导（已知精确频率）");
    case CalibrationReference::GsmFcch:
        return QString::fromUtf8("GSM FCCH 精确纯音");
    case CalibrationReference::Manual:
        return QString::fromUtf8("手动（任意已知精确频率）");
    }
    return QString::fromUtf8("未知参考源");
}

CalibrationMeasurement measureCarrier(
        const std::vector<std::complex<float>>& iq,
        double sampleRateHz, const CalibratorConfig& cfg) {
    CalibrationMeasurement m;
    m.expectedFreqHz = cfg.expectedBasebandHz;

    const int blockN = static_cast<int>(iq.size());
    const int fftN = autoFftSize(blockN, cfg);
    m.fftSize = fftN;
    if (fftN < cfg.minFftSize || blockN < cfg.minFftSize)
        return m;   // honestly not measurable
    const double binHz = sampleRateHz / fftN;

    // Window the first fftN samples (Hann) then transform.
    std::vector<std::complex<float>> spec(fftN);
    for (int k = 0; k < fftN; ++k) {
        const double w = 0.5 - 0.5 * std::cos(2.0 * kPi * k / (fftN - 1));
        spec[k] = iq[k] * static_cast<float>(w);
    }
    fftRadix2(spec);

    // Magnitude + power, DC centred.
    std::vector<float> mag(fftN);
    std::vector<double> power(fftN);
    for (int k = 0; k < fftN; ++k) {
        mag[k] = std::abs(spec[k]);
        power[k] = static_cast<double>(mag[k]) * mag[k];
    }

    // Median power across bins is the honest local noise reference for the gate.
    std::vector<double> sortedPow = power;
    std::nth_element(sortedPow.begin(),
                     sortedPow.begin() + fftN / 2, sortedPow.end());
    const double medianPow = std::max(sortedPow[fftN / 2], 1e-20);

    // Peak search bounded around the expected baseband position.
    const double half = searchHalfHz(cfg.centreFreqHz, binHz);
    const double expectBin = cfg.expectedBasebandHz / binHz;
    const int reach = static_cast<int>(std::ceil(half / binHz)) + 1;
    int peakK = -1;
    float peakMag = -1.0f;
    for (int d = -reach; d <= reach; ++d) {
        // Search in signed-bin space, fold to a raw FFT index.
        int sb = static_cast<int>(std::llround(expectBin)) + d;
        if (sb < -fftN / 2 || sb > fftN / 2) continue;
        int k = (sb >= 0) ? sb : sb + fftN;
        if (mag[k] > peakMag) { peakMag = mag[k]; peakK = k; }
    }
    if (peakK < 0) return m;

    const double peakPow = std::max(power[peakK], 1e-20);
    const double snrDb = 10.0 * std::log10(peakPow / medianPow);
    m.peakSnrDb = snrDb;
    if (snrDb < cfg.thresholdDb)
        return m;   // peak too weak -- honestly not a reference carrier

    // Parabolic sub-bin interpolation on the magnitude, with wrap-around
    // neighbours (the FFT is circular).
    auto wrap = [fftN](int k) { return (k + fftN) % fftN; };
    const float left = mag[wrap(peakK - 1)];
    const float centre = mag[peakK];
    const float right = mag[wrap(peakK + 1)];
    const float denom = (left - 2.0f * centre + right);
    float p = 0.0f;
    if (std::fabs(denom) > 1e-12f)
        p = 0.5f * (left - right) / denom;
    p = std::clamp(p, -1.0f, 1.0f);

    const double fracSigned = signedBin(peakK, fftN) + p;
    const double measuredHz = fracSigned * binHz;

    m.detected = true;
    m.measuredFreqHz = measuredHz;
    m.offsetHz = measuredHz - cfg.expectedBasebandHz;
    if (std::fabs(cfg.centreFreqHz) > 1.0)
        m.ppm = -m.offsetHz / cfg.centreFreqHz * 1.0e6;
    return m;
}

CalibrationResult calibrateFromBlocks(
        const std::vector<std::vector<std::complex<float>>>& blocks,
        double sampleRateHz, CalibrationReference ref,
        const CalibratorConfig& cfg) {
    CalibrationResult r;
    r.reference = ref;
    r.expectedFreqHz = cfg.expectedBasebandHz;
    r.measurementsAttempted = static_cast<int>(blocks.size());

    for (const auto& blk : blocks)
        r.segments.push_back(measureCarrier(blk, sampleRateHz, cfg));

    std::vector<CalibrationMeasurement> good;
    for (const auto& m : r.segments)
        if (m.detected) good.push_back(m);

    if (good.empty()) {
        r.detected = false;
        r.status = QString::fromUtf8(
            "未检测到参考载波——请确认参考源正在发射、频率正确，且已按引导操作。");
        return r;
    }

    // Consistency: median ppm of the detected measurements, then drop outliers.
    std::vector<double> ppms;
    for (const auto& m : good) ppms.push_back(m.ppm);
    std::vector<double> sortedPpm = ppms;
    std::nth_element(sortedPpm.begin(),
                     sortedPpm.begin() + sortedPpm.size() / 2, sortedPpm.end());
    const double medianPpm = sortedPpm[sortedPpm.size() / 2];

    std::vector<CalibrationMeasurement> accepted;
    for (const auto& m : good)
        if (std::fabs(m.ppm - medianPpm) <= cfg.consistencyPpm)
            accepted.push_back(m);

    if (accepted.empty()) {
        r.detected = false;
        r.status = QString::fromUtf8(
            "测量结果彼此不一致，无法给出可靠 ppm（可能存在多个峰或信号不稳定）。");
        return r;
    }

    const int n = static_cast<int>(accepted.size());
    double sumPpm = 0, sumOff = 0, sumMeas = 0, worstSnr = 1e9;
    for (const auto& m : accepted) {
        sumPpm += m.ppm;
        sumOff += m.offsetHz;
        sumMeas += m.measuredFreqHz;
        worstSnr = std::min(worstSnr, m.peakSnrDb);
    }
    const double meanPpm = sumPpm / n;
    double varPpm = 0;
    for (const auto& m : accepted)
        varPpm += (m.ppm - meanPpm) * (m.ppm - meanPpm);
    const double spreadPpm = (n > 1) ? std::sqrt(varPpm / (n - 1)) : 0.0;

    r.detected = true;
    r.ppm = meanPpm;
    r.spreadPpm = spreadPpm;
    r.meanOffsetHz = sumOff / n;
    r.measuredFreqHz = sumMeas / n;
    r.worstSnrDb = worstSnr;
    r.measurementsUsed = n;

    // Confidence 0..1: enough independent measurements, tight spread, and a
    // comfortable SNR margin above the gate.
    const double countF = std::min(n / 3.0, 1.0);
    const double spreadF = 1.0 / (1.0 + spreadPpm);
    const double snrF = std::clamp(
        (worstSnr - cfg.thresholdDb) / 20.0, 0.0, 1.0);
    r.confidence = countF * (0.5 * spreadF + 0.5 * snrF);

    r.status = QString::fromUtf8("参考载波已锁定：%1，估计 ppm = %2 ± %3，置信度 %4%")
        .arg(calibrationReferenceName(ref))
        .arg(meanPpm, 0, 'f', 3)
        .arg(spreadPpm, 0, 'f', 3)
        .arg(std::lround(r.confidence * 100.0));
    return r;
}

CalibrationResult calibrateFromCapture(
        const std::vector<std::complex<float>>& iq,
        double sampleRateHz, CalibrationReference ref,
        const CalibratorConfig& cfg, int segments) {
    if (segments < 1) segments = 1;
    const int totalN = static_cast<int>(iq.size());
    const int piece = totalN / segments;
    std::vector<std::vector<std::complex<float>>> blocks;
    if (piece >= cfg.minFftSize) {
        for (int s = 0; s < segments; ++s) {
            std::vector<std::complex<float>> b;
            b.reserve(piece);
            for (int k = s * piece; k < (s + 1) * piece && k < totalN; ++k)
                b.push_back(iq[k]);
            blocks.push_back(std::move(b));
        }
    } else {
        blocks.push_back(iq);   // too short to split: one measurement
    }
    return calibrateFromBlocks(blocks, sampleRateHz, ref, cfg);
}

SpectrumPreview buildSpectrumPreview(
        const std::vector<std::complex<float>>& iq,
        double sampleRateHz, const CalibratorConfig& cfg) {
    SpectrumPreview out;
    const int blockN = static_cast<int>(iq.size());
    const int fftN = autoFftSize(blockN, cfg);
    out.fftSize = fftN;
    if (fftN < cfg.minFftSize) return out;
    const double binHz = sampleRateHz / fftN;

    std::vector<std::complex<float>> spec(fftN);
    for (int k = 0; k < fftN; ++k) {
        const double w = 0.5 - 0.5 * std::cos(2.0 * kPi * k / (fftN - 1));
        spec[k] = iq[k] * static_cast<float>(w);
    }
    fftRadix2(spec);

    // Normalise dB to the strongest bin so the preview is level-independent.
    float maxMag = 1e-12f;
    std::vector<float> raw(fftN);
    for (int k = 0; k < fftN; ++k) {
        raw[k] = std::abs(spec[k]);
        maxMag = std::max(maxMag, raw[k]);
    }
    out.dbBins.resize(fftN);
    for (int k = 0; k < fftN; ++k) {
        // Emit DC-centred order: index 0 -> -Fs/2.
        int src = (k + fftN / 2) % fftN;
        out.dbBins[k] = static_cast<float>(
            20.0 * std::log10(std::max(raw[src], 1e-9f) / maxMag));
    }

    // Peak near the expected position (same bounded search as the measurement).
    const double half = searchHalfHz(cfg.centreFreqHz, binHz);
    const double expectBin = cfg.expectedBasebandHz / binHz;
    const int reach = static_cast<int>(std::ceil(half / binHz)) + 1;
    int peakSigned = -fftN;
    float peakMag = -1.0f;
    for (int d = -reach; d <= reach; ++d) {
        int sb = static_cast<int>(std::llround(expectBin)) + d;
        if (sb < -fftN / 2 || sb > fftN / 2) continue;
        int k = (sb >= 0) ? sb : sb + fftN;
        if (raw[k] > peakMag) { peakMag = raw[k]; peakSigned = sb; }
    }
    if (peakSigned < -fftN / 2) return out;
    out.peakBin = peakSigned;
    auto wrap = [fftN](int k) { return (k + fftN) % fftN; };
    const int k0 = wrap(peakSigned);
    const float left = raw[wrap(k0 - 1)];
    const float centre = raw[k0];
    const float right = raw[wrap(k0 + 1)];
    const float denom = (left - 2.0f * centre + right);
    float p = 0.0f;
    if (std::fabs(denom) > 1e-12f)
        p = 0.5f * (left - right) / denom;
    out.peakFractionalBin = peakSigned + std::clamp(p, -1.0f, 1.0f);
    out.peakDb = static_cast<float>(
        20.0 * std::log10(std::max(peakMag, 1e-9f) / maxMag));
    return out;
}

// ---- QSettings persistence (QtCore only; engine forwarding is the caller's) -
void savePpmSetting(double ppm) {
    QSettings s;
    s.setValue(QStringLiteral("rtl/ppm"), ppm);
}

double currentPpmSetting() {
    QSettings s;
    return s.value(QStringLiteral("rtl/ppm"), 0.0).toDouble();
}

double predictedResidualHz(double measuredResidualHz,
                           double centreHz, double ppmToApply) {
    return measuredResidualHz + centreHz * ppmToApply / 1.0e6;
}

} // namespace dsp
} // namespace mbdsdr
