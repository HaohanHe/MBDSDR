// SPDX-License-Identifier: MIT
#include "rds_decoder.h"

#include <cmath>
#include <cstring>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

// ---------------------------------------------------------------------------
// EN 300 401 (RDS) block code.
//
// Each RDS block is 26 bits: 16 data bits + 10 check bits. The check bits are
// the remainder of a cyclic code with generator polynomial
//
//   G(x) = x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1   (= 0x5B9)
//
// then XORed with a block-specific offset word. The offset words (Annex C,
// table C.1) and their positions in the 4-block group frame:
//   A=252 pos0, B=408 pos1, C=360 pos2, D=436 pos3, C'=848 pos2.
// ---------------------------------------------------------------------------

namespace {

constexpr uint32_t kPoly = 0x5B9u;       // G(x), 10 bits
constexpr int       kPolyLen = 10;

const uint32_t kOffsetWord[5] = {252, 408, 360, 436, 848};
const int      kOffsetPos[5]   = {0, 1, 2, 3, 2};

// Syndrome of the 16 data bits followed by kPolyLen zero bits.
// (Equivalent to dividing by G(x) and keeping the remainder.)
uint32_t calcSyndrome(uint32_t message, int mlen) {
    uint32_t reg = 0;
    for (int i = mlen; i > 0; --i) {
        reg = (reg << 1) | ((message >> (i - 1)) & 0x1u);
        if (reg & (1u << kPolyLen)) reg ^= kPoly;
    }
    for (int i = kPolyLen; i > 0; --i) {
        reg <<= 1;
        if (reg & (1u << kPolyLen)) reg ^= kPoly;
    }
    return reg & ((1u << kPolyLen) - 1u);
}

// Precomputed block syndromes: calcSyndrome(offsetWord[j], 10).
// Cross-checked against gqrx constants.h: {383, 14, 303, 663, 748}.
uint32_t blockSyndrome(int j) {
    return calcSyndrome(kOffsetWord[j], kPolyLen);
}

} // namespace

RdsDecoder::RdsDecoder(double sampleRateHz)
    : fs_(sampleRateHz),
      lpf_(2500.0 / fs_, 255),
      lpfQ_(2500.0 / fs_, 255) {
    ncoDPhi_ = 2.0f * (float)M_PI * 57000.0f / (float)fs_;
    spsInv_ = 2375.0 / fs_;           // PSK symbols (2x bit rate) per sample
    // Align the free-running strobe to the PSK symbol centers, accounting for
    // the linear-phase LPF group delay ((taps-1)/2 samples).
    double sps = fs_ / 2375.0;
    double delaySamples = (255 - 1) / 2.0;
    double firstStrobe = 0.5 * sps + delaySamples;
    symFrac_ = 1.0 - (firstStrobe + 1.0) * spsInv_;
    while (symFrac_ < 0.0) symFrac_ += 1.0;
    while (symFrac_ >= 1.0) symFrac_ -= 1.0;
}

void RdsDecoder::reset() {
    ncoPhase_ = 0.0f;
    lpf_.reset();
    lpfQ_.reset();
    double sps = fs_ / 2375.0;
    double delaySamples = (255 - 1) / 2.0;
    double firstStrobe = 0.5 * sps + delaySamples;
    symFrac_ = 1.0 - (firstStrobe + 1.0) * spsInv_;
    while (symFrac_ < 0.0) symFrac_ += 1.0;
    while (symFrac_ >= 1.0) symFrac_ -= 1.0;

    prevX_ = 0.0f;
    prevY_ = 0.0f;
    clockCount_ = 0;
    polarity_ = 0;
    havePolarity_ = false;
    polarEvenSum_ = polarOddSum_ = 0.0f;
    polarCount_ = 0;

    syncState_ = SyncState::NoSync;
    reg_ = 0;
    bitCounter_ = 0;
    presync_ = false;
    lastSeenOffset_ = 0;
    lastSeenOffsetCounter_ = 0;
    blockNumber_ = 0;
    blockBitCounter_ = 0;
    wrongBlocks_ = 0;
    blocksCounter_ = 0;
    groupAssemblyStarted_ = false;
    groupGoodBlocks_ = 0;
    group_[0] = group_[1] = group_[2] = group_[3] = 0;

    verifiedGroups_.clear();

    std::memset(ps_, ' ', sizeof(ps_));
    psSegmentFlags_ = 0;
    std::memset(rt_, 0, sizeof(rt_));
    rtSegmentFlags_ = 0;
    rtABFlag_ = 0;
    pty_ = -1;
    haveAny_ = false;
}

void RdsDecoder::feed(const std::vector<float>& mpx) {
    if (mpx.empty()) return;

    // 1) Complex mix-down: z_i = mpx_i * (cos φ - j sin φ). Keeping both I and Q
    //    arms lets us detect the biphase data with a phase-invariant differential
    //    product below, so an arbitrary (or slowly drifting) 57 kHz carrier
    //    phase no longer fades the real arm to zero.
    std::vector<float> mixedI(mpx.size());
    std::vector<float> mixedQ(mpx.size());
    for (std::size_t i = 0; i < mpx.size(); ++i) {
        const float c = std::cos(ncoPhase_);
        const float s = std::sin(ncoPhase_);
        mixedI[i] = mpx[i] * c;
        mixedQ[i] = -mpx[i] * s;
        ncoPhase_ += ncoDPhi_;
        while (ncoPhase_ > (float)M_PI) ncoPhase_ -= 2.0f * (float)M_PI;
        while (ncoPhase_ < -(float)M_PI) ncoPhase_ += 2.0f * (float)M_PI;
    }

    // 2) Low-pass each arm to the biphase main lobe (~2.4 kHz); matched delays.
    std::vector<float> iLp, qLp;
    lpf_.process(mixedI, iLp);
    lpfQ_.process(mixedQ, qLp);

    // 3) Strobe at the PSK symbol rate (2375 sym/s = 2 x 1187.5 bit/s). At each
    //    strobe we hold a complex sample z_k = x_k + j y_k. The biphase data is
    //    recovered with the phase-invariant differential product
    //        dot = Re(z_k * conj(z_{k-1})) = x_k*x_{k-1} + y_k*y_{k-1}
    //    whose e^{jφ} cancels between consecutive strobes. Boundary strobes always
    //    carry a transition (large |z_k-z_{k-1}|); middle strobes carry the data
    //    (dot = +1 for a '1', -1 for a '0'), so no NRZ-I delta decode is needed.
    const std::size_t n = iLp.size();
    for (std::size_t idx = 0; idx < n; ++idx) {
        symFrac_ += spsInv_;
        if (symFrac_ < 1.0) continue;
        symFrac_ -= 1.0;

        const float x = iLp[idx];
        const float y = qLp[idx];
        const float dot = x * prevX_ + y * prevY_;      // Re(z_k * conj(z_{k-1}))
        const float dx = x - prevX_;
        const float dy = y - prevY_;
        const float dmag2 = dx * dx + dy * dy;            // transition energy
        prevX_ = x;
        prevY_ = y;

        if ((clockCount_ & 1) == 0) polarEvenSum_ += dmag2;
        else                       polarOddSum_  += dmag2;
        ++polarCount_;

        if (!havePolarity_ && polarCount_ >= 64) {
            // Higher transition energy = boundary strobes; the lower-energy parity
            // is the data-bearing middle strobes we sample.
            polarity_ = (polarEvenSum_ < polarOddSum_) ? 0 : 1;
            havePolarity_ = true;
        }

        if (havePolarity_ && (clockCount_ & 1) == polarity_) {
            // Middle strobe: dot sign directly yields the biphase bit.
            processBit(dot >= 0.0f);
        }
        ++clockCount_;
    }
}

void RdsDecoder::processBit(bool bit) {
    reg_ = ((reg_ << 1) | (bit ? 1u : 0u)) & 0x3FFFFFFu;   // keep 26 bits
    ++bitCounter_;

    if (syncState_ == SyncState::NoSync) {
        // Slide the 26-bit window; match a known block syndrome to presync.
        const uint32_t syn = calcSyndrome(reg_, 26);
        for (int j = 0; j < 5; ++j) {
            if (syn != blockSyndrome(j)) continue;
            if (!presync_) {
                presync_ = true;
                lastSeenOffset_ = j;
                lastSeenOffsetCounter_ = bitCounter_;
            } else {
                const uint32_t bitDistance = bitCounter_ - lastSeenOffsetCounter_;
                int blockDistance;
                if (kOffsetPos[lastSeenOffset_] >= kOffsetPos[j])
                    blockDistance = kOffsetPos[j] + 4 - kOffsetPos[lastSeenOffset_];
                else
                    blockDistance = kOffsetPos[j] - kOffsetPos[lastSeenOffset_];
                if ((uint32_t)(blockDistance * 26u) != bitDistance) {
                    presync_ = false;
                } else {
                    // Locked: the current window is block j, next is j+1.
                    syncState_ = SyncState::Sync;
                    wrongBlocks_ = 0;
                    blocksCounter_ = 0;
                    blockBitCounter_ = 0;
                    blockNumber_ = (j + 1) % 4;
                    groupAssemblyStarted_ = false;
                }
            }
            break;
        }
        return;
    }

    // SYNC: wait until 26 bits have accumulated, then check the block.
    if (blockBitCounter_ < 25) {
        ++blockBitCounter_;
        return;
    }
    blockBitCounter_ = 0;

    const uint32_t dataword  = (reg_ >> 10) & 0xFFFFu;
    const uint32_t calcCrc  = calcSyndrome(dataword, 16);
    const uint32_t checkword = reg_ & 0x3FFu;

    bool good = false;
    if (blockNumber_ == 2) {
        // Block C may carry offset C (360) or C' (848).
        if ((checkword ^ kOffsetWord[2]) == calcCrc)       good = true;
        else if ((checkword ^ kOffsetWord[4]) == calcCrc) good = true;
    } else {
        if ((checkword ^ kOffsetWord[blockNumber_]) == calcCrc) good = true;
    }
    if (!good) ++wrongBlocks_;

    if (blockNumber_ == 0 && good) {
        groupAssemblyStarted_ = true;
        groupGoodBlocks_ = 1;
    }
    if (groupAssemblyStarted_) {
        if (!good) {
            groupAssemblyStarted_ = false;
        } else {
            group_[blockNumber_] = dataword;
            ++groupGoodBlocks_;
        }
        if (groupGoodBlocks_ == 5) {   // A counted once + A,B,C,D increments
            handleGroup();
        }
    }

    blockNumber_ = (blockNumber_ + 1) % 4;
    ++blocksCounter_;
    if (blocksCounter_ >= 50) {
        if (wrongBlocks_ > 35) {       // lost sync -> re-acquire
            syncState_ = SyncState::NoSync;
            presync_ = false;
        }
        blocksCounter_ = 0;
        wrongBlocks_ = 0;
    }
}

void RdsDecoder::handleGroup() {
    RdsGroup g;
    g.pi       = static_cast<int>(group_[0]);
    g.blockB   = static_cast<int>(group_[1]);
    g.blockC   = static_cast<int>(group_[2]);
    g.blockD   = static_cast<int>(group_[3]);
    g.groupType = static_cast<int>((group_[1] >> 12) & 0xFu);
    g.versionB  = ((group_[1] >> 11) & 0x1u) != 0;
    g.pty       = static_cast<int>((group_[1] >> 5) & 0x1Fu);
    g.crcOk = true;
    haveAny_ = true;
    verifiedGroups_.push_back(g);
    parseGroup(g);
}

void RdsDecoder::parseGroup(const RdsGroup& g) {
    pty_ = g.pty;

    if (g.groupType == 0 && !g.versionB) {
        // Basic tuning/PS: Block D holds two chars, segment address = Block B[1:0].
        const int seg = g.blockB & 0x3;
        ps_[seg * 2]     = static_cast<char>((g.blockD >> 8) & 0xFF);
        ps_[seg * 2 + 1] = static_cast<char>(g.blockD & 0xFF);
        psSegmentFlags_ |= (1 << seg);
    } else if (g.groupType == 2 && !g.versionB) {
        // RadioText (2A): Block C+D hold four chars, segment address = Block B[3:0].
        const int seg = g.blockB & 0xF;
        const int ab  = (g.blockB >> 4) & 0x1;
        if (ab != rtABFlag_) {
            rtSegmentFlags_ = 0;
            std::memset(rt_, 0, sizeof(rt_));
        }
        rtABFlag_ = ab;
        rt_[seg * 4]     = static_cast<char>((g.blockC >> 8) & 0xFF);
        rt_[seg * 4 + 1] = static_cast<char>(g.blockC & 0xFF);
        rt_[seg * 4 + 2] = static_cast<char>((g.blockD >> 8) & 0xFF);
        rt_[seg * 4 + 3] = static_cast<char>(g.blockD & 0xFF);
        rtSegmentFlags_ |= (1 << seg);
    }
    // TODO(rds): type 4 Clock-Time, type 8 TMC, and 0B/2B group versions are
    // intentionally not implemented in this offline core slice.
}

std::vector<RdsGroup> RdsDecoder::takeVerifiedGroups() {
    auto ret = std::move(verifiedGroups_);
    verifiedGroups_.clear();
    return ret;
}

RdsInfo RdsDecoder::info() const {
    RdsInfo i;
    i.programService = QString::fromLatin1(ps_, 8);
    i.pty = pty_;
    int len = 0;
    while (len < 64 && rt_[len] != '\0' && rt_[len] != '\r') ++len;
    i.radioText = QString::fromLatin1(rt_, len);
    i.haveAny = haveAny_;
    return i;
}

} // namespace dsp
} // namespace mbdsdr
