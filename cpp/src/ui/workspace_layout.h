// SPDX-License-Identifier: MIT
//
// workspace_layout.{h,cpp} -- AIGUI P0-a: workspace layout MODEL + pure-function
// message layer + a thin Qt application layer (LayoutEngine).
//
// This implements the A2UI v1.0 SUBSET described in agent-ui-protocol-basis.md
// §3/§9. It deliberately does NOT wire up the live MainWindow (that is a later
// batch) and does NOT touch data acquisition / hardware / the three tool
// channels. It is:
//
//   * a plain data model (WorkspaceLayout),
//   * pure functions that translate A2UI messages into that model
//     (createSurface / updateComponents / deleteSurface / workspaceOverlay),
//   * a serializer (workspaceSpecToJson) for the get side,
//   * a Qt LayoutEngine that *applies* a WorkspaceLayout to a real QSplitter +
//     QTabWidget WITHOUT inventing widgets for unknown ids (those become
//     honest placeholders, listed in the returned note).
//
// SECURITY:
//   * Component ids are resolved ONLY against the compile-time
//     component_registry. Unknown ids go to placeholder_ids -- never rendered
//     as a real widget, never eval()'d, never loaded from a .ui string.
//   * Props in the message are treated as untrusted input: numeric sizes are
//     clamped to a sane floor, enums are whitelisted, missing fields fall back
//     to the default spec rather than crashing.
//   * deleteSurface explicitly resets to the default (current three-column)
//     spec; there is no "remember the previous script" escape hatch.

#pragma once

#include <QJsonObject>
#include <QJsonValue>
#include <QString>
#include <QStringList>

#include <array>
#include <optional>

class QSplitter;
class QTabWidget;

namespace aigui {

// Soft floor (logical base px) for any splitter pane after clamping. Mirrors
// the task spec: kMinPanel = 120. Panes smaller than this are clamped up so
// the rail never collapses to zero-width by an adversarial/typo'd message.
inline constexpr int kMinPanel = 120;

// The default three-column spec, matching cpp/src/ui/main_window.cpp
// (splitter->setStretchFactor(0/1/2, 1/3/1); setSizes({280,800,280})).
inline constexpr int kDefaultSizeL = 280;
inline constexpr int kDefaultSizeM = 800;
inline constexpr int kDefaultSizeR = 280;

// Pure data model of the workspace. No Qt widgets live here; this serializes
// to/from JSON and is the single thing LayoutEngine applies.
struct WorkspaceLayout {
    std::array<int, 3> splitter_sizes = {kDefaultSizeL, kDefaultSizeM, kDefaultSizeR};
    std::array<int, 3> splitter_stretch = {1, 3, 1};

    // Right-panel tab bookkeeping.
    QStringList visible_tabs;    // tab ids the overlay asked to keep visible
    QString current_tab;         // tab:<name> to select, or empty for default
    QStringList hidden_panels;   // tab ids the overlay explicitly hid

    // Honest empty state: any component id referenced in the message that is
    // NOT in the compile-time whitelist, or a child id that was referenced but
    // never defined. LayoutEngine does NOT synthesize a widget for these; the
    // get-side JSON lists them under "placeholders".
    QStringList placeholder_ids;

    // Per-component data-model slot (A2UI v1.0 updateDataModel MINIMAL subset).
    // Maps a whitelisted component id -> an arbitrary JSON object payload. The
    // layout layer does NOT interpret the payload (no @path binding, no DSP
    // wiring); it only stores, serializes, and lets the agent upsert/clear it.
    // value=null clears (removes) the slot; an object upserts (whole-replaces)
    // it. This is the in-memory data model the panel can later bind to.
    QJsonObject component_data;
};

// ---- Pure functions (no Qt widget dependency; safe to unit test headless) ----

// The default spec: exactly the current three-column main window, all known
// right-panel tabs visible, current_tab empty (MainWindow picks its own).
WorkspaceLayout defaultWorkspaceSpec();

// createSurface: build a fresh WorkspaceLayout from a createSurface message.
// An empty/invalid message yields the default spec. Unknown ids are collected
// into placeholder_ids rather than throwing.
WorkspaceLayout applyCreateSurface(const QJsonObject& msg);

// updateComponents: overlay `base` with an updateComponents message and
// return the merged result. Unknown ids are collected into placeholder_ids;
// sizes are clamped; no previously-unknown tab becomes visible unless the
// message explicitly lists it.
WorkspaceLayout applyUpdateComponents(const WorkspaceLayout& base, const QJsonObject& msg);

// deleteSurface: A2UI deleteSurface explicitly tears the surface down and
// returns us to the default spec.
WorkspaceLayout applyDeleteSurface();

// One-shot parser shared by createSurface/updateComponents: a flat adjacency
// list of nodes (root hangs off "surface"). Each node:
//   {"id":..., "type":..., "props":{...}, "children":[...]}
// Unknown ids / unresolved child references -> placeholder_ids.
WorkspaceLayout applyWorkspaceOverlay(const QJsonObject& msg);

// Serialize a WorkspaceLayout back to JSON (get side / diagnostics).
QJsonObject workspaceSpecToJson(const WorkspaceLayout& m);

// Inverse of workspaceSpecToJson: parse the flat model JSON produced by the
// serializer back into a WorkspaceLayout. Used by the round-trip invariant
// test and by the get-side cache restore. Unknown/garbage fields fall back to
// the default spec rather than throwing.
WorkspaceLayout workspaceSpecFromJson(const QJsonObject& o);

// Result of an updateDataModel minimal upsert/clear on the in-memory data
// slot. Kept out of the Qt envelope so the pure layer stays testable headless.
enum class DataUpdateStatus {
    Applied,           // object upserted, or null cleared the slot
    UnknownComponent,  // componentId not in the compile-time whitelist
    BadValueType,      // value was neither object nor null
};

// updateDataModel minimal subset: upsert (value is an object -> whole-replace
// the slot) or clear (value is null -> remove the key) for one component.
// Unknown ids are REJECTED (not stored, not rendered) -- same whitelist gate as
// layout nodes. The layout layer never interprets the payload; it only stores
// it. `m` is mutated in place.
DataUpdateStatus applyUpdateComponentData(WorkspaceLayout& m,
                                          const QString& componentId,
                                          const QJsonValue& value);

// ---- Qt application layer (thin; does NOT touch hardware/DP) ----

// Applies `layout` to a real horizontal QSplitter + right QTabWidget.
//   * setSizes / setStretchFactor on the splitter
//   * QTabBar::setTabVisible for each known tab id, setCurrentIndex for
//     current_tab if it resolves
// Unknown tab ids are NOT rendered (no widget is invented) -- they are
// listed in the returned note. LayoutEngine never calls into DSP, audio, or
// the three tool channels.
QString applyLayoutEngine(QSplitter* splitter, QTabWidget* rightTabs,
                          const WorkspaceLayout& layout);

}  // namespace aigui
