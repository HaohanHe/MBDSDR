// SPDX-License-Identifier: MIT
// In-memory and file-stream transports (tests / replay / off-line parsing).
//
// MemoryTransport: a whole recorded buffer is handed in at construction;
// readLine() returns one line at a time (like a serial port), then false at
// end. FileTransport: same semantics over a regular NMEA log file.
//
// 录制样例·非硬件 NOT HARDWARE: these backends never touch physical hardware.
#pragma once

#include "i_transport.h"

#include <QByteArray>
#include <QFile>
#include <QString>

namespace mbdsdr {
namespace gnss {

// Replay a fully-provided byte buffer. Non-blocking; readLine never blocks.
class MemoryTransport : public ITransport {
public:
    explicit MemoryTransport(QByteArray data);

    bool open() override;
    void close() override;
    bool isOpen() const override;
    QString errorString() const override;
    bool readLine(QByteArray& lineOut) override;

    // Test helper: append more bytes after construction (simulates a live
    // stream arriving in chunks). readLine() picks them up on the next call.
    void appendData(const QByteArray& more);

private:
    QByteArray m_data;
    qint64 m_pos = 0;
    bool m_open = false;
    QString m_error;
};

// Read an NMEA log file line by line.
class FileTransport : public ITransport {
public:
    explicit FileTransport(QString path);
    ~FileTransport() override;

    bool open() override;
    void close() override;
    bool isOpen() const override;
    QString errorString() const override;
    bool readLine(QByteArray& lineOut) override;

private:
    QString m_path;
    QFile m_file;
    QString m_error;
};

} // namespace gnss
} // namespace mbdsdr
