// SPDX-License-Identifier: MIT
#include "component_registry.h"

#include <array>

namespace aigui {
namespace {

// The whitelist. This is the ONLY source of truth for which component ids the
// agent may address. It is a static table built on first use (Meyers
// singleton, no static-init order fiasco); nothing here is populated from
// runtime/untrusted input.
//
// The right-panel tab ids use the exact labels passed to rightTabs_->addTab()
// in main_window.cpp so that a tab reference resolves 1:1 to a real page.
// (Source of truth: cpp/src/ui/main_window.cpp, rightTabs_->addTab(...) calls.)
const std::array<ComponentInfo, 23>& table() {
    static const std::array<ComponentInfo, 23> t = {{
        // ---- structural containers (the current three-column layout) ----
        {QStringLiteral("splitter"),        QStringLiteral("container"),
            {QStringLiteral("resize")}, 120},
        {QStringLiteral("left_rail"),      QStringLiteral("rail"),
            {QStringLiteral("resize")}, 120},
        {QStringLiteral("center_spectrum"), QStringLiteral("view"),
            {QStringLiteral("resize")}, 320},
        {QStringLiteral("right_tabs"),      QStringLiteral("tab_container"),
            {QStringLiteral("resize"), QStringLiteral("select"),
             QStringLiteral("show"),   QStringLiteral("hide")}, 160},
        {QStringLiteral("status_bar"),      QStringLiteral("status"),
            {}, 0},

        // ---- right-panel QTabWidget pages (labels match main_window.cpp) ----
        {QStringLiteral("tab:CW"),          QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:ADS-B"),       QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:FT8"),         QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:寻呼"),       QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:数据"),       QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:m17"),         QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:VOR"),        QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:星座"),       QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:天空"),       QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:扫描/书签"), QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:录制库"),   QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:AI 助手"),  QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:电台"),       QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},
        {QStringLiteral("tab:VNA"),         QStringLiteral("tab"),
            {QStringLiteral("select"), QStringLiteral("show"), QStringLiteral("hide")}, 0},

        // The center stack also hosts a few secondary views the agent may
        // point at; they are real widgets in main_window.cpp but are NOT
        // right-panel tabs. Registered as views for completeness.
        {QStringLiteral("center:频谱"),   QStringLiteral("view"), {}, 0},
        {QStringLiteral("center:世界"),   QStringLiteral("view"), {}, 0},
        {QStringLiteral("center:气象"),   QStringLiteral("view"), {}, 0},
        {QStringLiteral("center:时空视图"), QStringLiteral("view"), {}, 0},
    }};
    return t;
}

}  // namespace

bool isRegistered(const QString& id) {
    for (const auto& e : table()) {
        if (e.id == id) return true;
    }
    return false;
}

std::optional<ComponentInfo> lookup(const QString& id) {
    for (const auto& e : table()) {
        if (e.id == id) return e;
    }
    return std::nullopt;
}

QList<ComponentInfo> listAll() {
    QList<ComponentInfo> out;
    for (const auto& e : table()) out.push_back(e);
    return out;
}

QList<ComponentInfo> listTabs() {
    QList<ComponentInfo> out;
    for (const auto& e : table()) {
        if (e.category == QLatin1String("tab")) out.push_back(e);
    }
    return out;
}

}  // namespace aigui
