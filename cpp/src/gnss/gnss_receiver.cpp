// SPDX-License-Identifier: MIT
#include "gnss_receiver.h"

#include "nmea_parser.h"

#include <QMutexLocker>

namespace mbdsdr {
namespace gnss {

GnssReceiver::GnssReceiver(QObject* parent) : QThread(parent) {}

GnssReceiver::~GnssReceiver() { stop(); }

void GnssReceiver::setDevice(const QString& path, int baud) {
    QMutexLocker lk(&m_mutex);
    m_device = path;
    m_baud = baud;
}

void GnssReceiver::setTransport(std::unique_ptr<ITransport> transport) {
    QMutexLocker lk(&m_mutex);
    m_transport = std::move(transport);
}

GnssFix GnssReceiver::lastFix() const {
    QMutexLocker lk(&m_mutex);
    return m_lastFix;
}

bool GnssReceiver::connected() const {
    QMutexLocker lk(&m_mutex);
    return m_connected;
}

void GnssReceiver::stop() {
    if (isRunning()) {
        requestInterruption();
        QMutexLocker lk(&m_mutex);
        if (m_transport) m_transport->close(); // unblock readLine()
        lk.unlock();
        wait(3000);
    }
}

void GnssReceiver::run() {
    // Materialise the transport if the caller used setDevice() only.
    {
        QMutexLocker lk(&m_mutex);
        if (!m_transport) m_transport = createSerialTransport(m_device, m_baud);
    }

    ITransport* t = nullptr;
    {
        QMutexLocker lk(&m_mutex);
        t = m_transport.get();
    }
    if (!t) {
        emit connectionChanged(false, QStringLiteral("no transport"));
        return;
    }

    if (!t->open()) {
        const QString err = t->errorString();
        {
            QMutexLocker lk(&m_mutex);
            m_connected = false;
        }
        emit connectionChanged(false, err);
        return;
    }

    {
        QMutexLocker lk(&m_mutex);
        m_connected = true;
    }
    emit connectionChanged(true, m_device.isEmpty() ? QStringLiteral("transport") : m_device);

    NmeaParser parser;
    QByteArray line;
    while (!isInterruptionRequested()) {
        line.clear();
        if (!t->readLine(line)) break; // EOF / error / close()
        if (parser.feed(line + '\n') > 0) {
            QMutexLocker lk(&m_mutex);
            m_lastFix = parser.fix();
            lk.unlock();
            emit newFix(parser.fix());
        }
    }

    t->close();
    {
        QMutexLocker lk(&m_mutex);
        m_connected = false;
    }
    emit connectionChanged(false, t->errorString().isEmpty()
                                     ? QStringLiteral("closed")
                                     : t->errorString());
}

} // namespace gnss
} // namespace mbdsdr

// IMPORTANT: no #include "gnss_receiver.moc" here; the scratch build uses
// AUTOMOC, which generates the moc file automatically.
