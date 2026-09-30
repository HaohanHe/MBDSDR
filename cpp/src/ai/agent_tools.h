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

/// Whether a tool mutates receiver/device state (a "write" action). Read-only
/// tools (get_status and other get_* readers) return false. The write list is
/// maintained alongside toolDefs()/executeTool() in agent_tools.cpp -- when a
/// tool is added there, update the list here in the same change.
bool isWriteTool(const QString& name);

/// Manual-mode gate result for a gated write tool: a JSON string declaring the
/// action was NOT executed, so the model can explain manual mode to the user.
/// Mirrors the Flutter side (mobile/lib/app/ai_tools.dart _gated()).
QString gatedToolResult(const QString& toolName);

} // namespace ai
} // namespace mbdsdr
