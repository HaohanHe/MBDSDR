// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <QList>
#include <QJsonObject>
#include <QVariantList>

namespace mbdsdr {
namespace ai {

// The on-wire ToolDef shape lives in ai/llm_client.h (M4 lands the full
// ChatMessage/ToolDef contract). A forward declaration is enough here so this
// header stays light and does not pull QtNetwork into every includer.
struct ToolDef;

// One declarative parameter slot. Numeric bounds and enum are OPTIONAL and are
// emitted into the JSON Schema only when set -- this keeps the generated
// schema to the subset both OpenAI-compatible and MiMo backends accept
// (see docs/learn/model-tool-calling/09-tool-schema-and-validation.md).
struct ToolParamSpec {
    QString name;
    QString type;          // JSON Schema type: "number" | "string" | ...
    QString description;   // model-facing; empty => the key is omitted
    double min = 0.0;
    double max = 0.0;
    bool hasMin = false;
    bool hasMax = false;
    QVariantList enumValues; // non-empty => emitted as "enum"
    bool required = false;
};

struct ToolSchemaSpec {
    QString name;
    QString description;
    QList<ToolParamSpec> params;
};

// Render a spec to the tool-parameters object both backends expect:
//   {"type":"object","properties":{...},"required":[...]}
// number slots gain minimum/maximum; slots carrying enumValues gain "enum".
QJsonObject buildToolSchema(const ToolSchemaSpec& spec);

// Build the on-wire ToolDef list from declarative specs. name/description pass
// through verbatim so UI copy and existing expectations never drift.
QList<ToolDef> toolDefsFromSpecs(const QList<ToolSchemaSpec>& specs);

// The 7 SDR tools, the single source of truth for the declarative schema side.
// Every numeric boundary is pulled from core/tokens.h / core/bandwidth_preset.h
// -- no magic numbers here.
QList<ToolSchemaSpec> registeredToolSpecs();

} // namespace ai
} // namespace mbdsdr
