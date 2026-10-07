// SPDX-License-Identifier: MIT
//
// ACARS (ARINC 618) packet-text decoder -- clean-room, public standard only.
//
// Mechanism (learned from public ARINC 618 description, re-derived):
//   VHF airband ACARS carries 8-bit characters over MSK at 2400 bit/s with
//   +/-600 Hz peak deviation.  MSK is recovered here by reusing the existing
//   2-FSK demodulator (FskDemod): at symbolRate=2400 Bd and deviation=600 Hz
//   the modulation index h = 2*dev/rate = 0.5, i.e. exactly MSK.
//
// Wire character frame (after the TX has serialized bytes LSB-first onto the
// MSK carrier):
//
//   preamble 0x7F (>= kMinPreambleBytes bytes)
//     -> SYN 0x01
//     -> BOT 0x8B (air->ground) | 0x86 (ground->air)
//     -> message body (mode, label, blockId, ack, text)
//     -> ETX 0x03
//     -> 2 block-check bytes (CRC-16/CCITT-FALSE, big-endian)
//     -> EOT 0x04
//
// Block check = CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no xorout)
// computed over BOT..ETX inclusive.  Frames whose block check fails are still
// EMITTED with crcOk=false (honest reporting, never dropped silently).
//
// No hardcoded RF carrier frequency (131.525 MHz etc. lives in docs only).
#pragma once

#include <complex>
#include <cstdint>
#include <string>
#include <vector>

#include "fsk_demod.h"   // reuse: MSK recovered as 2-FSK 2400 Bd / +/-600 Hz

namespace mbdsdr {
namespace dsp {

// ---- Named wire constants (no magic numbers anywhere) ---------------------
inline constexpr double   kAcarsSymbolRateBd = 2400.0;   // 2400 bit/s MSK
inline constexpr double   kAcarsDeviationHz  = 600.0;    // +/- peak deviation
inline constexpr uint8_t  kAcarsPreambleChar = 0x7Fu;    // bit-sync / word-sync
inline constexpr uint8_t  kAcarsSynChar      = 0x01u;    // synchronize
inline constexpr uint8_t  kAcarsBotAir       = 0x8Bu;    // begin-of-transmission, aircraft -> ground
inline constexpr uint8_t  kAcarsBotGround    = 0x86u;    // begin-of-transmission, ground -> aircraft
inline constexpr uint8_t  kAcarsEtxChar      = 0x03u;    // end-of-text
inline constexpr uint8_t  kAcarsEotChar      = 0x04u;    // end-of-transmission
inline constexpr int      kAcarsMinPreambleBytes = 3;    // >=3 x 0x7F before SYN
inline constexpr int      kAcarsMaxFrameBytes    = 256; // safety bound on body
inline constexpr int      kAcarsKeepTailBits     = 256; // rx-bit ring tail to keep
inline constexpr uint16_t kAcarsCrcPoly      = 0x1021u;  // CRC-16/CCITT-FALSE
inline constexpr uint16_t kAcarsCrcInit      = 0xFFFFu;
inline constexpr char     kAcarsNoAckChar    = '_';      // emitted when no ack char

struct AcarsPacket {
    enum class Direction { Unknown, Air, Ground };

    Direction direction = Direction::Unknown;
    std::string mode;      // 2-char mode word
    std::string label;     // 2-char label
    std::string blockId;   // 1 char
    std::string ack;       // 1 char ('_' when no ack char)
    std::string text;
    bool crcOk = false;    // block check over BOT..ETX inclusive
};

class AcarsDecoder {
public:
    AcarsDecoder();
    explicit AcarsDecoder(double sampleRateHz);

    void setSampleRate(double hz);
    void feed(const std::vector<std::complex<float>>& baseband);
    std::vector<AcarsPacket> takeNewPackets();
    void reset();

    // CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF). Public so tests and the
    // TX-side fixture can share the exact same block-check.
    static uint16_t crc16(const uint8_t* data, int len);

private:
    void rebuildDemod();
    void startCollect(uint8_t botByte);
    void processCollectedByte(uint8_t b);
    void emitPacket();
    void abortFrame();

    double sampleRateHz_ = 48000.0;
    FskDemod demod_;

    // Recovered-but-unconsumed bits (oldest first), FIFO.
    std::vector<int> bits_;
    std::size_t pos_ = 0;          // next unconsumed bit index into bits_

    // Byte/framing state machine.
    enum class Phase { Hunt, Collect } phase_ = Phase::Hunt;
    std::vector<uint8_t> frame_;   // BOT .. ETX inclusive (growing)
    int  subPhase_ = 0;            // 0 body, 1 crcHi, 2 crcLo, 3 expect-EOT
    uint8_t curByte_ = 0;
    int  bitsInCurByte_ = 0;
    uint8_t crcHi_ = 0, crcLo_ = 0;

    std::vector<AcarsPacket> out_;

    uint8_t byteAt(std::size_t bitIdx) const;
};

} // namespace dsp
} // namespace mbdsdr
