// SPDX-License-Identifier: MIT
#include "memory_store.h"

#include <QDateTime>
#include <QDir>
#include <QRegularExpression>

#include <sqlite3.h>
#include <cstring>

namespace mbdsdr {
namespace ai {

QString defaultMemoryDbPath() {
    // Testability seam: MBDSDR_MEMORY_DB redirects the whole tool layer to a
    // throwaway file (unit tests); production uses the fixed ~/.mbdsdr path.
    const QByteArray env = qgetenv("MBDSDR_MEMORY_DB");
    if (!env.isEmpty()) return QString::fromUtf8(env);
    return QDir::homePath() + QStringLiteral("/.mbdsdr/memory.db");
}

namespace {

bool isHanChar(const QChar c) {
    const uint u = c.unicode();
    return (u >= 0x4E00 && u <= 0x9FFF)   // CJK Unified Ideographs
        || (u >= 0x3400 && u <= 0x4DBF)   // CJK Extension A
        || (u >= 0xF900 && u <= 0xFAFF);  // CJK Compatibility Ideographs
}

// Insert a space between every pair of adjacent Han chars. unicode61 treats a
// contiguous Han run as ONE token, which makes CJK substring search impossible;
// splitting per Han char on BOTH the index and the query restores per-character
// AND/phrase matching. ASCII word spaces are left untouched.
QString cjkSplit(const QString& in) {
    QString out;
    QChar prev;
    for (const QChar c : in) {
        if (!prev.isNull() && isHanChar(prev) && isHanChar(c)) out += QLatin1Char(' ');
        out += c;
        prev = c;
    }
    return out;
}

// Inverse of cjkSplit for read-back: remove only the spaces we inserted (those
// sitting between two Han chars), preserving real ASCII word spacing.
QString cjkJoin(const QString& in) {
    QString out;
    const int n = in.size();
    for (int i = 0; i < n; ++i) {
        if (in[i] == QLatin1Char(' ') && i > 0 && i + 1 < n
            && isHanChar(in[i - 1]) && isHanChar(in[i + 1]))
            continue;
        out += in[i];
    }
    return out;
}

// Split a free-text query into safe FTS5 terms: any non letter/number boundary
// ends a token (so "after-injection" -> after AND injection, never merged),
// then each token is CJK-split and tokens are AND-ed. Raw user text can never
// inject MATCH syntax (all values are bound parameters).
QString sanitizeFtsQuery(const QString& raw) {
    QStringList out;
    QString cur;
    auto flush = [&]() {
        if (!cur.isEmpty()) { out << cjkSplit(cur); cur.clear(); }
    };
    for (const QChar c : raw) {
        if (c.isLetterOrNumber() || c.isMark()) cur += c;
        else flush();
    }
    flush();
    return out.join(QStringLiteral(" AND "));
}

QString sqliteErr(sqlite3* db, const char* fallback) {
    const char* m = db ? sqlite3_errmsg(db) : nullptr;
    return QString::fromUtf8(m && *m ? m : fallback);
}

} // namespace

MemoryStore::MemoryStore(const QString& dbPath) : m_path(dbPath) {}

MemoryStore::~MemoryStore() {
    if (m_db) sqlite3_close_v2(static_cast<sqlite3*>(m_db));
}

bool MemoryStore::open(QString* err) {
    if (m_db) return true;
    // Create parent directory (~/.mbdsdr) on first use.
    const QDir parent = QFileInfo(m_path).absoluteDir();
    if (!parent.exists() && !parent.mkpath(QStringLiteral("."))) {
        if (err) *err = QStringLiteral("无法创建记忆库目录 %1").arg(parent.absolutePath());
        return false;
    }
    sqlite3* db = nullptr;
    const int rc = sqlite3_open_v2(m_path.toUtf8().constData(), &db,
                                   SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE,
                                   nullptr);
    if (rc != SQLITE_OK) {
        const QString m = sqliteErr(db, "sqlite3_open 失败");
        if (db) sqlite3_close(db);
        if (err) *err = QStringLiteral("打开 %1 失败：%2").arg(m_path, m);
        return false;
    }
    // WAL + normal sync: multi-process friendly, honest durability trade-off
    // for a scratch memory layer (host process owns the shutdown path).
    sqlite3_exec(db, "PRAGMA journal_mode=WAL;", nullptr, nullptr, nullptr);
    sqlite3_exec(db, "PRAGMA synchronous=NORMAL;", nullptr, nullptr, nullptr);

    const char* kMeta =
        "CREATE TABLE IF NOT EXISTS mem_meta ("
        " scope TEXT NOT NULL,"
        " scope_id TEXT NOT NULL,"
        " key TEXT NOT NULL,"
        " updated_ms INTEGER NOT NULL,"
        " PRIMARY KEY(scope, key))";
    if (sqlite3_exec(db, kMeta, nullptr, nullptr, nullptr) != SQLITE_OK) {
        if (err) *err = QStringLiteral("建 mem_meta 失败：%1").arg(sqliteErr(db, ""));
        sqlite3_close(db);
        return false;
    }
    const char* kFts =
        "CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5 ("
        " scope, scope_id, key, content, tokenize='unicode61')";
    if (sqlite3_exec(db, kFts, nullptr, nullptr, nullptr) != SQLITE_OK) {
        if (err) *err = QStringLiteral("建 mem_fts 失败（FTS5 不可用？）：%1").arg(sqliteErr(db, ""));
        sqlite3_close(db);
        return false;
    }
    m_db = db;
    return true;
}

bool MemoryStore::isOpen() const { return m_db != nullptr; }

bool MemoryStore::add(const QString& scope, const QString& scopeId,
                      const QString& key, const QString& content, QString* err) {
    auto* db = static_cast<sqlite3*>(m_db);
    if (!db) { if (err) *err = QStringLiteral("记忆库未打开"); return false; }

    sqlite3_exec(db, "BEGIN IMMEDIATE;", nullptr, nullptr, nullptr);
    sqlite3_stmt* st = nullptr;
    const char* sql =
        "INSERT INTO mem_meta(scope, scope_id, key, updated_ms) VALUES(?,?,?,?)";
    if (sqlite3_prepare_v2(db, sql, -1, &st, nullptr) != SQLITE_OK) {
        if (err) *err = sqliteErr(db, "prepare mem_meta insert 失败");
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        return false;
    }
    sqlite3_bind_text(st, 1, scope.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 2, scopeId.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 3, key.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int64(st, 4, (sqlite3_int64)QDateTime::currentMSecsSinceEpoch());
    const int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_ROW && rc != SQLITE_DONE) {
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        if (rc == SQLITE_CONSTRAINT) {
            if (err) *err = QStringLiteral("key 已存在（scope=%1 key=%2）；覆盖请用 memory_update")
                                .arg(scope, key);
        } else if (err) {
            *err = sqliteErr(db, "插入 mem_meta 失败");
        }
        return false;
    }
    const qint64 rowid = (qint64)sqlite3_last_insert_rowid(db);

    sqlite3_stmt* ft = nullptr;
    const char* fsql =
        "INSERT INTO mem_fts(rowid, scope, scope_id, key, content) VALUES(?,?,?,?,?)";
    if (sqlite3_prepare_v2(db, fsql, -1, &ft, nullptr) != SQLITE_OK) {
        if (err) *err = sqliteErr(db, "prepare mem_fts insert 失败");
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        return false;
    }
    sqlite3_bind_int64(ft, 1, (sqlite3_int64)rowid);
    sqlite3_bind_text(ft, 2, scope.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ft, 3, scopeId.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ft, 4, key.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ft, 5, cjkSplit(content).toUtf8().constData(), -1, SQLITE_TRANSIENT);
    const int frc = sqlite3_step(ft);
    sqlite3_finalize(ft);
    if (frc != SQLITE_ROW && frc != SQLITE_DONE) {
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        if (err) *err = sqliteErr(db, "插入 mem_fts 失败");
        return false;
    }
    sqlite3_exec(db, "COMMIT;", nullptr, nullptr, nullptr);
    return true;
}

bool MemoryStore::update(const QString& scope, const QString& scopeId,
                        const QString& key, const QString& content, QString* err) {
    auto* db = static_cast<sqlite3*>(m_db);
    if (!db) { if (err) *err = QStringLiteral("记忆库未打开"); return false; }

    sqlite3_exec(db, "BEGIN IMMEDIATE;", nullptr, nullptr, nullptr);
    // Upsert meta keyed by (scope, key); remember the rowid either way.
    sqlite3_stmt* st = nullptr;
    const char* sql =
        "INSERT INTO mem_meta(scope, scope_id, key, updated_ms) VALUES(?,?,?,?) "
        "ON CONFLICT(scope, key) DO UPDATE SET "
        " scope_id=excluded.scope_id, updated_ms=excluded.updated_ms";
    if (sqlite3_prepare_v2(db, sql, -1, &st, nullptr) != SQLITE_OK) {
        if (err) *err = sqliteErr(db, "prepare mem_meta upsert 失败");
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        return false;
    }
    sqlite3_bind_text(st, 1, scope.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 2, scopeId.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 3, key.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int64(st, 4, (sqlite3_int64)QDateTime::currentMSecsSinceEpoch());
    const int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_ROW && rc != SQLITE_DONE) {
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        if (err) *err = sqliteErr(db, "upsert mem_meta 失败");
        return false;
    }

    sqlite3_stmt* q = nullptr;
    const char* qsql = "SELECT rowid FROM mem_meta WHERE scope=? AND key=?";
    if (sqlite3_prepare_v2(db, qsql, -1, &q, nullptr) != SQLITE_OK) {
        if (err) *err = sqliteErr(db, "prepare mem_meta rowid 查询失败");
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        return false;
    }
    sqlite3_bind_text(q, 1, scope.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(q, 2, key.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    qint64 rowid = -1;
    if (sqlite3_step(q) == SQLITE_ROW) rowid = (qint64)sqlite3_column_int64(q, 0);
    sqlite3_finalize(q);
    if (rowid < 0) {
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        if (err) *err = QStringLiteral("upsert 后未找到行（不应发生）");
        return false;
    }

    // Replace the FTS row with the same rowid (delete-then-insert avoids
    // content= column ambiguity of external-content tables; we keep an ordinary
    // FTS5 table, so a direct rewrite is correct).
    sqlite3_stmt* ds = nullptr;
    if (sqlite3_prepare_v2(db, "DELETE FROM mem_fts WHERE rowid=?", -1, &ds, nullptr) == SQLITE_OK) {
        sqlite3_bind_int64(ds, 1, (sqlite3_int64)rowid);
        sqlite3_step(ds);
        sqlite3_finalize(ds);
    }
    sqlite3_stmt* ft = nullptr;
    const char* fsql =
        "INSERT INTO mem_fts(rowid, scope, scope_id, key, content) VALUES(?,?,?,?,?)";
    if (sqlite3_prepare_v2(db, fsql, -1, &ft, nullptr) != SQLITE_OK) {
        if (err) *err = sqliteErr(db, "prepare mem_fts rewrite 失败");
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        return false;
    }
    sqlite3_bind_int64(ft, 1, (sqlite3_int64)rowid);
    sqlite3_bind_text(ft, 2, scope.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ft, 3, scopeId.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ft, 4, key.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ft, 5, cjkSplit(content).toUtf8().constData(), -1, SQLITE_TRANSIENT);
    const int frc = sqlite3_step(ft);
    sqlite3_finalize(ft);
    if (frc != SQLITE_ROW && frc != SQLITE_DONE) {
        sqlite3_exec(db, "ROLLBACK;", nullptr, nullptr, nullptr);
        if (err) *err = sqliteErr(db, "rewrite mem_fts 失败");
        return false;
    }
    sqlite3_exec(db, "COMMIT;", nullptr, nullptr, nullptr);
    return true;
}

QList<MemoryHit> MemoryStore::search(const QString& query,
                                     const QString& scopeFilter,
                                     int limit, QString* err) {
    QMutexLocker lock(&m_searchGuard);
    QList<MemoryHit> out;
    auto* db = static_cast<sqlite3*>(m_db);
    if (!db) { if (err) *err = QStringLiteral("记忆库未打开"); return out; }

    const QString matchExpr = sanitizeFtsQuery(query);
    if (matchExpr.isEmpty()) {
        if (err) *err = QStringLiteral("查询词为空或不含可检索词元");
        return out;
    }
    int lim = qBound(1, limit <= 0 ? 20 : limit, 100);

    QString sql =
        "SELECT m.scope, m.scope_id, m.key, f.content, "
        "       snippet(mem_fts, 3, '【', '】', '…', 16) "
        "FROM mem_fts f JOIN mem_meta m ON m.rowid = f.rowid "
        "WHERE mem_fts MATCH ?";
    if (!scopeFilter.isEmpty()) sql += QStringLiteral(" AND m.scope=?");
    sql += QStringLiteral(" ORDER BY rank LIMIT ?");

    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(db, sql.toUtf8().constData(), -1, &st, nullptr) != SQLITE_OK) {
        if (err) *err = sqliteErr(db, "prepare search 失败");
        return out;
    }
    int arg = 1;
    sqlite3_bind_text(st, arg++, matchExpr.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    if (!scopeFilter.isEmpty())
        sqlite3_bind_text(st, arg++, scopeFilter.toUtf8().constData(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(st, arg, lim);

    while (sqlite3_step(st) == SQLITE_ROW) {
        MemoryHit h;
        h.scope     = QString::fromUtf8(reinterpret_cast<const char*>(sqlite3_column_text(st, 0)));
        h.scopeId   = QString::fromUtf8(reinterpret_cast<const char*>(sqlite3_column_text(st, 1)));
        h.key       = QString::fromUtf8(reinterpret_cast<const char*>(sqlite3_column_text(st, 2)));
        // FTS content is stored CJK-split for tokenization; rejoin on read-back.
        h.content   = cjkJoin(QString::fromUtf8(reinterpret_cast<const char*>(sqlite3_column_text(st, 3))));
        h.snippet   = cjkJoin(QString::fromUtf8(reinterpret_cast<const char*>(sqlite3_column_text(st, 4))));
        out.append(h);
    }
    sqlite3_finalize(st);
    return out;
}

} // namespace ai
} // namespace mbdsdr
