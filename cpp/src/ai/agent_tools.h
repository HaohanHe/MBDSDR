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

} // namespace ai
} // namespace mbdsdr
