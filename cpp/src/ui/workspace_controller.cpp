// SPDX-License-Identifier: MIT
#include "workspace_controller.h"

#include "component_registry.h"
#include "workspace_layout.h"

#include <QSplitter>
#include <QTabWidget>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>

namespace aigui {
namespace {

QJsonObject notConnectedError() {
    QJsonObject o;
    o.insert(QStringLiteral("ok"), false);
    o.insert(QStringLiteral("error"),
             QString::fromUtf8("工作台控制器未连接（splitter/rightTabs 为空，"
                              "无法应用布局到主界面）"));
    return o;
}

}  // namespace

WorkspaceController::WorkspaceController(QSplitter* splitter, QTabWidget* rightTabs)
    : splitter_(splitter), rightTabs_(rightTabs), model_(defaultWorkspaceSpec()) {}

WorkspaceController::~WorkspaceController() = default;

QJsonObject WorkspaceController::applyOverlay(const QJsonObject& msg) {
    if (!splitter_ || !rightTabs_) return notConnectedError();

    const QString op = msg.value(QStringLiteral("op")).toString();
    if (op == QLatin1String("deleteSurface")) {
        model_ = applyDeleteSurface();
    } else if (op == QLatin1String("createSurface")) {
        model_ = applyCreateSurface(msg);
    } else {
        // "updateComponents" (default / absent) overlays the current model.
        model_ = applyUpdateComponents(model_, msg);
    }

    const QString note = applyLayoutEngine(splitter_, rightTabs_, model_);

    QJsonObject o;
    o.insert(QStringLiteral("ok"), true);
    o.insert(QStringLiteral("placeholders"),
             QJsonArray::fromStringList(model_.placeholder_ids));
    o.insert(QStringLiteral("note"), note);
    return o;
}

QJsonObject WorkspaceController::currentSpec() {
    return workspaceSpecToJson(model_);
}

QJsonObject WorkspaceController::reset() {
    if (!splitter_ || !rightTabs_) return notConnectedError();

    model_ = applyDeleteSurface();
    const QString note = applyLayoutEngine(splitter_, rightTabs_, model_);

    QJsonObject o;
    o.insert(QStringLiteral("ok"), true);
    o.insert(QStringLiteral("placeholders"),
             QJsonArray::fromStringList(model_.placeholder_ids));
    o.insert(QStringLiteral("note"), note);
    return o;
}

QJsonObject WorkspaceController::componentCatalog() {
    QJsonArray arr;
    for (const ComponentInfo& c : listAll()) {
        QJsonObject co;
        co.insert(QStringLiteral("id"), c.id);
        co.insert(QStringLiteral("category"), c.category);
        QJsonArray caps;
        for (const QString& cap : c.capabilities) caps.append(cap);
        co.insert(QStringLiteral("capabilities"), caps);
        co.insert(QStringLiteral("min_size"), c.min_size);
        arr.append(co);
    }
    QJsonObject o;
    o.insert(QStringLiteral("ok"), true);
    o.insert(QStringLiteral("components"), arr);
    o.insert(QStringLiteral("count"), arr.size());
    return o;
}

}  // namespace aigui
