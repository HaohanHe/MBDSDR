// SPDX-License-Identifier: MIT
#include "radio_link.h"

#ifdef __unix__
#include <fcntl.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>
#endif

namespace mbdsdr {
namespace radio {

#ifdef __unix__
namespace {

class SerialRadioLink : public IRadioLink {
public:
    SerialRadioLink(const QString& device, int baud)
        : device_(device.toUtf8()), baud_(baud) {}

    ~SerialRadioLink() override { close(); }

    bool open() override {
        fd_ = ::open(device_.constData(), O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (fd_ < 0) {
            err_ = QStringLiteral("cannot open serial device");
            return false;
        }
        termios tio{};
        if (tcgetattr(fd_, &tio) != 0) {
            err_ = QStringLiteral("tcgetattr failed");
            ::close(fd_); fd_ = -1;
            return false;
        }
        speed_t spd = B9600;
        switch (baud_) {
            case 4800: spd = B4800; break;
            case 9600: spd = B9600; break;
            case 19200: spd = B19200; break;
            case 38400: spd = B38400; break;
            case 57600: spd = B57600; break;
            case 115200: spd = B115200; break;
            case 230400: spd = B230400; break;
            default: spd = B9600; break;
        }
        cfsetispeed(&tio, spd);
        cfsetospeed(&tio, spd);
        // Raw 8N1.
        cfmakeraw(&tio);
        tio.c_cflag |= (CLOCAL | CREAD);
        tio.c_cflag &= ~CRTSCTS;
        tio.c_cc[VMIN] = 0;
        tio.c_cc[VTIME] = 1;  // 100 ms granularity for read timeouts
        tcsetattr(fd_, TCSANOW, &tio);
        tcflush(fd_, TCIOFLUSH);
        return true;
    }

    void close() override {
        if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
    }

    bool isOpen() const override { return fd_ >= 0; }

    int write(const QByteArray& b) override {
        if (fd_ < 0) return 0;
        const ssize_t n = ::write(fd_, b.constData(), b.size());
        return n < 0 ? 0 : static_cast<int>(n);
    }

    QByteArray read(int maxBytes, int timeoutMs) override {
        if (fd_ < 0) return {};
        QByteArray out;
        out.resize(maxBytes);
        const int steps = qMax(1, timeoutMs / 100);
        int total = 0;
        for (int s = 0; s < steps && total < maxBytes; ++s) {
            const ssize_t n = ::read(fd_, out.data() + total, maxBytes - total);
            if (n > 0) total += static_cast<int>(n);
            if (total >= maxBytes) break;
        }
        out.resize(total);
        return out;
    }

    void setKeyLine(bool down) override {
        if (fd_ < 0) return;
        int bits = 0;
        if (ioctl(fd_, TIOCMGET, &bits) != 0) return;
        if (down) bits |= TIOCM_RTS;
        else bits &= ~TIOCM_RTS;
        ioctl(fd_, TIOCMSET, &bits);
    }

    QString errorString() const override { return err_; }

private:
    QByteArray device_;
    int baud_;
    int fd_ = -1;
    QString err_;
};

} // namespace
#endif

std::unique_ptr<IRadioLink> createSerialRadioLink(const QString& device, int baud) {
#ifdef __unix__
    return std::make_unique<SerialRadioLink>(device, baud);
#else
    (void)device; (void)baud;
    return nullptr;
#endif
}

} // namespace radio
} // namespace mbdsdr
