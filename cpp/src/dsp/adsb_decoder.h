// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <QDateTime>
#include <vector>
#include <complex>
#include <array>
#include <QHash>
#include <limits>

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
};

// CPR global position decode (even/odd pair). Public for unit tests.
struct CprPair {
    int latEven = 0, lonEven = 0;
    int latOdd  = 0, lonOdd  = 0;
    bool haveEven = false, haveOdd = false;
};
bool cprGlobalDecode(const CprPair& p, double& latOut, double& lonOut);

class ADSBDecoder {
public:
    ADSBDecoder();

    void setSampleRate(double sr);
    void feed(const std::vector<std::complex<float>>& iq);
    std::vector<AircraftInfo> takeNewAircraft();
    void reset();

    static uint32_t crc24(const uint8_t* data, int len);

private:
    double sr_ = 2000000.0;
    std::vector<float> magBuf_;
    std::vector<AircraftInfo> newAircraft_;
    std::vector<AircraftInfo> knownAircraft_;

    bool checkPreamble(std::size_t idx) const;
    bool decodeFrame(std::size_t start, std::size_t totalBits, AircraftInfo& out);
    static QString decodeCallsign(const uint8_t* me);

    QHash<QString, CprPair> cprByIcao_;   // per-aircraft CPR even/odd state
};

} // namespace dsp
} // namespace mbdsdr
