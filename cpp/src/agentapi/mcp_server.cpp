// SPDX-License-Identifier: MIT
// Headless MCP JSON-RPC 2.0 line handler implementation.
#include "mcp_server.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QHash>

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
QJsonObject toolEntry(const ai::ToolDef& def) {
    QJsonObject schema = def.parameters;
    if (!schema.contains(QStringLiteral("required")))
        schema[QStringLiteral("required")] = QJsonArray();
    QJsonObject t;
    t[QStringLiteral("name")] = def.name;
    t[QStringLiteral("description")] = def.description;
    t[QStringLiteral("inputSchema")] = schema;
    return t;
}

// ---- Frozen secondary-domain directory (P1 #64, P3 #73, AIGUI P0-b) -------
// 10 domains, FROZEN and shared verbatim with the Python reference
// (mbdsdr_ai/core/mcp_server.py). This is a server-side grouping view ONLY:
// the schema + dispatch single source remains ai::toolDefs() /
// registeredToolSpecs(); we never edit the registry here, we only bucket its
// existing names. Every toolDefs() name appears in exactly one domain.
struct DomainGroup {
    QString name;
    QString summary;
    QList<QString> tools;   // assigned tool names (registry order preserved by bucket)
};

QList<DomainGroup> domainGroups() {
    return QList<DomainGroup>{
        {QStringLiteral("SDR 调谐/VFO"),
         QStringLiteral("接收机调谐、解调模式、带宽/静噪/噪声门限与多 VFO 及频谱显示参数控制。"),
         {QStringLiteral("tune_frequency"), QStringLiteral("set_mode"),
          QStringLiteral("set_bandwidth"), QStringLiteral("set_squelch"),
          QStringLiteral("get_squelch_status"), QStringLiteral("set_noise_blanker"),
          QStringLiteral("get_noise_blanker_status"), QStringLiteral("list_vfos"),
          QStringLiteral("add_vfo"), QStringLiteral("switch_vfo"),
          QStringLiteral("rename_vfo"), QStringLiteral("set_vfo_armed"),
          QStringLiteral("set_vfo_frequency"), QStringLiteral("set_vfo_mode"),
          QStringLiteral("set_vfo_bandwidth"), QStringLiteral("set_fft_params"),
          QStringLiteral("set_color_map"), QStringLiteral("get_spectrum_status"),
          QStringLiteral("set_doppler_compensation")}},
        {QStringLiteral("亚音数字解码"),
         QStringLiteral("CTCSS/DCS 亚音与 POCSAG/M17/VOR/ACARS/NAVTEX/ADS-B/FT8/LRPT 等数字报文解码。"),
         {QStringLiteral("set_ctcss"), QStringLiteral("get_ctcss_status"),
          QStringLiteral("set_cdcss"), QStringLiteral("get_cdcss_status"),
          QStringLiteral("get_pocsag_messages"), QStringLiteral("get_m17_calls"),
          QStringLiteral("get_vor_radial"), QStringLiteral("get_acars_packets"),
          QStringLiteral("get_navtex_messages"), QStringLiteral("get_adsb_aircraft"),
          QStringLiteral("set_ft8"),
          QStringLiteral("get_ft8_status"), QStringLiteral("get_ft8_history"),
          QStringLiteral("set_lrpt"),
          QStringLiteral("get_lrpt_status")}},
        {QStringLiteral("VNA 测量"),
         QStringLiteral("VNA 扫频设置、S 参数原始测量与设备级杂项（电池/频偏/电延迟/平均）。"),
         {QStringLiteral("set_vna_sweep"), QStringLiteral("get_vna_data"),
          QStringLiteral("get_vna_status"), QStringLiteral("vna_device")}},
        {QStringLiteral("VNA 分析"),
         QStringLiteral("对测量数据做谐振/TDR/滤波/等效电路/阻抗匹配等分析。"),
         {QStringLiteral("analyze_vna_resonance"), QStringLiteral("vna_tdr_cable"),
          QStringLiteral("analyze_vna_filter"), QStringLiteral("analyze_vna_equivalent"),
          QStringLiteral("analyze_vna_touchstone"), QStringLiteral("analyze_vna_match")}},
        {QStringLiteral("VNA 校准"),
         QStringLiteral("VNA 校准状态与校准流程控制。"),
         {QStringLiteral("vna_calibration")}},
        {QStringLiteral("导出演制"),
         QStringLiteral("IQ 录制、录制状态与各类快照/报文结果导出。"),
         {QStringLiteral("start_recording"), QStringLiteral("stop_recording"),
          QStringLiteral("export_iq_segment"), QStringLiteral("export_vna_snapshot"),
          QStringLiteral("export_spectrum_snapshot"), QStringLiteral("export_peaks_csv"),
          QStringLiteral("export_ft8_results"), QStringLiteral("list_recordings"),
          QStringLiteral("delete_recording"), QStringLiteral("export_recording"),
          QStringLiteral("get_recording_state")}},
        {QStringLiteral("书签扫描卫星"),
         QStringLiteral("书签管理、波段/链路扫描与卫星过境预报/调谐。"),
         {QStringLiteral("scan_band"), QStringLiteral("predict_passes"),
          QStringLiteral("start_scan_link"), QStringLiteral("stop_scan_link"),
          QStringLiteral("get_scan_link_status"), QStringLiteral("list_bookmarks"),
          QStringLiteral("add_bookmark"), QStringLiteral("tune_to_bookmark"),
          QStringLiteral("delete_bookmark")}},
        {QStringLiteral("系统设备"),
         QStringLiteral("设备连接、频率基准校准、网络音频/音源接入与整体状态。"),
         {QStringLiteral("get_status"), QStringLiteral("calibrate_frequency"),
          QStringLiteral("apply_frequency_correction"), QStringLiteral("set_network_audio_sink"),
          QStringLiteral("get_network_audio_status"), QStringLiteral("connect_network_source"),
          QStringLiteral("get_capabilities")}},
        {QStringLiteral("记忆编排"),
         QStringLiteral("本地持久记忆（SQLite FTS5）的写入、覆盖与全文检索，按 user/agent/session 作用域分层。"),
         {QStringLiteral("memory_add"), QStringLiteral("memory_search"),
          QStringLiteral("memory_update"), QStringLiteral("checkpoint_status"),
          QStringLiteral("memory_flush")}},
        {QStringLiteral("工作台编排"),
         QStringLiteral("AIGUI 三栏工作台布局编排：按编译期白名单调整 splitter 尺寸/右侧 tab 可见性，未知 id 诚实占位，不碰 DSP/音频/硬件。"),
         {QStringLiteral("arrange_workspace"), QStringLiteral("get_workspace_layout"),
          QStringLiteral("list_ui_components"), QStringLiteral("reset_workspace")}},
    };
}

// Reverse lookup: domain name for a tool. Returns "" when the tool is not in
// any group (the tests pin this never happens for ai::toolDefs()).
QString domainForTool(const QString& tool) {
    for (const DomainGroup& g : domainGroups())
        if (g.tools.contains(tool)) return g.name;
    return {};
}

// Resolve a "domain" param to a group: exact first, then trimmed / case-folded,
// then a unique unambiguous substring. Returns nullptr when no group matches,
// setting *legalNames to the sorted unique legal domain names (honest error).
const DomainGroup* resolveDomain(const QString& query, QStringList* legalNames) {
    const QList<DomainGroup> groups = domainGroups();
    for (const DomainGroup& g : groups)
        if (g.name == query) return &g;
    const QString q = query.trimmed().toLower();
    for (const DomainGroup& g : groups)
        if (g.name.toLower() == q) return &g;
    const DomainGroup* hit = nullptr;
    for (const DomainGroup& g : groups) {
        if (g.name.toLower().contains(q) || q.contains(g.name.toLower())) hit = &g;
    }
    if (legalNames) {
        *legalNames = {};
        for (const DomainGroup& g : groups) legalNames->append(g.name);
    }
    return hit;
}

// Build the tools/list result object from params. Returns false (and fills
// *errMessage) on an unknown "domain" / unknown "detail"; otherwise fills *out.
//
// detail (string, optional): "name" | "desc" | "schema".
//   * omitted -> the directory default (name-only cards); `detailed:true` is a
//     kept alias for "schema"; drilling into a single domain without an explicit
//     detail stays at "schema" (backward-compatible rich drill-in).
//   * unknown value -> -32602 listing the legal values.
bool buildToolsListResult(const QJsonObject& params, QJsonObject* out,
                          QString* errMessage) {
    const QList<DomainGroup> groups = domainGroups();
    const bool detailed = params.value(QStringLiteral("detailed")).toBool(false);
    const QJsonValue domVal = params.value(QStringLiteral("domain"));
    const bool hasDomain = domVal.isString() && !domVal.toString().isEmpty();

    // Resolve the granularity level. Explicit "detail" wins; else backward-compat
    // defaults. Legal values pinned for honest error reporting.
    static const QStringList kLevels{QStringLiteral("name"), QStringLiteral("desc"),
                                     QStringLiteral("schema")};
    QString level;
    const QJsonValue detailVal = params.value(QStringLiteral("detail"));
    if (detailVal.isString() && !detailVal.toString().isEmpty()) {
        level = detailVal.toString();
        if (!kLevels.contains(level)) {
            if (errMessage)
                *errMessage = QStringLiteral("未知 detail: %1（合法值: %2）")
                                  .arg(level, kLevels.join(QStringLiteral("、")));
            return false;
        }
    } else {
        level = detailed ? QStringLiteral("schema") : QStringLiteral("name");
    }

    // index tool name -> its full entry (schema single-sourced from toolDefs())
    QHash<QString, QJsonObject> entry;
    for (const ai::ToolDef& def : ai::toolDefs()) entry.insert(def.name, toolEntry(def));

    if (hasDomain) {
        QStringList legal;
        const DomainGroup* g = resolveDomain(domVal.toString(), &legal);
        if (!g) {
            if (errMessage)
                *errMessage = QStringLiteral("未知 domain: %1（合法域: %2）")
                                  .arg(domVal.toString(), legal.join(QStringLiteral("、")));
            return false;
        }
        // Backward-compat: drilling one domain WITHOUT an explicit detail stays
        // at full schema; an explicit detail governs the granularity otherwise.
        const QString effLevel = (detailVal.isString() && !detailVal.toString().isEmpty())
                                     ? level
                                     : QStringLiteral("schema");
        QJsonArray tools;
        for (const QString& tool : g->tools) {
            if (!entry.contains(tool)) continue;
            const QJsonObject defObj = entry.value(tool);
            if (effLevel == QLatin1String("schema")) {
                tools.append(defObj);
            } else if (effLevel == QLatin1String("desc")) {
                QJsonObject o;
                o[QStringLiteral("name")] = defObj.value(QStringLiteral("name"));
                o[QStringLiteral("description")] = defObj.value(QStringLiteral("description"));
                tools.append(o);
            } else {
                tools.append(defObj.value(QStringLiteral("name")));
            }
        }
        (*out)[QStringLiteral("tools")] = tools;
        return true;
    }

    // Directory view (no specific domain): 8 cards. Schema-level goes under
    // "domains" (historic key); name/desc cards under "tools" (historic default).
    QJsonArray doms;
    for (const DomainGroup& g : groups) {
        QJsonArray tools;
        for (const QString& tool : g.tools) {
            if (!entry.contains(tool)) continue;
            const QJsonObject full = entry.value(tool);  // full schema entry
            // Project down to the requested granularity level.
            if (level == QLatin1String("schema")) {
                tools.append(full);
            } else if (level == QLatin1String("desc")) {
                QJsonObject o;
                o[QStringLiteral("name")] = full.value(QStringLiteral("name"));
                o[QStringLiteral("description")] = full.value(QStringLiteral("description"));
                tools.append(o);
            } else {
                tools.append(full.value(QStringLiteral("name")));
            }
        }
        QJsonObject card;
        card[QStringLiteral("domain")] = g.name;
        card[QStringLiteral("summary")] = g.summary;
        card[QStringLiteral("tools")] = tools;
        doms.append(card);
    }
    if (level == QLatin1String("schema"))
        (*out)[QStringLiteral("domains")] = doms;
    else
        (*out)[QStringLiteral("tools")] = doms;
    return true;
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
    // Cache-invalidation notification (normally SERVER->client). The tool table
    // is static today, so the server never emits it; if a client echoes it back
    // with no id we silently ack (no stdout). With an id (misused as a request)
    // we honestly reply result:null rather than -32601.
    if (method == QLatin1String("notifications/tools/list_changed")) {
        if (!hasId) return QString();
        QJsonObject nullResult;
        return QString::fromUtf8(
            QJsonDocument(makeResponse(id, nullResult)).toJson(QJsonDocument::Compact));
    }
    if (!hasId)
        return QString();

    if (method == QLatin1String("initialize")) {
        QJsonObject serverInfo;
        serverInfo[QStringLiteral("name")] = QStringLiteral("mbdsdr-core");
        serverInfo[QStringLiteral("version")] = QStringLiteral("0.1.0");
        // Declare listChanged so MCP clients may cache tools/list and invalidate
        // on this notification. The registry is static now, so no emission fires;
        // when dynamic tool registration lands, the server sends this notification.
        QJsonObject toolsCaps;
        toolsCaps[QStringLiteral("listChanged")] = true;
        QJsonObject caps;
        caps[QStringLiteral("tools")] = toolsCaps;
        QJsonObject result;
        result[QStringLiteral("protocolVersion")] = QStringLiteral("2024-11-05");
        result[QStringLiteral("capabilities")] = caps;
        result[QStringLiteral("serverInfo")] = serverInfo;
        return QString::fromUtf8(
            QJsonDocument(makeResponse(id, result)).toJson(QJsonDocument::Compact));
    }

    if (method == QLatin1String("tools/list")) {
        QJsonObject result;
        QString domErr;
        if (!buildToolsListResult(params, &result, &domErr)) {
            // Unknown domain -> honest JSON-RPC error listing the legal names.
            return QString::fromUtf8(
                QJsonDocument(makeError(id, -32602, domErr)).toJson(QJsonDocument::Compact));
        }
        return QString::fromUtf8(
            QJsonDocument(makeResponse(id, result)).toJson(QJsonDocument::Compact));
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
