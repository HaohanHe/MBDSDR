// SPDX-License-Identifier: MIT
#include "ssdv_packet.h"

namespace mbdsdr {
namespace dsp {

bool parseSsdvHeader(const uint8_t* pkt, size_t len, SsdvHeader& out) {
    if (!pkt || len < kSsdvPacketLen) return false;
    const uint8_t f = pkt[0];
    out.fec         = (f & 0x80) != 0;
    out.colour      = (f & 0x40) != 0;
    out.reliableEnd = (f & 0x08) != 0;
    out.packetType  = f & 0x07;
    out.imageId     = pkt[1];
    out.sequence    = static_cast<int>(pkt[2]) |
                      (static_cast<int>(pkt[3] & 0x80) << 1);
    out.widthPx     = 8 * ((pkt[3] & 0x7F) + 1);
    out.jpegBytes   = static_cast<int>(pkt[4]) |
                      (static_cast<int>(pkt[5]) << 8);
    if (out.jpegBytes < 0 || out.jpegBytes > 65534) return false;
    out.jpegBytes += 1;   // wire value is length minus 1
    const size_t maxPayload = out.fec ? (kSsdvFecDataLen - kSsdvHeaderLen)
                                      : (kSsdvPacketLen - kSsdvHeaderLen);
    if (static_cast<size_t>(out.jpegBytes) > maxPayload) return false;
    return true;
}

const uint8_t* ssdvJpegPayload(const uint8_t* pkt, const SsdvHeader& hdr,
                               size_t& nOut) {
    nOut = 0;
    if (!pkt) return nullptr;
    nOut = static_cast<size_t>(hdr.jpegBytes);
    if (hdr.fec && nOut > kSsdvFecDataLen - kSsdvHeaderLen)
        nOut = kSsdvFecDataLen - kSsdvHeaderLen;
    return pkt + kSsdvHeaderLen;
}

// ---------------------------------------------------------------------------
// RS(255,223) CCSDS encoder. Tables mirror the public GF(256) construction:
// alpha=2, primitive polynomial 0x187, first root alpha^112, 0xFF symbol
// inversion. Known-answer tested against the Python fec.py reference.
// ---------------------------------------------------------------------------
RsCcsds::RsCcsds() {
    unsigned x = 1;   // 9-bit shift register: bit 8 is the reduction bit
    for (int i = 0; i < 255; ++i) {
        expT_[i] = static_cast<uint8_t>(x);
        logT_[x] = static_cast<uint8_t>(i);
        x <<= 1;
        if (x & 0x100) x ^= 0x187;  // primitive polynomial x^8+x^7+x^2+x+1
    }
    for (int i = 255; i < 512; ++i) expT_[i] = expT_[i - 255];

    // Generator g(x) = prod_{i=0..31} (x - alpha^(112+i)), highest power first.
    gen_[0] = 1;
    int deg = 0;  // current degree of gen (number of coefficients - 1)
    for (int i = 0; i < 32; ++i) {
        const uint8_t root = expT_[112 + i];
        uint8_t ng[33] = {0};
        for (int j = 0; j <= deg; ++j) {
            ng[j]     ^= mul(gen_[j], root);
            ng[j + 1] ^= gen_[j];
        }
        for (int j = 0; j <= deg + 1; ++j) gen_[j] = ng[j];
        ++deg;
    }
}

void RsCcsds::encode(const uint8_t* msg223, uint8_t parityOut[kSsdvNsym]) const {
    uint8_t m[255];
    for (int i = 0; i < 223; ++i) m[i] = static_cast<uint8_t>(msg223[i] ^ 0xFF);
    for (int i = 223; i < 255; ++i) m[i] = 0;
    for (int i = 0; i < 223; ++i) {
        const uint8_t coef = m[i];
        if (coef == 0) continue;
        for (int j = 0; j <= 32; ++j)
            m[i + j] ^= mul(gen_[j], coef);
    }
    for (int i = 0; i < 32; ++i)
        parityOut[i] = static_cast<uint8_t>(m[223 + i] ^ 0xFF);
}

} // namespace dsp
} // namespace mbdsdr
