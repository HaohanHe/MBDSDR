// SPDX-License-Identifier: MIT
#pragma once

#include <QObject>
#include <QString>
#include <QList>
#include <QMap>

namespace mbdsdr {
namespace ai {

// One chat line as persisted / rendered. role is one of:
//   "user"       -- the operator's message (rendered "You: ...")
//   "assistant"  -- the LLM / local-command reply (rendered "AI: ...")
//   "summary"    -- a context-compaction marker (rendered restrained as
//                   "〔已摘要〕 ..."). Never sent verbatim to the wire; the
//                   LLM layer maps it onto a system note before the request.
//
// Phase31 Wave2: `kind` separates ordinary chat lines from tool-call / tool-
// result / error events so the UI can render them distinctly (multi-session.md
// §4.1.1). `ts` is epoch-ms when the line was recorded. Both are BACKWARD
// COMPATIBLE: a legacy JSON row without them loads with kind == role and ts==0.
struct SessionMessage {
    QString role;
    QString content;
    QString kind;        // "chat"|"tool_call"|"tool_result"|"error"; empty on load => role
    qint64 ts = 0;       // epoch ms; 0 = legacy/unknown (never invented)
};

// Lightweight index entry (no message bodies).
struct SessionInfo {
    QString id;
    QString title;
    qint64 updatedAt = 0;   // epoch ms, bumped on every append/rename
    bool incomplete = false; // last streamed reply was cut off mid-write (crash)
};

// On-disk layout (injectable directory so tests use a temp dir):
//   <dir>/index.json                 -- { "current": id, "sessions": [ ... ] }
//   <dir>/sessions/<id>.json         -- { id, title, messages: [ {role,content} ] }
//
// On first run with an empty/missing directory exactly ONE empty session is
// created (title "新会话") and selected -- no seeded / fake conversation.
class AiSessionStore : public QObject {
    Q_OBJECT
public:
    // dir empty => default under QStandardPaths::AppDataLocation/ai_sessions.
    // The env override MBDSDR_AI_SESSIONS_DIR wins, so tests/screenshots can
    // point at a throwaway dir without touching the user's data.
    explicit AiSessionStore(const QString& dir = QString(), QObject* parent = nullptr);

    QString directory() const { return dir_; }

    // Index access (oldest first; the most recently used sorts last on bump).
    QList<SessionInfo> sessions() const;
    QString currentId() const { return currentId_; }

    // CRUD -----------------------------------------------------------------
    // Creates a new session, selects it, persists. Returns its id.
    QString createSession(const QString& title = QString());
    void    setCurrent(const QString& id);
    void    renameSession(const QString& id, const QString& title);
    void    deleteSession(const QString& id);   // never leaves zero sessions

    // Messages -------------------------------------------------------------
    QList<SessionMessage> messages(const QString& id) const;
    void appendMessage(const QString& id, const SessionMessage& m);
    // Replace the whole message list of a session (used after context
    // compaction so the persisted record matches what was sent to the LLM).
    void setMessages(const QString& id, const QList<SessionMessage>& msgs);

    // Incomplete (cut-off) marker, multi-session.md §4.1.3: a streamed assistant
    // reply written half-way then crashed leaves a half line on disk. The caller
    // (streaming UI) calls setIncomplete(id,true) when a reply starts streaming
    // and setIncomplete(id,false) when it completes; a brand-new user message
    // clears it automatically. On load a true flag survives so the UI can honestly
    // badge the last line 〔上次未完成〕 instead of showing a half reply as final.
    void setIncomplete(const QString& id, bool incomplete);
    bool isIncomplete(const QString& id) const;
    // On-disk schema version written into index.json. Old files without it load
    // as v1 (the migration hook is: missing version == assume v1, apply defaults).
    static constexpr int kIndexVersion = 1;

signals:
    // The session index changed (create/rename/delete/select) -- refresh the
    // dropdown + current chat.
    void sessionsChanged();
    // The currently-selected session's message list changed -- re-render it.
    void currentSessionChanged();

private:
    void load();
    void saveIndex();
    void saveSession(const QString& id);
    QString sessionPath(const QString& id) const;
    static QString makeId();
    void touch(const QString& id);

    QString dir_;
    QMap<QString, SessionInfo> meta_;     // id -> info
    QStringList order_;                   // id order (oldest first)
    QString currentId_;
    mutable QMap<QString, QList<SessionMessage>> cache_;  // lazily loaded
};

} // namespace ai
} // namespace mbdsdr
