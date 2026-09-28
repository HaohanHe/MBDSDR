// SPDX-License-Identifier: MIT
// Tests for the NMEA 0183 parser.
//
// The sentence block below is a classic PUBLIC recorded NMEA sample
// (Wikipedia "NMEA 0183" example; the companion GGA is the well-known
// $GPGGA,123519,...*47 line). It is replayed here as a text fixture ONLY:
//
//          录制样例·非硬件 NOT HARDWARE  (recorded sample, no real device)
//
// Expected numbers are hand-computed from the sentence fields and checked
// against the parser output with real tolerance (not estimated).
#include "gnss/nmea_parser.h"

#include <QByteArray>
#include <QDateTime>
#include <cmath>
#include <cstdio>

using namespace mbdsdr::gnss;

static int failures = 0;
static void check(bool cond, const char* msg) {
    if (!cond) { ++failures; std::printf("FAIL: %s\n", msg); }
}
static bool closeEnough(double a, double b, double tol) {
    return std::fabs(a - b) <= tol;
}

// Recorded block (NOT HARDWARE). Checksums verified independently.
static const char* kSample =
    "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"
    "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n"
    "$GPGSA,A,3,01,02,03,04,05,06,07,08,,,,,2.1,0.9,1.9*38\r\n"
    "$GPGSV,2,1,08,01,40,083,42,02,17,306,35,03,07,260,30,04,28,165,41*77\r\n"
    "$GPGSV,2,2,08,05,40,083,42,06,17,306,35,07,07,260,30,08,28,165,41*7C\r\n"
    "$GPZDA,123519.00,23,03,1994,00,00*6C\r\n";

int main() {
    // --- 1. Full recorded block, parsed in one chunk ----------------------
    {
        NmeaParser p;
        int accepted = p.feed(QByteArray(kSample));
        std::printf("accepted=%d rejected=%d\n", accepted, p.rejectedCount());
        check(accepted == 6, "six valid sentences accepted");
        check(p.rejectedCount() == 0, "no valid line rejected");

        const GnssFix& f = p.fix();
        check(f.hasPosition, "hasPosition");
        // Hand-computed: lat = 48 + 7.038/60 = 48.117300
        check(closeEnough(f.latitude, 48.0 + 7.038/60.0, 1e-5), "lat==48.117300");
        // Hand-computed: lon = 11 + 31.000/60 = 11.516667
        check(closeEnough(f.longitude, 11.0 + 31.000/60.0, 1e-5), "lon==11.516667");
        check(closeEnough(f.altitudeMsl, 545.4, 1e-3), "alt==545.4");
        check(f.fixQuality == FixQuality::GpsFix, "quality==1 (GPS fix)");
        check(f.satellitesInUse == 8, "8 satellites in use");
        check(closeEnough(f.hdop, 0.9, 1e-3), "HDOP==0.9");
        check(closeEnough(f.pdop, 2.1, 1e-3), "PDOP==2.1");
        check(closeEnough(f.vdop, 1.9, 1e-3), "VDOP==1.9");
        check(f.fixType == FixType::Fix3D, "fixType==3 (3D)");

        // UTC time from GGA/ZDA + date from RMC/ZDA
        check(f.hasUtc, "hasUtc");
        check(f.utc.time().hour() == 12 && f.utc.time().minute() == 35 &&
              f.utc.time().second() == 19, "UTC 12:35:19");
        check(f.utc.date().year() == 1994 && f.utc.date().month() == 3 &&
              f.utc.date().day() == 23, "UTC date 1994-03-23");

        // Motion from RMC
        check(f.hasMotion, "hasMotion");
        check(closeEnough(f.speedKn, 22.4, 1e-3), "speed==22.4 kn");
        check(closeEnough(f.courseDeg, 84.4, 1e-3), "course==84.4 deg");

        // GSV: 8 visible satellites, all flagged used (all PRNs in GSA)
        check(f.visibleSatellites.size() == 8, "8 visible satellites");
        int usedCount = 0;
        for (const auto& s : f.visibleSatellites) if (s.used) ++usedCount;
        check(usedCount == 8, "all 8 visible flagged used");
        // first satellite prn=1 elev=40 azim=83 snr=42
        if (!f.visibleSatellites.empty()) {
            const auto& s0 = f.visibleSatellites.front();
            check(s0.prn == 1 && s0.elevation == 40 && s0.azimuth == 83 && s0.snr == 42,
                  "sat0 prn/elev/azim/snr");
        }
        std::printf("  lat=%.6f lon=%.6f alt=%.1f sats=%d hdop=%.2f utc=%s\n",
                    f.latitude, f.longitude, f.altitudeMsl, f.satellitesInUse, f.hdop,
                    f.utc.toString(Qt::ISODate).toLocal8Bit().constData());
    }

    // --- 2. Bad checksum rejected, does NOT pollute a good fix ------------
    {
        NmeaParser p;
        p.feed(QByteArray(kSample));
        GnssFix good = p.fix();
        double latBefore = good.latitude;

        // flip one checksum nibble -> wrong checksum
        QByteArray bad = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*99\r\n";
        int acc = p.feed(bad);
        check(acc == 0, "bad-checksum line not accepted");
        check(closeEnough(p.fix().latitude, latBefore, 1e-9), "fix unchanged after bad line");
    }

    // --- 3. Truncated / garbage / binary noise rejected ------------------
    {
        NmeaParser p;
        p.feed(QByteArray(kSample));
        int rejBefore = p.rejectedCount();

        p.feed("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9\r\n"); // no *HH
        p.feed("this is not a sentence\r\n");
        {
            // binary noise: build explicitly so embedded NULs are preserved
            QByteArray bin;
            bin.append('\x01').append('\x02').append('\xff').append('\xfe');
            bin.append("junk\r\n");
            p.feed(bin);
        }
        p.feed("$GPGGA,no,numeric,lat\r\n"); // malformed + no checksum
        check(p.rejectedCount() - rejBefore == 4, "truncated/garbage/binary rejected");
    }

    // --- 4. Half-line across chunk boundaries reassembles -----------------
    {
        NmeaParser whole;
        whole.feed(QByteArray(kSample));

        NmeaParser chunked;
        // feed 3 bytes at a time
        QByteArray blob(kSample);
        for (int i = 0; i < blob.size(); i += 3)
            chunked.feed(blob.mid(i, 3));

        const GnssFix& a = whole.fix();
        const GnssFix& b = chunked.fix();
        check(closeEnough(a.latitude, b.latitude, 1e-9), "chunked lat matches");
        check(closeEnough(a.longitude, b.longitude, 1e-9), "chunked lon matches");
        check(a.visibleSatellites.size() == b.visibleSatellites.size(),
              "chunked visible sat count matches");
        check(b.utc == a.utc, "chunked UTC matches");
        check(chunked.rejectedCount() == 0, "chunked produced no rejects");
    }

    // --- 5. verifyChecksum unit behaviour ---------------------------------
    check(NmeaParser::verifyChecksum("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"),
          "verifyChecksum accepts known-good *47");
    check(!NmeaParser::verifyChecksum("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*48"),
          "verifyChecksum rejects *48");

    if (failures == 0) std::printf("test_nmea_parser: ALL PASS\n");
    else std::printf("test_nmea_parser: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
