// SPDX-License-Identifier: MIT
#include "stream_transport.h"

#include "serial_transport.h"

#include <QIODevice>

namespace mbdsdr {
namespace gnss {

// ---- MemoryTransport ----------------------------------------------------

MemoryTransport::MemoryTransport(QByteArray data)
    : m_data(std::move(data)) {}

bool MemoryTransport::open() {
    m_error.clear();
    m_pos = 0;
    m_open = true;
    return true;
}

void MemoryTransport::close() { m_open = false; }

bool MemoryTransport::isOpen() const { return m_open; }

QString MemoryTransport::errorString() const { return m_error; }

void MemoryTransport::appendData(const QByteArray& more) { m_data.append(more); }

bool MemoryTransport::readLine(QByteArray& lineOut) {
    lineOut.clear();
    if (!m_open) { m_error = QStringLiteral("not open"); return false; }

    while (m_pos < m_data.size()) {
        int nl = m_data.indexOf('\n', static_cast<int>(m_pos));
        if (nl < 0) {
            // No newline yet: if the consumer later appends data we want that
            // data included; return false only when truly drained. To mirror a
            // live device we treat "no newline in current buffer" as end of
            // available lines (non-blocking).
            if (m_pos < m_data.size()) {
                QByteArray rest = m_data.mid(static_cast<int>(m_pos));
                m_pos = m_data.size();
                while (!rest.isEmpty() && rest.endsWith('\r')) rest.chop(1);
                lineOut = rest;
                return true;
            }
            return false;
        }
        QByteArray line = m_data.mid(static_cast<int>(m_pos), nl - static_cast<int>(m_pos));
        m_pos = nl + 1;
        while (!line.isEmpty() && line.endsWith('\r')) line.chop(1);
        lineOut = line;
        return true;
    }
    return false; // drained
}

// ---- FileTransport ------------------------------------------------------

FileTransport::FileTransport(QString path) : m_path(std::move(path)) {}

FileTransport::~FileTransport() { close(); }

bool FileTransport::open() {
    m_error.clear();
    m_file.setFileName(m_path);
    if (!m_file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        m_error = QStringLiteral("open('%1') failed: %2").arg(m_path, m_file.errorString());
        return false;
    }
    return true;
}

void FileTransport::close() { m_file.close(); }

bool FileTransport::isOpen() const { return m_file.isOpen(); }

QString FileTransport::errorString() const { return m_error; }

bool FileTransport::readLine(QByteArray& lineOut) {
    lineOut = m_file.readLine();
    if (lineOut.isEmpty()) return false; // EOF / error
    while (!lineOut.isEmpty() && (lineOut.endsWith('\n') || lineOut.endsWith('\r')))
        lineOut.chop(1);
    return true;
}

// ---- Factories ----------------------------------------------------------

std::unique_ptr<ITransport> createSerialTransport(const QString& device, int baud) {
    return std::unique_ptr<ITransport>(new SerialTransport(device, baud));
}

std::unique_ptr<ITransport> createFileTransport(const QString& path) {
    return std::unique_ptr<ITransport>(new FileTransport(path));
}

std::unique_ptr<ITransport> createMemoryTransport(const QByteArray& data) {
    return std::unique_ptr<ITransport>(new MemoryTransport(data));
}

} // namespace gnss
} // namespace mbdsdr
