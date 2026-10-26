// SPDX-License-Identifier: MIT
// 活动布局 A1 "折" -- CollapsibleGroup self-checks (offscreen, no mocks):
//   1. Identity is the bare title and never changes (chevron prefix is visual
//      only), so settings keys / leftPanelGroups_ matching stays stable.
//   2. setCollapsed() is a pure presentation flip: children hide/show, the
//      widget keeps its wiring, collapsedChanged fires exactly once per real
//      state change.
//   3. A left-click on the title strip toggles; a click below the header does
//      not (it belongs to the content widgets).
//   4. MainWindow round-trip: folds persist through saveUiState/restoreUiState
//      under ui/collapsedGroups; deleting the key honestly unfolds everything.
#include <QApplication>
#include <QDir>
#include <QLabel>
#include <QSettings>
#include <QtTest>

#include "core/app_settings.h"
#include "core/tokens.h"
#include "ui/collapsible_group.h"
#include "ui/main_window.h"

using namespace mbdsdr;

class TestCollapsibleGroup : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void identityIsBareTitle();
    void collapseIsPurePresentation();
    void titleClickToggles();
    void foldsPersistAcrossLaunch();
};

void TestCollapsibleGroup::initTestCase() {
    const QString cfgDir = QDir::tempPath() + "/mbdsdr_cfg_collapsible_" +
                           QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(cfgDir);
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, cfgDir);
    qputenv("MBDSDR_SETTINGS_DIR", cfgDir.toUtf8());
    mbdsdr::core::AppSettings s;
    s.setValue(QString::fromUtf8("ui/onboardingDismissed"), true);
    s.sync();
}

void TestCollapsibleGroup::identityIsBareTitle() {
    ui::CollapsibleGroup g(QStringLiteral("源与连接"));
    QCOMPARE(g.bareTitle(), QStringLiteral("源与连接"));
    QVERIFY(g.title().contains(QStringLiteral("源与连接")));
    QVERIFY(!g.collapsed());
    g.setCollapsed(true);
    // Identity survives the visual chevron.
    QCOMPARE(g.bareTitle(), QStringLiteral("源与连接"));
    QVERIFY(g.title().contains(QStringLiteral("源与连接")));
    QVERIFY(g.title().contains(QString::fromUtf8("▶")));
}

void TestCollapsibleGroup::collapseIsPurePresentation() {
    ui::CollapsibleGroup g(QStringLiteral("录制"));
    auto* kid = new QLabel(QStringLiteral("x"), &g);
    QVERIFY(kid->isVisibleTo(&g));
    QSignalSpy spy(&g, &ui::CollapsibleGroup::collapsedChanged);
    g.setCollapsed(true);
    QVERIFY(g.collapsed());
    QVERIFY(!kid->isVisibleTo(&g));       // folded: content hidden
    QCOMPARE(spy.count(), 1);
    QCOMPARE(spy.takeFirst().at(0).toBool(), true);
    // No-op set must not re-emit.
    g.setCollapsed(true);
    QCOMPARE(spy.count(), 0);
    g.setCollapsed(false);
    QVERIFY(kid->isVisibleTo(&g));        // unfolded: same widget, back visible
    QCOMPARE(spy.count(), 1);
    QCOMPARE(spy.takeFirst().at(0).toBool(), false);
    // The child is still the same live object -- nothing was rebuilt.
    QCOMPARE(kid->parent(), &g);
}

void TestCollapsibleGroup::titleClickToggles() {
    ui::CollapsibleGroup g(QStringLiteral("静噪"));
    auto* kid = new QLabel(QStringLiteral("x"), &g);
    g.resize(240, 120);
    g.show();
    QVERIFY(QTest::qWaitForWindowExposed(&g));
    // Click the title strip (top band) -> fold.
    QTest::mouseClick(&g, Qt::LeftButton, {}, QPoint(40, 12));
    QVERIFY(g.collapsed());
    QVERIFY(!kid->isVisibleTo(&g));
    // Click the title strip again -> unfold.
    QTest::mouseClick(&g, Qt::LeftButton, {}, QPoint(40, 12));
    QVERIFY(!g.collapsed());
    QVERIFY(kid->isVisibleTo(&g));
}

void TestCollapsibleGroup::foldsPersistAcrossLaunch() {
    const QString foldedId = QStringLiteral("接收参数");
    {
        MainWindow w;
        w.show();
        QVERIFY(QTest::qWaitForWindowExposed(&w));
        // Fold via the same path a title click takes.
        auto* groups = w.findChildren<ui::CollapsibleGroup*>();
        QVERIFY(!groups.isEmpty());
        ui::CollapsibleGroup* target = nullptr;
        for (auto* g : groups)
            if (g->bareTitle() == foldedId) target = g;
        QVERIFY(target != nullptr);
        target->setCollapsed(true);
        // Force the debounced save to flush now (scheduleSave is 500 ms).
        QTest::qWait(700);
        const QStringList folded =
            mbdsdr::core::AppSettings()
                .value(QString::fromUtf8(tokens::kSettingsKeyCollapsedGroups))
                .toStringList();
        QVERIFY(folded.contains(foldedId));
    }
    {
        // Fresh window (same store) must come back with the fold in place.
        MainWindow w2;
        ui::CollapsibleGroup* target = nullptr;
        for (auto* g : w2.findChildren<ui::CollapsibleGroup*>())
            if (g->bareTitle() == foldedId) target = g;
        QVERIFY(target != nullptr);
        QVERIFY(target->collapsed());
    }
    {
        // One-step reset: delete the key -> honest default (everything open).
        mbdsdr::core::AppSettings().remove(
            QString::fromUtf8(tokens::kSettingsKeyCollapsedGroups));
        MainWindow w3;
        for (auto* g : w3.findChildren<ui::CollapsibleGroup*>())
            QVERIFY2(!g->collapsed(),
                     qPrintable(g->bareTitle() + QStringLiteral(" stayed folded")));
    }
}

QTEST_MAIN(TestCollapsibleGroup)
#include "test_collapsible_group.moc"
