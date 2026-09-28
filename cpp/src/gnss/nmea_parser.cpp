// SPDX-License-Identifier: MIT
#include "nmea_parser.h"

#include <QByteArray>
#include <QDate>
#include <QTime>
#include <QTimeZone>

#include <cctype>

namespace mbdsdr {
namespace gnss {

NmeaParser::NmeaParser() { reset(); }

void NmeaParser::reset() {
    m_leftover.clear();
    m_fix = GnssFix();
    m_accepted = 0;
    m_rejected = 0;
    m_gsvExpected = 0;
    m_gsvSeen = 0;
    m_gsvTmp.clear();
    m_usedPrns.clear();
}

bool NmeaParser::toDouble(const QByteArray& s, double& out) {
    if (s.isEmpty()) return false;
    bool ok = false;
    double v = s.toDouble(&ok);
    if (!ok) return false;
    out = v;
    return true;
}

bool NmeaParser::toInt(const QByteArray& s, int& out) {
    if (s.isEmpty()) return false;
    bool ok = false;
    int v = s.toInt(&ok);
    if (!ok) return false;
    out = v;
    return true;
}

double NmeaParser::dmmToDegrees(const QByteArray& dmm, char hemi) {
    double v = 0.0;
    if (!toDouble(dmm, v)) return 0.0;
    const int deg = static_cast<int>(v / 100.0);
    const double min = v - deg * 100.0;
    double dec = deg + min / 60.0;
    if (hemi == 'S' || hemi == 'W') dec = -dec;
    return dec;
}

bool NmeaParser::verifyChecksum(const QByteArray& line) {
    // line is a complete sentence e.g. "$GPGGA,...*47"
    const int star = line.lastIndexOf('*');
    if (line.size() < 5 || line[0] != '$' || star < 2) return false;
    const QByteArray body = line.mid(1, star - 1);
    const QByteArray cs = line.mid(star + 1).trimmed();
    if (cs.size() < 2) return false;
    unsigned int expected = 0;
    {
        bool ok = false;
        expected = cs.left(2).toUInt(&ok, 16);
        if (!ok) return false;
    }
    unsigned char x = 0;
    for (char c : body) x ^= static_cast<unsigned char>(c);
    return x == expected;
}

int NmeaParser::feed(const QByteArray& chunk) {
    m_leftover.append(chunk);
    const int acceptedBefore = m_accepted;

    int nl = 0;
    while ((nl = m_leftover.indexOf('\n')) >= 0) {
        QByteArray line = m_leftover.left(nl);
        m_leftover.remove(0, nl + 1);
        while (!line.isEmpty() && (line.endsWith('\r') || line.endsWith(' ') || line.endsWith('\t')))
            line.chop(1);
        if (!line.isEmpty()) processLine(line);
    }
    return m_accepted - acceptedBefore;
}

void NmeaParser::processLine(const QByteArray& raw) {
    if (raw.size() < 6 || raw[0] != '$') { ++m_rejected; return; }
    if (raw.indexOf('*') < 0) { ++m_rejected; return; } // truncated / no checksum
    if (!verifyChecksum(raw)) { ++m_rejected; return; }

    // body = "$GPGGA,...."  (everything before '*')
    const int star = raw.lastIndexOf('*');
    QByteArray body = raw.mid(0, star); // includes leading '$'
    ++m_accepted;
    dispatch(body);
}

void NmeaParser::dispatch(const QByteArray& body) {
    QByteArrayList f = body.split(',');
    if (f.isEmpty()) return;
    QByteArray head = f[0];
    if (head.startsWith('$')) head.remove(0, 1);
    if (head.size() < 5) return;
    const QByteArray type = head.right(3);

    if (type == "GGA")      parseGga(f);
    else if (type == "RMC") parseRmc(f);
    else if (type == "GSA") parseGsa(f);
    else if (type == "GSV") parseGsv(f);
    else if (type == "ZDA") parseZda(f);
    // GLL / VTG accepted-but-not-required: ignored intentionally.
}

static void applyTime(QDateTime& dt, bool& hasUtc, int h, int m, double sec) {
    QDate d = dt.isValid() ? dt.date() : QDate(1970, 1, 1);
    int s = static_cast<int>(sec);
    int ms = static_cast<int>((sec - s) * 1000.0 + 0.5);
    dt = QDateTime(d, QTime(h, m, s, ms), QTimeZone::UTC);
    if (d.year() >= 1980) hasUtc = true;
}

static void applyDate(QDateTime& dt, bool& hasUtc, int yy, int mon, int day) {
    const int year = (yy >= 80 ? 1900 : 2000) + yy;
    QDate d(year, mon, day);
    if (!d.isValid()) return;
    QTime t = (dt.isValid() && dt.time().isValid()) ? dt.time() : QTime(0, 0);
    dt = QDateTime(d, t, QTimeZone::UTC);
    if (t.isValid()) hasUtc = true;
}

void NmeaParser::parseGga(const QByteArrayList& f) {
    // $GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47
    if (f.size() < 10) return;

    // time f[1]
    {
        double sec = 0; int hh = 0, mm = 0;
        QByteArray t = f[1];
        if (t.size() >= 6) {
            hh = t.left(2).toInt();
            mm = t.mid(2, 2).toInt();
            NmeaParser::toDouble(t.mid(4), sec);
            applyTime(m_fix.utc, m_fix.hasUtc, hh, mm, sec);
        }
    }

    double lat = dmmToDegrees(f[2], f[3].isEmpty() ? 'N' : f[3][0]);
    double lon = dmmToDegrees(f[4], f[5].isEmpty() ? 'E' : f[5][0]);
    if (!f[2].isEmpty() && !f[4].isEmpty()) {
        m_fix.latitude = lat;
        m_fix.longitude = lon;
        m_fix.hasPosition = true;
    }

    int q = 0;
    if (toInt(f[6], q)) m_fix.fixQuality = static_cast<FixQuality>(q);

    int sats = 0;
    if (toInt(f[7], sats)) m_fix.satellitesInUse = sats;

    double hdop = 0;
    if (toDouble(f[8], hdop)) { m_fix.hdop = hdop; m_fix.hasDop = true; }

    double alt = 0;
    if (toDouble(f[9], alt)) m_fix.altitudeMsl = alt;

    double sep = 0;
    if (f.size() > 11 && toDouble(f[11], sep)) m_fix.geoidSeparation = sep;

    m_fix.receivedAt = QDateTime::currentDateTime();
}

void NmeaParser::parseRmc(const QByteArrayList& f) {
    // $GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A
    if (f.size() < 10) return;

    if (f[2] == "A") { // valid
        double lat = dmmToDegrees(f[3], f[4].isEmpty() ? 'N' : f[4][0]);
        double lon = dmmToDegrees(f[5], f[6].isEmpty() ? 'E' : f[6][0]);
        if (!f[3].isEmpty() && !f[5].isEmpty()) {
            m_fix.latitude = lat;
            m_fix.longitude = lon;
            m_fix.hasPosition = true;
        }
    }

    // time
    {
        double sec = 0; int hh = 0, mm = 0;
        QByteArray t = f[1];
        if (t.size() >= 6) {
            hh = t.left(2).toInt();
            mm = t.mid(2, 2).toInt();
            toDouble(t.mid(4), sec);
            applyTime(m_fix.utc, m_fix.hasUtc, hh, mm, sec);
        }
    }

    double speed = 0, course = 0;
    if (toDouble(f[7], speed))  { m_fix.speedKn = speed; m_fix.hasMotion = true; }
    if (toDouble(f[8], course)) m_fix.courseDeg = course;

    // date ddmmyy
    if (f[9].size() == 6) {
        int dd = f[9].left(2).toInt();
        int mon = f[9].mid(2, 2).toInt();
        int yy = f[9].right(2).toInt();
        applyDate(m_fix.utc, m_fix.hasUtc, yy, mon, dd);
    }
}

void NmeaParser::parseGsa(const QByteArrayList& f) {
    // $GPGSA,A,3,01,02,...,08,,,,,2.1,0.9,1.9*38
    if (f.size() < 18) return;
    int type = 0;
    if (toInt(f[2], type)) m_fix.fixType = static_cast<FixType>(type);

    m_usedPrns.clear();
    for (int i = 3; i <= 14; ++i) {
        int prn = 0;
        if (toInt(f[i], prn)) {
            m_usedPrns.insert(prn);
            // flag any already-known visible satellite
            for (auto& s : m_fix.visibleSatellites)
                if (s.prn == prn) s.used = true;
        }
    }
    // keep satellitesInUse in sync with the GSA used set (GGA reports it too).
    if (!m_usedPrns.empty()) m_fix.satellitesInUse = static_cast<int>(m_usedPrns.size());

    double pdop = 0, hdop = 0, vdop = 0;
    if (toDouble(f[15], pdop)) m_fix.pdop = pdop;
    if (toDouble(f[16], hdop)) m_fix.hdop = hdop;
    if (toDouble(f[17], vdop)) m_fix.vdop = vdop;
    m_fix.hasDop = true;
}

void NmeaParser::parseGsv(const QByteArrayList& f) {
    // $GPGSV,total,msg,inView,prn,elev,azim,snr, ... *xx
    if (f.size() < 4) return;
    int total = 0, msg = 0;
    if (!toInt(f[1], total) || !toInt(f[2], msg)) return;

    if (msg == 1) { m_gsvTmp.clear(); m_gsvExpected = total; m_gsvSeen = 0; }
    m_gsvSeen++;

    // 4 fields per satellite, starting at index 4
    for (int i = 4; i + 3 < f.size(); i += 4) {
        GnssSatellite s;
        if (!toInt(f[i], s.prn)) continue;
        toInt(f[i + 1], s.elevation);
        toInt(f[i + 2], s.azimuth);
        int snr = -1;
        if (toInt(f[i + 3], snr)) s.snr = snr;
        s.used = (m_usedPrns.find(s.prn) != m_usedPrns.end());
        m_gsvTmp.push_back(s);
    }

    if (m_gsvExpected > 0 && msg >= m_gsvExpected) {
        m_fix.visibleSatellites = m_gsvTmp;
        m_gsvTmp.clear();
        m_gsvExpected = 0;
        m_gsvSeen = 0;
    }
}

void NmeaParser::parseZda(const QByteArrayList& f) {
    // $GPZDA,123519.00,23,03,1994,00,00*6C
    if (f.size() < 5) return;
    double sec = 0; int hh = 0, mm = 0;
    QByteArray t = f[1];
    if (t.size() >= 6) {
        hh = t.left(2).toInt();
        mm = t.mid(2, 2).toInt();
        toDouble(t.mid(4), sec);
        applyTime(m_fix.utc, m_fix.hasUtc, hh, mm, sec);
    }
    int day = 0, mon = 0, year4 = 0;
    if (toInt(f[2], day) && toInt(f[3], mon) && toInt(f[4], year4)) {
        QDate d(year4, mon, day);
        if (d.isValid()) {
            QTime tm = (m_fix.utc.isValid() && m_fix.utc.time().isValid()) ? m_fix.utc.time() : QTime(0, 0);
            m_fix.utc = QDateTime(d, tm, QTimeZone::UTC);
            if (tm.isValid()) m_fix.hasUtc = true;
        }
    }
}

QString fixQualityToString(FixQuality q) {
    switch (q) {
    case FixQuality::Invalid:   return QStringLiteral("Invalid");
    case FixQuality::GpsFix:    return QStringLiteral("GPS fix");
    case FixQuality::DgpsFix:   return QStringLiteral("DGPS fix");
    case FixQuality::PpsFix:    return QStringLiteral("PPS fix");
    case FixQuality::RtkFixed:  return QStringLiteral("RTK fixed");
    case FixQuality::RtkFloat:  return QStringLiteral("RTK float");
    case FixQuality::Estimated: return QStringLiteral("Estimated");
    case FixQuality::Manual:    return QStringLiteral("Manual");
    case FixQuality::Simulation:return QStringLiteral("Simulation");
    }
    return QStringLiteral("Unknown");
}

QString fixTypeToString(FixType t) {
    switch (t) {
    case FixType::Invalid: return QStringLiteral("Unknown");
    case FixType::NoFix:   return QStringLiteral("No fix");
    case FixType::Fix2D:   return QStringLiteral("2D fix");
    case FixType::Fix3D:   return QStringLiteral("3D fix");
    }
    return QStringLiteral("Unknown");
}

} // namespace gnss
} // namespace mbdsdr
