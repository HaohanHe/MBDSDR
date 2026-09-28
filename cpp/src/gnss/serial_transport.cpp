// SPDX-License-Identifier: MIT
#include "serial_transport.h"

#include <QByteArray>

#include <cerrno>
#include <cstring>

#if defined(__unix__)
#  include <fcntl.h>
#  include <poll.h>
#  include <unistd.h>
#  include <sys/ioctl.h>
#  include <termios.h>
#  include <mutex>
#endif

namespace mbdsdr {
namespace gnss {

SerialTransport::SerialTransport(QString device, int baud)
    : m_device(std::move(device)), m_baud(baud) {}

SerialTransport::~SerialTransport() { close(); }

#if defined(__unix__)

namespace {
speed_t baudToConstant(int baud) {
    switch (baud) {
    case 9600:   return B9600;
    case 38400:  return B38400;
    case 115200: return B115200;
    default:     return B9600;
    }
}
QString errnoToString(int e) {
    switch (e) {
    case ENOENT:  return QStringLiteral("No such device: %1").arg(QString::fromLocal8Bit(strerror(e)));
    case EACCES:  return QStringLiteral("Permission denied (need read/write on device)");
    case EBUSY:   return QStringLiteral("Device busy");
    default:      return QString::fromLocal8Bit(strerror(e));
    }
}
} // namespace

bool SerialTransport::open() {
    std::lock_guard<std::mutex> lk(mtx);
    m_error.clear();
    if (m_fd >= 0) return true;

    int fd = ::open(m_device.toLocal8Bit().constData(),
                    O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        m_error = QStringLiteral("open('%1') failed: %2 (errno %3)")
                      .arg(m_device).arg(errnoToString(errno)).arg(errno);
        return false;
    }

    termios tty{};
    if (tcgetattr(fd, &tty) != 0) {
        m_error = QStringLiteral("tcgetattr failed: %1").arg(QString::fromLocal8Bit(strerror(errno)));
        ::close(fd);
        return false;
    }

    // Raw 8N1, no flow control, receiver enabled.
    cfmakeraw(&tty);
    tty.c_cflag &= ~PARENB;
    tty.c_cflag &= ~CSTOPB;
    tty.c_cflag &= ~CSIZE;
    tty.c_cflag |= CS8;
    tty.c_cflag &= ~CRTSCTS;
    tty.c_cflag |= (CREAD | CLOCAL);

    // Non-blocking read; we drive timing with poll() so close() can wake us.
    tty.c_cc[VMIN]  = 0;
    tty.c_cc[VTIME] = 0;

    speed_t sp = baudToConstant(m_baud);
    if (cfsetispeed(&tty, sp) != 0 || cfsetospeed(&tty, sp) != 0) {
        m_error = QStringLiteral("cfsetispeed(%1) failed").arg(m_baud);
        ::close(fd);
        return false;
    }
    if (tcsetattr(fd, TCSANOW, &tty) != 0) {
        m_error = QStringLiteral("tcsetattr failed: %1").arg(QString::fromLocal8Bit(strerror(errno)));
        ::close(fd);
        return false;
    }
    tcflush(fd, TCIOFLUSH);

    m_fd = fd;
    m_buf.clear();
    return true;
}

void SerialTransport::close() {
    std::lock_guard<std::mutex> lk(mtx);
    if (m_fd < 0) return;
    // poll() runs with a 250 ms timeout, so a blocked reader notices m_fd==-1
    // within one interval and returns. (shutdown() is socket-only and does not
    // apply to ttys, so we deliberately do not call it.)
    ::close(m_fd);
    m_fd = -1;
    m_buf.clear();
}

bool SerialTransport::isOpen() const {
    std::lock_guard<std::mutex> lk(const_cast<std::mutex&>(mtx));
    return m_fd >= 0;
}

QString SerialTransport::errorString() const { return m_error; }

bool SerialTransport::readLine(QByteArray& lineOut) {
    lineOut.clear();
    while (true) {
        // Try to peel a complete line from the accumulator first.
        int nl = m_buf.indexOf('\n');
        if (nl >= 0) {
            QByteArray line = m_buf.left(nl);
            m_buf.remove(0, nl + 1);
            while (!line.isEmpty() && line.endsWith('\r')) line.chop(1);
            lineOut = line;
            return true;
        }

        int fd;
        {
            std::lock_guard<std::mutex> lk(mtx);
            fd = m_fd;
        }
        if (fd < 0) { m_error = QStringLiteral("device closed"); return false; }

        pollfd pfd{fd, POLLIN, 0};
        int pr = ::poll(&pfd, 1, 250); // wake periodically so close()/stop is prompt
        if (pr < 0) {
            if (errno == EINTR) continue;
            std::lock_guard<std::mutex> lk(mtx);
            m_error = QStringLiteral("poll failed: %1").arg(QString::fromLocal8Bit(strerror(errno)));
            return false;
        }
        if (pr == 0) {
            // timeout: re-check fd (maybe closed) and loop
            continue;
        }
        if (pfd.revents & (POLLERR | POLLNVAL)) {
            std::lock_guard<std::mutex> lk(mtx);
            m_error = QStringLiteral("device error (poll POLLERR/POLLNVAL)");
            return false;
        }
        if (pfd.revents & (POLLHUP | POLLRDHUP)) {
            // modem hang-up / unplugged
            std::lock_guard<std::mutex> lk(mtx);
            m_error = QStringLiteral("device disconnected (hang-up)");
            return false;
        }

        char chunk[4096];
        ssize_t n = ::read(fd, chunk, sizeof(chunk));
        if (n < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) continue;
            if (errno == EIO) { // typical USB-serial unplug
                std::lock_guard<std::mutex> lk(mtx);
                m_error = QStringLiteral("device I/O error (unplugged?)");
                return false;
            }
            std::lock_guard<std::mutex> lk(mtx);
            m_error = QStringLiteral("read failed: %1").arg(QString::fromLocal8Bit(strerror(errno)));
            return false;
        }
        if (n == 0) {
            // EOF on the tty side
            std::lock_guard<std::mutex> lk(mtx);
            m_error = QStringLiteral("end of stream");
            return false;
        }
        m_buf.append(chunk, static_cast<int>(n));
        // Bound the buffer so a missing newline can never grow unbounded.
        if (m_buf.size() > 64 * 1024) m_buf.remove(0, m_buf.size() - 4096);
    }
}

#else // !__unix__

bool SerialTransport::open() {
    m_error = QStringLiteral("serial transport is not supported on this platform (termios)");
    return false;
}
void SerialTransport::close() {}
bool SerialTransport::isOpen() const { return false; }
QString SerialTransport::errorString() const { return m_error; }
bool SerialTransport::readLine(QByteArray& lineOut) { lineOut.clear(); return false; }

#endif

} // namespace gnss
} // namespace mbdsdr
