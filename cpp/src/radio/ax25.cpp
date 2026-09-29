// SPDX-License-Identifier: MIT
#include "ax25.h"

namespace mbdsdr {
namespace radio {

bool parseAx25Addr(const QString& text, Ax25Addr& out) {
    QString t = text.trimmed().toUpper();
    int ssid = 0;
    const int dash = t.indexOf('-');
    if (dash >= 0) {
        bool ok = true;
        ssid = t.mid(dash + 1).toInt(&ok);
        if (!ok || ssid < 0 || ssid > 15) return false;
        t = t.left(dash);
    }
    t = t.trimmed();
    if (t.isEmpty() || t.size() > 6) return false;
    out.call = t;
    out.ssid = ssid;
    return true;
}

static void encodeAddr(QByteArray& out, const Ax25Addr& addr, bool last) {
    QString call = addr.call.toUpper();
    while (call.size() < 6) call += QLatin1Char(' ');
    for (int i = 0; i < 6; ++i)
        out.append(static_cast<char>(call.at(i).toLatin1() << 1));
    unsigned char ssidByte = 0x80 | ((addr.ssid & 0x0F) << 1) | (last ? 0x01 : 0x00);
    out.append(static_cast<char>(ssidByte));
}

QByteArray encodeAx25(const Ax25Frame& frame) {
    if (frame.dst.call.isEmpty() || frame.dst.call.size() > 6 ||
        frame.src.call.isEmpty() || frame.src.call.size() > 6)
        return {};
    for (const auto& p : frame.path)
        if (p.call.isEmpty() || p.call.size() > 6) return {};

    QByteArray out;
    out.reserve(16 + frame.info.size());
    encodeAddr(out, frame.dst, /*last=*/false);
    const int pathCount = static_cast<int>(frame.path.size());
    if (pathCount == 0) {
        encodeAddr(out, frame.src, /*last=*/true);
    } else {
        encodeAddr(out, frame.src, /*last=*/false);
        for (int i = 0; i < pathCount; ++i)
            encodeAddr(out, frame.path[i], /*last=*/i == pathCount - 1);
    }
    out.append(static_cast<char>(frame.control));
    out.append(static_cast<char>(frame.pid));
    out.append(frame.info);
    return out;
}

static Ax25Addr decodeAddr(const QByteArray& block) {
    Ax25Addr a;
    QString call;
    for (int i = 0; i < 6; ++i)
        call += QLatin1Char(static_cast<char>((unsigned char)block[i] >> 1));
    a.call = call.trimmed();
    a.ssid = ((unsigned char)block[6] >> 1) & 0x0F;
    return a;
}

bool decodeAx25(const QByteArray& raw, Ax25Frame& out) {
    if (raw.size() < 16) return false;  // dst + src + control + pid
    int offset = 0;
    bool gotDst = false, gotSrc = false, terminated = false;
    std::vector<Ax25Addr> addrs;
    while (offset + 7 <= raw.size()) {
        QByteArray block = raw.mid(offset, 7);
        const bool last = ((unsigned char)block[6] & 0x01) != 0;
        addrs.push_back(decodeAddr(block));
        offset += 7;
        if (addrs.size() == 1) gotDst = true;
        if (addrs.size() == 2) gotSrc = true;
        if (last) { terminated = true; break; }
        if (addrs.size() > 10) return false;  // malformed: never terminates
    }
    if (!gotDst || !gotSrc || !terminated) return false;
    if (offset + 2 > raw.size()) return false;  // need control + pid

    out = Ax25Frame{};
    out.dst = addrs[0];
    out.src = addrs[1];
    for (std::size_t i = 2; i < addrs.size(); ++i) out.path.push_back(addrs[i]);
    out.control = (unsigned char)raw[offset++];
    out.pid = (unsigned char)raw[offset++];
    out.info = raw.mid(offset);
    return true;
}

} // namespace radio
} // namespace mbdsdr
