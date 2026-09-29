// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <vector>
#include <cstdint>

#include "dsp/demod.h"   // mbdsdr::dsp::FirLowpass

namespace mbdsdr {
namespace dsp {

// One fully received RDS group (4 blocks x 26 bit), after block sync and CRC.
// Block A..D are the 16 data bits of each block. Only groups whose A block
// passed CRC are surfaced; the decoder still exposes all four blocks so the
// upper layer can parse type/version/PTY/text fields.
struct RdsGroup {
    int  pi = 0;           // Block A (program identification), CRC-verified.
    int  blockB = 0;       // Block B raw 16 bits.
    int  blockC = 0;       // Block C raw 16 bits.
    int  blockD = 0;       // Block D raw 16 bits.
    int  groupType = 0;    // 0..15 = Block B high nibble.
    bool versionB = false; // Block B A/B version bit (true = B).
    int  pty = 0;         // Program Type 0..31 (Block B bits 9..5).
    bool crcOk = true;     // Block A CRC status (always true when surfaced).
};

// Accumulated program-service state the upper layer cares about.
struct RdsInfo {
    QString programService;  // PS (type 0A) assembled to 8 chars (caller trims).
    int     pty = -1;         // Program type, -1 = not yet received.
    QString radioText;       // RadioText (type 2A), up to 64 chars.
    bool    haveAny = false;  // At least one verified group decoded.
};

/// Offline, hardware-independent FM RDS (EN 300 401) data-link decoder.
///
/// Feed the de-emphasized WFM baseband MPX (which contains the 57 kHz
/// subcarrier) at ~240 kHz. The decoder mixes the 57 kHz subcarrier down to
/// baseband, low-passes, performs biphase / NRZ-I symbol recovery, block sync
/// on the 26-bit CRC blocks, and parses PS (0A), PTY and RadioText (2A).
///
/// Not implemented this round (left as TODO in code): CT clock (type 4),
/// TMC (type 8), and 0B/2B version fallback. Clean-room reimplementation from
/// the EN 300 401 standard; no GPL code copied.
class RdsDecoder {
public:
    explicit RdsDecoder(double sampleRateHz);

    /// Feed de-emphasized MPX baseband samples (contains the 57 kHz subcarrier).
    void feed(const std::vector<float>& mpx);

    /// CRC-verified whole groups since the last call.
    std::vector<RdsGroup> takeVerifiedGroups();

    /// Internal PS / PTY / RadioText state.
    RdsInfo info() const;

    void reset();

private:
    // ---- configuration ----
    double fs_ = 240000.0;

    // ---- 57 kHz complex mix-down NCO (I/Q arms; phase-invariant) ----
    float ncoPhase_ = 0.0f;   // radians, running
    float ncoDPhi_ = 0.0f;    // radians per sample
    FirLowpass lpf_;          // I-arm baseband low-pass after mixing
    FirLowpass lpfQ_;         // Q-arm baseband low-pass after mixing

    // ---- symbol strobe (PSK rate = 2 * 1187.5 = 2375 sym/s) ----
    double symFrac_ = 0.5;    // fractional accumulator (init = symbol center)
    double spsInv_ = 0.0;     // symbols advanced per sample
    float prevX_ = 0.0f;      // previous strobed complex sample (real arm)
    float prevY_ = 0.0f;      // previous strobed complex sample (imag arm)
    int   clockCount_ = 0;    // counts strobes, picks biphase polarity
    int   polarity_ = 0;       // chosen data-bearing (middle) strobe parity
    bool  havePolarity_ = false;
    float polarEvenSum_ = 0.0f;
    float polarOddSum_  = 0.0f;
    int   polarCount_ = 0;

    // ---- bit stream -> block sync ----
    enum class SyncState { NoSync, Sync };
    SyncState syncState_ = SyncState::NoSync;
    uint32_t  reg_ = 0;             // last 26 bits, MSB first
    uint32_t  bitCounter_ = 0;     // total bits fed (presync distance)
    bool      presync_ = false;
    int       lastSeenOffset_ = 0;
    uint32_t  lastSeenOffsetCounter_ = 0;
    int       blockNumber_ = 0;     // 0=A 1=B 2=C/C' 3=D
    int       blockBitCounter_ = 0;
    int       wrongBlocks_ = 0;
    int       blocksCounter_ = 0;
    bool      groupAssemblyStarted_ = false;
    int       groupGoodBlocks_ = 0;
    uint32_t  group_[4] = {0,0,0,0};

    // ---- assembled output ----
    std::vector<RdsGroup> verifiedGroups_;

    // ---- PS / RT parse state ----
    char ps_[8] = {' ',' ',' ',' ',' ',' ',' ',' '};
    int  psSegmentFlags_ = 0;
    char rt_[64] = {0};
    int  rtSegmentFlags_ = 0;
    int  rtABFlag_ = 0;
    int  pty_ = -1;
    bool haveAny_ = false;

    // ---- helpers ----
    void processBit(bool bit);
    void handleGroup();
    void parseGroup(const RdsGroup& g);
};

} // namespace dsp
} // namespace mbdsdr
