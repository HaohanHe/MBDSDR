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
    touch(id);
    saveSession(id);
    saveIndex();
    if (id == currentId_) emit currentSessionChanged();
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
    obj["current"] = currentId_;
    QJsonArray arr;
    for (const QString& id : order_) {
        if (!meta_.contains(id)) continue;
        QJsonObject o;
        o["id"] = id;
        o["title"] = meta_[id].title;
        o["updatedAt"] = meta_[id].updatedAt;
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
    QJsonArray arr;
    for (const auto& m : cache_.value(id)) {
        QJsonObject o;
        o["role"] = m.role;
        o["content"] = m.content;
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
    currentId_ = obj["current"].toString();
    for (const auto& v : obj["sessions"].toArray()) {
        auto o = v.toObject();
        SessionInfo info;
        info.id = o["id"].toString();
        info.title = o["title"].toString();
        info.updatedAt = o["updatedAt"].toVariant().toLongLong();
        if (info.id.isEmpty()) continue;
        meta_.insert(info.id, info);
        order_.append(info.id);
    }
}

} // namespace ai
} // namespace mbdsdr
