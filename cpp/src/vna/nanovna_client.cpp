// SPDX-License-Identifier: MIT
// NanoVNA text-protocol client implementation (clean-room, self-written).
#include "nanovna_client.h"

#include <QString>
#include <QStringList>
#include <QRegularExpression>

#include <cmath>
#include <limits>

#if defined(__unix__)
#include <fcntl.h>
#include <errno.h>
#include <termios.h>
#include <unistd.h>
#include <cstring>
#endif

namespace mbdsdr {
namespace vna {

// ---- RF conversion pure functions -----------------------------------------

double returnLossDb(std::complex<double> s11) {
    double g = std::abs(s11);
    if (g <= 0.0) return std::numeric_limits<double>::infinity();
    return -20.0 * std::log10(g);
}

double vswr(std::complex<double> s11) {
    double g = std::abs(s11);
    if (g >= 1.0) return std::numeric_limits<double>::infinity();
    if (g <= 0.0) return 1.0;
    return (1.0 + g) / (1.0 - g);
}

std::complex<double> s11ToImpedance(std::complex<double> s11, double z0) {
    std::complex<double> denom = 1.0 - s11;
    if (denom == std::complex<double>(0.0, 0.0))
        return {std::numeric_limits<double>::infinity(),
                std::numeric_limits<double>::infinity()};
    return z0 * (1.0 + s11) / denom;
}

double s21GainDb(std::complex<double> s21) {
    double a = std::abs(s21);
    if (a <= 0.0) return -std::numeric_limits<double>::infinity();
    return 20.0 * std::log10(a);
}

double s21PhaseDeg(std::complex<double> s21) {
    return std::atan2(s21.imag(), s21.real()) * 180.0 / M_PI;
}

// ---- Real termios transport -----------------------------------------------
#if defined(__unix__)
namespace {
class TermiosVnaTransport : public VnaTransport {
public:
    explicit TermiosVnaTransport(QString device) : dev_(std::move(device)) {}
    ~TermiosVnaTransport() override { close(); }

    bool open() override {
        fd_ = ::open(dev_.toLocal8Bit().constData(), O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (fd_ < 0) { err_ = QStringLiteral("open %1: %2").arg(dev_).arg(::strerror(errno)); return false; }
        termios tty{};
        if (tcgetattr(fd_, &tty) != 0) { err_ = QStringLiteral("tcgetattr failed"); ::close(fd_); fd_=-1; return false; }
        cfmakeraw(&tty);
        cfsetispeed(&tty, B115200);
        cfsetospeed(&tty, B115200);
        tty.c_cc[VMIN] = 0; tty.c_cc[VTIME] = 1; // 0.1s read timeout
        tcsetattr(fd_, TCSANOW, &tty);
        // clear O_NONBLOCK so read() honors the VTIME timeout
        int fl = fcntl(fd_, F_GETFL, 0);
        fcntl(fd_, F_SETFL, fl & ~O_NONBLOCK);
        return true;
    }
    void close() override { if (fd_ >= 0) { ::close(fd_); fd_ = -1; } }
    bool isOpen() const override { return fd_ >= 0; }
    QString errorString() const override { return err_; }
    void write(const QByteArray& d) override {
        if (fd_ >= 0) ::write(fd_, d.constData(), d.size());
    }
    bool readLine(QByteArray& out) override {
        out.clear();
        if (fd_ < 0) return false;
        char c;
        while (true) {
            ssize_t n = ::read(fd_, &c, 1);
            if (n <= 0) {
                if (!out.isEmpty()) return true; // return what we have, EOF after
                return false;
            }
            if (c == '\n') return true;
            if (c != '\r') out.append(c);
        }
    }
    void resetInputBuffer() override { if (fd_ >= 0) tcflush(fd_, TCIFLUSH); }
private:
    QString dev_; int fd_ = -1; QString err_;
};
} // namespace
#else
namespace {
class StubVnaTransport : public VnaTransport {
public:
    explicit StubVnaTransport(QString) {}
    bool open() override { return false; }
    void close() override {}
    bool isOpen() const override { return false; }
    QString errorString() const override { return QStringLiteral("serial unsupported on this platform"); }
    void write(const QByteArray&) override {}
    bool readLine(QByteArray& out) override { out.clear(); return false; }
    void resetInputBuffer() override {}
};
} // namespace
#endif

std::unique_ptr<VnaTransport> createSerialTransport(const QString& device) {
#if defined(__unix__)
    return std::make_unique<TermiosVnaTransport>(device);
#else
    return std::make_unique<StubVnaTransport>(device);
#endif
}

// ---- Client ----------------------------------------------------------------

NanoVnaClient::NanoVnaClient() = default;
NanoVnaClient::NanoVnaClient(std::unique_ptr<VnaTransport> transport)
    : tr_(std::move(transport)) {}
NanoVnaClient::~NanoVnaClient() { close(); }

bool NanoVnaClient::connect() {
    close();
    if (!tr_) return false;
    if (!tr_->open()) return false;
    // help (capability probe; tolerated even if it yields nothing)
    exec(QStringLiteral("help"));
    // version
    QList<QString> ver = exec(QStringLiteral("version"));
    if (!ver.isEmpty()) version_ = ver.first();
    // info (first line = board/model name)
    QList<QString> info = exec(QStringLiteral("info"));
    if (!info.isEmpty()) model_ = info.first();
    connected_ = true;
    return true;
}

bool NanoVnaClient::connectSerial(const QString& device) {
    close();
    tr_ = createSerialTransport(device);
    return connect();
}

void NanoVnaClient::close() {
    if (tr_) tr_->close();
    connected_ = false;
}

QList<QString> NanoVnaClient::exec(const QString& command) {
    QList<QString> out;
    if (!tr_ || !tr_->isOpen()) return out;
    tr_->resetInputBuffer();
    QByteArray cmd = command.toUtf8();
    cmd.append('\r');
    tr_->write(cmd);
    for (int i = 0; i < 4096; ++i) {
        QByteArray raw;
        if (!tr_->readLine(raw)) { if (!out.isEmpty()) break; continue; }
        QString line = QString::fromUtf8(raw).trimmed();
        if (line.isEmpty()) continue;
        if (line == command) continue;            // echo suppression
        if (line.startsWith(QStringLiteral("ch>"))) break;  // prompt
        out.append(line);
    }
    return out;
}

bool NanoVnaClient::setSweep(long startHz, long stopHz, int points, QString* err) {
    if (!connected_) { if (err) *err = QStringLiteral("NanoVNA 未连接"); return false; }
    if (stopHz <= startHz) { if (err) *err = QStringLiteral("stop 必须大于 start"); return false; }
    if (points <= 0) { if (err) *err = QStringLiteral("points 必须为正"); return false; }
    exec(QStringLiteral("sweep %1 %2 %3").arg(startHz).arg(stopHz).arg(points));
    sStartHz_ = startHz; sStopHz_ = stopHz; sPoints_ = points; hasSweep_ = true;
    return true;
}

std::vector<long> NanoVnaClient::readFrequencies() {
    std::vector<long> out;
    if (!connected_) return out;
    for (const QString& line : exec(QStringLiteral("frequencies"))) {
        bool ok = false;
        long v = line.toLong(&ok);
        if (ok) out.push_back(v);
    }
    return out;
}

std::vector<std::complex<double>> NanoVnaClient::readData(int channel) {
    std::vector<std::complex<double>> out;
    if (!connected_ || (channel != 0 && channel != 1)) return out;
    for (const QString& line : exec(QStringLiteral("data %1").arg(channel))) {
        QStringList p = line.split(QRegularExpression(QStringLiteral("\\s+")), Qt::SkipEmptyParts);
        if (p.size() < 2) continue;
        bool ok1 = false, ok2 = false;
        double re = p[0].toDouble(&ok1);
        double im = p[1].toDouble(&ok2);
        if (ok1 && ok2) out.emplace_back(re, im);
    }
    return out;
}

QStringList NanoVnaClient::readCalStatus() {
    if (!connected_) return {};
    QStringList lines = exec(QStringLiteral("cal"));
    cal_ = lines.join(QLatin1Char(' ')).split(QRegularExpression(QStringLiteral("\\s+")), Qt::SkipEmptyParts);
    return cal_;
}

} // namespace vna
} // namespace mbdsdr
