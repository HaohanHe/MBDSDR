// SPDX-License-Identifier: MIT
//
// NAVTEX / SITOR-B (CCIR 476 / ITU-R M.540) packet-text decoder.
//
// Clean-room implementation: protocol mechanism derived from public
// ITU-R M.540 (message layout) and CCIR 476 / M.476 (SITOR-B FEC interleave)
// and the ITA-2 / CCITT-2 alphabet. No GPL source copied; no hardcoded RF
// frequencies (518/490 kHz live in docs only, never here).
//
// Chain:
//   complex baseband IQ (2-FSK, 100 baud, +/-85 Hz deviation)
//     -> owned FskDemod (symbolRateBd=100, deviationHz=85) -> hard bits
//     -> 5-bit ITA-2 character grouping (synchronous, no start/stop)
//     -> SITOR-B wire de-interleave (msg channel vs delayed copy channel)
//     -> diversity mismatch counting (honest; no silent correction)
//     -> frame state machine: phasing (XYXY, >=20) -> ZCZC -> space
//        -> B1 B2 B3 B4 -> ITA-2 letters/figures text -> NNNN end
//
// The FskDemod is held opaquely (void*) so this header does not leak
// fsk_demod.h includes into integration units.
#pragma once

#include <complex>
#include <string>
#include <vector>

namespace mbdsdr {
namespace dsp {

// -- Frozen named constants (see _PHASE60_SPEC.md, "Decoder contracts") -------
constexpr double kNavtexSymbolRateBd   = 100.0;   // baud
constexpr double kNavtexDeviationHz    = 85.0;    // +/- peak deviation
constexpr int    kNavtexBitsPerChar   = 5;       // ITA-2 synchronous char
constexpr int    kNavtexCharsPerSec    = 20;      // 100 bd / 5 bits
constexpr int    kIta2Null             = 0;
constexpr int    kIta2Space            = 4;
constexpr int    kIta2Cr               = 8;
constexpr int    kIta2Lf               = 2;
constexpr int    kIta2Figs             = 27;     // figures-shift code
constexpr int    kIta2Ltrs             = 31;     // letters-shift code
constexpr int    kIta2Z                = 17;
constexpr int    kIta2C                = 14;
constexpr int    kIta2N                = 12;
constexpr int    kNavtexPhasingA       = 30;     // phasing tone A (V)
constexpr int    kNavtexPhasingB       = 15;     // phasing tone B (K)
constexpr int    kNavtexPhasingMinChars= 20;     // >= this locks phasing

// -- Decoded message ---------------------------------------------------------
struct NavtexMessage {
    std::string stationB1;    // 1 char
    std::string typeB2;       // 1 char
    std::string numberB3B4;   // 2 chars
    std::string text;
    bool diversityOk  = true;
    bool phasingOk    = false;
    int  diversityErrors = 0; // honest mismatch count (msg channel vs copy)
};

// -- Decoder -----------------------------------------------------------------
// Non-copyable: owns an opaque FskDemod with streaming state.
class NavtexDecoder {
public:
    NavtexDecoder();
    ~NavtexDecoder();

    NavtexDecoder(const NavtexDecoder&) = delete;
    NavtexDecoder& operator=(const NavtexDecoder&) = delete;

    // MUST be called before feed(); (re)builds the internal FskDemod.
    void setSampleRate(double hz);

    // Feed channelized complex baseband IQ. Blocks may be arbitrary length.
    void feed(const std::vector<std::complex<float>>& baseband);

    // Drain decoded messages emitted since the last take (clears queue).
    std::vector<NavtexMessage> takeNewMessages();

    // Reset all demod / state-machine / diversity state.
    void reset();

private:
    void* demod_ = nullptr;   // opaque FskDemod (cpp casts it)
    double sampleRateHz_ = 0.0;

    // Bit stream buffer + 5-bit char alignment search.
    // The owned FskDemod recovers symbol-aligned bits, but the group delay of
    // its Nuttall LPF places the first recovered symbol at an arbitrary offset
    // within the 5-bit ITA-2 char.  We scan the buffered bits for the phasing
    // pattern (alternating A=30 / B=15) across all 5 possible bit offsets and
    // lock onto the alignment that yields a >= kNavtexPhasingMinChars run.
    std::vector<int> bits_;        // recovered bits since last drain
    int  alignBits_ = -1;          // -1 = not aligned yet; else 0..4 offset
    long charConsumed_ = 0;        // how many chars already handed to state machine

    // SITOR-B wire de-interleave state
    long   wireIdx_ = 0;      // position in the wire character stream
    std::vector<int> msgChars_;   // message-channel chars so far (for diversity)

    // Frame state machine
    enum class FState {
        kIdle,           // scanning for phasing run
        kPreamble,       // phasing locked, expecting ZCZC
        kExpectSpace,    // ZCZC seen, expect space
        kHeader,         // reading B1 B2 B3 B4 (4 chars)
        kText            // accumulating body until NNNN
    };
    FState  fstate_ = FState::kIdle;
    int     phaseCount_ = 0;      // consecutive alternating phasing chars
    int     phaseExpect_ = kNavtexPhasingA; // next expected phasing char
    int     preambleGot_ = 0;     // how many of Z,C,Z,C seen
    int     headerGot_ = 0;        // 0..4
    std::string headerBuf_;
    int     nRun_ = 0;            // consecutive N chars in text
    // ITA-2 shift for the current message body
    bool    shiftFigures_ = false;
    // Per-message accumulators
    NavtexMessage cur_;
    int  diversityErrors_ = 0;    // running count for the message in progress
    bool phasingOk_ = false;

    std::vector<NavtexMessage> out_;

    // Internal helpers
    void processChar(int code);          // one 5-bit char from the wire
    void handleMessageChar(int code);    // char on the message channel
    void resetFrameState();
    void tryAlign();                     // scan bits_ for phasing alignment
    int  charAt(int alignOffset, long charIdx) const; // 5-bit code at char idx
};

// -- ITA-2 tables (free functions, public so tests can round-trip) -----------
// code is the 5-bit value 0..31. Returns the ASCII letter in letters case,
// or a placeholder ('.') for null/shift/unknown.
char ita2Letters(int code);
char ita2Figures(int code);

} // namespace dsp
} // namespace mbdsdr
