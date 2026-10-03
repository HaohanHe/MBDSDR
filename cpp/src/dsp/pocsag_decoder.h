// SPDX-License-Identifier: MIT
//
// POCSAG (CCIR Radiopaging Code No.1) pager decoder -- clean-room C++.
//
// Protocol facts below are public standard (CCIR/RCC paging code No.1); the
// DSP, framing state machine, BCH table derivation and naming here are written
// from scratch. Mechanism studied from the project's own mbdsdr_ai/
// pocsag_decoder.py (MIT) and, for the streaming feed->take shape only, from
// adsb_decoder.h / rds_decoder.h. No GPL code (SDR++ pager_decoder) was copied.
//
// Air interface (what this block consumes):
//   - Modulation is 2-FSK already demodulated upstream by FskDemod, which hands
//     back hard symbol decisions (bits 0/1). This block therefore does no
//     discriminator / clock recovery; it only aligns on the frame sync.
//   - A batch = 32-bit sync word SC + 16 codewords (8 frames x 2 words).
//   - Codeword (32 bit, MSB first):
//       bit31      = message flag (0 = address, 1 = message)
//       bits30..11 = 20 data bits (21 info bits incl. bit31)
//       bits10..1  = BCH(31,21) parity (10 bits)
//       bit0       = overall even parity
//   - Numeric mode packs 5 BCD nibbles per message word; alpha mode packs
//     7-bit bit-reversed ASCII into the continuous nibble stream.
//   - Address words carry the RIC (high 18 bits) + 2 function bits; the low 3
//     RIC bits equal the frame number (0..7) where the address slot lives.
//
// Output: message words are grouped under the most recent address word and
// surfaced via takeMessages() -- the same feed()/take*() shape as the other
// dsp decoders. Pure C++17, deterministic, no hardware, no UI.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace mbdsdr {
namespace dsp {

// ---- Named protocol constants (public standard) ------------------------
// Callers reference these rather than raw magic hex.
constexpr uint32_t kPocsagSyncWord    = 0x7CD215D8u;  // batch sync (SC)
constexpr uint32_t kPocsagIdleWord    = 0x7A89C197u;  // idle codeword
constexpr uint32_t kPocsagMessageFlag = 0x80000000u;  // bit31: 1 = message
constexpr int      kPocsagPreambleBits = 576;          // 1010... preamble
constexpr int      kPocsagBitsPerWord  = 32;
constexpr int      kPocsagWordsPerBatch = 16;          // 8 frames x 2
constexpr int      kPocsagInfoBits     = 21;           // BCH(31,21) info
constexpr int      kPocsagParityBits   = 10;           // BCH parity length
constexpr uint32_t kPocsagBchPoly     = 0x769u;       // generator (octal 03551)

// One fully assembled pager message, grouped under its RIC.
struct PocsagMessage {
    uint32_t address  = 0;        // RIC (0..2097151); low 3 bits = frame
    int      function = 0;        // function bits 0..3
    std::string text;             // decoded payload (BCD numeric / ASCII alpha)
    enum class Type { Unknown, Numeric, Alpha };
    Type type = Type::Unknown;
};

/// Streaming POCSAG decoder. Feed recovered bits; drain decoded messages.
class PocsagDecoder {
public:
    PocsagDecoder();

    /// Feed recovered symbol decisions (0/1) from FskDemod::takeBits().
    void feed(const std::vector<int>& bits);

    /// Drain messages decoded since the last call. Clears the internal queue.
    std::vector<PocsagMessage> takeMessages();

    void reset();

    // ---- Public BCH(31,21) codec (unit-tested; reused by test synthesis) --
    /// Encode a 21-bit information word into a 32-bit POCSAG codeword.
    static uint32_t bchEncode21(uint32_t data21);

    /// Correct a 32-bit codeword. Returns (correctedWord, nErrors);
    /// nErrors < 0 means uncorrectable (caller should flush pending message).
    static std::pair<uint32_t, int> bchCorrect(uint32_t cw);

    /// Build a 32-bit address codeword from a RIC + 2-bit function field.
    static uint32_t buildAddressWord(uint32_t address, int function);

    // ---- Test-only synthesis (clean-room frame builder) -------------------
    /// Build a complete bitstream (preamble + one batch) carrying an address
    /// and its message, MSB-first. Used by unit tests to verify the chain;
    /// not used by the receive path.
    static std::vector<int> buildFrameBits(uint32_t address,
                                           const std::string& message);

private:
    // ---- streaming bit buffer / sync state ----
    std::vector<int> buf_;
    std::size_t pos_ = 0;
    enum class State { Hunt, Sync };
    State   state_   = State::Hunt;
    int     polarity_ = 0;     // 0 = normal, 1 = recovered bits were inverted
    int     wordIdx_  = 0;     // 0..15 within a batch once synced

    // ---- pending message assembly (nibble stream -> bytes) ----
    bool        havePending_ = false;
    uint32_t    pendingAddr_  = 0;
    int         pendingFunc_  = 0;
    std::vector<uint8_t> nibBuf_;
    int         numNibbles_  = 0;

    std::vector<PocsagMessage> out_;

    // ---- helpers ----
    uint32_t readWord(std::size_t at) const;   // 32 bits MSB-first
    void handleWord(uint32_t cw, int nerr, int wordIdx);
    void flushPending();
    void pump();

    static int detectFunction(const std::string& message);
    static std::vector<uint32_t> encodeMessageWords(const std::string& message,
                                                    int function);
    static std::string decodeNumeric(const std::vector<uint8_t>& buf,
                                     int nibbles);
    static std::string decodeAlpha(const std::vector<uint8_t>& buf,
                                   int nibbles);
};

} // namespace dsp
} // namespace mbdsdr
