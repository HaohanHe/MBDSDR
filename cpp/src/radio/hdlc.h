// SPDX-License-Identifier: MIT
//
// HDLC bit-level receiver for AX.25/APRS over a recovered FSK bit stream
// (independent; AX.25 v2.2 link layer + ISO 3309 HDLC frame structure).
//
// Chain (all deterministic, no hardware):
//   recovered FSK symbols (0/1 per sample-symbol)
//     -> NRZI decode (level transition = 0, no transition = 1)
//     -> flag hunt (0x7E = 01111110) + zero-deletion (5 consecutive 1s
//        followed by a 0: the 0 is removed)
//     -> CRC-16/X.25 (poly 0x1021, init 0xFFFF, reflected, result XOR 0xFFFF)
//        over address..PID; the 2-byte FCS is checked, not returned
//     -> raw AX.25 frame bytes (excluding flags), ready for decodeAx25()
//
// Bits are consumed MSB-first within each QByteArray byte (index 0 = oldest).
// This matches the natural ordering of a recovered symbol queue.
#pragma once

#include <QByteArray>
#include <QList>

namespace mbdsdr {
namespace radio {

// NRZI-decode a recovered symbol stream: transition -> 0, no transition -> 1.
// `bits` is one bit per byte (0/1). Returns decoded bits in the same shape.
QByteArray nrziDecode(const QByteArray& bits);

// Hunt for 0x7E flags, remove bit stuffing, and collect complete frames.
// `nrziBits` is the NRZI-decoded stream (one bit per byte). Each returned
// entry is one frame payload (address..info, excluding flags and FCS) that
// passed the CRC-16/X.25 check. Honest empty list on no valid frame.
QList<QByteArray> hdlcDeframe(const QByteArray& nrziBits);

// CRC-16/X.25 over the given bytes (used both to check received frames and to
// build synthetic frames in tests). Returns the two FCS bytes to append,
// LSB-first byte order (AX.25 convention: FCS sent low byte first).
QByteArray crcX25(const QByteArray& data);

// Encode an AX.25 frame payload into a full HDLC bit stream: flag + NRZI
// encoding + bit stuffing + FCS + closing flag. Used by the test fixture to
// build synthetic over-the-air streams (honest placeholder callsigns only).
QByteArray hdlcEncode(const QByteArray& framePayload);

} // namespace radio
} // namespace mbdsdr
