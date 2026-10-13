// SPDX-License-Identifier: MIT
//
// test_workspace_layout.cpp -- AIGUI P0-a pinning tests for the workspace
// layout model + component registry + LayoutEngine.
//
// Covers (per task spec):
//   * unknown component id -> placeholder, no crash
//   * missing child reference -> placeholder
//   * splitter sizes out-of-bounds -> clamped to kMinPanel
//   * create -> update -> delete lifecycle (delete resets to default)
//   * defaultWorkspaceSpec round-trip invariant (model -> JSON -> model -> JSON)
//   * registry: every whitelisted right tab isRegistered==true; "eval"/"<script>"
//     style fake ids are false
//   * LayoutEngine on an empty/default model does not crash (offscreen Qt)

#include <QtTest/QtTest>

#include <QSplitter>
#include <QTabWidget>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>

#include "ui/component_registry.h"
#include "ui/workspace_layout.h"

using namespace aigui;

namespace {

QJsonObject splitterNode(const std::array<int,3>& sizes,
                         const std::array<int,3>& stretch = {1,3,1}) {
    QJsonArray s, st;
    for (int i=0;i<3;++i){ s.push_back(sizes[i]); st.push_back(stretch[i]); }
    QJsonObject props;
    props.insert(QStringLiteral("sizes"), s);
    props.insert(QStringLiteral("stretch"), st);
    QJsonObject n;
    n.insert(QStringLiteral("id"), QStringLiteral("splitter"));
    n.insert(QStringLiteral("type"), QStringLiteral("splitter"));
    n.insert(QStringLiteral("props"), props);
    return n;
}

QJsonObject rightTabsNode(const QStringList& children, const QString& current = {}) {
    QJsonObject props;
    if (!current.isEmpty()) props.insert(QStringLiteral("current"), current);
    QJsonArray kids;
    for (const QString& c : children) kids.push_back(c);
    QJsonObject n;
    n.insert(QStringLiteral("id"), QStringLiteral("right_tabs"));
    n.insert(QStringLiteral("type"), QStringLiteral("tab_container"));
    n.insert(QStringLiteral("props"), props);
    n.insert(QStringLiteral("children"), kids);
    return n;
}

QJsonObject tabNode(const QString& id, bool visible) {
    QJsonObject props;
    props.insert(QStringLiteral("visible"), visible);
    QJsonObject n;
    n.insert(QStringLiteral("id"), id);
    n.insert(QStringLiteral("type"), QStringLiteral("tab"));
    n.insert(QStringLiteral("props"), props);
    return n;
}

}  // namespace

class TestWorkspaceLayout : public QObject {
    Q_OBJECT
private slots:
    void defaultSpecIsThreeColumn();
    void unknownIdGoesToPlaceholder();
    void missingChildReferenceGoesToPlaceholder();
    void sizesOutOfBoundsAreClamped();
    void lifecycleCreateUpdateDelete();
    void roundTripDefaultSpec();
    void registryWhitelistTabs();
    void engineOnEmptyModelDoesNotCrash();
    void dataUpsertAndClear();
    void dataUnknownComponentRejected();
    void dataBadValueTypeRejected();
    void dataRoundTripThroughJson();
    void topLevelTabHideFlatShape();
    void topLevelTabShowFlatShape();
    void topLevelTabHidePropsShape();
};

void TestWorkspaceLayout::defaultSpecIsThreeColumn() {
    WorkspaceLayout d = defaultWorkspaceSpec();
    QCOMPARE(d.splitter_sizes[0], 280);
    QCOMPARE(d.splitter_sizes[1], 800);
    QCOMPARE(d.splitter_sizes[2], 280);
    QCOMPARE(d.splitter_stretch[0], 1);
    QCOMPARE(d.splitter_stretch[1], 3);
    QCOMPARE(d.splitter_stretch[2], 1);
    // All known tabs visible by default, no placeholders.
    QVERIFY(d.placeholder_ids.isEmpty());
    QVERIFY(!d.visible_tabs.isEmpty());
}

void TestWorkspaceLayout::unknownIdGoesToPlaceholder() {
    QJsonObject msg;
    QJsonArray comps;
    comps.push_back(splitterNode({280,800,280}));
    comps.push_back(tabNode(QStringLiteral("tab:definitely-not-real"), true));
    msg.insert(QStringLiteral("components"), comps);

    WorkspaceLayout m = applyCreateSurface(msg);
    QVERIFY(m.placeholder_ids.contains(QStringLiteral("tab:definitely-not-real")));
    // No throw, no crash; sizes still applied from the splitter node.
    QCOMPARE(m.splitter_sizes[1], 800);
}

void TestWorkspaceLayout::missingChildReferenceGoesToPlaceholder() {
    // right_tabs references a child id that has NO node in the components list
    // AND is not in the registry -> placeholder.
    QJsonObject msg;
    QJsonArray comps;
    comps.push_back(rightTabsNode({QStringLiteral("tab:ghost-tab")}));
    msg.insert(QStringLiteral("components"), comps);

    WorkspaceLayout m = applyCreateSurface(msg);
    QVERIFY(m.placeholder_ids.contains(QStringLiteral("tab:ghost-tab")));
    QVERIFY(!m.visible_tabs.contains(QStringLiteral("tab:ghost-tab")));
}

void TestWorkspaceLayout::sizesOutOfBoundsAreClamped() {
    QJsonObject msg;
    QJsonArray comps;
    // Adversarial: negative / tiny / huge widths.
    comps.push_back(splitterNode({-50, 0, 5}));
    msg.insert(QStringLiteral("components"), comps);

    WorkspaceLayout m = applyCreateSurface(msg);
    QCOMPARE(m.splitter_sizes[0], kMinPanel);  // -50 clamped up
    QCOMPARE(m.splitter_sizes[1], kMinPanel);  // 0  clamped up
    QCOMPARE(m.splitter_sizes[2], kMinPanel);  // 5  clamped up
}

void TestWorkspaceLayout::lifecycleCreateUpdateDelete() {
    // create
    QJsonObject c;
    {
        QJsonArray comps;
        comps.push_back(splitterNode({400, 900, 300}));
        comps.push_back(rightTabsNode({QStringLiteral("tab:CW"),
                                       QStringLiteral("tab:ADS-B")},
                                      QStringLiteral("tab:CW")));
        comps.push_back(tabNode(QStringLiteral("tab:CW"), true));
        comps.push_back(tabNode(QStringLiteral("tab:ADS-B"), false));
        c.insert(QStringLiteral("components"), comps);
    }
    WorkspaceLayout created = applyCreateSurface(c);
    QCOMPARE(created.splitter_sizes[0], 400);
    QCOMPARE(created.current_tab, QStringLiteral("tab:CW"));
    QVERIFY(created.visible_tabs.contains(QStringLiteral("tab:CW")));
    QVERIFY(created.hidden_panels.contains(QStringLiteral("tab:ADS-B")));

    // update: shrink left rail, switch current
    QJsonObject u;
    {
        QJsonArray comps;
        comps.push_back(splitterNode({200, 1000, 300}));
        comps.push_back(rightTabsNode({}, QStringLiteral("tab:ADS-B")));
        u.insert(QStringLiteral("components"), comps);
    }
    WorkspaceLayout updated = applyUpdateComponents(created, u);
    QCOMPARE(updated.splitter_sizes[0], 200);
    QCOMPARE(updated.splitter_sizes[1], 1000);
    QCOMPARE(updated.current_tab, QStringLiteral("tab:ADS-B"));

    // delete -> back to default
    WorkspaceLayout gone = applyDeleteSurface();
    WorkspaceLayout d = defaultWorkspaceSpec();
    QCOMPARE(gone.splitter_sizes[0], d.splitter_sizes[0]);
    QCOMPARE(gone.splitter_sizes[1], d.splitter_sizes[1]);
    QCOMPARE(gone.splitter_sizes[2], d.splitter_sizes[2]);
    QCOMPARE(gone.current_tab, d.current_tab);
}

void TestWorkspaceLayout::roundTripDefaultSpec() {
    WorkspaceLayout d = defaultWorkspaceSpec();
    QJsonObject j1 = workspaceSpecToJson(d);
    WorkspaceLayout back = workspaceSpecFromJson(j1);
    QJsonObject j2 = workspaceSpecToJson(back);
    QCOMPARE(j1, j2);
    // And re-serializing once more is stable.
    QJsonObject j3 = workspaceSpecToJson(workspaceSpecFromJson(j2));
    QCOMPARE(j2, j3);
}

void TestWorkspaceLayout::registryWhitelistTabs() {
    // Every registered right-panel tab must resolve true.
    for (const auto& t : listTabs()) {
        QVERIFY2(isRegistered(t.id), qPrintable(t.id));
    }
    // Known structural containers.
    QVERIFY(isRegistered(QStringLiteral("splitter")));
    QVERIFY(isRegistered(QStringLiteral("right_tabs")));
    QVERIFY(isRegistered(QStringLiteral("left_rail")));
    QVERIFY(isRegistered(QStringLiteral("center_spectrum")));
    QVERIFY(isRegistered(QStringLiteral("status_bar")));

    // Injection-shaped ids MUST be rejected.
    QVERIFY(!isRegistered(QStringLiteral("eval")));
    QVERIFY(!isRegistered(QStringLiteral("<script>")));
    QVERIFY(!isRegistered(QStringLiteral("system")));
    QVERIFY(!isRegistered(QStringLiteral("require('fs')")));
    QVERIFY(!isRegistered(QStringLiteral("tab:不存在的页")));
    // Real tabs added in the 2026 layout (FT8 / 数据 / VNA) resolve true.
    QVERIFY(isRegistered(QStringLiteral("tab:FT8")));
    QVERIFY(isRegistered(QStringLiteral("tab:数据")));
    QVERIFY(isRegistered(QStringLiteral("tab:VNA")));
}

void TestWorkspaceLayout::engineOnEmptyModelDoesNotCrash() {
    // Build a real (offscreen) splitter + tab widget and apply the default
    // model. Must not crash; unknown tabs are NOT rendered.
    QSplitter splitter(Qt::Horizontal);
    splitter.addWidget(new QWidget);
    splitter.addWidget(new QWidget);
    splitter.addWidget(new QWidget);

    QTabWidget tabs;
    tabs.addTab(new QWidget, QStringLiteral("CW"));
    tabs.addTab(new QWidget, QStringLiteral("ADS-B"));
    tabs.addTab(new QWidget, QStringLiteral("not-a-real-tab"));  // placeholder

    WorkspaceLayout m = defaultWorkspaceSpec();
    m.placeholder_ids.push_back(QStringLiteral("tab:not-a-real-tab"));

    // Should simply run; note mentions placeholder.
    QString note = applyLayoutEngine(&splitter, &tabs, m);
    QVERIFY(note.contains(QStringLiteral("placeholders")));

    // Also exercise a null splitter/tabs path (defensive).
    QString note2 = applyLayoutEngine(nullptr, nullptr, m);
    Q_UNUSED(note2);
}

// A2UI updateDataModel minimal subset: object upserts (whole-replace), null
// clears the key. The layout layer stores the payload verbatim.
void TestWorkspaceLayout::dataUpsertAndClear() {
    WorkspaceLayout m = defaultWorkspaceSpec();

    QJsonObject payload;
    payload.insert(QStringLiteral("freq_hz"), 145000000.0);
    payload.insert(QStringLiteral("label"), QStringLiteral("145.000"));
    QVERIFY(applyUpdateComponentData(m, QStringLiteral("tab:CW"), payload)
            == DataUpdateStatus::Applied);
    QVERIFY(m.component_data.contains(QStringLiteral("tab:CW")));
    QCOMPARE(m.component_data.value(QStringLiteral("tab:CW")).toObject()
                 .value(QStringLiteral("freq_hz")).toDouble(), 145000000.0);

    // Second upsert on the SAME key whole-replaces (not merged).
    QJsonObject payload2;
    payload2.insert(QStringLiteral("mode"), QStringLiteral("USB"));
    QVERIFY(applyUpdateComponentData(m, QStringLiteral("tab:CW"), payload2)
            == DataUpdateStatus::Applied);
    QJsonObject back = m.component_data.value(QStringLiteral("tab:CW")).toObject();
    QVERIFY2(!back.contains(QStringLiteral("freq_hz")),
             "upsert must whole-replace, not merge");
    QCOMPARE(back.value(QStringLiteral("mode")).toString(), QStringLiteral("USB"));

    // null clears the key.
    QVERIFY(applyUpdateComponentData(m, QStringLiteral("tab:CW"), QJsonValue::Null)
            == DataUpdateStatus::Applied);
    QVERIFY(!m.component_data.contains(QStringLiteral("tab:CW")));
}

// Unknown component ids are REJECTED (whitelist gate), never stored.
void TestWorkspaceLayout::dataUnknownComponentRejected() {
    WorkspaceLayout m = defaultWorkspaceSpec();
    QJsonObject payload; payload.insert(QStringLiteral("x"), 1);
    QVERIFY(applyUpdateComponentData(m, QStringLiteral("tab:ghost"), payload)
            == DataUpdateStatus::UnknownComponent);
    QVERIFY(!m.component_data.contains(QStringLiteral("tab:ghost")));
}

// Arrays / strings / numbers / bools are not accepted payloads.
void TestWorkspaceLayout::dataBadValueTypeRejected() {
    WorkspaceLayout m = defaultWorkspaceSpec();
    QVERIFY(applyUpdateComponentData(m, QStringLiteral("tab:CW"),
                                     QJsonValue(QStringLiteral("not-an-object")))
            == DataUpdateStatus::BadValueType);
    QVERIFY(applyUpdateComponentData(m, QStringLiteral("tab:CW"),
                                     QJsonValue(42.0))
            == DataUpdateStatus::BadValueType);
    QVERIFY(!m.component_data.contains(QStringLiteral("tab:CW")));
}

// component_data survives the JSON round-trip (get-side cache restore).
void TestWorkspaceLayout::dataRoundTripThroughJson() {
    WorkspaceLayout m = defaultWorkspaceSpec();
    QJsonObject payload; payload.insert(QStringLiteral("freq_hz"), 145000000.0);
    applyUpdateComponentData(m, QStringLiteral("tab:FT8"), payload);

    QJsonObject j = workspaceSpecToJson(m);
    QVERIFY(j.value(QStringLiteral("component_data")).isObject());
    WorkspaceLayout back = workspaceSpecFromJson(j);
    QVERIFY(back.component_data.contains(QStringLiteral("tab:FT8")));
    QCOMPARE(back.component_data.value(QStringLiteral("tab:FT8")).toObject()
                 .value(QStringLiteral("freq_hz")).toDouble(), 145000000.0);

    // Re-serialize is stable.
    QCOMPARE(workspaceSpecToJson(back).value(QStringLiteral("component_data"))
                 .toObject().value(QStringLiteral("tab:FT8")).toObject(),
             payload);
}

// A top-level whitelisted tab node carrying visible:false at the NODE TOP LEVEL
// (flat A2UI shape, the same shape the offscreen screenshot harness uses) must
// register the tab into hidden_panels AND remove it from visible_tabs, so the
// layout engine actually hides the tab. Regression guard for the fixture that
// previously sent {"id":"tab:数据","visible":false} as a standalone node and
// silently dropped the flag.
void TestWorkspaceLayout::topLevelTabHideFlatShape() {
    QJsonObject hidden;
    hidden.insert(QStringLiteral("id"), QStringLiteral("tab:数据"));
    hidden.insert(QStringLiteral("visible"), false);

    QJsonObject msg;
    QJsonArray comps;
    comps.push_back(hidden);
    msg.insert(QStringLiteral("components"), comps);

    WorkspaceLayout m = applyCreateSurface(msg);
    QVERIFY2(m.hidden_panels.contains(QStringLiteral("tab:数据")),
             "top-level visible:false must register hidden_panels");
    QVERIFY2(!m.visible_tabs.contains(QStringLiteral("tab:数据")),
             "top-level visible:false must remove the tab from visible_tabs");
    // No placeholder: tab:数据 is whitelisted.
    QVERIFY(!m.placeholder_ids.contains(QStringLiteral("tab:数据")));
}

// The inverse: a top-level tab node with visible:true must NOT hide the tab
// (must not appear in hidden_panels) and must leave visible_tabs containing it.
void TestWorkspaceLayout::topLevelTabShowFlatShape() {
    // Start from a model where tab:数据 is already hidden, then send a
    // top-level visible:true node to reverse it.
    WorkspaceLayout base = defaultWorkspaceSpec();
    base.hidden_panels.push_back(QStringLiteral("tab:数据"));
    base.visible_tabs.removeAll(QStringLiteral("tab:数据"));

    QJsonObject showNode;
    showNode.insert(QStringLiteral("id"), QStringLiteral("tab:数据"));
    showNode.insert(QStringLiteral("visible"), true);

    QJsonObject msg;
    QJsonArray comps;
    comps.push_back(showNode);
    msg.insert(QStringLiteral("components"), comps);

    WorkspaceLayout m = applyUpdateComponents(base, msg);
    QVERIFY2(!m.hidden_panels.contains(QStringLiteral("tab:数据")),
             "top-level visible:true must clear hidden_panels");
    QVERIFY2(m.visible_tabs.contains(QStringLiteral("tab:数据")),
             "top-level visible:true must restore visible_tabs");
}

// Canonical shape: visible flag nested under props (the tabNode() helper
// produces this). Top-level tab node with props.visible:false must also hide.
void TestWorkspaceLayout::topLevelTabHidePropsShape() {
    QJsonObject msg;
    QJsonArray comps;
    comps.push_back(tabNode(QStringLiteral("tab:FT8"), false));
    msg.insert(QStringLiteral("components"), comps);

    WorkspaceLayout m = applyCreateSurface(msg);
    QVERIFY(m.hidden_panels.contains(QStringLiteral("tab:FT8")));
    QVERIFY(!m.visible_tabs.contains(QStringLiteral("tab:FT8")));
}

QTEST_MAIN(TestWorkspaceLayout)
#include "test_workspace_layout.moc"
