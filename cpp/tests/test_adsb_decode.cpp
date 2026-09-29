// SPDX-License-Identifier: MIT
// Comprehensive 1090 Mode-S / ADS-B decoder tests (C++ port of the validated
// Python suite tests/adsb_real_roundtrip.py). Numbers must match that reference.
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <vector>
#include <complex>
#include <QString>
#include "dsp/adsb_decoder.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static int checks = 0;
static void check(bool c, const char* m, const char* detail="") {
    ++checks;
    if (!c) { ++failures; std::printf("FAIL: %s %s\n", m, detail); }
    else    { std::printf("  ok:  %s %s\n", m, detail); }
}

// --------------------------------------------------------------------------- //
// Frame building helpers
// --------------------------------------------------------------------------- //
static std::vector<uint8_t> fromHex(const char* h) {
    std::vector<uint8_t> v;
    for (const char* p = h; *p && *(p+1); p += 2) {
        int b; sscanf(p, "%2x", &b); v.push_back((uint8_t)b);
    }
    return v;
}

// DF/CA + ICAO(3B) + ME(7B), append CRC-24.
static std::vector<uint8_t> buildLongFrame(uint8_t dfCa, uint32_t icao,
                                            const std::vector<uint8_t>& me7) {
    std::vector<uint8_t> f;
    f.push_back(dfCa);
    f.push_back((icao>>16)&0xFF); f.push_back((icao>>8)&0xFF); f.push_back(icao&0xFF);
    for (auto b : me7) f.push_back(b);
    uint32_t crc = ADSBDecoder::crc24(f.data(), 11);
    f.push_back((crc>>16)&0xFF); f.push_back((crc>>8)&0xFF); f.push_back(crc&0xFF);
    return f;
}

// Pack 8 callsign chars -> 6 bytes (48 bits), digits at code points 48..57.
static std::vector<uint8_t> encodeCallsign(const QString& cs) {
    int code[8];
    for (int i=0;i<8;i++) code[i]=32;
    for (int i=0;i<cs.size() && i<8;i++) {
        QChar c = cs.at(i).toUpper();
        if (c>=QLatin1Char('A') && c<=QLatin1Char('Z')) code[i]=c.toLatin1()-'A'+1;
        else if (c>=QLatin1Char('0') && c<=QLatin1Char('9')) code[i]=c.toLatin1()-'0'+48;
        else code[i]=32;
    }
    std::vector<uint8_t> out(6,0);
    int bit=0;
    for (int i=0;i<8;i++)
        for (int b=5;b>=0;b--) { if((code[i]>>b)&1) out[bit/8]|=(0x80>>(bit%8)); bit++; }
    return out;
}

static std::vector<uint8_t> buildIdentFrame(uint32_t icao, const QString& cs) {
    std::vector<uint8_t> me(7,0);
    me[0] = (1<<3);                       // TC=1 aircraft identification
    auto packed = encodeCallsign(cs);
    for (int i=0;i<6;i++) me[1+i]=packed[i];
    return buildLongFrame(0x8D, icao, me);
}

// Build a 7-byte ME from (first,last,value) 1-based inclusive fields.
static std::vector<uint8_t> buildMe(std::vector<std::pair<std::pair<int,int>,int>> fields) {
    int bits[56]={0};
    for (auto& f : fields) {
        int first=f.first.first, last=f.first.second, val=f.second;
        int w=last-first+1;
        for (int i=0;i<w;i++) bits[first-1+w-1-i]=(val>>i)&1;
    }
    std::vector<uint8_t> me(7,0);
    for (int i=0;i<56;i++) if(bits[i]) me[i/8]|=(0x80>>(i%8));
    return me;
}

// --------------------------------------------------------------------------- //
// PPM/OOK baseband synthesis (preamble + data), rate independent.
// --------------------------------------------------------------------------- //
static std::vector<std::complex<float>> modulate(const std::vector<uint8_t>& frame,
                                                 double sr, double leadUs=50.0) {
    double sps = sr/1e6;
    int nbits = (int)frame.size()*8;
    std::vector<int> bits(nbits);
    for (int i=0;i<nbits;i++) bits[i]=(frame[i/8]>>(7-i%8))&1;
    int lead = (int)std::round(leadUs*sps);
    int total = lead + (int)std::ceil((8.0+nbits)*sps) + 4;
    std::vector<std::complex<float>> out(total);
    static const double pu[4][2]={{0,0.5},{1,1.5},{3.5,4.0},{4.5,5.0}};
    for (int s=0;s<total;s++){
        double t=(s-lead)/sps;
        float v=0.0f;
        if (t>=0){
            bool high=false;
            for (auto& w:pu) if(t>=w[0]&&t<w[1]) high=true;
            if (t>=8.0){
                int bi=(int)std::floor(t-8.0);
                if(bi>=0&&bi<nbits){
                    double frac=(t-8.0)-bi;
                    if(bits[bi]==1){ if(frac<0.5) high=true; }
                    else            { if(frac>=0.5) high=true; }
                }
            }
            v=high?1.0f:0.0f;
        }
        out[s]={v,0.0f};
    }
    return out;
}

// Feed a single frame's IQ at the given rate; return the first new aircraft.
static AircraftInfo feedOne(const std::vector<uint8_t>& frame, double sr,
                            std::vector<AircraftInfo>* allOut=nullptr) {
    ADSBDecoder dec;
    dec.setSampleRate(sr);
    auto iq = modulate(frame, sr);
    dec.feed(iq);
    auto out = dec.takeNewAircraft();
    if (allOut) *allOut = out;
    return out.empty() ? AircraftInfo() : out.front();
}

// --------------------------------------------------------------------------- //
// CPR encode (port of Python encode_cpr_airborne)
// --------------------------------------------------------------------------- //
static void encodeCpr(double lat, double lon,
                      int& le, int&lo, int&oe, int&oo) {
    double d0=360.0/60.0, d1=360.0/59.0;
    le=(int)std::floor(((lat-std::floor(lat/d0)*d0)/d0)*131072.0);
    oe=(int)std::floor(((lat-std::floor(lat/d1)*d1)/d1)*131072.0);
    int ni=cprNL(lat), niO=std::max(ni-1,1);
    double e0=360.0/ni, e1=360.0/niO;
    lo=(int)std::floor(((lon-std::floor(lon/e0)*e0)/e0)*131072.0);
    oo=(int)std::floor(((lon-std::floor(lon/e1)*e1)/e1)*131072.0);
}

static std::vector<uint8_t> makePosFrame(uint32_t icao, bool odd, int clat, int clon) {
    auto me = buildMe({{{1,5},11},{{22,22},odd?1:0},{{23,39},clat},{{40,56},clon}});
    return buildLongFrame(0x8D, icao, me);
}

int main() {
    std::printf("== ADS-B decode comprehensive tests ==\n");

    // --- 1. known public DF17 frame ---------------------------------------- //
    std::printf("\n[1] known DF17 frame\n");
    auto known = fromHex("8D40621D58C382D690C8AC2863A7");
    uint32_t syn = ADSBDecoder::crc24(known.data(), (int)known.size());
    check(syn==0, "known frame whole-frame syndrome==0",
          (std::string("syndrome=")+std::to_string(syn)).c_str());
    {
        auto ac = feedOne(known, 2400000.0);
        check(ac.df==17, "DF=17", QString::number(ac.df).toLatin1().data());
        check(ac.icao=="40621D", "ICAO=40621D", ac.icao.toLatin1().data());
        check(ac.altitudeFt==38000, "altitude=38000ft",
              QString::number(ac.altitudeFt).toLatin1().data());
        check(ac.crcOk, "crcOk");
    }
    // flip last bit -> must be dropped
    {
        auto bad = known; bad.back() ^= 0x01;
        std::vector<AircraftInfo> out;
        auto ac = feedOne(bad, 2400000.0, &out);
        check(out.empty(), "flipped-bit frame dropped (no aircraft)");
    }

    // --- 2. callsign roundtrip -------------------------------------------- //
    std::printf("\n[2] TC1-4 callsign roundtrip\n");
    for (QString cs : {"BAW123","UAL2167","CES582","B4MIB"}) {
        auto f = buildIdentFrame(0x4CA8EE, cs);
        auto ac = feedOne(f, 2400000.0);
        check(ac.crcOk, (cs+" crc ok").toLatin1().data());
        check(ac.callsign==cs, (cs+" callsign exact").toLatin1().data(),
              ac.callsign.toLatin1().data());
    }

    // --- 3. TC19 velocity --------------------------------------------------- //
    std::printf("\n[3] TC19 subtype-1 ground velocity\n");
    {
        auto me = buildMe({{{1,5},19},{{6,8},1},{{15,24},300},{{26,35},250}});
        auto f = buildLongFrame(0x8D, 0x4CA8EE, me);
        auto ac = feedOne(f, 2400000.0);
        double expGs = std::hypot(299.0,249.0);
        double expTr = std::atan2(299.0,249.0)*180.0/M_PI;
        check(ac.hasVelocity, "hasVelocity");
        char d[128];
        std::snprintf(d,sizeof d,"got gs=%.3f exp=%.3f", ac.groundspeedKt, expGs);
        check(std::fabs(ac.groundspeedKt-expGs)<0.2, "groundspeed ~389.1kt", d);
        std::snprintf(d,sizeof d,"got tr=%.3f exp=%.3f", ac.headingDeg, expTr);
        check(std::fabs(ac.headingDeg-expTr)<0.2, "heading ~50.2deg", d);
    }

    // --- 4. NL values ------------------------------------------------------- //
    std::printf("\n[4] CPR NL table\n");
    check(cprNL(0)==59, "NL(0)=59");
    check(cprNL(10)==59, "NL(10)=59");
    check(cprNL(50)==38, "NL(50)=38");
    check(cprNL(51.5)==37, "NL(51.5)=37");
    check(cprNL(88)==1, "NL(88)=1");
    check(cprNL(80)>=8 && cprNL(80)<=12, "NL(80) in [8,12]");
    check(cprNL(40)>=40 && cprNL(40)<=50, "NL(40) in [40,50]");

    // --- 5. CPR global encode->decode roundtrip ----------------------------- //
    std::printf("\n[5] CPR global roundtrip\n");
    for (auto [la,lo] : std::vector<std::pair<double,double>>{
            {51.47,-0.45},{39.9,116.4},{-33.86,151.2},{46.0,7.5}}) {
        int le,lo2,oe,oo; encodeCpr(la,lo,le,lo2,oe,oo);
        CprPair p; p.latEven=le; p.lonEven=lo2; p.latOdd=oe; p.lonOdd=oo;
        p.haveEven=p.haveOdd=true; p.evenNewest=true;
        double la2,lo3;
        bool ok=cprGlobalDecode(p,la2,lo3);
        char d[160];
        std::snprintf(d,sizeof d,"(%.2f,%.2f)->(%.5f,%.5f)",la,lo,la2,lo3);
        check(ok && std::fabs(la2-la)<0.001 && std::fabs(lo3-lo)<0.001,
              "CPR roundtrip <0.001deg", d);
    }

    // --- 6. real recorded UK CPR vector (dump1090 cprtests.c) --------------- //
    std::printf("\n[6] UK recorded CPR vector\n");
    {
        CprPair p; p.latEven=80536; p.lonEven=9432; p.latOdd=61720; p.lonOdd=9192;
        p.haveEven=p.haveOdd=true;
        double la,lo; char d[160];
        p.evenNewest=true;
        bool ok=cprGlobalDecode(p,la,lo);
        std::snprintf(d,sizeof d,"even-newest -> (%.6f,%.6f)",la,lo);
        check(ok && std::fabs(la-51.686646)<1e-3 && std::fabs(lo-0.700156)<1e-3,
              "even-newest ~(51.686646,0.700156)", d);
        p.evenNewest=false;
        ok=cprGlobalDecode(p,la,lo);
        std::snprintf(d,sizeof d,"odd-newest -> (%.6f,%.6f)",la,lo);
        check(ok && std::fabs(la-51.686763)<1e-3 && std::fabs(lo-0.701294)<1e-3,
              "odd-newest ~(51.686763,0.701294)", d);
    }

    // --- 7. stateful pairing through the decoder --------------------------- //
    std::printf("\n[7] stateful even/odd pairing\n");
    {
        int le,lo,oe,oo; encodeCpr(39.9,116.4,le,lo,oe,oo);
        ADSBDecoder dec; dec.setSampleRate(2400000.0);
        dec.feed(modulate(makePosFrame(0x40621D,false,le,lo), 2400000.0));
        dec.takeNewAircraft(); // even frame: no position yet
        dec.feed(modulate(makePosFrame(0x40621D,true,oe,oo), 2400000.0));
        auto out = dec.takeNewAircraft();
        bool found=false; double la,lo2;
        for (auto& a : out) if (a.hasPosition) { found=true; la=a.lat; lo2=a.lon; }
        char d[160];
        std::snprintf(d,sizeof d,"pos=(%.5f,%.5f)",la,lo2);
        check(found && std::fabs(la-39.9)<0.001 && std::fabs(lo2-116.4)<0.001,
              "paired position ~(39.9,116.4)", d);
    }

    // --- 8. physical-layer end-to-end at multiple sample rates -------------- //
    std::printf("\n[8] PPM physical layer end-to-end\n");
    {
        auto f = buildIdentFrame(0x4CA8EE, "BAW123");
        for (double sr : {2.0e6, 2.4e6, 3.2e6}) {
            auto ac = feedOne(f, sr);
            char d[64]; std::snprintf(d,sizeof d,"sr=%.1fMHz", sr/1e6);
            check(ac.icao=="4CA8EE" && ac.callsign=="BAW123",
                  "e2e decodes ICAO+callsign", d);
        }
        // pure silence -> honest empty
        ADSBDecoder dec; dec.setSampleRate(2400000.0);
        std::vector<std::complex<float>> silence(2000, {0.0f,0.0f});
        dec.feed(silence);
        check(dec.takeNewAircraft().empty(), "silence IQ yields no aircraft");
        // low noise floor -> still no aircraft (CRC gate)
        ADSBDecoder dec2; dec2.setSampleRate(2400000.0);
        std::vector<std::complex<float>> noise(4000);
        unsigned seed=12345;
        for (auto& s : noise){ seed=seed*1103515245u+12345u; float r=((seed>>16)&0x7fff)/32767.0f; s={r*0.02f,0}; }
        dec2.feed(noise);
        check(dec2.takeNewAircraft().empty(), "low noise IQ yields no aircraft");
        // preamble present but CRC broken -> dropped
        auto bad = f; bad[13] ^= 0xFF;
        std::vector<AircraftInfo> out;
        feedOne(bad, 2400000.0, &out);
        check(out.empty(), "preamble ok but CRC broken -> no aircraft");
    }

    std::printf("\n== checks: %d, failures: %d ==\n", checks, failures);
    return failures?1:0;
}
