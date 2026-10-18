// SPDX-License-Identifier: MIT
// P3 #74: checkpoint writer against a REAL temp db + REAL checkpoint.md.
// No mocked business data: rows are persisted through memory_store and verified
// by searching them back.
#include <QtTest>
#include <QDir>
#include <QFile>
#include <QStandardPaths>
#include <QThread>

#include "ai/checkpoint_writer.h"
#include "ai/memory_store.h"

using namespace mbdsdr::ai;

class TestCheckpointWriter : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void cleanupTestCase();
    void submitFlushPersistsRows();
    void backgroundDrainAsyncPersists();
    void flushReturnsCountAndWritesCheckpointMd();
    void badSubmitHonestError();
    void statusFields();

private:
    QString m_tmp;
    QString dbPath() const { return m_tmp + "/mem.db"; }
    QString ckPath() const { return m_tmp + "/checkpoint.md"; }
};

void TestCheckpointWriter::initTestCase() {
    m_tmp = QStandardPaths::writableLocation(QStandardPaths::TempLocation)
            + QStringLiteral("/mbdsdr_ck_") + QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(m_tmp);
}

void TestCheckpointWriter::cleanupTestCase() {
    QDir(m_tmp).removeRecursively();
}

void TestCheckpointWriter::submitFlushPersistsRows() {
    CheckpointWriter w(dbPath(), ckPath());
    QString err;
    QVERIFY2(w.submit(MemoryWriteRequest::Add, "user", "st-1", "k1", "station east antenna 12m", &err),
             qPrintable(err));
    QVERIFY2(w.submit(MemoryWriteRequest::Add, "agent", "s-1", "k2", "EFHW topology note", &err),
             qPrintable(err));
    QCOMPARE(w.pendingCount(), 2);

    const int n = w.flush(&err);
    QCOMPARE(n, 2);
    QCOMPARE(w.pendingCount(), 0);
    QVERIFY(w.lastFlushMs() > 0);

    // Read back through a fresh store on the same file.
    MemoryStore s(dbPath());
    QVERIFY(s.open(&err));
    QList<MemoryHit> h1 = s.search("east", {}, 20, &err);
    QCOMPARE(h1.size(), 1);
    QCOMPARE(h1[0].key, "k1");
    QCOMPARE(h1[0].scope, "user");
    QCOMPARE(h1[0].content, "station east antenna 12m");
    QList<MemoryHit> h2 = s.search("EFHW", {}, 20, &err);
    QCOMPARE(h2.size(), 1);
    QCOMPARE(h2[0].scopeId, "s-1");
}

void TestCheckpointWriter::backgroundDrainAsyncPersists() {
    CheckpointWriter w(dbPath(), ckPath());
    w.start();
    QString err;
    QVERIFY2(w.submit(MemoryWriteRequest::Add, "session", "live-1", "async-key",
                      QString::fromUtf8("会话现场异步条目"), &err), qPrintable(err));

    // Poll the background drain to COMPLETION: the queue is swapped out before
    // the store write + checkpoint rewrite + lastFlushMs stamp, so pending==0
    // alone is a transient state -- wait for the full completion signal.
    int waitedMs = 0;
    while ((w.pendingCount() != 0 || w.lastFlushMs() == 0) && waitedMs < 3000) {
        QThread::msleep(20);
        waitedMs += 20;
    }
    QVERIFY2(w.pendingCount() == 0, "background drain never emptied the queue");
    QVERIFY2(w.lastFlushMs() > 0, "background drain never stamped a completed flush");

    MemoryStore s(dbPath());
    QVERIFY(s.open(&err));
    QList<MemoryHit> h = s.search(QString::fromUtf8("异步"), {}, 20, &err);
    QCOMPARE(h.size(), 1);
    QCOMPARE(h[0].key, "async-key");
    w.stop();
}

void TestCheckpointWriter::flushReturnsCountAndWritesCheckpointMd() {
    CheckpointWriter w(dbPath(), ckPath());
    QString err;
    w.submit(MemoryWriteRequest::Add, "user", "st-9", "snap-key", "snapshot content", &err);
    const int n = w.flush(&err);
    QCOMPARE(n, 1);

    QFile f(ckPath());
    QVERIFY2(f.exists(), "checkpoint.md must exist after flush");
    QVERIFY(f.open(QIODevice::ReadOnly));
    const QString text = QString::fromUtf8(f.readAll());
    QVERIFY2(text.contains(QStringLiteral("checkpoint")), qPrintable(text));
    QVERIFY2(text.contains("snap-key"), qPrintable(text));
    QVERIFY2(text.contains(QStringLiteral("落库: 1")), qPrintable(text));
}

void TestCheckpointWriter::badSubmitHonestError() {
    CheckpointWriter w(dbPath(), ckPath());
    QString err;
    QVERIFY2(!w.submit(MemoryWriteRequest::Add, "badscope", "s", "k", "c", &err),
             "bad scope must fail honestly");
    QVERIFY2(!err.isEmpty(), "error message must be honest");
    err.clear();
    QVERIFY2(!w.submit(MemoryWriteRequest::Add, "user", "s", "", "c", &err),
             "empty key must fail honestly");
}

void TestCheckpointWriter::statusFields() {
    CheckpointWriter w(dbPath(), ckPath());
    // Fresh writer: never flushed yet.
    QCOMPARE(w.pendingCount(), 0);
    QCOMPARE(w.queueDepth(), 0);
    QCOMPARE(w.lastFlushMs(), (qint64)0);

    QString err;
    w.submit(MemoryWriteRequest::Add, "user", "st-1", "status-key", "status content", &err);
    QCOMPARE(w.pendingCount(), 1);
    QCOMPARE(w.queueDepth(), 1);
    w.flush(&err);
    QVERIFY(w.lastFlushMs() > 0);
    QCOMPARE(w.pendingCount(), 0);
}

QTEST_MAIN(TestCheckpointWriter)
#include "test_checkpoint_writer.moc"
