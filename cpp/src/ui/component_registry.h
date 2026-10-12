// SPDX-License-Identifier: MIT
//
// component_registry.{h,cpp} -- AIGUI P0-a: compile-time whitelist of UI
// components the agent may reference through the A2UI v1.0 message subset.
//
// SECURITY MODEL (see agent-ui-protocol-basis.md §3/§4.3/§8/§9):
//   * The set of valid component ids is fixed at COMPILE TIME. There is no
//     register-from-untrusted-input path. Any id that does not appear in this
//     table MUST render as an honest placeholder (or be rejected by the
//     caller) -- never via eval(), dynamic import(), or loading an arbitrary
//     .ui/.qml string.
//   * Attribute payloads in A2UI messages are treated as untrusted input;
//     this registry only declares *what exists* and its declared capabilities,
//     never executes anything.
//   * This file intentionally does NOT touch data acquisition, hardware, or
//     the three tool channels. It is a pure whitelist + query layer.
//
// The whitelist mirrors the CURRENT three-column main window:
//   splitter (QSplitter, horizontal) -> left_rail | center_spectrum | right_tabs
// right_tabs is a QTabWidget whose tabs are enumerated below with their exact
// in-source labels (main_window.cpp rightTabs_->addTab(...) sites).

#pragma once

#include <QList>
#include <QSet>
#include <QString>
#include <optional>

namespace aigui {

// Static descriptor for one whitelisted component.
struct ComponentInfo {
    QString id;            // canonical, stable id (e.g. "splitter", "tab:CW")
    QString category;      // container | rail | view | tab_container | tab | status
    QSet<QString> capabilities;  // e.g. {"resize"}, {"select","show","hide"}
    int min_size = 0;      // logical base px; soft floor the layout layer clamps to
};

// True iff `id` is in the compile-time whitelist. Unknown ids return false
// (never throws, never falls back to a dynamic lookup).
bool isRegistered(const QString& id);

// Descriptor for `id`, or std::nullopt if not whitelisted.
std::optional<ComponentInfo> lookup(const QString& id);

// All whitelisted ids, in declaration order. Used by the get-side of the
// workspace spec and by tests.
QList<ComponentInfo> listAll();

// Convenience: every "tab:<...>" entry (the right-panel QTabWidget pages).
QList<ComponentInfo> listTabs();

}  // namespace aigui
