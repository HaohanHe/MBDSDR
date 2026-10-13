// SPDX-License-Identifier: MIT
#include "workspace_layout.h"

#include "component_registry.h"

#include <QSplitter>
#include <QTabBar>
#include <QTabWidget>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>

#include <algorithm>

namespace aigui {
namespace {

int clampSize(int v) {
    if (v < kMinPanel) return kMinPanel;
    return v;
}

// Read a 3-element int array out of `obj`[key]; fall back to `fb` on any
// shape mismatch. This is the untrusted-input boundary: no throws, no NaN
// propagation.
std::array<int, 3> readTriple(const QJsonObject& obj, const char* key,
                              const std::array<int, 3>& fb) {
    std::array<int, 3> out = fb;
    const QJsonValue v = obj.value(QLatin1String(key));
    if (!v.isArray()) return out;
    const QJsonArray a = v.toArray();
    if (a.size() != 3) return out;
    for (int i = 0; i < 3; ++i) {
        if (a.at(i).isDouble()) out[i] = a.at(i).toInt(out[i]);
    }
    return out;
}

// Parse the component list from an overlay message. Fills `out` in place.
// Unknown ids / unresolved child references are appended to out.placeholder_ids
// (de-duplicated).
void parseComponents(const QJsonObject& msg, WorkspaceLayout& out) {
    const QJsonValue comps = msg.value(QStringLiteral("components"));
    if (!comps.isArray()) return;

    // Index nodes by id so we can resolve children.
    QJsonObject byId;
    for (const QJsonValue& cv : comps.toArray()) {
        if (!cv.isObject()) continue;
        const QJsonObject o = cv.toObject();
        const QString id = o.value(QStringLiteral("id")).toString();
        if (id.isEmpty()) continue;
        byId.insert(id, o);
    }

    auto markPlaceholder = [&out](const QString& id) {
        if (!out.placeholder_ids.contains(id)) out.placeholder_ids.push_back(id);
    };

    for (const QJsonValue& cv : comps.toArray()) {
        if (!cv.isObject()) continue;
        const QJsonObject o = cv.toObject();
        const QString id = o.value(QStringLiteral("id")).toString();
        if (id.isEmpty()) continue;

        // Whitelist gate.
        if (!isRegistered(id)) {
            markPlaceholder(id);
            continue;
        }

        const QJsonObject props = o.value(QStringLiteral("props")).toObject();

        if (id == QLatin1String("splitter")) {
            auto sizes = readTriple(props, "sizes", out.splitter_sizes);
            auto stretch = readTriple(props, "stretch", out.splitter_stretch);
            for (int i = 0; i < 3; ++i) {
                sizes[i] = clampSize(sizes[i]);
                if (stretch[i] < 0) stretch[i] = 0;
            }
            out.splitter_sizes = sizes;
            out.splitter_stretch = stretch;
            continue;
        }

        if (id == QLatin1String("right_tabs")) {
            const QString cur = props.value(QStringLiteral("current")).toString();
            if (!cur.isEmpty()) {
                if (isRegistered(cur)) out.current_tab = cur;
                else markPlaceholder(cur);
            }
            // Walk children (flat adjacency list).
            const QJsonValue kids = o.value(QStringLiteral("children"));
            if (kids.isArray()) {
                for (const QJsonValue& kv : kids.toArray()) {
                    const QString kid = kv.toString();
                    if (kid.isEmpty()) continue;
                    if (!isRegistered(kid)) { markPlaceholder(kid); continue; }
                    // Per-node visible flag (default true).
                    bool visible = true;
                    if (byId.contains(kid)) {
                        const QJsonObject kidProps =
                            byId.value(kid).toObject().value(QStringLiteral("props")).toObject();
                        if (kidProps.contains(QStringLiteral("visible")))
                            visible = kidProps.value(QStringLiteral("visible")).toBool(true);
                    }
                    if (visible) {
                        if (!out.visible_tabs.contains(kid)) out.visible_tabs.push_back(kid);
                        out.hidden_panels.removeAll(kid);
                    } else {
                        if (!out.hidden_panels.contains(kid)) out.hidden_panels.push_back(kid);
                        out.visible_tabs.removeAll(kid);
                    }
                }
            }
            continue;
        }

        // A top-level whitelisted tab node may carry its own visible flag
        // (mirroring right_tabs.children[] semantics). Accept the flag at the
        // node top level (flat A2UI shape: {"id":"tab:数据","visible":false})
        // or under props (canonical shape). visible:false registers the tab
        // into hidden_panels and removes it from visible_tabs so the layout
        // engine actually hides it; visible:true reverses that.
        if (id.startsWith(QLatin1String("tab:"))) {
            QJsonValue visVal = o.value(QStringLiteral("visible"));
            if (!visVal.isBool()) visVal = props.value(QStringLiteral("visible"));
            if (visVal.isBool()) {
                const bool visible = visVal.toBool();
                if (visible) {
                    if (!out.visible_tabs.contains(id)) out.visible_tabs.push_back(id);
                    out.hidden_panels.removeAll(id);
                } else {
                    if (!out.hidden_panels.contains(id)) out.hidden_panels.push_back(id);
                    out.visible_tabs.removeAll(id);
                }
            }
            continue;
        }
        // Other registered containers (left_rail/center_spectrum/status_bar)
        // are accepted but carry no per-node layout payload in this subset.
    }
}

}  // namespace

WorkspaceLayout defaultWorkspaceSpec() {
    WorkspaceLayout m;
    m.splitter_sizes = {kDefaultSizeL, kDefaultSizeM, kDefaultSizeR};
    m.splitter_stretch = {1, 3, 1};
    m.visible_tabs.clear();
    m.current_tab.clear();
    m.hidden_panels.clear();
    m.placeholder_ids.clear();
    for (const auto& t : listTabs()) m.visible_tabs.push_back(t.id);
    return m;
}

WorkspaceLayout applyWorkspaceOverlay(const QJsonObject& msg) {
    WorkspaceLayout out = defaultWorkspaceSpec();
    if (msg.isEmpty()) return out;
    parseComponents(msg, out);
    return out;
}

WorkspaceLayout applyCreateSurface(const QJsonObject& msg) {
    return applyWorkspaceOverlay(msg);
}

WorkspaceLayout applyUpdateComponents(const WorkspaceLayout& base, const QJsonObject& msg) {
    WorkspaceLayout out = base;
    if (msg.isEmpty()) return out;
    // updateComponents is an overlay: reset placeholder list for the new
    // message but keep base sizes/tabs unless the message overrides them.
    out.placeholder_ids.clear();
    parseComponents(msg, out);
    return out;
}

WorkspaceLayout applyDeleteSurface() {
    return defaultWorkspaceSpec();
}

DataUpdateStatus applyUpdateComponentData(WorkspaceLayout& m,
                                          const QString& componentId,
                                          const QJsonValue& value) {
    // Whitelist gate: unknown ids are rejected, never stored (same gate as
    // layout nodes -- no runtime/untrusted registration).
    if (!isRegistered(componentId)) return DataUpdateStatus::UnknownComponent;

    if (value.isNull()) {
        // null = delete key (clear the slot). Absent key is a no-op success.
        m.component_data.remove(componentId);
        return DataUpdateStatus::Applied;
    }
    if (value.isObject()) {
        // object = upsert (whole-replace the slot).
        m.component_data.insert(componentId, value.toObject());
        return DataUpdateStatus::Applied;
    }
    // Arrays / strings / numbers / bools are not accepted payloads -- the tool
    // contract is "object or null". Honest refusal, no silent coercion.
    return DataUpdateStatus::BadValueType;
}

WorkspaceLayout applyResponsiveCompaction(const WorkspaceLayout& requested,
                                          int availableWidth) {
    // Wide window, or width unknown (headless / null splitter): never second-
    // guess the agent's requested form. compact=false.
    if (availableWidth >= kCompactBreakpoint || availableWidth <= 0) {
        WorkspaceLayout out = requested;
        out.compact = false;
        return out;
    }

    WorkspaceLayout out = requested;
    out.compact = true;

    // Re-plan column widths: hold the two control rails at readable compact
    // widths and give the center (the high-density spectrum/VSWR/return-loss
    // view) the remainder. A pane never drops below the soft floor.
    int left = kCompactRailW;
    int right = kCompactRightW;
    int center = availableWidth - left - right;
    if (center < kMinPanel) {
        // Not enough room for rails + a usable center: shrink the rails toward
        // the floor rather than squeezing the center below the floor.
        center = kMinPanel;
        const int slack = availableWidth - center;
        left = slack / 2;
        right = slack - left;
        if (left < kMinPanel) left = kMinPanel;
        if (right < kMinPanel) right = kMinPanel;
    }
    out.splitter_sizes = {left, center, right};

    // Fold the secondary right tabs to the agent-declared PRIMARY panel, but
    // only when the agent actually picked a current_tab: on a too-narrow window
    // the whole tab strip cannot be hosted, so we show just the one instrument
    // the agent marked primary. We never hide panels the agent left visible.
    if (!out.current_tab.isEmpty() && isRegistered(out.current_tab)) {
        out.visible_tabs = {out.current_tab};
        out.hidden_panels.clear();
        for (const auto& t : listTabs()) {
            if (t.id != out.current_tab) out.hidden_panels.push_back(t.id);
        }
    }
    return out;
}

QJsonObject workspaceSpecToJson(const WorkspaceLayout& m) {
    QJsonObject o;
    QJsonArray sizes, stretch;
    for (int i = 0; i < 3; ++i) {
        sizes.push_back(m.splitter_sizes[i]);
        stretch.push_back(m.splitter_stretch[i]);
    }
    o.insert(QStringLiteral("splitter_sizes"), sizes);
    o.insert(QStringLiteral("splitter_stretch"), stretch);
    o.insert(QStringLiteral("visible_tabs"), QJsonArray::fromStringList(m.visible_tabs));
    o.insert(QStringLiteral("current_tab"), m.current_tab);
    o.insert(QStringLiteral("hidden_panels"), QJsonArray::fromStringList(m.hidden_panels));
    o.insert(QStringLiteral("placeholders"), QJsonArray::fromStringList(m.placeholder_ids));
    // Per-component data slots (A2UI updateDataModel minimal subset). Emitted
    // verbatim; empty object when no component has data.
    o.insert(QStringLiteral("component_data"), m.component_data);
    // Responsive derived form (self-owned value-add). Additive read-back.
    o.insert(QStringLiteral("compact"), m.compact);
    return o;
}

WorkspaceLayout workspaceSpecFromJson(const QJsonObject& o) {
    WorkspaceLayout m = defaultWorkspaceSpec();
    auto readIntArray = [](const QJsonObject& obj, const char* key,
                           std::array<int, 3>& dst) {
        const QJsonValue v = obj.value(QLatin1String(key));
        if (!v.isArray()) return;
        const QJsonArray a = v.toArray();
        for (int i = 0; i < 3 && i < a.size(); ++i) {
            if (a.at(i).isDouble()) dst[i] = a.at(i).toInt(dst[i]);
        }
    };
    readIntArray(o, "splitter_sizes", m.splitter_sizes);
    readIntArray(o, "splitter_stretch", m.splitter_stretch);
    for (int i = 0; i < 3; ++i) m.splitter_sizes[i] = clampSize(m.splitter_sizes[i]);

    m.visible_tabs.clear();
    for (const QJsonValue& v : o.value(QStringLiteral("visible_tabs")).toArray())
        if (v.isString()) m.visible_tabs.push_back(v.toString());
    m.current_tab = o.value(QStringLiteral("current_tab")).toString();
    m.hidden_panels.clear();
    for (const QJsonValue& v : o.value(QStringLiteral("hidden_panels")).toArray())
        if (v.isString()) m.hidden_panels.push_back(v.toString());
    m.placeholder_ids.clear();
    for (const QJsonValue& v : o.value(QStringLiteral("placeholders")).toArray())
        if (v.isString()) m.placeholder_ids.push_back(v.toString());
    // Per-component data slots: read verbatim (untrusted but inert -- the layout
    // layer never executes it). Missing/non-object -> empty (default).
    m.component_data = o.value(QStringLiteral("component_data")).toObject();
    // Responsive derived form: read back; default false when absent.
    m.compact = o.value(QStringLiteral("compact")).toBool(false);
    return m;
}

QString applyLayoutEngine(QSplitter* splitter, QTabWidget* rightTabs,
                          const WorkspaceLayout& layout) {
    // Responsive compaction: re-plan to the narrow-width compact form when the
    // measured surface width is below the breakpoint; above it the request is
    // honored verbatim. This is the auto form-switch the narrow-window
    // acceptance scenario requires -- not by editing the fixed control rail's
    // buttons, but by re-planning the splitter geometry + folding secondary tabs.
    const int avail = splitter ? splitter->width() : 0;
    const WorkspaceLayout eff = applyResponsiveCompaction(layout, avail);

    QStringList notes;
    if (eff.compact) {
        notes << (QStringLiteral("compact:on (width ") +
                  QString::number(avail) + QStringLiteral(" <= ") +
                  QString::number(kCompactBreakpoint) + QStringLiteral(")"));
    }

    if (splitter) {
        splitter->setStretchFactor(0, eff.splitter_stretch[0]);
        splitter->setStretchFactor(1, eff.splitter_stretch[1]);
        splitter->setStretchFactor(2, eff.splitter_stretch[2]);
        QList<int> sizes = {eff.splitter_sizes[0],
                            eff.splitter_sizes[1],
                            eff.splitter_sizes[2]};
        splitter->setSizes(sizes);
    }

    if (rightTabs) {
        // Map tab id -> index. Only whitelisted ids resolve; unknowns are
        // placeholders and are NOT rendered.
        for (int i = 0; i < rightTabs->count(); ++i) {
            const QString label = rightTabs->tabText(i);
            const QString id = QStringLiteral("tab:") + label;
            if (!isRegistered(id)) continue;  // e.g. disabled section headers
            const bool show = eff.visible_tabs.contains(id);
            rightTabs->setTabVisible(i, show);
        }
        // Select current_tab if it resolves to a visible index.
        if (!eff.current_tab.isEmpty()) {
            const QString want = eff.current_tab;
            for (int i = 0; i < rightTabs->count(); ++i) {
                if (QStringLiteral("tab:") + rightTabs->tabText(i) == want) {
                    rightTabs->setCurrentIndex(i);
                    break;
                }
            }
        }
    }

    if (!eff.placeholder_ids.isEmpty()) {
        notes << QStringLiteral("placeholders: ") + eff.placeholder_ids.join(QStringLiteral(", "));
    }
    return notes.join(QStringLiteral("; "));
}

}  // namespace aigui
