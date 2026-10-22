// SPDX-License-Identifier: MIT
// P3 #73: SQLite FTS5 local memory store, exercised against a real on-disk
// database in a temp dir. No mocking of SQLite itself.
#include <QtTest>
#include <QDir>
#include <QFile>
#include <QStandardPaths>

#include "ai/memory_store.h"

using namespace mbdsdr::ai;

class TestMemoryStore : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void cleanupTestCase();
    void addThenSearchRoundTrip();
    void scopeIsolation();
    void updateOverwrites();
    void duplicateAddHonestError();
    void emptyQueryHonestError();
    void unsanitizedQueryNeverCrashes();
    void unwritablePathHonestError();

private:
    QString m_tmp;
    QString dbPath() const { return m_tmp + "/memory.db"; }
};

void TestMemoryStore::initTestCase() {
    m_tmp = QStandardPaths::writableLocation(QStandardPaths::TempLocation)
            + QStringLiteral("/mbdsdr_mem_") + QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(m_tmp);
}

void TestMemoryStore::cleanupTestCase() {
    QDir(m_tmp).removeRecursively();
}

void TestMemoryStore::addThenSearchRoundTrip() {
    MemoryStore s(dbPath());
    QString err;
    QVERIFY2(s.open(&err), qPrintable(err));
    QVERIFY2(s.add("user", "station-1", "vna-battery-note",
                   QString::fromUtf8("NanoVNA 电池电压低于 3.3V 时读数漂移"), &err), qPrintable(err));
    QVERIFY2(s.add("agent", "session-x", "efhw-tip",
                   "EFHW matching needs load_r > 50 ohm for the default topology", &err), qPrintable(err));

    // Chinese hit: content tokenized through unicode61.
    QList<MemoryHit> zh = s.search(QString::fromUtf8("电池电压"), {}, 20, &err);
    QCOMPARE(zh.size(), 1);
    QCOMPARE(zh[0].key, "vna-battery-note");
    QVERIFY(!zh[0].snippet.isEmpty());

    // English hit.
    QList<MemoryHit> en = s.search("EFHW topology", {}, 20, &err);
    QCOMPARE(en.size(), 1);
    QCOMPARE(en[0].scope, "agent");
    QCOMPARE(en[0].key, "efhw-tip");
}

void TestMemoryStore::scopeIsolation() {
    MemoryStore s(dbPath());
    QString err;
    QVERIFY(s.open(&err));
    QVERIFY(s.add("user", "station-1", "shared-key",
                  "user scope fact: east antenna 12m", &err));
    QVERIFY(s.add("session", "live-1", "shared-key",
                  "session observation: east band noise", &err));

    // "east" appears in both rows' content; FTS indexes all four columns.
    QList<MemoryHit> all = s.search("east", {}, 20, &err);
    int userHits = 0, sessionHits = 0;
    for (const MemoryHit& h : all) {
        if (h.scope == "user") ++userHits;
        if (h.scope == "session") ++sessionHits;
    }
    QCOMPARE(userHits, 1);
    QCOMPARE(sessionHits, 1);

    QList<MemoryHit> onlyUser = s.search("east", "user", 20, &err);
    QCOMPARE(onlyUser.size(), 1);
    QCOMPARE(onlyUser[0].scope, "user");
}

void TestMemoryStore::updateOverwrites() {
    MemoryStore s(dbPath());
    QString err;
    QVERIFY(s.open(&err));
    QVERIFY(s.add("agent", "session-x", "tmp-note", "draft text v1", &err));
    QVERIFY2(s.update("agent", "session-x", "tmp-note", "final text v2", &err), qPrintable(err));

    QList<MemoryHit> oldHit = s.search("draft", {}, 20, &err);
    QCOMPARE(oldHit.size(), 0);   // overwritten content is gone

    QList<MemoryHit> newHit = s.search("final text", {}, 20, &err);
    QCOMPARE(newHit.size(), 1);
    QCOMPARE(newHit[0].content, "final text v2");
    QCOMPARE(newHit[0].scopeId, "session-x");
}

void TestMemoryStore::duplicateAddHonestError() {
    MemoryStore s(dbPath());
    QString err;
    QVERIFY(s.open(&err));
    QVERIFY(s.add("user", "station-1", "dup-key", "first", &err));
    QVERIFY2(!s.add("user", "station-1", "dup-key", "second", &err), "dup add must fail");
    QVERIFY2(err.contains(QString::fromUtf8("memory_update")), qPrintable(err));
}

void TestMemoryStore::emptyQueryHonestError() {
    MemoryStore s(dbPath());
    QString err;
    QVERIFY(s.open(&err));
    QVERIFY2(s.search("", {}, 20, &err).isEmpty(), "empty query returns no hits");
    QVERIFY2(!err.isEmpty(), "empty query must carry an honest error");
    QVERIFY2(s.search("   ;--", {}, 20, &err).isEmpty(), "punctuation-only query = no tokens");
}

void TestMemoryStore::unsanitizedQueryNeverCrashes() {
    MemoryStore s(dbPath());
    QString err;
    QVERIFY(s.open(&err));
    // Classic FTS5 injection payload; sanitization must neutralize syntax chars.
    QList<MemoryHit> h = s.search("station' OR 1=1; DROP TABLE mem_meta;--", {}, 20, &err);
    Q_UNUSED(h);
    // The meta table must still be usable afterwards (no drop happened).
    QString err2;
    QVERIFY2(s.add("user", "station-1", "after-injection", "still writable", &err2), qPrintable(err2));
    QCOMPARE(s.search("after-injection", {}, 20, &err2).size(), 1);
}

void TestMemoryStore::unwritablePathHonestError() {
    // 跨平台"不可写路径"：父路径是一个普通文件（不是目录），SQLite 无法在
    // 其下创建数据库——Linux/Windows 语义一致。此前写死 /proc/... 为 Linux
    // 专属路径，Windows 上会解析成盘根下可创建的目录，open 误成功、测试误红。
    QFile blocker(QDir(m_tmp).filePath("memstore_blocker_file"));
    QVERIFY2(blocker.open(QIODevice::WriteOnly), "temp blocker file must be creatable");
    blocker.close();
    MemoryStore s(blocker.fileName() + QStringLiteral("/memory.db"));
    QString err;
    QVERIFY2(!s.open(&err), "opening inside a non-writable path must fail");
    QVERIFY2(!err.isEmpty(), "error message must be honest");
    QFile::remove(blocker.fileName());
}

QTEST_MAIN(TestMemoryStore)
#include "test_memory_store.moc"
