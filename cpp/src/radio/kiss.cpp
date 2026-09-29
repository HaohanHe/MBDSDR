// SPDX-License-Identifier: MIT
#include "kiss.h"

namespace mbdsdr {
namespace radio {

QByteArray kissWrap(const QByteArray& ax25Payload, int port) {
    QByteArray out;
    out.append(static_cast<char>(KISS_FEND));
    out.append(static_cast<char>((port & 0x0F) << 4));  // data command
    for (unsigned char b : ax25Payload) {
        if (b == KISS_FEND) {
            out.append(static_cast<char>(KISS_FESC));
            out.append(static_cast<char>(KISS_TFEND));
        } else if (b == KISS_FESC) {
            out.append(static_cast<char>(KISS_FESC));
            out.append(static_cast<char>(KISS_TFESC));
        } else {
            out.append(static_cast<char>(b));
        }
    }
    out.append(static_cast<char>(KISS_FEND));
    return out;
}

void KissDecoder::feed(const QByteArray& bytes) {
    for (unsigned char b : bytes) {
        if (b == KISS_FEND) {
            if (inFrame_ && haveCmd_) frames_.push_back(rx_);
            inFrame_ = true;
            haveCmd_ = false;
            rx_.clear();
            escape_ = false;
        } else if (!inFrame_) {
            // Ignore noise before the first frame delimiter.
        } else if (escape_) {
            if (b == KISS_TFEND) rx_.append(static_cast<char>(KISS_FEND));
            else if (b == KISS_TFESC) rx_.append(static_cast<char>(KISS_FESC));
            else rx_.append(static_cast<char>(b));  // unspecified transpose
            escape_ = false;
        } else if (b == KISS_FESC) {
            escape_ = true;
        } else if (!haveCmd_) {
            haveCmd_ = true;  // command/port byte; data follows
        } else {
            rx_.append(static_cast<char>(b));
        }
    }
}

std::vector<QByteArray> KissDecoder::takeFrames() {
    std::vector<QByteArray> out;
    out.swap(frames_);
    return out;
}

std::vector<QByteArray> kissDecode(const QByteArray& stream) {
    KissDecoder dec;
    dec.feed(stream);
    return dec.takeFrames();
}

} // namespace radio
} // namespace mbdsdr
