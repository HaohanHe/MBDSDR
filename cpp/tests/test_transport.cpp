// SPDX-License-Identifier: MIT
// Tests for transport backends + factories + a receiver smoke test.
//
// Recorded lines below are text fixtures only: 录制样例·非硬件 NOT HARDWARE.
#include "gnss/i_transport.h"
#include "gnss/gnss_receiver.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QTextStream>
#include <cstdio>

using namespace mbdsdr::gnss;

static int failures = 0;
static void check(bool cond, const char* msg) {
    if (!cond) { ++failures; std::printf("FAIL: %s\n", msg); }
}

// NOT HARDWARE fixture.
static const char* kLines =
    "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\n"
    "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\n"
    "$GPZDA,123519.00,23,03,1994,00,00*6C\n";

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);

    // --- Factories return non-null ---------------------------------------
    {
        auto s = createSerialTransport("/dev/null", 9600);
        auto f = createFileTransport("/nonexistent/x.log");
        auto m = createMemoryTransport(QByteArray("x\n"));
        check(s != nullptr && f != nullptr && m != nullptr, "factories return instances");
    }

    // --- Memory transport: full round-trip of every line ------------------
    {
        auto t = createMemoryTransport(QByteArray(kLines));
        check(t->open(), "memory open");
        check(t->isOpen(), "memory isOpen");
        QByteArray line;
        int n = 0;
        while (t->readLine(line)) {
            ++n;
            check(line.startsWith("$GP"), "memory line starts with $GP");
        }
        check(n == 3, "memory delivered all 3 lines");
        t->close();
        check(!t->isOpen(), "memory closed");
    }

    // --- File transport: write a log, read every line back ----------------
    {
        const QString path = QDir::temp().filePath("mbdsdr_gnss_test.log");
        {
            QFile out(path);
            out.open(QIODevice::WriteOnly | QIODevice::Truncate);
            out.write(kLines);
        }
        auto t = createFileTransport(path);
        check(t->open(), "file open");
        QByteArray line;
        int n = 0;
        while (t->readLine(line)) { ++n; }
        check(n == 3, "file delivered all 3 lines");
        t->close();
        QFile::remove(path);
    }

    // --- File open on a missing path fails softly -------------------------
    {
        auto t = createFileTransport("/nonexistent_dir_xyz/no.log");
        check(!t->open(), "missing file open()==false");
        check(!t->errorString().isEmpty(), "missing file errorString non-empty");
    }

    // --- Serial: nonexistent device fails gracefully (no crash/hang) ------
    {
        auto t = createSerialTransport("/dev/mbdsdr_nonexistent_zzz_999", 9600);
        bool ok = t->open();
        const QString errAfterOpen = t->errorString(); // capture before readLine touches it
        check(!ok, "nonexistent serial open()==false");
        check(!errAfterOpen.isEmpty(), "serial errorString non-empty");
        check(!t->isOpen(), "serial not open after failure");
        // readLine on a failed transport must return false, not block/crash
        QByteArray line;
        check(!t->readLine(line), "serial readLine false when not open");
        std::printf("  serial err: %s\n", errAfterOpen.toLocal8Bit().constData());
    }

    // --- Receiver smoke: memory transport produces a merged fix ----------
    {
        GnssReceiver rx;
        auto mem = createMemoryTransport(QByteArray(kLines));
        rx.setTransport(std::move(mem));
        rx.start();
        rx.wait(3000); // memory EOF => run() returns on its own
        check(!rx.isRunning(), "receiver stopped after EOF");
        GnssFix f = rx.lastFix();
        check(f.isValid(), "receiver lastFix valid");
        check(f.satellitesInUse == 1 || f.fixQuality == FixQuality::GpsFix,
              "receiver parsed GGA quality/sats");
        check(!rx.connected(), "receiver disconnected after EOF");
        std::printf("  receiver lat=%.6f quality=%d\n", f.latitude, (int)f.fixQuality);
    }

    if (failures == 0) std::printf("test_transport: ALL PASS\n");
    else std::printf("test_transport: %d FAILURE(S)\n", failures);
    return failures ? 1 : 0;
}
