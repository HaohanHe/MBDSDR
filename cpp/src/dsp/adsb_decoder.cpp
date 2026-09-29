// SPDX-License-Identifier: MIT
#include "adsb_decoder.h"
#include <cmath>
#include <cstring>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

// Mode-S CRC-24 generator polynomial (top bit omitted).
// Source: dump1090 crc.c:28 modesChecksum. Correct value is 0xFFF409, NOT the
// commonly misquoted 0xFFFA04.
static const uint32_t CRC24_POLY = 0xFFF409;

// ---------------------------------------------------------------------------
// CPR: NL (Number of Longitude bands). Table of absolute-latitude breakpoints
// mirrored across the equator, straight from dump1090 cpr.c:77 cprNLFunction.
// Using the table (rather than the unstable acos() form) matches dump1090 and
// the validated Python reference at the pole boundary (NL(88)=1).
// ---------------------------------------------------------------------------
struct NlBreak { double lat; int nl; };
static const NlBreak kNlBreaks[] = {
    {10.47047130,59},{14.82817437,58},{18.18626357,57},{21.02939493,56},
    {23.54504487,55},{25.82924707,54},{27.93898710,53},{29.91135686,52},
    {31.77209708,51},{33.53993436,50},{35.22899598,49},{36.85025108,48},
    {38.41241892,47},{39.92256684,46},{41.38651832,45},{42.80914012,44},
    {44.19454951,43},{45.54626723,42},{46.86733252,41},{48.16039128,40},
    {49.42776439,39},{50.67150166,38},{51.89342469,37},{53.09516153,36},
    {54.27817472,35},{55.44378444,34},{56.59318756,33},{57.72747354,32},
    {58.84763776,31},{59.95459277,30},{61.04917774,29},{62.13216659,28},
    {63.20427479,27},{64.26616523,26},{65.31845310,25},{66.36171008,24},
    {67.39646774,23},{68.42322022,22},{69.44242631,21},{70.45451075,20},
    {71.45986473,19},{72.45884545,18},{73.45177442,17},{74.43893416,16},
    {75.42056257,15},{76.39684391,14},{77.36789461,13},{78.33374083,12},
    {79.29428225,11},{80.24923213,10},{81.19801349, 9},{82.13956981, 8},
    {83.07199445, 7},{83.99173563, 6},{84.89166191, 5},{85.75541621, 4},
    {86.53536998, 3},{87.00000000, 2},
};

int cprNL(double latDeg) {
    double a = std::fabs(latDeg);
    for (const NlBreak& b : kNlBreaks) {
        if (a < b.lat) return b.nl;
    }
    return 1;
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

namespace {
// Integer modulus that stays positive (matches Python's % for negatives).
inline int modInt(int a, int b) {
    int r = a % b;
    return r < 0 ? r + b : r;
}
} // namespace

// Global airborne CPR decode. Port of dump1090 cpr.c:162 decodeCPRairborne and
// the validated Python reference mbdsdr_ai/adsb.py:decode_cpr_airborne.
bool cprGlobalDecode(const CprPair& p, double& latOut, double& lonOut) {
    if (!p.haveEven || !p.haveOdd) return false;

    const double dlat0 = 360.0/60.0;  // even
    const double dlat1 = 360.0/59.0;  // odd

    int j = (int)std::floor((59.0*p.latEven - 60.0*p.latOdd)/131072.0 + 0.5);
    double rlat0 = dlat0 * (modInt(j,60) + p.latEven/131072.0);
    double rlat1 = dlat1 * (modInt(j,59) + p.latOdd/131072.0);
    if (rlat0 >= 270.0) rlat0 -= 360.0;
    if (rlat1 >= 270.0) rlat1 -= 360.0;
    if (rlat0 < -90.0 || rlat0 > 90.0) return false;
    if (rlat1 < -90.0 || rlat1 > 90.0) return false;
    // Crossed a latitude band -> pairs are not on the same NL belt, wait.
    if (cprNL(rlat0) != cprNL(rlat1)) return false;

    double rlat, rlon;
    if (p.evenNewest) {
        int ni = cprNL(rlat0);
        int m = (int)std::floor(((double)p.lonEven*(ni-1) -
                                 (double)p.lonOdd*ni)/131072.0 + 0.5);
        double dlon = 360.0/ni;
        rlon = dlon * (modInt(m,ni) + p.lonEven/131072.0);
        rlat = rlat0;
    } else {
        int ni = std::max(cprNL(rlat1) - 1, 1);
        int nl1 = cprNL(rlat1);
        int m = (int)std::floor(((double)p.lonEven*(nl1-1) -
                                 (double)p.lonOdd*nl1)/131072.0 + 0.5);
        double dlon = 360.0/ni;
        rlon = dlon * (modInt(m,ni) + p.lonOdd/131072.0);
        rlat = rlat1;
    }
    // Wrap longitude into (-180,180].
    rlon -= std::floor((rlon + 180.0)/360.0) * 360.0;
    latOut = rlat; lonOut = rlon;
    return true;
}

ADSBDecoder::ADSBDecoder() = default;

void ADSBDecoder::setSampleRate(double sr) {
    sr_ = sr;
    sps_ = sr / 1e6;   // samples per microsecond (may be fractional, e.g. 2.4)
}

void ADSBDecoder::reset() {
    magBuf_.clear();
    scanPos_ = 0;
    newAircraft_.clear();
    knownAircraft_.clear();
    cprByIcao_.clear();
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

// ---------------------------------------------------------------------------
// Physical layer: rate-independent window energy over the magnitude envelope.
// Sample s (>=base) represents time (s-base)/sps_ microseconds after `base`.
// ---------------------------------------------------------------------------
float ADSBDecoder::windowMean(std::size_t base, double usA, double usB) const {
    double lo = base + usA*sps_;
    double hi = base + usB*sps_;
    std::size_t s0 = (std::size_t)std::ceil(lo - 1e-9);
    std::size_t s1 = (std::size_t)std::ceil(hi - 1e-9);   // [lo,hi) half-open
    if (s1 > magBuf_.size()) s1 = magBuf_.size();
    if (s0 >= s1) return 0.0f;
    double acc = 0.0;
    for (std::size_t s = s0; s < s1; ++s) acc += magBuf_[s];
    return (float)(acc / (double)(s1 - s0));
}

// 8us preamble: four 0.5us pulses at 0.0 / 1.0 / 3.5 / 4.5 us; quiet guard
// bands in the gaps. Source: dump1090 demod_2400.c:147-151,208-218.
bool ADSBDecoder::checkPreamble(std::size_t base) const {
    if (base + (std::size_t)(8.0*sps_) + 1 > magBuf_.size()) return false;

    static const double kPulse[4][2] = {
        {0.0,0.5}, {1.0,1.5}, {3.5,4.0}, {4.5,5.0}
    };
    static const double kGuard[4][2] = {
        {0.5,1.0}, {1.5,3.5}, {4.0,4.5}, {5.0,8.0}
    };

    // Every pulse window must be energetic, and every quiet band must stay low.
    // Using min(pulses) vs max(guards) rejects shifted/template-drift triggers.
    float pulseMin = 1e30f;
    for (auto& w : kPulse) pulseMin = std::min(pulseMin, windowMean(base, w[0], w[1]));
    float guardMax = 0.0f;
    for (auto& w : kGuard) guardMax = std::max(guardMax, windowMean(base, w[0], w[1]));

    // Gentle absolute floor + pulse/guard contrast (~3.5 dB => ratio ~2.2).
    if (pulseMin < 0.1f) return false;
    if (pulseMin < 2.2f * guardMax) return false;
    return true;
}

// PPM: each data bit is 1us = two 0.5us chips. Front chip high -> bit 1.
bool ADSBDecoder::ppmBit(std::size_t base, int bitIdx) const {
    double t = 8.0 + bitIdx;             // bit window start, us after preamble
    float front = windowMean(base, t, t + 0.5);
    float back  = windowMean(base, t + 0.5, t + 1.0);
    return front > back;
}

int ADSBDecoder::probeDf(std::size_t base) const {
    int df = 0;
    for (int i = 0; i < 5; ++i) df = (df << 1) | (ppmBit(base, i) ? 1 : 0);
    return df;
}

// ---------------------------------------------------------------------------
// Callsign TC1-4: 8 chars x 6 bits packed across ME bytes 1..6.
// Source: dump1090 mode_s.c:798 decodeESIdentAndCategory + ais_charset.c.
//
// The 6-bit alphabet order expressed for reference (letters then digits):
//   "ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789"
// NOTE: digit code points in the full 64-entry table are 48..57 (i.e. '0'..'9'),
//       NOT 27..34 -- digits must be encoded/decoded at table index 48+.
// Accepted characters: A-Z (1..26), space (32), 0-9 (48..57). Anything else
// (including '@' padding at index 0) maps to space and is trimmed.
// ---------------------------------------------------------------------------
QString ADSBDecoder::decodeCallsign(const uint8_t* me) {
    // me[1..6] = 48 bits, MSB first.
    int bits[48];
    for (int b = 0; b < 6; ++b)
        for (int j = 0; j < 8; ++j)
            bits[b*8 + j] = (me[1+b] >> (7-j)) & 1;

    QString cs;
    cs.reserve(8);
    for (int i = 0; i < 8; ++i) {          // exactly 8 chars, no overrun
        int code = 0;
        for (int j = 0; j < 6; ++j) code = (code << 1) | bits[i*6 + j];
        QChar c;
        if (code >= 1 && code <= 26)       c = QChar(QLatin1Char('A' + code - 1));
        else if (code == 32)               c = QChar(QLatin1Char(' '));
        else if (code >= 48 && code <= 57) c = QChar(QLatin1Char('0' + code - 48));
        else                               c = QChar(QLatin1Char(' ')); // padding/illegal
        cs.append(c);
    }
    return cs.trimmed();
}

// 12-bit barometric altitude (Q=1, 25 ft resolution). Q=0 Gillham -> *ok=false.
int ADSBDecoder::ac12Altitude(uint16_t ac12, bool& ok) {
    ok = false;
    if (!(ac12 & 0x0010)) return 0;        // Q bit: Gillham, TODO
    int n = ((ac12 & 0x0FE0) >> 1) | (ac12 & 0x000F);
    ok = true;
    return n*25 - 1000;
}

// Mode-S parity / address table. Returns true when the frame is acceptable and
// fills icaoOut with the 24-bit address (when applicable).
//  - DF17/18: DF=17/18: data parity, syndrome must be 0; ICAO in plaintext AA.
//  - DF11:    low 7 bits of syndrome = IID; accept spontaneous squitter IID==0.
//  - others:  address parity, syndrome == 24-bit address; DF20/21 plaintext AA
//             must equal the recovered address.
bool ADSBDecoder::checkParity(int df, const uint8_t* frame, int nbytes,
                              uint32_t& icaoOut) {
    uint32_t syndrome = crc24(frame, nbytes);
    uint32_t aa = ((uint32_t)frame[1] << 16) | ((uint32_t)frame[2] << 8) | frame[3];
    icaoOut = 0;
    if (df == 17 || df == 18) {
        if (syndrome != 0) return false;
        icaoOut = aa;
        return true;
    }
    if (df == 11) {
        if ((syndrome & 0x7Fu) != 0) return false;   // accept IID==0 only
        icaoOut = aa;
        return true;
    }
    // Address parity: syndrome is the address.
    if (nbytes == 14 && (df == 20 || df == 21 || df == 24)) {
        if (syndrome != aa) return false;
        icaoOut = aa;
    } else {
        icaoOut = syndrome;
    }
    return icaoOut != 0;
}

void ADSBDecoder::processFrame(std::size_t base, int df, int nbits) {
    int nbytes = nbits / 8;
    std::vector<uint8_t> frame(nbytes, 0);
    for (int i = 0; i < nbits; ++i)
        if (ppmBit(base, i)) frame[i/8] |= (0x80 >> (i % 8));

    uint32_t icao24 = 0;
    if (!checkParity(df, frame.data(), nbytes, icao24)) return;  // bad frame -> drop

    AircraftInfo info;
    info.df = df;
    info.crcOk = true;
    info.icao = QString::number(icao24, 16).rightJustified(6, QChar('0')).toUpper();

    if (df == 17 && nbits == 112) {
        const uint8_t* me = &frame[4];   // 56-bit ME field
        int mb[56];
        for (int b = 0; b < 7; ++b)
            for (int j = 0; j < 8; ++j)
                mb[b*8+j] = (me[b] >> (7-j)) & 1;
        auto gb = [&](int f, int l) {  // 1-based inclusive getbits
            int v = 0; for (int i=f-1;i<l;++i) v=(v<<1)|mb[i]; return v;
        };
        int tc = gb(1,5);

        if (tc >= 1 && tc <= 4) {
            info.callsign = decodeCallsign(me);
        } else if (tc == 19) {
            int sub = gb(6,8);
            if (sub >= 1 && sub <= 4) {
                if (sub == 1 || sub == 2) {
                    int ewRaw = gb(15,24), nsRaw = gb(26,35);
                    int ewSign = mb[13] ? -1 : 1;   // bit14 (1-based)
                    int nsSign = mb[24] ? -1 : 1;   // bit25
                    int scale = (sub == 2) ? 4 : 1;
                    double ew = (double)(ewRaw - 1) * ewSign * scale;
                    double ns = (double)(nsRaw - 1) * nsSign * scale;
                    if (ewRaw > 1 && nsRaw > 1) {
                        info.groundspeedKt = std::sqrt(ns*ns + ew*ew);
                        double tr = std::atan2(ew, ns) * 180.0/M_PI;
                        if (tr < 0) tr += 360.0;
                        info.headingDeg = tr;
                        info.hasVelocity = true;
                    }
                    int vert = gb(38,46);
                    if (vert > 1) {
                        int vSign = mb[36] ? -1 : 1; // bit37 (1-based)
                        info.verticalRateFpm = (vert - 1) * vSign * 64;
                        info.hasVerticalRate = true;
                    }
                }
                // sub 3/4 (airspeed) TODO: not required this round.
            }
        } else if ((tc >= 9 && tc <= 18) || tc == 0 ||
                   (tc >= 20 && tc <= 22)) {
            uint16_t ac12 = (uint16_t)gb(9,20);
            bool altOk = false;
            info.altitudeFt = ac12Altitude(ac12, altOk);
            if (!altOk) info.altitudeFt = -1;

            bool odd = mb[21] != 0;             // bit22 F flag
            int cprLat = gb(23,39);
            int cprLon = gb(40,56);

            CprPair& st = cprByIcao_[info.icao];
            if (!odd) { st.latEven=cprLat; st.lonEven=cprLon; st.haveEven=true; st.evenNewest=true; }
            else      { st.latOdd=cprLat;  st.lonOdd=cprLon;  st.haveOdd=true;  st.evenNewest=false; }

            double la, lo;
            if (cprGlobalDecode(st, la, lo)) {
                info.hasPosition = true; info.lat = la; info.lon = lo;
                info.positionTime = QDateTime::currentDateTime();
            } else if (haveRef_) {
                double la2, lo2;
                if (cprLocalDecode(cprLat, cprLon, odd, refLat_, refLon_, la2, lo2)) {
                    info.hasPosition = true; info.lat = la2; info.lon = lo2;
                    info.positionTime = QDateTime::currentDateTime();
                }
            }
        }
        // TC5-8 ground position: CPR components cached above; global surface
        // decode is a TODO (airborne focus this round).
    }

    // Merge into known-aircraft table and emit.
    info.firstSeen = QDateTime::currentDateTime();
    info.lastSeen = info.firstSeen;
    bool found = false;
    for (auto& ac : knownAircraft_) {
        if (ac.icao == info.icao) {
            ac.lastSeen = info.lastSeen;
            if (!info.callsign.isEmpty()) ac.callsign = info.callsign;
            if (info.altitudeFt > 0) ac.altitudeFt = info.altitudeFt;
            if (info.hasPosition) { ac.hasPosition=true; ac.lat=info.lat; ac.lon=info.lon; ac.positionTime=info.positionTime; }
            if (info.hasVelocity) {
                ac.hasVelocity=true; ac.groundspeedKt=info.groundspeedKt;
                ac.headingDeg=info.headingDeg;
                if (info.hasVerticalRate) { ac.hasVerticalRate=true; ac.verticalRateFpm=info.verticalRateFpm; }
            }
            found = true;
            newAircraft_.push_back(ac);
            break;
        }
    }
    if (!found) {
        knownAircraft_.push_back(info);
        newAircraft_.push_back(info);
    }
}

void ADSBDecoder::feed(const std::vector<std::complex<float>>& iq) {
    if (sps_ <= 0.0) sps_ = sr_/1e6;
    for (auto c : iq) magBuf_.push_back(std::abs(c));

    // How much of the stream we must retain so a frame that straddles two feed()
    // calls is never truncated: 8us preamble + 112us long frame + small margin.
    std::size_t keep = (std::size_t)std::ceil(130.0 * sps_);

    while (true) {
        // Need room to at least inspect the 8us preamble and probe the first bits.
        if (scanPos_ + (std::size_t)std::ceil((8.0 + 5.0)*sps_) > magBuf_.size()) break;
        if (!checkPreamble(scanPos_)) { scanPos_ += 1; continue; }

        int df = probeDf(scanPos_);
        int nbits = (df & 0x10) ? 112 : 56;   // DF>=16 -> long frame
        std::size_t frameSamples = (std::size_t)std::ceil((8.0 + nbits)*sps_);
        if (scanPos_ + frameSamples > magBuf_.size()) break;  // wait for rest

        processFrame(scanPos_, df, nbits);
        scanPos_ += frameSamples;
    }

    // Discard consumed prefix but keep a ~130us overlap tail so a frame split
    // across feed() boundaries survives.
    if (scanPos_ > keep) {
        std::size_t e = scanPos_ - keep;
        magBuf_.erase(magBuf_.begin(), magBuf_.begin() + (std::ptrdiff_t)e);
        scanPos_ -= e;
    } else if (magBuf_.size() > keep + (std::size_t)std::ceil(2000.0*sps_)) {
        // Safety valve: never let the buffer grow without bound.
        std::size_t e = magBuf_.size() - keep;
        magBuf_.erase(magBuf_.begin(), magBuf_.begin() + (std::ptrdiff_t)e);
        scanPos_ = 0;
    }
}

std::vector<AircraftInfo> ADSBDecoder::takeNewAircraft() {
    auto ret = std::move(newAircraft_);
    newAircraft_.clear();
    return ret;
}

} // namespace dsp
} // namespace mbdsdr
