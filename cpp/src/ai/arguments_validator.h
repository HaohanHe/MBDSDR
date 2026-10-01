// SPDX-License-Identifier: MIT
// M2: tool arguments validator (pure function, no network, no side effects).
//
// Mirrors the "reject" side of the JSON-Schema capability boundary: an LLM does
// NOT guarantee that generated arguments are valid JSON, and may invent
// parameters the tool schema never declared (MiMo openai-api arguments warning,
// mimo-openai-api.txt:88-89 and :114).  Before a tool call reaches real SDR
// hardware we validate the already-parsed QJsonObject against the tool's
// JSON-Schema `parameters` object.  Any failure yields a compact errorJson that
// the caller hands straight back as a role=tool message so the model can
// self-correct on the next loop turn (M4).
#pragma once

#include <QString>
#include <QStringList>
#include <QJsonObject>

namespace mbdsdr {
namespace ai {

struct ValidationResult {
    bool ok = false;          // true only when every check passes
    QString errorJson;        // compact {"ok":false,...} content when !ok; empty when ok
    QStringList reasons;      // all failure reasons; reasons.first() == error tail
};

// Validate `args` (already JSON-parsed) against `schema` (the tool's parameters
// JSON Schema: {"type":"object","properties":{...},"required":[...]}).
// Checks, in order:
//   1. schema is an object carrying a `properties` object;
//   2. every key listed in `required` exists (a JSON null counts as missing);
//   3. each supplied declared value matches its declared type
//      (number / integer / string / boolean; integer demands an integral number);
//   4. if the property declares `enum`, the value must be one of its members;
//   5. if the property declares minimum/maximum, a numeric value must stay inside;
//   6. any key in `args` NOT declared in `properties` is rejected (hallucinated arg);
//   7. a declared property present as JSON null is treated as "not supplied".
// Never throws, never touches the network, never evals arguments.
ValidationResult validateArguments(const QString& toolName,
                                   const QJsonObject& args,
                                   const QJsonObject& schema);

} // namespace ai
} // namespace mbdsdr
