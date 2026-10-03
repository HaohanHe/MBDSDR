// SPDX-License-Identifier: MIT
//
// M17 digital-voice frame decoder (clean-room, own naming).
//
// Protocol facts implemented here are taken from the PUBLIC M17 specification
// (m17project) and cross-checked against our own MIT reference
// mbdsdr_ai/m17_adapter.py.  No GPL source (repos/m17, repos/sdrpp) was copied;
// only the public frame layout, constants and algorithm *mechanisms* were
// re-derived independently.
//
// Chain (per M17 spec):
//   channelized complex baseband IQ @ 48 kHz
//     -> quadrature discriminator -> small FIR lowpass (same mechanism as
//        dsp/fsk_demod.{h,cpp}, re-derived here so this file stays self-contained)
//     -> nominal symbol strobe (10 sps @ 4800 sym/s) -> 4-level hard slicer
//        -> dibits (0..3) -> bits
//     -> 16-bit frame sync correlation (0x55F7 LSF / 0xFF5D data / 0x3243 stream)
//     -> 368 payload bits: derandomize (XOR 46-byte DC), deinterleave
//        p(i) = (45 i + 92 i^2) mod 368, depuncture P1, Viterbi (K=5,
//        g1=0o31, g2=0o27) -> 240 bits = 30-byte Link Setup Frame
//     -> LSF: base-40 SRC/DST callsigns, TYPE, META, CRC-16 (poly 0x5935)
//
// Voice: M17 stream frames carry Codec2 (3200) frames.  Codec2 is NOT built
// in here -- no proprietary/limited codec is bundled -- so voice frames are
// reported as metadata only (callsigns + raw payload bytes), honestly flagged.
//
// Pure C++17, no Qt, deterministic, testable headless.
#pragma once

#include <complex>
#include <cstdint>
#include <string>
#include <vector>

namespace mbdsdr {
namespace dsp {

// ---------------------------------------------------------------------------
// Result of a decoded M17 call / frame.  feed() produces these; takeCalls()
// drains them in arrival order (feed -> take style, like adsb_decoder).
// ---------------------------------------------------------------------------
struct M17Call {
    /// Decoded source callsign ("BROADCAST" when the field is all-ones).
    std::string src;
    /// Decoded destination callsign ("BROADCAST" when all-ones).
    std::string dst;
    /// Raw 16-bit LSF TYPE word (big-endian lsf[12..13]).
    uint16_t type = 0;
    /// true = stream (voice/data), false = packet.
    bool isStream = false;
    /// Payload class from TYPE bits: 0 unknown/reserved, 1 data, 2 voice, 3 mixed.
    int  payloadClass = 0;
    /// Raw 14 META bytes (lsf[14..27]); may be empty for data frames.
    std::vector<uint8_t> meta;
    /// Raw frame payload bytes (for data/packet frames) -- NOT Codec2-decoded.
    std::vector<uint8_t> payload;
    /// 1 = Link Setup Frame, 2 = data/stream frame, 3 = LICH fragment.
    int frameKind = 0;
    /// CRC-16 over the decoded LSF verified.
    bool crcOk = false;
    /// Viterbi path metric (lower = better); -1 when not applicable.
    int viterbiCost = -1;
    /// True when this is a voice stream (Codec2 not decoded -- honest note).
    bool voiceUndecoded = false;
};

class M17Decoder {
public:
    M17Decoder();
    explicit M17Decoder(double sampleRateHz);

    void reset();

    /// Feed channelized complex baseband IQ (48 kHz nominal, 4800 sym/s).
    void feed(const std::vector<std::complex<float>>& iq);

    /// Feed already-hard-symbolized dibits (each in {0,1,2,3}).  Public so the
    /// synthetic unit test can drive the protocol core deterministically
    /// without depending on the analogue front-end.
    void feedDibits(const std::vector<int>& dibits);

    /// Drain decoded calls/frames; clears the internal queue.
    std::vector<M17Call> takeCalls();

    // -- protocol helpers (public for unit tests) ---------------------------
    /// base-40 decode a 6-byte big-endian callsign field.
    static std::string decodeCallsign(const uint8_t b[6]);
    /// base-40 encode a callsign string into 6 bytes (big-endian).
    static bool encodeCallsign(const std::string& cs, uint8_t out[6]);
    /// M17 CRC-16 (poly 0x5935, init 0xFFFF, MSB-first).
    static uint16_t crc16(const uint8_t* data, int len);
    /// Golay(24,12) correct a 24-bit codeword in place.  Returns #bit errors
    /// corrected (0..3) or -1 when the error pattern is uncorrectable.
    static int golay24Correct(uint32_t& codeword);

    static constexpr double kSymbolRateBd = 4800.0;
    static constexpr double kDefaultSampleRateHz = 48000.0;

    // Sync words (16-bit, MSB first) -- public for tests.
    static constexpr uint16_t kSyncStream = 0x3243;
    static constexpr uint16_t kSyncLsf    = 0x55F7;
    static constexpr uint16_t kSyncData   = 0xFF5D;

private:
    // -- analogue front-end state ------------------------------------------
    double sampleRateHz_;
    double sps_;
    float  discGain_;
    std::complex<float> prev_{1.0f, 0.0f};

    // Simple moving-average lowpass over the discriminator output.
    std::vector<float> lpfTap_;          // window taps (length = lpfLen_)
    int lpfLen_ = 0;
    std::vector<float> lpfDelay_;
    int lpfPos_ = 0;

    // Free-running symbol strobe.
    float nAcc_ = 0.0f;
    float omega_ = 0.0f;
    float prevFilt_ = 0.0f;

    // -- bit/symbol stream state -------------------------------------------
    std::vector<int> dibits_;            // pending dibits accumulated
    std::vector<M17Call> out_;

    // -- helpers -----------------------------------------------------------
    void pushDibit(int d);
    void processBits();                  // runs the frame state machine
    int  matchSync(const int* bits16) const;
    void handleLsfFrame(const int* payloadBits);    // 368 bits
    void handleDataFrame(uint16_t sync, const int* payloadBits);
};

} // namespace dsp
} // namespace mbdsdr
