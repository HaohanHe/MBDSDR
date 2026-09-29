// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <QDateTime>
#include <vector>
#include <complex>
#include <array>
#include <QHash>
#include <limits>
#include <cstddef>
#include <cstdint>

namespace mbdsdr {
namespace dsp {

struct AircraftInfo {
    QString icao;
    QString callsign;
    int altitudeFt = -1;
    int df = 0;
    bool crcOk = false;
    QDateTime firstSeen;
    QDateTime lastSeen;
    double lat = std::numeric_limits<double>::quiet_NaN();
    double lon = std::numeric_limits<double>::quiet_NaN();
    bool hasPosition = false;
    double groundspeedKt = -1;
    double headingDeg = -1;
    bool hasVelocity = false;
    int verticalRateFpm = 0;
    bool hasVerticalRate = false;
    QDateTime positionTime;
};

// CPR global position decode (even/odd pair). Public for unit tests.
struct CprPair {
    int latEven = 0, lonEven = 0;
    int latOdd  = 0, lonOdd  = 0;
    bool haveEven = false, haveOdd = false;
    /// Which slot produced the latest frame: true = even newest, false = odd newest.
    /// The global decoder reports coordinates in the newest frame's slot.
    bool evenNewest = true;
};
bool cprGlobalDecode(const CprPair& p, double& latOut, double& lonOut);
int  cprNL(double latDeg);
/// Local decode using a reference (station) position -- single frame, ~10 km error.
bool cprLocalDecode(int cprLat, int cprLon, bool isOdd,
                    double refLat, double refLon,
                    double& latOut, double& lonOut);

class ADSBDecoder {
public:
    ADSBDecoder();

    void setSampleRate(double sr);
    void feed(const std::vector<std::complex<float>>& iq);
    std::vector<AircraftInfo> takeNewAircraft();
    void reset();
    /// Reference (station) position for CPR local decode.
    void setReferencePosition(double latDeg, double lonDeg) {
        refLat_ = latDeg; refLon_ = lonDeg; haveRef_ = true;
    }

    static uint32_t crc24(const uint8_t* data, int len);

private:
    double sr_ = 2000000.0;
    double sps_ = 2.0;              // samples per microsecond = sr/1e6
    std::vector<float> magBuf_;     // rolling magnitude (envelope) stream
    std::size_t scanPos_ = 0;       // next candidate preamble offset inside magBuf_
    std::vector<AircraftInfo> newAircraft_;
    std::vector<AircraftInfo> knownAircraft_;

    // --- internal helpers (physical layer) ---
    /// Average magnitude envelope over the open half-window [usA, usB) that
    /// starts at buffer index `base`. Rate independent (uses floating us->sample).
    float windowMean(std::size_t base, double usA, double usB) const;
    bool checkPreamble(std::size_t base) const;
    /// PPM bit level at bit index `bitIdx` (data bits start at 8us).
    bool ppmBit(std::size_t base, int bitIdx) const;
    /// Decode the first 5 data bits -> Downlink Format.
    int  probeDf(std::size_t base) const;
    /// Pull a full frame into `out` (nbits=56 or 112), run parity, decode ME.
    void processFrame(std::size_t base, int df, int nbits);

    static QString decodeCallsign(const uint8_t* me);
    static int  ac12Altitude(uint16_t ac12, bool& ok);
    /// Apply the Mode-S parity/address table; returns true when the frame is
    /// accepted and fills icaoOut (24-bit, uppercased hex string set by caller).
    static bool checkParity(int df, const uint8_t* frame, int nbytes,
                            uint32_t& icaoOut);

    QHash<QString, CprPair> cprByIcao_;   // per-aircraft CPR even/odd state
    double refLat_ = 0, refLon_ = 0;
    bool haveRef_ = false;
};

} // namespace dsp
} // namespace mbdsdr
