// SPDX-License-Identifier: MIT
//
// Offscreen TLE freshness/network test: a loopback QTcpServer serves a real
// celestrak-style TLE blob; TleClient pulls it, caches with a timestamp, and
// the epoch is parsed into a real date. Then a dead endpoint forces the
// honest cache->builtin fallback. Pure epoch/days functions are asserted.
#include <QtTest>
#include <QTcpServer>
#include <QTcpSocket>
#include <QDateTime>

#include "dsp/tle_client.h"

using namespace mbdsdr;

static const char* kTle =
    "ISS (ZARYA)\n"
    "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9001\n"
    "2 25544  51.6400 208.9163 0006703  83.1036  25.7611 15.49185869123456\n";

class LoopbackServer : public QObject {
    Q_OBJECT
public:
    QTcpServer srv;
    int hits = 0;
    explicit LoopbackServer(QObject* p = nullptr) : QObject(p) {
        srv.listen(QHostAddress::LocalHost, 0);
        connect(&srv, &QTcpServer::newConnection, this, [this]() {
            QTcpSocket* c = srv.nextPendingConnection();
            connect(c, &QTcpSocket::readyRead, this, [this, c]() {
                c->readAll();
                ++hits;
                const QByteArray body = kTle;
                QByteArray head = "HTTP/1.1 200 OK\r\n"
                                  "Content-Type: text/plain\r\n"
                                  "Content-Length: " +
                                  QByteArray::number(body.size()) + "\r\n\r\n";
                c->write(head + body);
                c->disconnectFromHost();
            });
        });
    }
    quint16 port() const { return srv.serverPort(); }
};

class TestTleFetch : public QObject {
    Q_OBJECT
private slots:
    void epochParsedPure();
    void loopbackFetchCachesAndCounts();
    void offlineFallsBackToCache();
};

void TestTleFetch::epochParsedPure() {
    const QDateTime ep =
        dsp::TleClient::parseTleEpoch(QString::fromLatin1(
            "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9001"));
    QVERIFY2(ep.isValid(), "epoch must parse");
    QCOMPARE(ep.date().year(), 2024);
    QCOMPARE(ep.date().month(), 1);
    QCOMPARE(ep.date().day(), 1);   // day-of-year 1 -> Jan 1
    // ~12:00 UTC on Jan 1 (0.5 day frac).
    QVERIFY(std::abs(ep.time().hour() - 12) <= 1);
    const double d = dsp::TleClient::daysSinceEpoch(ep, ep.addDays(20.0));
    QVERIFY(std::abs(d - 20.0) < 0.01);
    QVERIFY(!dsp::TleClient::parseTleEpoch("garbage").isValid());
}

void TestTleFetch::loopbackFetchCachesAndCounts() {
    LoopbackServer srv;
    dsp::TleClient tle;
    tle.setBaseUrl(QString("http://127.0.0.1:%1").arg(srv.port()));
    QSignalSpy ready(&tle, &dsp::TleClient::passesReady);
    QSignalSpy failed(&tle, &dsp::TleClient::fetchFailed);
    tle.fetch(39.9, 116.4);
    // Wait for the 4 groups (stations/weather/gnss/amateur) to round-trip.
    QTRY_VERIFY_WITH_TIMEOUT(ready.count() >= 1 || failed.count() >= 1, 5000);
    QCOMPARE(srv.hits, 4);   // exactly one request per group, no duplicate pull
    QVERIFY2(ready.count() >= 1,
             qPrintable(QString("fetch should succeed, got: %1").arg(
                 failed.count() ? failed.takeFirst().at(0).toString() : "none")));
    dsp::TleCache c = tle.cachedTle();
    QVERIFY(c.valid);
    QVERIFY(c.fetchedAt.isValid());
    QVERIFY(!c.entries.isEmpty());
    QVERIFY(!c.fetchedAt.secsTo(QDateTime::currentDateTimeUtc()) < 120);
}

void TestTleFetch::offlineFallsBackToCache() {
    // Cache from the previous test still on disk. Point at a dead endpoint.
    LoopbackServer dead;
    dsp::TleClient tle;
    tle.setBaseUrl(QString("http://127.0.0.1:1"));   // nothing listens
    QSignalSpy failed(&tle, &dsp::TleClient::fetchFailed);
    tle.fetch(39.9, 116.4);
    QTRY_VERIFY_WITH_TIMEOUT(failed.count() >= 1, 5000);
    // Honest fallback: the on-disk cache is still there, not wiped by the error.
    dsp::TleCache c = tle.cachedTle();
    QVERIFY2(c.valid, "offline must fall back to the existing cache");
    QVERIFY(!c.entries.isEmpty());
}

QTEST_MAIN(TestTleFetch)
#include "test_tle_fetch.moc"
