// SPDX-License-Identifier: MIT
// Local persistent memory store for the agent tool layer.
//
// Backed by the system SQLite C API (no Qt SQL plugin): a FTS5 virtual table
// over (scope, scope_id, key, content) plus a small meta table keyed by
// (scope, key). Executors open -> operate -> close on every call: there is no
// global/long-lived handle, so process restarts lose nothing and concurrent
// writers serialize through SQLite itself.
#pragma once

#include <QList>
#include <QMutex>
#include <QJsonObject>
#include <QString>

namespace mbdsdr {
namespace ai {

struct MemoryHit {
    QString scope;
    QString scopeId;
    QString key;
    QString content;
    QString snippet;
};

// One completed TaskRunner plan run (G1: task-result persistence). Append-only
// observability: the LLM tool-loop never rewrites history; get_task_history is
// its only reader.
struct TaskRunRecord {
    qint64 id = -1;
    QString name;          // TaskPlan.name
    QString summary;      // orchestrator's final NL summary
    QString state;         // succeeded / failed / aborted / gated / partial
    qint64 finishedMs = 0; // wall-clock completion time
    int stepCount = 0;
    QString stepsJson;     // raw JSON array text (per-step tool/state/summary/...)
};

// Default on-disk location: ~/.mbdsdr/memory.db (created on first write).
QString defaultMemoryDbPath();

// G3: build the chat-start <UNTRUSTED_CONTENT> memory-injection block. Reads the
// most-recent rows of the "user" and "agent" scopes ONLY (session scope never
// crosses sessions), wraps them in an untrusted-content disclaimer, and caps
// the whole block at ~charBudget characters (oldest rows dropped first).
// Returns "" on an empty DB / db-open failure -- the caller then injects nothing
// (honest empty state, never fabricated). The block is meant for the system
// prompt and must NOT be appended to the persisted conversation history.
QString buildMemoryInjectionBlock(int charBudget = 6000);

class MemoryStore {
public:
    explicit MemoryStore(const QString& dbPath);
    ~MemoryStore();

    MemoryStore(const MemoryStore&) = delete;
    MemoryStore& operator=(const MemoryStore&) = delete;

    // Opens (and, on first use, migrates) the database. Creates the parent
    // directory. Returns false with *err on any failure (directory not
    // writable, corrupted file, SQLite unavailable).
    bool open(QString* err);
    bool isOpen() const;

    // Inserts a new (scope, key) record. Honest error when the key already
    // exists (callers should use update() to overwrite).
    bool add(const QString& scope, const QString& scopeId,
             const QString& key, const QString& content, QString* err);

    // Upsert by (scope, key): inserts or overwrites content/scope_id/updated_ms.
    bool update(const QString& scope, const QString& scopeId,
                const QString& key, const QString& content, QString* err);

    // Full-text search over content (and key/scope columns). The free-text
    // query is tokenized and sanitized before reaching FTS5 — raw user strings
    // are never spliced into the MATCH expression. scopeFilter (empty = any)
    // restricts hits to one scope. limit is clamped to 1..100.
    QList<MemoryHit> search(const QString& query, const QString& scopeFilter,
                            int limit, QString* err);

    // ---- G1: task-run history (append-only, same on-disk DB) -----------------
    // Append one completed plan run; returns the new row id (<0 on failure).
    qint64 recordTaskRun(const QString& name, const QString& summary,
                        const QString& state, int stepCount,
                        const QString& stepsJson, QString* err);
    // Newest-first listing; limit clamped to 1..50. Empty DB -> empty list.
    QList<TaskRunRecord> listTaskRuns(int limit, QString* err);
    // Honest lookup: returns false + *err="not_found" when the id does not exist.
    bool getTaskRun(qint64 id, TaskRunRecord* out, QString* err);

    // ---- G2: memory deletion -------------------------------------------------
    // Delete one (scope, key) record (meta row + its FTS row). *found reports
    // whether a row actually existed -- callers surface "not found" honestly.
    bool deleteKey(const QString& scope, const QString& key, bool* found, QString* err);

    // Read-only stats: row count, per-scope counts, most-recent updated_ms.
    // Returns an object shape the tool layer serializes directly.
    QJsonObject stats(QString* err);

    // G3: most-recently-updated rows of one scope (newest first, limit 1..50).
    // Used by the chat-start memory injection (NOT full-text search).
    QList<MemoryHit> listRecent(const QString& scope, int limit, QString* err);

private:
    QString m_path;
    void* m_db = nullptr;  // sqlite3*, opaque to keep the sqlite3 header local.
    // Serializes search() against concurrent writer drains (readers run on the
    // agent thread while the writer thread persists rows).
    QMutex mutable m_searchGuard;
};

} // namespace ai
} // namespace mbdsdr
