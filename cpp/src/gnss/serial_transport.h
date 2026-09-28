// SPDX-License-Identifier: MIT
// Raw termios serial backend for NMEA devices (Linux).
//
// Designed to fail soft: a missing device / bad permissions yields open()==false
// and a non-empty errorString(); it never crashes and never blocks forever.
// On non-Unix platforms the whole termios body is replaced by a stub whose
// open() returns "not supported on this platform" so the code still compiles
// (and runs) on Windows.
#pragma once

#include "i_transport.h"

#include <QString>

#include <mutex>

namespace mbdsdr {
namespace gnss {

class SerialTransport : public ITransport {
public:
    // baud accepted: 9600, 38400, 115200 (others -> 9600).
    SerialTransport(QString device, int baud);
    ~SerialTransport() override;

    bool open() override;
    void close() override;
    bool isOpen() const override;
    QString errorString() const override;
    bool readLine(QByteArray& lineOut) override;

private:
    QString m_device;
    int m_baud;
    QString m_error;

#if defined(__unix__)
    int m_fd = -1;
    QByteArray m_buf;   // partial-line accumulator
    std::mutex mtx;     // guards m_fd / m_buf across close()-vs-readLine()
#endif
};

} // namespace gnss
} // namespace mbdsdr
