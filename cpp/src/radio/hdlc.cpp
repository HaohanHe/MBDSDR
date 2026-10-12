// SPDX-License-Identifier: MIT
#include "hdlc.h"

#include <QVector>

namespace mbdsdr {
namespace radio {

namespace {

// CRC-16/X.25: poly 0x1021, init 0xFFFF, reflected (LSB-first), XOR-out 0xFFFF.
quint16 crc16x25Step(quint16 crc, quint8 byte) {
    crc ^= static_cast<quint16>(byte);
    for (int i = 0; i < 8; ++i) {
        if (crc & 0x0001u)
            crc = static_cast<quint16>((crc >> 1) ^ 0x8408u);   // reflected poly
        else
            crc = static_cast<quint16>(crc >> 1);
    }
    return crc;
}

quint16 crc16x25(const QByteArray& data) {
    quint16 crc = 0xFFFFu;
    for (char c : data)
        crc = crc16x25Step(crc, static_cast<quint8>(c));
    return static_cast<quint16>(crc ^ 0xFFFFu);
}

} // namespace

QByteArray nrziDecode(const QByteArray& bits) {
    QByteArray out;
    out.reserve(bits.size());
    bool prev = false;   // reference: an implicit leading low level
    for (char ch : bits) {
        const bool cur = (ch != 0);
        out.append(prev == cur ? '\x01' : '\x00');   // no transition = 1
        prev = cur;
    }
    return out;
}

QByteArray crcX25(const QByteArray& data) {
    const quint16 crc = crc16x25(data);
    // AX.25 transmits the FCS low byte first.
    return QByteArray(1, static_cast<char>(crc & 0xFFu))
         + QByteArray(1, static_cast<char>((crc >> 8) & 0xFFu));
}

QList<QByteArray> hdlcDeframe(const QByteArray& nrziBits) {
    QList<QByteArray> frames;

    // Frame scanner state.
    const int n = nrziBits.size();
    bool inFrame = false;
    int runOnes = 0;            // consecutive 1s since last zero
    QByteArray payload;         // de-stuffed bytes being assembled
    int bitBuf = 0;             // shift register for byte assembly (LSB-first)
    int bitCount = 0;

    auto flushByte = [&]() {
        if (bitCount > 0) {
            payload.append(static_cast<char>(bitBuf));
            bitBuf = 0;
            bitCount = 0;
        }
    };

    auto closeFrame = [&]() {
        // Discard any partial byte assembled from flag preamble bits
        // (the flag's leading zero plus its first ones are not data).
        bitBuf = 0;
        bitCount = 0;
        if (payload.size() >= 2) {
            const QByteArray body = payload.left(payload.size() - 2);
            const QByteArray fcs  = payload.right(2);
            const quint16 expect = static_cast<quint16>(
                (static_cast<quint8>(fcs[0])) | (static_cast<quint16>(static_cast<quint8>(fcs[1])) << 8));
            if (crc16x25(body) == expect)
                frames.append(body);
        }
        payload.clear();
        inFrame = false;
        bitBuf = 0;
        bitCount = 0;
    };

    for (int i = 0; i < n; ++i) {
        const bool bit = (nrziBits[i] != 0);

        if (!inFrame) {
            // Flag hunt: 0x7E = 01111110 -- six ones framed by zeros.
            // Track the last 8 bits in an LSB-first shift register.
            bitBuf = ((bitBuf >> 1) | (bit ? 0x80 : 0)) & 0xFF;
            if (bitBuf == 0x7E) {
                inFrame = true;
                payload.clear();
                bitBuf = 0;
                bitCount = 0;
                runOnes = 0;
            }
            continue;
        }

        if (bit) {
            ++runOnes;
            if (runOnes >= 6) {
                // Closing (or abort) flag reached inside a frame.
                if (runOnes == 6 && !inFrame) { /* unreachable */ }
                // Six consecutive ones = flag 0x7E if surrounded by zeros,
                // otherwise an abort. We only accept a clean close: exactly 6
                // ones (the previous bit was a zero and the next will be too).
                if (runOnes == 6) {
                    // The next bit decides: zero -> real flag, one -> abort.
                    const bool nextIsOne = (i + 1 < n) && (nrziBits[i + 1] != 0);
                    if (!nextIsOne) {
                        closeFrame();
                        // Consume the closing zero by letting the loop advance.
                    } else {
                        // Abort: discard partial frame.
                        payload.clear();
                        inFrame = false;
                        bitBuf = 0;
                        bitCount = 0;
                    }
                }
                continue;
            }
            // Accumulate a data bit (still inside frame, run < 6).
            bitBuf |= (bit ? (1 << bitCount) : 0);
            ++bitCount;
            if (bitCount == 8) flushByte();
            continue;
        }

        // Zero bit.
        if (runOnes >= 5) {
            // This zero is the stuffed zero after five ones: drop it.
            runOnes = 0;
            continue;
        }
        // Ordinary zero data bit.
        bitBuf |= (bit ? (1 << bitCount) : 0);
        ++bitCount;
        if (bitCount == 8) flushByte();
        runOnes = 0;
    }

    return frames;
}

QByteArray hdlcEncode(const QByteArray& framePayload) {
    const QByteArray body = framePayload + crcX25(framePayload);

    // Serialize LSB-first to a bit stream with zero-deletion after five 1s.
    QByteArray stream;
    int runOnes = 0;
    for (char ch : body) {
        quint8 v = static_cast<quint8>(ch);
        for (int i = 0; i < 8; ++i) {
            const bool bit = (v & (1u << i)) != 0;
            stream.append(bit ? '\x01' : '\x00');
            if (bit) {
                ++runOnes;
                if (runOnes == 5) {
                    stream.append('\x00');   // stuff a zero
                    runOnes = 0;
                }
            } else {
                runOnes = 0;
            }
        }
    }

    // NRZI encode: 1 = no transition, 0 = transition.
    QByteArray nrz;
    bool level = false;
    auto emitBit = [&](bool b) {
        if (!b) level = !level;
        nrz.append(level ? '\x01' : '\x00');
    };

    QByteArray flag(8, '\x00');
    flag[0] = 0; flag[1] = 1; flag[2] = 1; flag[3] = 1;
    flag[4] = 1; flag[5] = 1; flag[6] = 1; flag[7] = 0;   // 01111110
    for (char ch : flag) emitBit(ch != 0);
    for (char ch : stream) emitBit(ch != 0);
    for (char ch : flag) emitBit(ch != 0);
    return nrz;
}

} // namespace radio
} // namespace mbdsdr
