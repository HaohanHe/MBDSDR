// SPDX-License-Identifier: MIT
#include "checkpoint_writer.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSet>
#include <QTextStream>
#include <QWaitCondition>

namespace mbdsdr {
namespace ai {

QString defaultCheckpointPath() {
    return QDir::homePath() + QStringLiteral("/.mbdsdr/checkpoint.md");
}

CheckpointWriter::CheckpointWriter(const QString& dbPath, const QString& checkpointPath)
    : m_checkpointPath(checkpointPath), m_store(dbPath) {}

CheckpointWriter::~CheckpointWriter() {
    stop();
}

void CheckpointWriter::start() {
    if (m_worker.joinable()) return;
    m_stop = false;
    m_worker = std::thread([this] { workerLoop(); });
}

void CheckpointWriter::stop() {
    m_stop = true;
    m_wake.wakeAll();
    if (m_worker.joinable()) m_worker.join();
}

bool CheckpointWriter::submit(MemoryWriteRequest::Op op, const QString& scope,
                              const QString& scopeId, const QString& key,
                              const QString& content, QString* err) {
    static const QSet<QString> kScopes{
        QStringLiteral("user"), QStringLiteral("agent"), QStringLiteral("session")};
    if (!kScopes.contains(scope)) {
        if (err) *err = QStringLiteral("scope 必填枚举 user/agent/session");
        return false;
    }
    if (scopeId.isEmpty()) { if (err) *err = QStringLiteral("scope_id 必填"); return false; }
    if (key.isEmpty())     { if (err) *err = QStringLiteral("key 必填"); return false; }
    if (content.isEmpty()) { if (err) *err = QStringLiteral("content 必填"); return false; }

    QMutexLocker q(&m_queueMutex);
    m_queue.push_back({op, scope, scopeId, key, content});
    return true;
}

int CheckpointWriter::pendingCount() {
    QMutexLocker q(&m_queueMutex);
    return m_queue.size();
}

qint64 CheckpointWriter::lastFlushMs() {
    QMutexLocker s(&m_stateMutex);
    return m_lastFlushMs;
}

int CheckpointWriter::drainLocked(QString* err) {
    QList<MemoryWriteRequest> drained;
    {
        QMutexLocker q(&m_queueMutex);
        drained.swap(m_queue);
    }
    if (drained.isEmpty()) return 0;

    if (!m_store.isOpen()) {
        QString e;
        if (!m_store.open(&e)) {
            if (err) *err = QStringLiteral("记忆库不可用：%1").arg(e);
            return 0;
        }
    }

    int processed = 0;
    QString firstErr;
    for (const MemoryWriteRequest& r : drained) {
        QString e;
        const bool ok = (r.op == MemoryWriteRequest::Add)
                            ? m_store.add(r.scope, r.scopeId, r.key, r.content, &e)
                            : m_store.update(r.scope, r.scopeId, r.key, r.content, &e);
        if (ok) ++processed;
        else if (firstErr.isEmpty()) firstErr = e;
    }

    // Rewrite the human-readable checkpoint snapshot from what was just written.
    // mkpath first: ~/.mbdsdr/ may not exist on a fresh machine (CI runner,
    // clean profile) -- QFile::open would fail silently without it. Same
    // convention as memory_store.cpp:95 and wav_writer.cpp:31.
    QDir().mkpath(QFileInfo(m_checkpointPath).absolutePath());
    QFile f(m_checkpointPath);
    if (f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        QTextStream ts(&f);
        ts << "# MBDSDR 记忆 checkpoint\n";
        ts << QStringLiteral("生成时间: %1\n").arg(
            QDateTime::currentDateTime().toString(Qt::ISODate));
        ts << QStringLiteral("本次落库: %1 条\n").arg(processed);
        ts << "最近写入:\n";
        for (const MemoryWriteRequest& r : drained)
            ts << QStringLiteral("- [%1/%2] %3\n").arg(r.scope, r.scopeId, r.key);
        ts.flush();
    } else if (firstErr.isEmpty()) {
        firstErr = QStringLiteral("无法写 checkpoint.md：%1").arg(m_checkpointPath);
    }

    {
        QMutexLocker s(&m_stateMutex);
        m_lastFlushMs = QDateTime::currentMSecsSinceEpoch();
    }
    if (!firstErr.isEmpty() && err) *err = firstErr;
    return processed;
}

int CheckpointWriter::flush(QString* err) {
    QMutexLocker d(&m_drainMutex);
    return drainLocked(err);
}

void CheckpointWriter::workerLoop() {
    while (true) {
        {
            QMutexLocker s(&m_sleepMutex);
            // Periodic drain (~150ms); wake() also kicks it for fast latency.
            m_wake.wait(&m_sleepMutex, 150);
            if (m_stop) return;
        }
        QMutexLocker d(&m_drainMutex);
        QString err;
        drainLocked(&err);   // empty queue is a no-op
    }
}

CheckpointWriter& memoryWriter() {
    static CheckpointWriter w(defaultMemoryDbPath(), defaultCheckpointPath());
    static bool started = false;
    if (!started) {
        w.start();
        started = true;
    }
    return w;
}

} // namespace ai
} // namespace mbdsdr
