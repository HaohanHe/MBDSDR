// SPDX-License-Identifier: MIT
#include "radio_link.h"

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#elif defined(__unix__)
#  include <fcntl.h>
#  include <sys/ioctl.h>
#  include <termios.h>
#  include <unistd.h>
#endif

namespace mbdsdr {
namespace radio {

#ifdef _WIN32
namespace {

// Win32 serial link (CAT / CW / AX.25 over a COM port). Uses the documented
// Win32 comm API: CreateFileW for the device, DCB for baud/framing,
// COMMTIMEOUTS for bounded reads, EscapeCommFunction for the RTS key line.
class WinSerialRadioLink : public IRadioLink {
public:
    WinSerialRadioLink(const QString& device, int baud)
        : device_(device), baud_(baud) {}

    ~WinSerialRadioLink() override { close(); }

    bool open() override {
        // COM ports above COM9 require the "\\.\" device prefix; apply it
        // unconditionally (harmless for COM1..COM9).
        QString path = QStringLiteral("\\\\.\\") + device_.trimmed();
        h_ = ::CreateFileW(reinterpret_cast<LPCWSTR>(path.utf16()),
                           GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                           OPEN_EXISTING, 0, nullptr);
        if (h_ == INVALID_HANDLE_VALUE) {
            err_ = QStringLiteral("cannot open serial device: %1")
                       .arg(winErrText(::GetLastError()));
            return false;
        }
        DCB dcb{};
        dcb.DCBlength = sizeof(DCB);
        if (!::GetCommState(h_, &dcb)) {
            err_ = QStringLiteral("GetCommState failed");
            close();
            return false;
        }
        dcb.BaudRate = static_cast<DWORD>(baud_);
        dcb.fBinary = TRUE;
        dcb.fParity = FALSE;
        dcb.fNull = FALSE;
        dcb.fErrorChar = FALSE;
        dcb.fAbortOnError = FALSE;
        dcb.ByteSize = 8;
        dcb.Parity = NOPARITY;
        dcb.StopBits = ONESTOPBIT;
        dcb.fOutxCtsFlow = FALSE;
        dcb.fOutxDsrFlow = FALSE;
        dcb.fDtrControl = DTR_CONTROL_DISABLE;
        dcb.fRtsControl = RTS_CONTROL_DISABLE;   // keyed manually below
        dcb.fOutX = FALSE;
        dcb.fInX = FALSE;
        if (!::SetCommState(h_, &dcb)) {
            err_ = QStringLiteral("SetCommState failed: %1")
                       .arg(winErrText(::GetLastError()));
            close();
            return false;
        }
        ::PurgeComm(h_, PURGE_RXCLEAR | PURGE_TXCLEAR);
        return true;
    }

    void close() override {
        if (h_ != INVALID_HANDLE_VALUE) {
            ::CloseHandle(h_);
            h_ = INVALID_HANDLE_VALUE;
        }
    }

    bool isOpen() const override { return h_ != INVALID_HANDLE_VALUE; }

    int write(const QByteArray& b) override {
        if (h_ == INVALID_HANDLE_VALUE || b.isEmpty()) return 0;
        DWORD n = 0;
        if (!::WriteFile(h_, b.constData(), static_cast<DWORD>(b.size()), &n,
                         nullptr)) {
            err_ = QStringLiteral("WriteFile failed: %1")
                       .arg(winErrText(::GetLastError()));
            return 0;
        }
        return static_cast<int>(n);
    }

    QByteArray read(int maxBytes, int timeoutMs) override {
        if (h_ == INVALID_HANDLE_VALUE || maxBytes <= 0) return {};
        // The MAXDWORD/MAXDWORD/timeout combination returns as soon as any
        // bytes are available or after timeoutMs, whichever is first.
        COMMTIMEOUTS to{};
        to.ReadIntervalTimeout = MAXDWORD;
        to.ReadTotalTimeoutMultiplier = MAXDWORD;
        to.ReadTotalTimeoutConstant =
            static_cast<DWORD>(qMax(0, timeoutMs));
        to.WriteTotalTimeoutMultiplier = 0;
        to.WriteTotalTimeoutConstant = 0;
        ::SetCommTimeouts(h_, &to);

        QByteArray out;
        out.resize(maxBytes);
        DWORD total = 0;
        if (!::ReadFile(h_, out.data(), static_cast<DWORD>(maxBytes), &total,
                        nullptr)) {
            err_ = QStringLiteral("ReadFile failed: %1")
                       .arg(winErrText(::GetLastError()));
            return {};
        }
        out.resize(static_cast<int>(total));
        return out;
    }

    void setKeyLine(bool down) override {
        if (h_ == INVALID_HANDLE_VALUE) return;
        ::EscapeCommFunction(h_, down ? SETRTS : CLRRTS);
    }

    QString errorString() const override { return err_; }

private:
    static QString winErrText(DWORD code) {
        LPWSTR buf = nullptr;
        const DWORD n = ::FormatMessageW(
            FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                FORMAT_MESSAGE_IGNORE_INSERTS,
            nullptr, code, MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
            reinterpret_cast<LPWSTR>(&buf), 0, nullptr);
        QString out;
        if (n && buf) {
            out = QString::fromWCharArray(buf, static_cast<int>(n)).trimmed();
            ::LocalFree(buf);
        }
        if (out.isEmpty()) out = QStringLiteral("error %1").arg(code);
        return out;
    }

    QString device_;
    int baud_;
    HANDLE h_ = INVALID_HANDLE_VALUE;
    QString err_;
};

} // namespace
#elif defined(__unix__)
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
#ifdef _WIN32
    return std::make_unique<WinSerialRadioLink>(device, baud);
#elif defined(__unix__)
    return std::make_unique<SerialRadioLink>(device, baud);
#else
    (void)device; (void)baud;
    return nullptr;
#endif
}

} // namespace radio
} // namespace mbdsdr
