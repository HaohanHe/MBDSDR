// SPDX-License-Identifier: MIT
#include "ai_session_store.h"

#include <QStandardPaths>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QDateTime>
#include <QUuid>

namespace mbdsdr {
namespace ai {

namespace {
QString defaultDir() {
    QByteArray env = qgetenv("MBDSDR_AI_SESSIONS_DIR");
    if (!env.isEmpty()) return QString::fromLocal8Bit(env);
    auto base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    if (base.isEmpty()) base = QDir::tempPath() + "/mbdsdr_ai";
    return base + "/ai_sessions";
}
} // namespace

AiSessionStore::AiSessionStore(const QString& dir, QObject* parent)
    : QObject(parent), dir_(dir.isEmpty() ? defaultDir() : dir) {
    QDir().mkpath(dir_);
    QDir().mkpath(dir_ + "/sessions");
    load();
    if (order_.isEmpty()) {
        // First run: exactly one empty session, no seeded content.
        createSession(QString::fromUtf8("新会话"));
    } else {
        if (!meta_.contains(currentId_) || !order_.contains(currentId_))
            currentId_ = order_.constLast();
    }
}

QString AiSessionStore::makeId() {
    return QUuid::createUuid().toString(QUuid::WithoutBraces);
}

QList<SessionInfo> AiSessionStore::sessions() const {
    QList<SessionInfo> out;
    for (const QString& id : order_)
        if (meta_.contains(id)) out.append(meta_.value(id));
    return out;
}

QString AiSessionStore::createSession(const QString& title) {
    const QString id = makeId();
    SessionInfo info;
    info.id = id;
    info.title = title.isEmpty() ? QString::fromUtf8("新会话") : title;
    info.updatedAt = QDateTime::currentMSecsSinceEpoch();
    meta_.insert(id, info);
    order_.append(id);
    cache_.insert(id, {});   // empty message list
    currentId_ = id;
    saveIndex();
    saveSession(id);
    emit sessionsChanged();
    emit currentSessionChanged();
    return id;
}

void AiSessionStore::setCurrent(const QString& id) {
    if (!meta_.contains(id) || id == currentId_) return;
    currentId_ = id;
    saveIndex();
    emit currentSessionChanged();
}

void AiSessionStore::renameSession(const QString& id, const QString& title) {
    if (!meta_.contains(id) || title.trimmed().isEmpty()) return;
    meta_[id].title = title.trimmed();
    touch(id);
    saveIndex();
    saveSession(id);
    emit sessionsChanged();
}

void AiSessionStore::deleteSession(const QString& id) {
    if (!meta_.contains(id)) return;
    if (order_.size() == 1) {
        // Never allow zero sessions: clear the one instead of deleting it.
        cache_[id] = {};
        meta_[id].title = QString::fromUtf8("新会话");
        touch(id);
        saveIndex();
        saveSession(id);
        emit sessionsChanged();
        emit currentSessionChanged();
        return;
    }
    QFile::remove(sessionPath(id));
    meta_.remove(id);
    cache_.remove(id);
    order_.removeAll(id);
    if (currentId_ == id) currentId_ = order_.constLast();
    saveIndex();
    emit sessionsChanged();
    emit currentSessionChanged();
}

QList<SessionMessage> AiSessionStore::messages(const QString& id) const {
    if (!meta_.contains(id)) return {};
    if (!cache_.contains(id)) {
        QFile f(sessionPath(id));
        QList<SessionMessage> out;
        if (f.open(QIODevice::ReadOnly)) {
            auto doc = QJsonDocument::fromJson(f.readAll());
            for (const auto& v : doc.object()["messages"].toArray()) {
                auto o = v.toObject();
                SessionMessage m;
                m.role = o["role"].toString();
                m.content = o["content"].toString();
                // Backward compatible: a legacy row has no "kind" -> it is just
                // a normal chat line whose kind mirrors its role.
                m.kind = o["kind"].toString();
                if (m.kind.isEmpty()) m.kind = m.role;
                m.ts = o["ts"].toVariant().toLongLong();
                out.append(m);
            }
        }
        const_cast<AiSessionStore*>(this)->cache_.insert(id, out);
    }
    return cache_.value(id);
}

void AiSessionStore::appendMessage(const QString& id, const SessionMessage& m) {
    if (!meta_.contains(id)) return;
    auto& list = cache_[id];
    list.append(m);
    // A brand-new user turn closes whatever streamed reply was left incomplete
    // (the operator moved on); the old half-reply is now superseded honestly.
    if (m.role == QLatin1String("user") && meta_[id].incomplete)
        meta_[id].incomplete = false;
    touch(id);
    saveSession(id);
    saveIndex();
    if (id == currentId_) emit currentSessionChanged();
}

void AiSessionStore::setIncomplete(const QString& id, bool incomplete) {
    if (!meta_.contains(id)) return;
    if (meta_[id].incomplete == incomplete) return;
    meta_[id].incomplete = incomplete;
    saveSession(id);
    saveIndex();
    if (id == currentId_) emit currentSessionChanged();
}

bool AiSessionStore::isIncomplete(const QString& id) const {
    return meta_.value(id).incomplete;
}

void AiSessionStore::setMessages(const QString& id, const QList<SessionMessage>& msgs) {
    if (!meta_.contains(id)) return;
    cache_[id] = msgs;
    touch(id);
    saveSession(id);
    saveIndex();
    if (id == currentId_) emit currentSessionChanged();
}

void AiSessionStore::touch(const QString& id) {
    if (meta_.contains(id))
        meta_[id].updatedAt = QDateTime::currentMSecsSinceEpoch();
}

QString AiSessionStore::sessionPath(const QString& id) const {
    return dir_ + "/sessions/" + id + ".json";
}

void AiSessionStore::saveIndex() {
    QJsonObject obj;
    obj["version"] = kIndexVersion;
    obj["current"] = currentId_;
    QJsonArray arr;
    for (const QString& id : order_) {
        if (!meta_.contains(id)) continue;
        QJsonObject o;
        o["id"] = id;
        o["title"] = meta_[id].title;
        o["updatedAt"] = meta_[id].updatedAt;
        o["incomplete"] = meta_[id].incomplete;
        arr.append(o);
    }
    obj["sessions"] = arr;
    QFile f(dir_ + "/index.json");
    if (f.open(QIODevice::WriteOnly | QIODevice::Truncate))
        f.write(QJsonDocument(obj).toJson());
}

void AiSessionStore::saveSession(const QString& id) {
    if (!meta_.contains(id)) return;
    QJsonObject obj;
    obj["id"] = id;
    obj["title"] = meta_[id].title;
    obj["incomplete"] = meta_[id].incomplete;
    QJsonArray arr;
    for (const auto& m : cache_.value(id)) {
        QJsonObject o;
        o["role"] = m.role;
        o["content"] = m.content;
        // kind falls back to role so a legacy "user"/"assistant"/"summary" row
        // round-trips as itself; ts omitted when unknown (0 = legacy).
        o["kind"] = m.kind.isEmpty() ? m.role : m.kind;
        if (m.ts != 0) o["ts"] = m.ts;
        arr.append(o);
    }
    obj["messages"] = arr;
    QFile f(sessionPath(id));
    if (f.open(QIODevice::WriteOnly | QIODevice::Truncate))
        f.write(QJsonDocument(obj).toJson());
}

void AiSessionStore::load() {
    QFile f(dir_ + "/index.json");
    if (!f.open(QIODevice::ReadOnly)) return;
    auto doc = QJsonDocument::fromJson(f.readAll());
    auto obj = doc.object();
    // Migration hook (multi-session.md §4.1.2): a missing "version" means a
    // pre-Phase31 index; it is read as v1 (defaults applied) -- no error, no data
    // loss. Newer versions would plug a migration here.
    (void)obj["version"].toInt(kIndexVersion);
    currentId_ = obj["current"].toString();
    for (const auto& v : obj["sessions"].toArray()) {
        auto o = v.toObject();
        SessionInfo info;
        info.id = o["id"].toString();
        info.title = o["title"].toString();
        info.updatedAt = o["updatedAt"].toVariant().toLongLong();
        info.incomplete = o["incomplete"].toBool(false);
        if (info.id.isEmpty()) continue;
        meta_.insert(info.id, info);
        order_.append(info.id);
    }
}

} // namespace ai
} // namespace mbdsdr
