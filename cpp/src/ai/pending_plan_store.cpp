// SPDX-License-Identifier: MIT
#include "pending_plan_store.h"

#include "core/tokens.h"

#include <QJsonArray>
#include <QMutexLocker>

#include <algorithm>

namespace mbdsdr {
namespace ai {

QString pendingPlanStateName(PendingPlanState s) {
    switch (s) {
    case PendingPlanState::Pending:   return QStringLiteral("pending");
    case PendingPlanState::Running:   return QStringLiteral("running");
    case PendingPlanState::Succeeded: return QStringLiteral("succeeded");
    case PendingPlanState::Failed:    return QStringLiteral("failed");
    case PendingPlanState::Aborted:   return QStringLiteral("aborted");
    case PendingPlanState::Gated:     return QStringLiteral("gated");
    case PendingPlanState::Partial:   return QStringLiteral("partial");
    }
    return QStringLiteral("pending");
}

PendingPlanState pendingPlanStateFromName(const QString& name) {
    if (name == QLatin1String("running"))   return PendingPlanState::Running;
    if (name == QLatin1String("succeeded")) return PendingPlanState::Succeeded;
    if (name == QLatin1String("failed"))    return PendingPlanState::Failed;
    if (name == QLatin1String("aborted"))   return PendingPlanState::Aborted;
    if (name == QLatin1String("gated"))     return PendingPlanState::Gated;
    if (name == QLatin1String("partial"))   return PendingPlanState::Partial;
    return PendingPlanState::Pending;
}

PendingPlanStore& PendingPlanStore::instance() {
    static PendingPlanStore store;
    return store;
}

int PendingPlanStore::submit(const TaskPlan& plan, const QString& templateName) {
    QMutexLocker lock(&mutex_);
    PendingPlanEntry e;
    e.id = nextId_++;
    e.templateName = templateName;
    e.plan = plan;
    e.state = PendingPlanState::Pending;
    entries_.append(e);

    // 容量上限：只淘汰"已结束"的旧记录，Pending 的计划绝不丢（还没人消费）。
    while (entries_.size() > tokens::kPendingPlanMaxEntries) {
        int drop = -1;
        for (int i = 0; i < entries_.size(); ++i) {
            if (entries_.at(i).state != PendingPlanState::Pending) { drop = i; break; }
        }
        if (drop < 0) break;   // 全是 Pending：宁可超限也不丢计划
        entries_.removeAt(drop);
    }
    return e.id;
}

bool PendingPlanStore::take(int id, PendingPlanEntry* out) {
    QMutexLocker lock(&mutex_);
    for (PendingPlanEntry& e : entries_) {
        if (e.id != id) continue;
        if (e.state != PendingPlanState::Pending) return false;   // 只可消费一次
        e.state = PendingPlanState::Running;
        if (out) *out = e;
        return true;
    }
    return false;
}

bool PendingPlanStore::finish(int id, PendingPlanState state, const QString& report) {
    QMutexLocker lock(&mutex_);
    for (PendingPlanEntry& e : entries_) {
        if (e.id != id) continue;
        if (e.state != PendingPlanState::Running) return false;   // 不覆盖历史
        e.state = state;
        e.report = report;
        return true;
    }
    return false;
}

int PendingPlanStore::oldestPendingId() const {
    QMutexLocker lock(&mutex_);
    for (const PendingPlanEntry& e : entries_)   // entries_ 按登记序，最早在前
        if (e.state == PendingPlanState::Pending) return e.id;
    return 0;
}

QList<PendingPlanEntry> PendingPlanStore::list() const {
    QMutexLocker lock(&mutex_);
    QList<PendingPlanEntry> out = entries_;
    std::reverse(out.begin(), out.end());   // 新→旧
    return out;
}

QJsonObject PendingPlanStore::toJson() const {
    QJsonArray arr;
    for (const PendingPlanEntry& e : list()) {
        QJsonObject p;
        p["id"] = e.id;
        p["template_name"] = e.templateName;
        p["plan_name"] = e.plan.name;
        p["step_count"] = e.plan.steps.size();
        p["state"] = pendingPlanStateName(e.state);
        if (!e.report.isEmpty()) p["report"] = e.report;
        arr.append(p);
    }
    QJsonObject o;
    o["plans"] = arr;
    o["count"] = arr.size();
    return o;
}

int PendingPlanStore::pendingCount() const {
    QMutexLocker lock(&mutex_);
    int n = 0;
    for (const PendingPlanEntry& e : entries_)
        if (e.state == PendingPlanState::Pending) ++n;
    return n;
}

void PendingPlanStore::clear() {
    QMutexLocker lock(&mutex_);
    entries_.clear();
    nextId_ = 1;
}

} // namespace ai
} // namespace mbdsdr
