// SPDX-License-Identifier: MIT
// Headless MCP JSON-RPC 2.0 line handler implementation.
#include "mcp_server.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>

#include "ai/agent_tools.h"
#include "ai/llm_protocol.h"   // ToolDef (type-only)

namespace mbdsdr {
namespace agentapi {

namespace {

QJsonObject makeResponse(const QJsonValue& id, const QJsonObject& result) {
    QJsonObject o;
    o[QStringLiteral("jsonrpc")] = QStringLiteral("2.0");
    o[QStringLiteral("id")] = id;
    o[QStringLiteral("result")] = result;
    return o;
}

QJsonObject makeError(const QJsonValue& id, int code, const QString& message) {
    QJsonObject e;
    e[QStringLiteral("code")] = code;
    e[QStringLiteral("message")] = message;
    QJsonObject o;
    o[QStringLiteral("jsonrpc")] = QStringLiteral("2.0");
    o[QStringLiteral("id")] = id;
    o[QStringLiteral("error")] = e;
    return o;
}

// Build the tools/list payload straight from the declarative registry. Each
// ToolDef.parameters is already a complete JSON Schema object; we only ensure
// a "required" array exists (empty when the schema omits it) per MCP convention.
QJsonObject listToolsResult() {
    QJsonArray tools;
    for (const ai::ToolDef& def : ai::toolDefs()) {
        QJsonObject schema = def.parameters;
        if (!schema.contains(QStringLiteral("required")))
            schema[QStringLiteral("required")] = QJsonArray();
        QJsonObject t;
        t[QStringLiteral("name")] = def.name;
        t[QStringLiteral("description")] = def.description;
        t[QStringLiteral("inputSchema")] = schema;
        tools.append(t);
    }
    QJsonObject o;
    o[QStringLiteral("tools")] = tools;
    return o;
}

// tools/call dispatch with the write-tool manual gate. Returns the text payload
// that goes inside content[0].text (already a JSON string).
QString callToolText(const QString& name, QJsonObject arguments,
                      dsp::SpectrumEngine* engine, ui::BookmarkManager* bm) {
    if (ai::isWriteTool(name)) {
        // Manual gate: real execution only when the caller explicitly arms it.
        if (arguments.value(QStringLiteral("arm_manual")) != true) {
            return ai::gatedToolResult(name);   // tool NOT executed
        }
        arguments.remove(QStringLiteral("arm_manual"));  // don't leak to executor
    }
    return ai::executeTool(name, arguments, engine, bm);
}

} // namespace

QString handleRequestLine(const QString& line,
                          dsp::SpectrumEngine* engine,
                          ui::BookmarkManager* bm) {
    const QString trimmed = line.trimmed();
    if (trimmed.isEmpty()) return QString();   // blank line: nothing to do

    QJsonParseError pe{};
    QJsonDocument doc = QJsonDocument::fromJson(trimmed.toUtf8(), &pe);
    if (pe.error != QJsonParseError::NoError || !doc.isObject()) {
        // Aligned with Python: log to stderr, do NOT write stdout.
        qWarning("[mcp] 无法解析 JSON: %s", qPrintable(pe.errorString()));
        return QString();
    }

    QJsonObject req = doc.object();
    const QString method = req.value(QStringLiteral("method")).toString();
    const QJsonValue id = req.value(QStringLiteral("id"));
    const bool hasId = req.contains(QStringLiteral("id"));
    QJsonObject params = req.value(QStringLiteral("params")).toObject();

    // Notifications (no id) never get a reply on the protocol stream.
    if (method == QLatin1String("notifications/initialized"))
        return QString();
    if (!hasId)
        return QString();

    if (method == QLatin1String("initialize")) {
        QJsonObject serverInfo;
        serverInfo[QStringLiteral("name")] = QStringLiteral("mbdsdr-core");
        serverInfo[QStringLiteral("version")] = QStringLiteral("0.1.0");
        QJsonObject caps;
        caps[QStringLiteral("tools")] = QJsonObject();
        QJsonObject result;
        result[QStringLiteral("protocolVersion")] = QStringLiteral("2024-11-05");
        result[QStringLiteral("capabilities")] = caps;
        result[QStringLiteral("serverInfo")] = serverInfo;
        return QString::fromUtf8(
            QJsonDocument(makeResponse(id, result)).toJson(QJsonDocument::Compact));
    }

    if (method == QLatin1String("tools/list")) {
        return QString::fromUtf8(
            QJsonDocument(makeResponse(id, listToolsResult())).toJson(QJsonDocument::Compact));
    }

    if (method == QLatin1String("tools/call")) {
        const QString name = params.value(QStringLiteral("name")).toString();
        QJsonObject args = params.value(QStringLiteral("arguments")).toObject();
        const QString text = callToolText(name, args, engine, bm);
        QJsonObject contentItem;
        contentItem[QStringLiteral("type")] = QStringLiteral("text");
        contentItem[QStringLiteral("text")] = text;
        QJsonArray content;
        content.append(contentItem);
        QJsonObject result;
        result[QStringLiteral("content")] = content;
        return QString::fromUtf8(
            QJsonDocument(makeResponse(id, result)).toJson(QJsonDocument::Compact));
    }

    // Unknown method.
    return QString::fromUtf8(
        QJsonDocument(makeError(
            id, -32601,
            QStringLiteral("未知方法: %1").arg(method.isEmpty() ? QStringLiteral("(空)") : method)))
            .toJson(QJsonDocument::Compact));
}

} // namespace agentapi
} // namespace mbdsdr
