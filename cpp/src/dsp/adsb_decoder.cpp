// SPDX-License-Identifier: MIT
#include "adsb_decoder.h"
#include <cmath>
#include <cstring>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

static const uint32_t CRC24_POLY = 0xFFF409;

// NL(lat): number of longitude zones at given latitude (standard formula).
int cprNL(double latDeg) {
    double a = std::abs(latDeg);
    if (a >= 87.0) return 1;
    constexpr double NZ = 15.0;
    double cosLat = std::cos(a * M_PI / 180.0);
    double denom = cosLat * cosLat;
    if (denom < 1e-12) return 1;
    double val = 1.0 - (1.0 - std::cos(M_PI / (2.0 * NZ))) / denom;
    if (val < -1.0) val = -1.0;
    if (val > 1.0) val = 1.0;
    double nl = 2.0 * M_PI / std::acos(val);
    int r = (int)std::floor(nl);
    return r < 1 ? 1 : r;
}

bool cprLocalDecode(int cprLat, int cprLon, bool isOdd,
                    double refLat, double refLon,
                    double& latOut, double& lonOut) {
    double dlat = isOdd ? 360.0/59.0 : 360.0/60.0;
    double rlat = std::fmod(refLat, dlat);
    if (rlat < 0) rlat += dlat;
    int j = (int)std::floor(refLat/dlat) +
            (int)std::floor(0.5 + rlat/dlat - cprLat/131072.0);
    double lat = dlat * (j + cprLat/131072.0);
    if (lat > 270.0) lat -= 360.0;
    if (lat < -90.0 || lat > 90.0) return false;
    int nl = cprNL(lat);
    double dlon = (nl <= 1) ? 360.0 : 360.0/(nl - (isOdd?1:0));
    double rlon = std::fmod(refLon, dlon);
    if (rlon < 0) rlon += dlon;
    int m = (int)std::floor(refLon/dlon) +
            (int)std::floor(0.5 + rlon/dlon - cprLon/131072.0);
    double lon = dlon * (m + cprLon/131072.0);
    if (lon >= 360.0) lon -= 360.0;
    if (lon > 180.0) lon -= 360.0;
    latOut = lat; lonOut = lon;
    return true;
}

bool cprGlobalDecode(const CprPair& p, double& latOut, double& lonOut) {
    if (!p.haveEven || !p.haveOdd) return false;
    double j = std::floor((59.0*p.latEven - 60.0*p.latOdd)/131072.0 + 0.5);
    double latEven = (360.0/60.0) * (std::fmod((int)j,60) + p.latEven/131072.0);
    double latOdd  = (360.0/59.0) * (std::fmod((int)j,59) + p.latOdd/131072.0);
    if (latEven >= 270.0) latEven -= 360.0;
    if (latOdd  >= 270.0) latOdd  -= 360.0;
    if (std::abs(latEven) > 90) return false;
    int n = cprNL(latEven);
    int m = (int)std::floor(((double)p.lonEven*(n-1) - (double)p.lonOdd*n)/131072.0 + 0.5);
    int nEven = (n >= 1) ? n : 1;
    double lon = (360.0/nEven) * (std::fmod((int)m, nEven) + p.lonEven/131072.0);
    if (lon >= 360.0) lon -= 360.0;
    latOut = latEven; lonOut = lon;
    return true;
}

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

            // CPR airborne position: ME bytes frameBytes[4..10].
            const uint8_t* me = &frameBytes[4];
            int cprFmt = (me[2] >> 4) & 1;         // 0=even, 1=odd
            int cprLat = ((me[2] & 0x0F) << 13) | (me[3] << 5) | (me[4] >> 3);
            int cprLon = ((me[4] & 0x07) << 14) | (me[5] << 6) | (me[6] >> 2);
            CprPair& st = cprByIcao_[out.icao];
            if (cprFmt == 0) { st.latEven=cprLat; st.lonEven=cprLon; st.haveEven=true; }
            else             { st.latOdd=cprLat;  st.lonOdd=cprLon;  st.haveOdd=true; }
            double la, lo;
            if (cprGlobalDecode(st, la, lo)) {
                out.hasPosition = true; out.lat = la; out.lon = lo;
            } else if (haveRef_) {
                // Fallback: local decode against station position.
                double la2, lo2;
                if (cprLocalDecode(cprLat, cprLon, cprFmt==1, refLat_, refLon_, la2, lo2)) {
                    out.hasPosition = true; out.lat = la2; out.lon = lo2;
                }
            }
        } else if (tc == 19) {
            // Airborne velocity: me[1..6] = 48 bits.
            // subtype = me[0] low 3 bits, ns sign/mag, ew sign/mag.
            const uint8_t* me = &frameBytes[4];
            int subtype = me[0] & 0x07;
            if (subtype == 1 || subtype == 2) {
                int nsRaw = ((me[1] & 0x3F) << 4) | (me[2] >> 4);
                int ewRaw = ((me[2] & 0x0F) << 6) | (me[3] >> 2);
                int nsSign = (me[1] >> 7) & 1;
                int ewSign = (me[2] >> 3) & 1;
                double ns = (nsSign ? -1 : 1) * (nsRaw - 1);
                double ew = (ewSign ? -1 : 1) * (ewRaw - 1);
                out.groundspeedKt = std::sqrt(ns*ns + ew*ew);
                out.headingDeg = std::fmod(std::atan2(ew, ns)*180.0/M_PI + 360.0, 360.0);
                out.hasVelocity = true;
                // Vertical rate: bits 35..45 of the 48-bit ME field.
                uint64_t me48 = ((uint64_t)me[0]<<40) | ((uint64_t)me[1]<<32) |
                                ((uint64_t)me[2]<<24) | ((uint64_t)me[3]<<16) |
                                ((uint64_t)me[4]<<8) | me[5];
                int vrateSign = (me48 >> 11) & 1;   // bit 36
                int vrateMag  = (me48 >> 2) & 0x1FF; // bits 37..45
                if (vrateMag > 0) {
                    out.verticalRateFpm = (vrateSign ? -1 : 1) * (vrateMag - 1) * 64;
                    out.hasVerticalRate = true;
                }
            }
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
                        if (info.hasPosition) { ac.hasPosition=true; ac.lat=info.lat; ac.lon=info.lon; }
                        if (info.hasVelocity) { ac.hasVelocity=true; ac.groundspeedKt=info.groundspeedKt; ac.headingDeg=info.headingDeg; }
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
