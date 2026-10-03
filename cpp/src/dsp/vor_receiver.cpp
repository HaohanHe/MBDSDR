// SPDX-License-Identifier: MIT
#include "vor_receiver.h"

#include "core/tokens.h"   // kVor* shared physics/business constants

#include <cmath>
#include <algorithm>
#include <unordered_map>

namespace mbdsdr {
namespace dsp {

namespace {
constexpr double kPi   = 3.14159265358979323846;
constexpr double kTwoPi = 6.28318530717958647692;
// Variable-30 Hz coherence floor (normalized |I+jQ|/N). A real station tone
// lands ~0.15 at unit amplitude; pure noise ~sigma/sqrt(2N) ~1e-3 and silence
// is exactly 0. Below this we refuse to report a bearing (honest unlock).
constexpr double kMinVarMag = 0.02;
// Absolute floor so a quiet band is never keyed into Morse by the noise floor.
constexpr double kMinMorseLevel = 0.01;

const std::unordered_map<std::string, char> kMorseTable = {
    {".-", 'A'}, {"-...", 'B'}, {"-.-.", 'C'}, {"-..", 'D'}, {".", 'E'},
    {"..-.", 'F'}, {"--.", 'G'}, {"....", 'H'}, {"..", 'I'}, {".---", 'J'},
    {"-.-", 'K'}, {".-..", 'L'}, {"--", 'M'}, {"-.", 'N'}, {"---", 'O'},
    {".--.", 'P'}, {"--.-", 'Q'}, {".-.", 'R'}, {"...", 'S'}, {"-", 'T'},
    {"..-", 'U'}, {"...-", 'V'}, {".--", 'W'}, {"-..-", 'X'}, {"-.--", 'Y'},
    {"--..", 'Z'},
    {"-----", '0'}, {".----", '1'}, {"..---", '2'}, {"...--", '3'}, {"....-", '4'},
    {".....", '5'}, {"-....", '6'}, {"--...", '7'}, {"---..", '8'}, {"----.", '9'},
};
} // namespace

// ---------------------------------------------------------------------------
VorReceiver::VorReceiver(double sampleRateHz) {
    setSampleRate(sampleRateHz);
}

void VorReceiver::setSampleRate(double sr) {
    fs_ = (sr > 1000.0) ? sr : 44100.0;
    designFilters();
    reset();
}

// ---------------------------------------------------------------------------
float VorReceiver::Biquad::process(float x) {
    float y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
    x2 = x1; x1 = x;
    y2 = y1; y1 = y;
    return y;
}
void VorReceiver::Biquad::reset() { x1 = x2 = y1 = y2 = 0.0f; }

void VorReceiver::designFilters() {
    const double Q = 5.0;
    const double w0 = kTwoPi * tokens::kVorMorseToneHz / fs_;
    const double c = std::cos(w0), s = std::sin(w0);
    const double alpha = s / (2.0 * Q);
    const double a0 = 1.0 + alpha;
    morseBp_.b0 = static_cast<float>((alpha) / a0);
    morseBp_.b1 = 0.0f;
    morseBp_.b2 = static_cast<float>((-alpha) / a0);
    morseBp_.a1 = static_cast<float>((-2.0 * c) / a0);
    morseBp_.a2 = static_cast<float>((1.0 - alpha) / a0);

    // Sub-carrier baseband low-pass: keep the +/-480 Hz deviation, reject the
    // 30 Hz variable tone, the 1020 Hz keying and the image.
    const double subCutoff = tokens::kVorSubcarrierDevHz * 1.5; // ~720 Hz
    subLpAlpha_ = 1.0 - std::exp(-kTwoPi * subCutoff / fs_);

    // Morse envelope smoothing (~5 ms) averages the 1020 Hz ripple.
    const double tau = 0.005;
    envAlpha_ = 1.0 - std::exp(-1.0 / (tau * fs_));
}

// ---------------------------------------------------------------------------
void VorReceiver::resetAccumulators() {
    iv_ = qv_ = ir_ = qr_ = 0.0;
    subN_ = 0;
}

void VorReceiver::reset() {
    d30_ = kTwoPi * tokens::kVorReferenceModHz / fs_;
    dSub_ = kTwoPi * tokens::kVorSubcarrierHz / fs_;
    th30_ = 0.0;
    thSub_ = 0.0;
    subI_ = subQ_ = 0.0;
    prevArg_ = 0.0;
    havePrevArg_ = false;
    resetAccumulators();
    subRx_.clear(); subIy_.clear(); subVarMag_.clear(); subRefMag_.clear();
    subCount_ = 0;
    morseBp_.reset();
    env_ = 0.0;
    envPeak_ = 0.0;
    morseOn_ = false;
    morseRun_ = 0;
    segs_.clear();
    haveResult_ = false;
    pending_ = VorResult();
}

// ---------------------------------------------------------------------------
double VorReceiver::wrapDeg(double deg) {
    double d = std::fmod(deg, 360.0);
    if (d < 0.0) d += 360.0;
    return d;
}

void VorReceiver::processSample(float x) {
    // Advance the two running NCOs.
    th30_ += d30_;      if (th30_ >= kTwoPi) th30_ -= kTwoPi;
    thSub_ += dSub_;    if (thSub_ >= kTwoPi) thSub_ -= kTwoPi;
    const double c30 = std::cos(th30_), s30 = std::sin(th30_);
    const double cSub = std::cos(thSub_), sSub = std::sin(thSub_);

    // (1) Variable 30 Hz: coherent product. Off-tone interferers average out
    //     over the integer-cycle sub-block.
    iv_ += x * c30;
    qv_ += x * s30;

    // (2) Reference 30 Hz: mix the 9960 subcarrier to baseband, low-pass,
    //     FM-discriminate (unwrap the phase difference -> Hz deviation).
    const double ib = x * cSub;
    const double qb = -x * sSub;
    subI_ += subLpAlpha_ * (ib - subI_);
    subQ_ += subLpAlpha_ * (qb - subQ_);
    const double curArg = std::atan2(subQ_, subI_);
    if (havePrevArg_) {
        double d = curArg - prevArg_;
        while (d >  kPi) d -= kTwoPi;
        while (d < -kPi) d += kTwoPi;
        const double devHz = d * fs_ / kTwoPi; // instantaneous freq deviation
        ir_ += devHz * c30;
        qr_ += devHz * s30;
    }
    prevArg_ = curArg;
    havePrevArg_ = true;

    // (3) 1020 Hz Morse keying: bandpass -> rectify -> envelope -> on/off.
    const float bp = morseBp_.process(x);
    const double e = std::fabs(bp);
    env_ += envAlpha_ * (e - env_);
    if (env_ > envPeak_) envPeak_ = env_;                 // fast up
    else                 envPeak_ += 0.001 * (env_ - envPeak_); // slow down

    bool on;
    if (envPeak_ < kMinMorseLevel) {
        on = false;                       // no carrier present: forced off
    } else {
        const double thr = 0.3 * envPeak_;
        const double onThr  = 1.2 * thr;
        const double offThr = 0.8 * thr;
        on = morseOn_ ? (env_ > offThr) : (env_ > onThr);
    }
    if (on == morseOn_) {
        ++morseRun_;
    } else {
        if (morseRun_ > 0) segs_.push_back({morseOn_, morseRun_ / fs_});
        morseOn_ = on;
        morseRun_ = 1;
    }

    // Fold a sub-measurement when the sub-block fills.
    ++subN_;
    const long subLen = static_cast<long>(fs_ * tokens::kVorBlockSeconds
                                          / tokens::kVorSubBlocks);
    if (subN_ >= subLen) endSubBlock();
}

// ---------------------------------------------------------------------------
void VorReceiver::endSubBlock() {
    const double varPhase = std::atan2(-qv_, iv_);
    const double refPhase = std::atan2(-qr_, ir_);
    const double radialK  = wrapDeg((varPhase - refPhase) * 180.0 / kPi);
    subRx_.push_back(std::cos(radialK * kPi / 180.0));
    subIy_.push_back(std::sin(radialK * kPi / 180.0));
    subVarMag_.push_back(std::hypot(iv_, qv_) / subN_);
    subRefMag_.push_back(std::hypot(ir_, qr_) / subN_);

    resetAccumulators();
    ++subCount_;
    if (subCount_ >= tokens::kVorSubBlocks) endBlock();
}

void VorReceiver::endBlock() {
    // Circular mean of the sub-measurement radials; resultant length R is the
    // honest confidence (R=1 all agree, R->0 scattered).
    double sx = 0.0, sy = 0.0, vm = 0.0, rm = 0.0;
    for (size_t i = 0; i < subRx_.size(); ++i) {
        sx += subRx_[i];
        sy += subIy_[i];
        vm += subVarMag_[i];
        rm += subRefMag_[i];
    }
    const double k = static_cast<double>(subRx_.size());
    const double R = (k > 0.0) ? std::hypot(sx, sy) / k : 0.0;
    const double meanRad = wrapDeg(std::atan2(sy, sx) * 180.0 / kPi);
    vm /= k; rm /= k;

    // Honest lock: the variable 30 Hz tone must actually be present AND the
    // sub-measurements must agree. Otherwise report UNLOCKED, never a guess.
    const bool locked = (vm >= kMinVarMag) && (R >= tokens::kVorQualityLock);

    pending_.locked    = locked;
    pending_.radialDeg = locked ? meanRad : 0.0;
    pending_.quality   = R;
    pending_.varLevel  = vm;
    pending_.refLevel  = rm;
    pending_.morseId   = QString::fromStdString(decodeMorseSegments(segs_, tokens::kVorMorseWpm));

    // Carry an in-progress keying across block boundaries: only drop the segment
    // list once the carrier has been quiet for a full inter-word gap (>=5 units),
    // so a station ID that straddles the 2 s measurement window is completed on a
    // later block instead of being truncated mid-character.
    double unit = -1.0;
    for (const Seg& s : segs_)
        if (s.on && (unit < 0.0 || s.sec < unit)) unit = s.sec;
    const bool keyingSettled =
        !segs_.empty() && !segs_.back().on && unit > 0.0 &&
        segs_.back().sec >= 5.0 * unit;
    if (keyingSettled) segs_.clear();

    subRx_.clear(); subIy_.clear(); subVarMag_.clear(); subRefMag_.clear();
    subCount_ = 0;
    haveResult_ = true;
}

// ---------------------------------------------------------------------------
std::string VorReceiver::decodeMorseSegments(const std::vector<Seg>& segs, double /*wpm*/) {
    if (segs.empty()) return "";
    double unit = -1.0;
    for (const Seg& s : segs)
        if (s.on && (unit < 0.0 || s.sec < unit)) unit = s.sec; // shortest mark = dot
    if (unit <= 0.0) return "";

    std::string out;
    std::string sym;
    auto flushChar = [&]() {
        if (sym.empty()) return;
        auto it = kMorseTable.find(sym);
        if (it != kMorseTable.end()) out.push_back(it->second);
        sym.clear();
    };
    for (const Seg& s : segs) {
        const double u = s.sec / unit;
        if (s.on) {
            sym.push_back(u < 2.0 ? '.' : '-');
        } else {
            if (u >= 5.0)      { flushChar(); out.push_back(' '); }
            else if (u >= 1.5) { flushChar(); }
            // <1.5: intra-character gap, keep accumulating symbols
        }
    }
    flushChar();
    // trim
    size_t a = out.find_first_not_of(' ');
    size_t b = out.find_last_not_of(' ');
    if (a == std::string::npos) return "";
    return out.substr(a, b - a + 1);
}

// ---------------------------------------------------------------------------
void VorReceiver::feed(const std::vector<float>& audio) {
    for (float x : audio) processSample(x);
}

VorResult VorReceiver::take() {
    // Flush any half-built Morse run into the segment list so a short clip still
    // yields its keying, then pull the pending reading.
    VorResult r;
    if (haveResult_) { r = pending_; pending_ = VorResult(); haveResult_ = false; }
    return r;
}

} // namespace dsp
} // namespace mbdsdr
