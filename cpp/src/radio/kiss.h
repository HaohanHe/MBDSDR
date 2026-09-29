// SPDX-License-Identifier: MIT
// KISS TNC framing (the serial byte protocol that carries AX.25 frames to a
// TNC). References: KISS spec (KISS protocol constants). Pure logic.
#pragma once

#include <QByteArray>
#include <vector>

namespace mbdsdr {
namespace radio {

constexpr unsigned char KISS_FEND = 0xC0;
constexpr unsigned char KISS_FESC = 0xDB;
constexpr unsigned char KISS_TFEND = 0xDC;
constexpr unsigned char KISS_TFESC = 0xDD;

// Wrap an AX.25 payload in a KISS data frame for the given port (0-15):
// FEND <command> <escaped payload> FEND.
QByteArray kissWrap(const QByteArray& ax25Payload, int port = 0);

// Streaming KISS decoder: feed arbitrary serial chunks; complete AX.25 payloads
// are returned by takeFrames(). Robust to repeated FENDs and split frames.
class KissDecoder {
public:
    void feed(const QByteArray& bytes);
    std::vector<QByteArray> takeFrames();

private:
    QByteArray rx_;
    bool inFrame_ = false;
    bool haveCmd_ = false;
    bool escape_ = false;
    std::vector<QByteArray> frames_;
};

// One-shot decode of a complete KISS byte stream (handy for tests).
std::vector<QByteArray> kissDecode(const QByteArray& stream);

} // namespace radio
} // namespace mbdsdr
