// SPDX-License-Identifier: MIT
// Parse an LLM natural-language reply into an executable TaskPlan.
//
// Honest-by-design: we only trust a well-formed JSON plan block embedded in the
// reply.  We never "guess" missing tool names or numeric values.  A reply that
// does not yield a valid, tool-whitelisted step sequence returns ok=false with a
// reason, and the caller falls back to a deterministic template or reports the
// failure -- it does NOT invent steps.
#pragma once

#include "task_orchestrator.h"
#include <QString>
#include <QJsonObject>

namespace mbdsdr {
namespace ai {

struct ParsedPlan {
    TaskPlan plan;
    bool ok = false;          // true only when every step is a known, whitelisted tool
    QString error;            // honest reason when ok==false
};

// Extract the first JSON object from `text` and read its "steps" array:
//   {"steps":[{"tool":"tune_frequency","args":{"freq_hz":100000000},"description":"..."}]}
// Every step's tool MUST be a known executable tool (engine tool or add_bookmark);
// args are passed through verbatim (numeric values are taken literally as Hz --
// the caller / LLM is expected to supply Hz, we do NOT assume MHz and scale).
// Parsing failure, empty steps, or an unknown tool => ok=false (no guessing).
ParsedPlan parsePlanFromLlm(const QString& text);

// Whitelist of tool names the orchestrator may execute (engine tools + bookmark).
bool isExecutableTool(const QString& tool);

} // namespace ai
} // namespace mbdsdr
