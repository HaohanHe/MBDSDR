// SPDX-License-Identifier: MIT
// NMEA 0183 parser for a merged GnssFix.
//
// Robust by design:
//  * verifies the '$...*HH' XOR checksum before trusting any sentence;
//  * skips truncated / binary-noise / garbage / wrong-checksum lines without
//    touching the already-accumulated fix;
//  * supports half-lines arriving across feed() chunk boundaries (a leftover
//    partial line is retained until its newline arrives);
//  * merges multi-packet GSV sky-view reports into one visible-satellite list.
//
// Recognised talkers/sentences: GGA, RMC, GSA, GSV, ZDA (GLL/VTG are parsed
// lightly if present but not required).
#pragma once

#include "gnss_types.h"

#include <QByteArray>
#include <QString>
#include <set>

namespace mbdsdr {
namespace gnss {

class NmeaParser {
public:
    NmeaParser();

    // Feed a raw chunk (may start/end mid-line). Returns the number of
    // complete sentences accepted from this chunk. Invalid lines are dropped
    // silently and never pollute fix().
    int feed(const QByteArray& chunk);

    // The accumulated, merged fix. Valid until the next feed()/reset().
    const GnssFix& fix() const { return m_fix; }
    GnssFix& fix() { return m_fix; }

    // Statistics (useful for tests / health).
    int acceptedCount() const { return m_accepted; }
    int rejectedCount() const { return m_rejected; }

    // Drop all accumulated state.
    void reset();

    // Exposed for unit tests: returns true iff body (between $ and *) XORs
    // to the two hex digits after '*'.
    static bool verifyChecksum(const QByteArray& line);

private:
    void processLine(const QByteArray& raw);
    void dispatch(const QByteArray& body); // body = "$...." without *HH

    void parseGga(const QByteArrayList& f);
    void parseRmc(const QByteArrayList& f);
    void parseGsa(const QByteArrayList& f);
    void parseGsv(const QByteArrayList& f);
    void parseZda(const QByteArrayList& f);

    // dddmm.mmmm -> decimal degrees, with sign from N/E.
    static double dmmToDegrees(const QByteArray& dmm, char hemi);
    static bool toDouble(const QByteArray& s, double& out);
    static bool toInt(const QByteArray& s, int& out);

    QByteArray m_leftover;       // partial line carried across chunks
    GnssFix m_fix;
    int m_accepted = 0;
    int m_rejected = 0;

    // GSV reassembly
    int m_gsvExpected = 0;
    int m_gsvSeen = 0;
    std::vector<GnssSatellite> m_gsvTmp;

    // PRNs currently contributing to the fix (from GSA), used to flag GSV rows.
    std::set<int> m_usedPrns;
};

} // namespace gnss
} // namespace mbdsdr
