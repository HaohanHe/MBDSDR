// SPDX-License-Identifier: GPL-3.0-or-later
// AX.25 frame encode/decode (UI frames, the subset used for APRS/beacons over
// a KISS TNC). References: AX.25 Link-Layer spec v2.2 (address field, SSID
// byte, control 0x03 UI, PID 0xF0). Pure logic, no hardware.
#pragma once

#include <QByteArray>
#include <QString>
#include <vector>

namespace mbdsdr {
namespace radio {

struct Ax25Addr {
    QString call;   // up to 6 alphanumerics, no trailing '-SSID'
    int ssid = 0;

    QString toString() const {
        return ssid ? (call + "-" + QString::number(ssid)) : call;
    }
};

struct Ax25Frame {
    Ax25Addr dst;
    Ax25Addr src;
    std::vector<Ax25Addr> path;   // digipeaters, may be empty
    unsigned char control = 0x03; // UI
    unsigned char pid = 0xF0;     // no layer-3
    QByteArray info;              // payload (e.g. APRS text)
};

// Parse "CALL-SSID" (SSID optional, defaults to 0). Returns false if the
// callsign is empty or longer than 6 characters.
bool parseAx25Addr(const QString& text, Ax25Addr& out);

// Serialize an AX.25 frame to raw bytes (no KISS delimiters). Returns an empty
// array on invalid addresses.
QByteArray encodeAx25(const Ax25Frame& frame);

// Parse a raw AX.25 frame (KISS payload). Returns false on truncation or an
// address field that never terminates.
bool decodeAx25(const QByteArray& raw, Ax25Frame& out);

} // namespace radio
} // namespace mbdsdr
