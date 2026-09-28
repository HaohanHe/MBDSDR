// SPDX-License-Identifier: MIT
// GNSS receiver thread: reads raw NMEA lines from an ITransport, parses them,
// and publishes the merged fix as a Qt signal.
//
// No Qt SerialPort: the transport is injected (serial/file/memory). The thread
// stops cleanly: requestInterruption() + transport->close() unblock readLine().
#pragma once

#include "gnss_types.h"
#include "i_transport.h"

#include <QMutex>
#include <QThread>

#include <memory>

namespace mbdsdr {
namespace gnss {

class NmeaParser;

class GnssReceiver : public QThread {
    Q_OBJECT
public:
    explicit GnssReceiver(QObject* parent = nullptr);
    ~GnssReceiver() override;

    // Configure a serial device path + baud (used when no transport is set).
    void setDevice(const QString& path, int baud);

    // Inject an explicit transport (tests / replay). Takes ownership.
    void setTransport(std::unique_ptr<ITransport> transport);

    GnssFix lastFix() const;
    bool connected() const;

    // Stop the thread cleanly (idempotent).
    void stop();

signals:
    // Emitted after each accepted sentence; carries the latest merged fix.
    void newFix(mbdsdr::gnss::GnssFix fix);
    // Emitted on connect / disconnect transitions.
    void connectionChanged(bool connected, QString description);

protected:
    void run() override;

private:
    std::unique_ptr<ITransport> m_transport;
    QString m_device;
    int m_baud = 9600;

    mutable QMutex m_mutex;
    GnssFix m_lastFix;
    bool m_connected = false;
};

} // namespace gnss
} // namespace mbdsdr

Q_DECLARE_METATYPE(mbdsdr::gnss::GnssFix)
