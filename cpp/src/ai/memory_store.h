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

// Default on-disk location: ~/.mbdsdr/memory.db (created on first write).
QString defaultMemoryDbPath();

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

private:
    QString m_path;
    void* m_db = nullptr;  // sqlite3*, opaque to keep the sqlite3 header local.
    // Serializes search() against concurrent writer drains (readers run on the
    // agent thread while the writer thread persists rows).
    QMutex mutable m_searchGuard;
};

} // namespace ai
} // namespace mbdsdr
