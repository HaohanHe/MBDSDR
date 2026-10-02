// SPDX-License-Identifier: MIT
//
// SSDV (Slow Scan Digital Video) byte-layer: 256-byte packet header parser and
// the CCSDS RS(255,223) encoder. Clean-room: the packet layout and the
// Reed-Solomon division algorithm are public standards (SP5WWP SSDV spec;
// CCSDS TM RS code); only the mechanism was learned, code below is re-derived.
// No GPL implementation was copied.
//
// Packet layout (256 bytes, little-endian multi-byte fields):
//   [0]   flags   bit7=1 FEC mode | bit6=1 colour | bit3=reliable-end | bits2:0 type
//   [1]   image id
//   [2]   packet sequence number low byte
//   [3]   bit7 = sequence high bit; bits6:0 = (imageWidth/8) - 1
//   [4:6] JPEG byte count in this packet, minus 1 (LE uint16)
//   [6..] JPEG payload
//
// FEC mode: bytes [0..222] are the 223-byte RS message (6 header + 217 payload),
// bytes [223..254] are the 32 RS parity bytes, byte 255 is padding.
#pragma once

#include <cstddef>
#include <cstdint>

namespace mbdsdr {
namespace dsp {

constexpr size_t kSsdvPacketLen = 256;
constexpr size_t kSsdvHeaderLen = 6;
constexpr size_t kSsdvFecDataLen = 223;   // RS message length
constexpr size_t kSsdvNsym      = 32;    // RS parity length

struct SsdvHeader {
    bool fec          = false;  // bit7: RS(255,223) FEC packet
    bool colour       = false;  // bit6: colour JPEG
    bool reliableEnd  = false;  // bit3: image ends with a non-full packet
    int  packetType   = 0;      // bits 2..0
    int  imageId      = 0;      // byte 1
    int  sequence     = 0;      // bytes 2..3 (8-bit LSB + bit7 of byte 3)
    int  widthPx      = 0;      // 8 * (byte3 bits6:0 + 1)
    int  jpegBytes    = 0;      // bytes 4..5 LE + 1
};

/// Parse the 6-byte header of a 256-byte packet. Returns false when len < 256
/// or the declared JPEG length exceeds the usable payload for the mode.
bool parseSsdvHeader(const uint8_t* pkt, size_t len, SsdvHeader& out);

/// Pointer/length of the JPEG payload inside the packet.
///  uncoded: bytes 6 .. 6+jpegBytes-1 (max 250 usable)
///  FEC:     bytes 6 .. 222 (217 bytes; RS parity lives at 223..254)
/// Returns nullptr when the header was not parsed.
const uint8_t* ssdvJpegPayload(const uint8_t* pkt, const SsdvHeader& hdr,
                               size_t& nOut);

/// CCSDS RS(255,223) encoder: primitive poly 0x187, first root alpha^112,
/// 0xFF symbol inversion on both message and parity (public CCSDS convention).
/// Deterministic, allocation-free, tables built once.
class RsCcsds {
public:
    RsCcsds();
    /// Encode a 223-byte message; writes 32 parity bytes to parityOut.
    void encode(const uint8_t* msg223, uint8_t parityOut[kSsdvNsym]) const;

private:
    uint8_t expT_[512];
    uint8_t logT_[256];
    uint8_t gen_[33];   // generator polynomial, highest power first (x^32 .. x^0)
    uint8_t mul(uint8_t a, uint8_t b) const {
        if (a == 0 || b == 0) return 0;
        return expT_[logT_[a] + logT_[b]];
    }
};

} // namespace dsp
} // namespace mbdsdr
