// SPDX-License-Identifier: MIT
#include "adsb_decoder.h"
#include <cmath>
#include <cstring>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

static const uint32_t CRC24_POLY = 0xFFF409;

ADSBDecoder::ADSBDecoder() = default;

void ADSBDecoder::setSampleRate(double sr) { sr_ = sr; }

void ADSBDecoder::reset() {
    magBuf_.clear();
    newAircraft_.clear();
    knownAircraft_.clear();
}

uint32_t ADSBDecoder::crc24(const uint8_t* data, int len) {
    uint32_t crc = 0;
    for (int i = 0; i < len; ++i) {
        crc ^= (static_cast<uint32_t>(data[i]) << 16);
        for (int b = 0; b < 8; ++b) {
            if (crc & 0x800000) crc = (crc << 1) ^ CRC24_POLY;
            else crc <<= 1;
        }
        crc &= 0xFFFFFF;
    }
    return crc;
}

bool ADSBDecoder::checkPreamble(std::size_t idx) const {
    // At 2MSPS, 1us = 2 samples. Preamble pulses at 0,1,3.5,4.5 us = samples 0,2,7,9
    if (idx + 12 > magBuf_.size()) return false;
    float p0 = magBuf_[idx+0];
    float p1 = magBuf_[idx+2];
    float p2 = magBuf_[idx+7];
    float p3 = magBuf_[idx+9];
    float avg = (p0+p1+p2+p3) / 4.0f;
    if (avg < 0.01f) return false;

    // Guard bands should be low
    float g0 = magBuf_[idx+1];  // 0.5-1us
    float g1 = magBuf_[idx+4];  // 1.5-3.5us approx
    float g2 = magBuf_[idx+10]; // 5us
    if (g0 > avg * 0.6f) return false;
    if (g1 > avg * 0.6f) return false;
    if (g2 > avg * 0.6f) return false;
    return true;
}

QString ADSBDecoder::decodeCallsign(const uint8_t* me) {
    static const char table[] = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?";
    QString cs;
    for (int i = 0; i < 8; ++i) {
        uint8_t c = me[1 + i] >> 2; // 6 bits from each byte
        if (c < 48) cs.append(QChar::fromLatin1(table[c]));
    }
    return cs.trimmed();
}

bool ADSBDecoder::decodeFrame(std::size_t start, std::size_t totalBits, AircraftInfo& out) {
    // 2 samples per bit at 2MSPS. After preamble (8us = 16 samples), bits start.
    std::size_t bitStart = start + 16;
    std::size_t totalSamples = totalBits * 2;
    if (bitStart + totalSamples > magBuf_.size()) return false;

    std::vector<uint8_t> frameBytes(totalBits / 8, 0);
    for (std::size_t i = 0; i < totalBits; ++i) {
        float firstHalf = magBuf_[bitStart + i*2];
        float secondHalf = magBuf_[bitStart + i*2 + 1];
        bool bit = firstHalf > secondHalf;
        if (bit) frameBytes[i/8] |= (0x80 >> (i % 8));
    }

    uint32_t crc = crc24(frameBytes.data(), static_cast<int>(totalBits/8 - 3));
    uint32_t receivedCrc = (frameBytes[totalBits/8 - 3] << 16)
                         | (frameBytes[totalBits/8 - 2] << 8)
                         | frameBytes[totalBits/8 - 1];
    out.crcOk = (crc == receivedCrc);
    if (!out.crcOk) return false;

    out.df = frameBytes[0] >> 3;
    out.icao = QString("%1").arg(
        ((uint32_t)frameBytes[1] << 16) | ((uint32_t)frameBytes[2] << 8) | frameBytes[3],
        6, 16, QChar('0')).toUpper();

    if (out.df == 17 && totalBits == 112) {
        int tc = frameBytes[4] >> 3;
        if (tc >= 1 && tc <= 4) {
            out.callsign = decodeCallsign(&frameBytes[4]);
        } else if (tc >= 9 && tc <= 18) {
            uint16_t ac = ((frameBytes[5] & 0x0F) << 8) | frameBytes[6];
            int mBit = (frameBytes[5] >> 4) & 1;
            int n;
            if (mBit) n = ((ac & 0x0FE0) >> 1) | (ac & 0x000F);
            else n = ((ac & 0x0FF0) >> 1) | (ac & 0x000F);
            out.altitudeFt = n * 25 - 1000;
        }
    }

    return true;
}

void ADSBDecoder::feed(const std::vector<std::complex<float>>& iq) {
    for (auto c : iq) magBuf_.push_back(std::abs(c));
    if (magBuf_.size() > 4096) magBuf_.erase(magBuf_.begin(), magBuf_.begin() + 1024);

    std::size_t i = 0;
    while (i + 100 < magBuf_.size()) {
        if (checkPreamble(i)) {
            AircraftInfo info;
            // Try 112-bit long frame first
            if (decodeFrame(i, 112, info)) {
                info.firstSeen = QDateTime::currentDateTime();
                info.lastSeen = info.firstSeen;
                // Update or add
                bool found = false;
                for (auto& ac : knownAircraft_) {
                    if (ac.icao == info.icao) {
                        ac.lastSeen = info.lastSeen;
                        if (!info.callsign.isEmpty()) ac.callsign = info.callsign;
                        if (info.altitudeFt > 0) ac.altitudeFt = info.altitudeFt;
                        found = true;
                        newAircraft_.push_back(ac);
                        break;
                    }
                }
                if (!found) {
                    knownAircraft_.push_back(info);
                    newAircraft_.push_back(info);
                }
                i += 16 + 112 * 2;
                continue;
            }
        }
        i += 2;
    }
}

std::vector<AircraftInfo> ADSBDecoder::takeNewAircraft() {
    auto ret = std::move(newAircraft_);
    newAircraft_.clear();
    return ret;
}

} // namespace dsp
} // namespace mbdsdr
