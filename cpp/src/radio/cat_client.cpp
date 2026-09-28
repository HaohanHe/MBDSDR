// SPDX-License-Identifier: GPL-3.0-or-later
#include "cat_client.h"

#include <cmath>

namespace mbdsdr {
namespace radio {

namespace {

// --- CI-V helpers ---
QByteArray civFrame(unsigned char to, unsigned char from,
                    std::initializer_list<unsigned char> body) {
    QByteArray f;
    f.append('\xFE').append('\xFE');
    f.append(static_cast<char>(to)).append(static_cast<char>(from));
    for (unsigned char b : body) f.append(static_cast<char>(b));
    f.append('\xFD');
    return f;
}

// 5 BCD bytes, transmission order: (10,1) (1k,100) (100k,10k) (10M,1M) (-,100M).
void civEncodeFreq(double hz, unsigned char out[5]) {
    long long f = static_cast<long long>(std::llround(hz));
    int d[10];
    for (int i = 0; i < 10; ++i) { d[i] = f % 10; f /= 10; }
    out[0] = (d[1] << 4) | d[0];
    out[1] = (d[3] << 4) | d[2];
    out[2] = (d[5] << 4) | d[4];
    out[3] = (d[7] << 4) | d[6];
    out[4] = (d[9] << 4) | d[8];
}

double civDecodeFreq(const unsigned char b[5]) {
    long long f = 0;
    f += (b[4] >> 4) * 1000000000LL + (b[4] & 0xF) * 100000000LL;
    f += (b[3] >> 4) * 10000000LL + (b[3] & 0xF) * 1000000LL;
    f += (b[2] >> 4) * 100000LL + (b[2] & 0xF) * 10000LL;
    f += (b[1] >> 4) * 1000LL + (b[1] & 0xF) * 100LL;
    f += (b[0] >> 4) * 10LL + (b[0] & 0xF);
    return static_cast<double>(f);
}

int civModeCode(const QString& m) {
    const QString s = m.toUpper();
    if (s == "LSB") return 0x00;
    if (s == "USB") return 0x01;
    if (s == "AM")  return 0x02;
    if (s == "CW" || s == "CWR") return 0x03;
    if (s == "FM" || s == "NFM") return 0x05;
    if (s == "WFM") return 0x06;
    return -1;
}

QString civModeName(int code) {
    switch (code) {
        case 0x00: return "LSB";
        case 0x01: return "USB";
        case 0x02: return "AM";
        case 0x03: return "CW";
        case 0x05: return "FM";
        case 0x06: return "WFM";
        default: return {};
    }
}

// Kenwood TS-2000-style mode characters.
QChar kwModeCode(const QString& m) {
    const QString s = m.toUpper();
    if (s == "LSB") return QLatin1Char('0');
    if (s == "USB") return QLatin1Char('1');
    if (s == "FM" || s == "NFM" || s == "WFM") return QLatin1Char('2');
    if (s == "AM")  return QLatin1Char('3');
    if (s == "CW" || s == "CWR") return QLatin1Char('4');
    return QLatin1Char('\0');
}

QString kwModeName(QChar c) {
    switch (c.toLatin1()) {
        case '0': return "LSB";
        case '1': return "USB";
        case '2': return "FM";
        case '3': return "AM";
        case '4': return "CW";
        default: return {};
    }
}

} // namespace

CatClient::CatClient(IRadioLink& link, Protocol protocol)
    : link_(link), proto_(protocol) {}

void CatClient::setCivAddresses(unsigned char transceiver,
                                unsigned char controller) {
    civTo_ = transceiver;
    civFrom_ = controller;
}

bool CatClient::open() {
    if (!link_.open()) { err_ = link_.errorString(); return false; }
    return true;
}

bool CatClient::isOpen() const { return link_.isOpen(); }

QByteArray CatClient::transact(const QByteArray& request, char terminator) {
    if (!link_.isOpen()) { err_ = QStringLiteral("link not open"); return {}; }
    link_.write(request);
    QByteArray resp;
    for (int attempt = 0; attempt < 20; ++attempt) {
        resp.append(link_.read(64, 50));
        if (resp.contains(terminator)) break;
    }
    return resp;
}

bool CatClient::setFrequencyHz(double hz) {
    if (hz <= 0) { err_ = QStringLiteral("invalid frequency"); return false; }
    if (proto_ == Protocol::CIV) {
        unsigned char b[5];
        civEncodeFreq(hz, b);
        QByteArray req = civFrame(civTo_, civFrom_,
            {0x05, b[0], b[1], b[2], b[3], b[4]});
        QByteArray r = transact(req, '\xFD');
        if (r.isEmpty() || r.contains('\xFA')) {
            err_ = QStringLiteral("radio rejected frequency"); return false;
        }
        return true;
    }
    const QString cmd = QStringLiteral("FA%1;")
        .arg(static_cast<long long>(std::llround(hz)), 11, 10, QLatin1Char('0'));
    QByteArray r = transact(cmd.toLatin1(), ';');
    if (r.isEmpty()) { err_ = QStringLiteral("no response"); return false; }
    return true;
}

bool CatClient::getFrequencyHz(double& out) {
    if (proto_ == Protocol::CIV) {
        QByteArray r = transact(civFrame(civTo_, civFrom_, {0x03}), '\xFD');
        const int idx = r.indexOf(static_cast<char>(0x03));
        if (idx < 0 || r.contains('\xFA') || idx + 6 > r.size()) {
            err_ = QStringLiteral("bad frequency response"); return false;
        }
        unsigned char b[5];
        for (int i = 0; i < 5; ++i) b[i] = (unsigned char)r[idx + 1 + i];
        out = civDecodeFreq(b);
        return true;
    }
    QByteArray r = transact(QByteArray("FA;"), ';');
    const QString s = QString::fromLatin1(r);
    const int i = s.indexOf("FA");
    QString digits;
    for (int k = i + 2; k < s.size() && s[k].isDigit(); ++k) digits += s[k];
    if (digits.isEmpty()) { err_ = QStringLiteral("bad frequency response"); return false; }
    out = digits.toDouble();
    return true;
}

bool CatClient::setMode(const QString& mode) {
    if (proto_ == Protocol::CIV) {
        const int code = civModeCode(mode);
        if (code < 0) { err_ = QStringLiteral("unsupported mode"); return false; }
        QByteArray req = civFrame(civTo_, civFrom_,
            {0x06, (unsigned char)code, 0x00});
        QByteArray r = transact(req, '\xFD');
        if (r.isEmpty() || r.contains('\xFA')) {
            err_ = QStringLiteral("radio rejected mode"); return false;
        }
        return true;
    }
    const QChar c = kwModeCode(mode);
    if (c == QLatin1Char('\0')) { err_ = QStringLiteral("unsupported mode"); return false; }
    QByteArray r = transact(("MD" + QString(c) + ";").toLatin1(), ';');
    return !r.isEmpty();
}

bool CatClient::getMode(QString& out) {
    if (proto_ == Protocol::CIV) {
        QByteArray r = transact(civFrame(civTo_, civFrom_, {0x04}), '\xFD');
        const int idx = r.indexOf(static_cast<char>(0x04));
        if (idx < 0 || idx + 2 > r.size()) {
            err_ = QStringLiteral("bad mode response"); return false;
        }
        out = civModeName((unsigned char)r[idx + 1]);
        return !out.isEmpty();
    }
    QByteArray r = transact(QByteArray("MD;"), ';');
    const QString s = QString::fromLatin1(r);
    const int i = s.indexOf("MD");
    if (i < 0 || i + 1 >= s.size()) { err_ = QStringLiteral("bad mode response"); return false; }
    out = kwModeName(s[i + 1]);
    return !out.isEmpty();
}

bool CatClient::setPtt(bool on) {
    if (proto_ == Protocol::CIV) {
        QByteArray req = civFrame(civTo_, civFrom_,
            {0x1C, 0x00, (unsigned char)(on ? 0x01 : 0x00)});
        QByteArray r = transact(req, '\xFD');
        if (r.isEmpty() || r.contains('\xFA')) {
            err_ = QStringLiteral("radio rejected PTT"); return false;
        }
        return true;
    }
    QByteArray r = transact(QByteArray(on ? "TX1;" : "TX0;"), ';');
    return !r.isEmpty();
}

} // namespace radio
} // namespace mbdsdr
