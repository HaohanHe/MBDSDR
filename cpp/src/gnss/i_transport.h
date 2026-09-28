// SPDX-License-Identifier: MIT
// Pluggable transport abstraction for raw NMEA byte streams.
//
// Minimal contract: open() / readLine() / close(), plus isOpen() and
// errorString(). readLine() BLOCKS until a '\n' terminated line is available;
// it returns false on EOF, device error, or a clean close() from another
// thread. Implementations must never crash on a missing / unreadable device.
//
// No Qt SerialPort anywhere in this subsystem.
#pragma once

#include <QByteArray>
#include <QString>

#include <memory>

namespace mbdsdr {
namespace gnss {

class ITransport {
public:
    virtual ~ITransport() = default;

    // Open the device/stream. Returns true on success; on failure returns
    // false and errorString() carries a human-readable reason (never throws).
    virtual bool open() = 0;

    // Release the underlying resource. Idempotent. A blocked readLine() must
    // unblock and return false shortly after close().
    virtual void close() = 0;

    virtual bool isOpen() const = 0;

    // Last error message; empty when no error. Valid after open() failure or
    // a readLine() that returned false.
    virtual QString errorString() const = 0;

    // Blocking read of one '\n'-terminated line. The terminator is stripped
    // from lineOut (any trailing '\r' is also removed). Returns false when no
    // more data can be produced (EOF / closed / device error).
    virtual bool readLine(QByteArray& lineOut) = 0;
};

// ---- Factories ---------------------------------------------------------
// Serial (termios) backend. baud in {9600, 38400, 115200}; other values fall
// back to 9600. On non-Unix builds open() honestly reports "unsupported".
std::unique_ptr<ITransport> createSerialTransport(const QString& device, int baud);

// File backend: reads an NMEA log file line by line (EOF => readLine false).
std::unique_ptr<ITransport> createFileTransport(const QString& path);

// In-memory backend: feeds a whole recorded buffer (tests / replay).
std::unique_ptr<ITransport> createMemoryTransport(const QByteArray& data);

} // namespace gnss
} // namespace mbdsdr
