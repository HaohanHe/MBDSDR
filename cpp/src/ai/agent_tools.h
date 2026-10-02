// SPDX-License-Identifier: MIT
#pragma once

#include "llm_client.h"
#include <QString>
#include <QList>

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }
namespace ai {

QList<ToolDef> toolDefs();
QString executeTool(const QString& name, const QJsonObject& args,
                    dsp::SpectrumEngine* engine);

/// Whether a tool mutates receiver/device state (a "write" action) and so is
/// gated in manual mode. The classification is read straight from the declarative
/// spec table (registeredToolSpecs, ToolSchemaSpec::write) -- there is no longer a
/// parallel hardcoded write set to keep in sync. Unknown names return false.
bool isWriteTool(const QString& name);

/// The exact set of tool names executeTool() dispatches on (the built-in
/// registry). This is introspected, not hardcoded, so a completeness test can
/// assert it equals registeredToolSpecs() name-for-name -- adding a tool without
/// wiring its executor (or vice versa) now fails the build's tests instead of
/// silently drifting.
QStringList executorToolNames();

/// Manual-mode gate result for a gated write tool: a JSON string declaring the
/// action was NOT executed, so the model can explain manual mode to the user.
/// Mirrors the Flutter side (mobile/lib/app/ai_tools.dart _gated()).
QString gatedToolResult(const QString& toolName);

} // namespace ai
} // namespace mbdsdr
