// SPDX-License-Identifier: MIT
#include "plan_parser.h"
#include "agent_tools.h"

#include <QJsonDocument>
#include <QJsonArray>

namespace mbdsdr {
namespace ai {

bool isExecutableTool(const QString& tool) {
    if (tool == QLatin1String("add_bookmark")) return true;   // orchestrator step
    if (tool == QLatin1String("get_status"))  return true;    // read tool
    return isWriteTool(tool);                                 // write tools whitelist
}

ParsedPlan parsePlanFromLlm(const QString& text) {
    ParsedPlan out;
    // Locate the first '{' ... matching last '}' that yields a JSON object.  The
    // LLM usually wraps the plan in ```json fences or prose; we take the largest
    // top-level object substring.
    const int open = text.indexOf(QLatin1Char('{'));
    if (open < 0) { out.error = QString::fromUtf8("回复中没有 JSON 计划块"); return out; }
    int close = text.lastIndexOf(QLatin1Char('}'));
    if (close <= open) { out.error = QString::fromUtf8("JSON 计划块不完整"); return out; }

    const QByteArray blob = text.mid(open, close - open + 1).toUtf8();
    QJsonDocument doc = QJsonDocument::fromJson(blob);
    if (!doc.isObject()) { out.error = QString::fromUtf8("JSON 计划块不是对象"); return out; }
    const QJsonObject root = doc.object();
    const QJsonArray steps = root.value(QLatin1String("steps")).toArray();
    if (steps.isEmpty()) { out.error = QString::fromUtf8("计划为空（steps 缺失或为空）"); return out; }

    TaskPlan plan;
    plan.name = root.value(QLatin1String("name")).toString(
                        QString::fromUtf8("LLM 规划任务"));
    int idx = 0;
    for (const auto& sv : steps) {
        ++idx;
        const QJsonObject so = sv.toObject();
        const QString tool = so.value(QLatin1String("tool")).toString();
        if (!isExecutableTool(tool)) {
            out.error = QString::fromUtf8("第 %1 步含未知工具「%2」，拒绝执行").arg(idx).arg(tool);
            return out;
        }
        TaskStep s;
        s.tool = tool;
        s.args = so.value(QLatin1String("args")).toObject();
        s.description = so.value(QLatin1String("description")).toString(tool);
        plan.steps.append(s);
    }
    out.plan = plan;
    out.ok = true;
    return out;
}

} // namespace ai
} // namespace mbdsdr
