// SPDX-License-Identifier: MIT
//
// workspace_controller.{h,cpp} -- AIGUI P0-b: the live Application layer that
// binds the P0-a pure model (WorkspaceLayout / LayoutEngine) to the real
// MainWindow QSplitter + right QTabWidget.
//
// This is the seam the three tool channels (Agent executor, ControlHub mirror,
// MCP domain) call into. It deliberately:
//   * never touches DSP / audio / hardware -- it only resizes the splitter and
//     shows/hides right-panel tabs,
//   * resolves component ids ONLY against the compile-time component_registry
//     (unknown ids become honest placeholders, never rendered),
//   * treats every incoming A2UI message as untrusted input: numeric sizes are
//     clamped by the pure layer, enums whitelisted, missing fields fall back to
//     the current model rather than crashing.
//
// NULL DEFENSE: if the splitter or the right QTabWidget passed to the ctor is
// null (headless unit test / not-yet-wired MainWindow), every mutating call
// returns an honest {ok:false, error:...} JSON envelope instead of crashing.

#pragma once

#include <QJsonObject>

#include "workspace_layout.h"   // WorkspaceLayout (by-value model member)

class QSplitter;
class QTabWidget;

namespace aigui {

class WorkspaceController {
public:
    // Binds the controller to the live widgets. The pointers are observing only
    // (MainWindow owns both). Either may be null in which case mutating calls
    // return honest errors; the get-side state still serializes.
    explicit WorkspaceController(QSplitter* splitter, QTabWidget* rightTabs);
    ~WorkspaceController();

    WorkspaceController(const WorkspaceController&) = delete;
    WorkspaceController& operator=(const WorkspaceController&) = delete;

    // Apply an A2UI v1.0 subset message to the live workspace. `msg.op` selects
    // the pure-layer transform:
    //   "createSurface"  -> model_ = applyCreateSurface(msg)
    //   "deleteSurface"  -> model_ = applyDeleteSurface()
    //   "updateComponents" / absent -> model_ = applyUpdateComponents(model_, msg)
    // After the model updates, applyLayoutEngine() pushes it to the real
    // splitter/tabs. Returns:
    //   {ok:true, placeholders:[...], note:"..."}
    //   {ok:false, error:"工作台控制器未连接..."} when splitter/tabs is null.
    QJsonObject applyOverlay(const QJsonObject& msg);

    // Serialize the current model (workspaceSpecToJson). Honest even when the
    // widgets are not connected -- this is a state read, not a widget touch.
    QJsonObject currentSpec();

    // Teardown: reset to the default three-column spec (applyDeleteSurface) and
    // push it to the widgets. Mirrors the A2UI deleteSurface semantic.
    QJsonObject reset();

    // The compile-time whitelist as a JSON catalog: [{id, category,
    // capabilities:[...], min_size}, ...]. Read-only introspection so the agent
    // can discover what it is allowed to reference.
    QJsonObject componentCatalog();

private:
    QSplitter*  splitter_ = nullptr;
    QTabWidget* rightTabs_ = nullptr;
    WorkspaceLayout model_;   // current state (initialized to the default spec)
};

}  // namespace aigui
