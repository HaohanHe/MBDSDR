// SPDX-License-Identifier: MIT
// Raw bidirectional byte link for CAT radio control. Serial backend uses
// termios (no Qt SerialPort); an in-memory link lets tests script responses.
#pragma once

#include <QByteArray>
#include <QString>
#include <memory>

namespace mbdsdr {
namespace radio {

class IRadioLink {
public:
    virtual ~IRadioLink() = default;
    virtual bool open() = 0;
    virtual void close() = 0;
    virtual bool isOpen() const = 0;
    virtual int write(const QByteArray& bytes) = 0;
    // Read whatever is available, waiting up to timeoutMs (0 = non-blocking).
    virtual QByteArray read(int maxBytes, int timeoutMs) = 0;
    // Toggle a hardware key line (RTS) for CW straight-keying. Default no-op on
    // links that cannot control modem lines.
    virtual void setKeyLine(bool /*down*/) {}
    virtual QString errorString() const = 0;
};

// Serial (termios) backend. Common CAT baud: 9600 / 19200 / 57600 / 115200.
std::unique_ptr<IRadioLink> createSerialRadioLink(const QString& device, int baud);

// In-memory link for tests: records everything written and serves bytes that
// tests enqueue as canned responses.
class MemoryRadioLink : public IRadioLink {
public:
    bool open() override { open_ = true; return true; }
    void close() override { open_ = false; }
    bool isOpen() const override { return open_; }
    int write(const QByteArray& b) override { written_.append(b); return b.size(); }
    QByteArray read(int maxBytes, int /*timeoutMs*/) override {
        int n = qMin(maxBytes, incoming_.size());
        QByteArray out = incoming_.left(n);
        incoming_.remove(0, n);
        return out;
    }
    QString errorString() const override { return err_; }

    void enqueue(const QByteArray& b) { incoming_.append(b); }
    QByteArray takeWritten() { QByteArray w = written_; written_.clear(); return w; }
    const QByteArray& written() const { return written_; }

private:
    bool open_ = false;
    QByteArray incoming_, written_;
    QString err_;
};

} // namespace radio
} // namespace mbdsdr
