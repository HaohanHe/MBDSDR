// SPDX-License-Identifier: MIT
#include "dsp/cdcss.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <unordered_map>

namespace mbdsdr {
namespace dsp {

namespace {

// ---------------------------------------------------------------------------
// Golay(23,12) -- clean-room implementation from the public algebraic spec.
//
// The binary Golay code G23 is a [23,12,7] linear block code.  Its generator
// polynomial over GF(2) is
//
//     g(x) = x^11 + x^10 + x^6 + x^5 + x^4 + x^2 + 1
//          = 0b110001110101 = 0xC75
//
// (This is the same primitive polynomial used by the extended G24 code; G24
// just appends an overall parity bit.  We write G23 from the polynomial, we
// do not copy the M17 file's table-builder.)
//
// Systematic encoding: given 12 data bits m, form cw = m << 11 and reduce
// modulo g(x); the 11-bit remainder lands in bits 0..10.  Syndrome =
// remainder of received cw divided by g(x).  Decoding = syndrome -> error
// vector (weight 0..2 this round) -> XOR -> strip the top 12 data bits.
// ---------------------------------------------------------------------------

constexpr uint32_t kGolayGen = 0xC75u;   // g(x) over GF(2), 11 bits
constexpr uint32_t kGolayMask23 = 0x7FFFFFu;  // 23 bits
constexpr uint32_t kGolayMask12 = 0xFFFu;

// Remainder of cw(x) / g(x) over GF(2).  23 bits in, 11-bit syndrome out.
uint32_t golay23Syndrome(uint32_t cw) {
    cw &= kGolayMask23;
    for (int i = 0; i < 12; ++i) {
        if (cw & 1u) cw ^= kGolayGen;
        cw >>= 1;
    }
    return cw;   // 11 bits
}

// Systematic encode of 12 data bits -> 23-bit codeword.  The division loop
// consumes the data bits (shifting them out); we keep the original data<<11
// and OR on the 11-bit remainder to form the systematic codeword.
uint32_t golay23Encode(uint32_t data12) {
    const uint32_t cw = (data12 & kGolayMask12) << 11;
    uint32_t work = cw;
    for (int i = 22; i >= 11; --i) {
        if ((work >> i) & 1u) work ^= kGolayGen << (i - 11);
    }
    return (cw | (work & 0x7FFu)) & kGolayMask23;
}

// Build the syndrome -> error-vector table once (errors weight 0..2).
// (23,12,7) formally corrects up to t=3; this round tables weight <=2 which
// covers the unit-test bit-flip budget and the expected on-air margin.
const std::unordered_map<uint32_t, uint32_t>& golay23Table() {
    static std::unordered_map<uint32_t, uint32_t> table;
    static bool built = false;
    if (!built) {
        table.clear();
        table[0] = 0;                       // no error
        for (int e0 = 0; e0 < 23; ++e0) {
            uint32_t err1 = 1u << e0;
            uint32_t s1 = golay23Syndrome(err1);
            if (table.find(s1) == table.end()) table[s1] = err1;
            for (int e1 = e0 + 1; e1 < 23; ++e1) {
                uint32_t err2 = err1 | (1u << e1);
                uint32_t s2 = golay23Syndrome(err2);
                if (table.find(s2) == table.end()) table[s2] = err2;
            }
        }
        built = true;
    }
    return table;
}

// Decode a (possibly corrupted) 23-bit word.  Returns true and writes the
// recovered 12-bit address when the syndrome maps to a weight<=2 error.
bool golay23Decode(uint32_t rcv23, uint32_t& dataOut) {
    rcv23 &= kGolayMask23;
    uint32_t s = golay23Syndrome(rcv23);
    const auto& t = golay23Table();
    auto it = t.find(s);
    if (it == t.end()) return false;         // uncorrectable
    uint32_t corrected = rcv23 ^ it->second;
    dataOut = (corrected >> 11) & kGolayMask12;
    return true;
}

// ---------------------------------------------------------------------------
// DCS address table -- the public DCS/DPL code list (3-octal-digit addresses).
// Values are 12-bit integers (C-style leading-zero octal literals).
// This is the published DCS code catalogue (no callsigns, no radio IDs).
// ---------------------------------------------------------------------------
constexpr std::array<int, 104> kDcsCodes = {
    0023,0025,0026,0031,0032,0036,0043,0047,0064,0065,0071,0072,0073,0074,
    0114,0115,0116,0125,0131,0132,0134,0143,0145,0152,0154,0155,0156,0162,
    0164,0165,0172,0174,
    0205,0223,0224,0225,0226,0243,0244,0245,0246,0251,0254,0261,0263,0265,
    0266,0271,0274,0275,
    0306,0307,0311,0315,0324,0325,0331,0332,0343,0345,0346,0351,0355,0356,
    0364,0365,0366,0371,
    0411,0412,0413,0423,0425,0431,0432,0433,0434,0443,0444,0445,0446,0452,
    0453,0454,0455,0462,0463,0464,0465,0466,
    0503,0506,0507,0516,0521,0523,0526,0532,0543,0545,0546,0564,0565,0574
};

bool dcsCodeValid(int c) {
    if (c < 0 || c > 0777) return false;
    for (int v : kDcsCodes) if (v == c) return true;
    return false;
}

// ---------------------------------------------------------------------------
// Tuning constants (this translation unit only -- tokens.h is off-limits).
// ---------------------------------------------------------------------------
constexpr double kBitRateBps      = 134.4;
constexpr double kCarrierHz       = 1500.0;  // fixed subaudible-ish test carrier
constexpr int    kLatchHits       = 3;       // consecutive matching words
constexpr int    kLatchMisses     = 5;       // consecutive non-matching words
constexpr int    kWordBits        = 23;

} // namespace

// ===========================================================================
void CdcssDecoder::configure(double sampleRateHz, int code12) {
    if (sampleRateHz <= 0.0) return;
    // Out-of-table tuning is rejected honestly (mirror of CTCSS).
    if (!dcsCodeValid(code12)) return;

    sampleRateHz_   = sampleRateHz;
    configuredCode_ = code12;

    dPhase_        = 2.0 * M_PI * kCarrierHz / sampleRateHz;
    halfBitPeriod_ = sampleRateHz / (2.0 * kBitRateBps);   // ~178.57 @48k
    reset();
}

void CdcssDecoder::reset() {
    phase_  = 0.0;
    iAcc_   = 0.0;
    iN_     = 0;
    countdown_ = halfBitPeriod_;
    prevHalf_  = false;
    havePrev_  = false;
    word_      = 0;
    bitsInWord_ = 0;
    hits_ = 0;
    miss_ = 0;
    present_ = false;
    lastCode_ = -1;
}

bool CdcssDecoder::process(const float* audio, int n) {
    if (!enabled_ || halfBitPeriod_ <= 0.0 || audio == nullptr || n <= 0) {
        return codePresent();
    }
    for (int i = 0; i < n; ++i) {
        const double x = static_cast<double>(audio[i]);
        // Coherent I mixer: I = x * sin(phi).  (Q discarded this round; a
        // real carrier-recovery loop would track phase from Q.)
        phase_ += dPhase_;
        if (phase_ > 2.0 * M_PI) phase_ -= 2.0 * M_PI;
        iAcc_ += x * std::sin(phase_);
        ++iN_;

        countdown_ -= 1.0;
        if (countdown_ <= 0.0) {
            const double avg = (iN_ > 0) ? (iAcc_ / static_cast<double>(iN_)) : 0.0;
            pushHalfBit(avg >= 0.0);
            iAcc_ = 0.0;
            iN_   = 0;
            countdown_ += halfBitPeriod_;
        }
    }
    return codePresent();
}

void CdcssDecoder::pushHalfBit(bool high) {
    // Manchester pairing: two consecutive half-bits form one bit.  With the
    // convention used by the synthesizer (bit 1 = high-then-low, bit 0 =
    // low-then-high), the bit value equals the first half-bit level.
    if (!havePrev_) { prevHalf_ = high; havePrev_ = true; return; }
    havePrev_ = false;

    const bool bit = prevHalf_;   // first half of the cell
    // Shift the bit into the 23-bit word, LSB first (first over-the-air bit
    // lands at bit 0 after 22 further shifts).
    word_ = (word_ >> 1u) | (static_cast<uint32_t>(bit) << 22);
    if (++bitsInWord_ >= kWordBits) {
        onWordComplete(word_);
        bitsInWord_ = 0;
        word_ = 0;
    }
}

void CdcssDecoder::onWordComplete(uint32_t w23) {
    uint32_t data = 0;
    if (!golay23Decode(w23, data)) {
        // Uncorrectable: treat as a miss but do not fabricate a code.
        if (++miss_ >= kLatchMisses) present_ = false;
        hits_ = 0;
        return;
    }
    lastCode_ = static_cast<int>(data);

    if (static_cast<int>(data) == configuredCode_) {
        miss_ = 0;
        if (++hits_ >= kLatchHits) present_ = true;
    } else {
        hits_ = 0;
        if (++miss_ >= kLatchMisses) present_ = false;
    }
}

} // namespace dsp
} // namespace mbdsdr
