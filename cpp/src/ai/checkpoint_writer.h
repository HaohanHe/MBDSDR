// SPDX-License-Identifier: MIT
// Background checkpoint writer for the agent memory family.
//
// Single-writer invariant: all memory_store.add/update calls happen on this
// writer's own worker thread (or inside an explicit flush() drain); readers
// (memory_search) open their own short-lived connections and are protected by
// SQLite/WAL plus a per-store search mutex. No cron timers: the worker wakes
// on a short periodic interval and drains whatever is queued.
#pragma once

#include <QDateTime>
#include <QMutex>
#include <QList>
#include <QString>
#include <QWaitCondition>
#include <atomic>
#include <thread>

#include "memory_store.h"

namespace mbdsdr {
namespace ai {

// Default snapshot location: ~/.mbdsdr/checkpoint.md (human-readable, rewritten
// after every drain). Respects MBDSDR_MEMORY_DB? No — the checkpoint path is
// independent; tests redirect via the constructor.
QString defaultCheckpointPath();

struct MemoryWriteRequest {
    enum Op { Add, Update } op;
    QString scope;
    QString scopeId;
    QString key;
    QString content;
};

class CheckpointWriter {
public:
    CheckpointWriter(const QString& dbPath, const QString& checkpointPath);
    ~CheckpointWriter();

    CheckpointWriter(const CheckpointWriter&) = delete;
    CheckpointWriter& operator=(const CheckpointWriter&) = delete;

    // Spawn the worker thread (idempotent). Writes go through it from now on.
    void start();
    // Stop the worker and join. Does NOT drain: call flush() first when the
    // caller needs durability. Safe to call when never started.
    void stop();

    // Non-blocking enqueue. Validates args honestly; returns false + *err on
    // bad input. The actual write happens on the worker thread.
    bool submit(MemoryWriteRequest::Op op, const QString& scope,
                const QString& scopeId, const QString& key,
                const QString& content, QString* err);

    // Synchronous drain on the CALLING thread (serialized with the worker's own
    // drains): process the whole queue, rewrite checkpoint.md. Returns the
    // number of requests actually persisted (>=0). First error, if any, goes
    // to *err but processing continues for the rest.
    int flush(QString* err);

    int pendingCount();      // queued but not yet persisted
    qint64 lastFlushMs();    // wall ms of last completed drain (0 = never)
    int queueDepth() { return pendingCount(); }

private:
    void workerLoop();
    // Drains m_queue under m_drainMutex and persists rows; shared by the worker
    // thread and flush() so checkpoint.md is only ever written by one drain.
    int drainLocked(QString* err);

    QString m_checkpointPath;
    MemoryStore m_store;

    QMutex m_queueMutex;
    QList<MemoryWriteRequest> m_queue;

    QMutex m_drainMutex;   // serializes worker drains vs explicit flush()

    QMutex m_sleepMutex;
    QWaitCondition m_wake;
    std::atomic<bool> m_stop{false};
    std::thread m_worker;

    QMutex m_stateMutex;
    qint64 m_lastFlushMs = 0;
};

// Process-wide accessor (thread-safe init): lazily starts the worker once.
// Tests redirect the db path via MBDSDR_MEMORY_DB before first call.
CheckpointWriter& memoryWriter();

} // namespace ai
} // namespace mbdsdr
