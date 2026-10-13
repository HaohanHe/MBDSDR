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
    // A2UI v1.0 envelope version marker (gap 1.1.7): every accepted arrange
    // reply self-describes the protocol version it honored.
    o.insert(QStringLiteral("version"), QStringLiteral("v1.0"));
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
    o.insert(QStringLiteral("version"), QStringLiteral("v1.0"));
    o.insert(QStringLiteral("placeholders"),
             QJsonArray::fromStringList(model_.placeholder_ids));
    o.insert(QStringLiteral("note"), note);
    return o;
}

QJsonObject WorkspaceController::updateComponentData(const QString& componentId,
                                                      const QJsonValue& data) {
    if (!splitter_ || !rightTabs_) return notConnectedError();

    QJsonObject o;
    const DataUpdateStatus st = applyUpdateComponentData(model_, componentId, data);
    if (st == DataUpdateStatus::UnknownComponent) {
        o.insert(QStringLiteral("ok"), false);
        o.insert(QStringLiteral("error"),
                 QString::fromUtf8("未知组件 id（不在编译期白名单内，"
                                  "不写入数据槽）：") + componentId);
        return o;
    }
    if (st == DataUpdateStatus::BadValueType) {
        o.insert(QStringLiteral("ok"), false);
        o.insert(QStringLiteral("error"),
                 QString::fromUtf8("data 仅接受 object（upsert）或 null（清空），"
                                  "收到其他类型不做静默转换"));
        return o;
    }
    // Applied: object upserted or null cleared.
    o.insert(QStringLiteral("ok"), true);
    o.insert(QStringLiteral("version"), QStringLiteral("v1.0"));
    o.insert(QStringLiteral("component_id"), componentId);
    if (data.isNull()) {
        o.insert(QStringLiteral("cleared"), true);
        o.insert(QStringLiteral("data"), QJsonValue::Null);
    } else {
        o.insert(QStringLiteral("cleared"), false);
        o.insert(QStringLiteral("data"), data.toObject());
    }
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
