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
struct SessionMessage {
    QString role;
    QString content;
};

// Lightweight index entry (no message bodies).
struct SessionInfo {
    QString id;
    QString title;
    qint64 updatedAt = 0;   // epoch ms, bumped on every append/rename
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
