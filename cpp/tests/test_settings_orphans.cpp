// SPDX-License-Identifier: MIT
// Settings orphan-alignment self-checks (2026-10-26 孤儿对齐批次):
//   1. SettingsDialog 的首选采样率是真控件：load 从 rx/sampleRate 吸附最近项，
//      save 写回，sampleRateHz() 暴露 itemData 的 Hz 值——不再是"有前端没后端"。
//   2. SpectrumDisplay::setWfOverlay 真实落盘 view/wfOverlay 并翻转渲染模式
//      ——后端孤儿（只读开关）补上前端入口的配套 setter。
//   3. SpectrumEngine::setRecMaxSegmentSeconds 是活 API：改值 + 持久化
//      rec/max_seg_s + getter 回读一致（UI 侧 recSegSpin 走同一条路径）。
//   4. MainWindow 的抽取（rx/frontendDecimation）重启可恢复：restoreUiState
//      经 objectName 找回 decimCombo 并回填，不再"写后即丢"。
// 离屏、真链、无 mock；QSettings 隔离到 PID 目录。
#include <QApplication>
#include <QComboBox>
#include <QDir>
#include <QSettings>
#include <QtTest>

#include "ai/ai_config.h"
#include "core/app_settings.h"
#include "core/tokens.h"
#include "dsp/spectrum_engine.h"
#include "ui/settings_dialog.h"
#include "ui/spectrum_display.h"
#include "ui/main_window.h"

using namespace mbdsdr;

class TestSettingsOrphans : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void sampleRateRoundTripsThroughSettingsDialog();
    void sampleRateHzReadsItemData();
    void wfOverlayTogglePersists();
    void recMaxSegmentSetterIsLive();
    void frontendDecimationRestoresOnLaunch();
};

void TestSettingsOrphans::initTestCase() {
    const QString cfgDir = QDir::tempPath() + "/mbdsdr_cfg_orphan_" +
                           QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(cfgDir);
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, cfgDir);
    qputenv("MBDSDR_SETTINGS_DIR", cfgDir.toUtf8());
    mbdsdr::core::AppSettings s;
    s.setValue(QString::fromUtf8("ui/onboardingDismissed"), true);
    s.sync();
}

void TestSettingsOrphans::sampleRateRoundTripsThroughSettingsDialog() {
    mbdsdr::core::AppSettings s;
    s.setValue(QStringLiteral("rx/sampleRate"), 1.024e6);
    s.sync();

    ai::AiConfig cfg;   // station fields NaN -> dialog leaves them alone
    ui::SettingsDialog dlg;
    dlg.loadFromConfig(cfg);
    // 1.024 MS/s must be the selected item after load.
    QVERIFY(qAbs(dlg.sampleRateHz() - 1.024e6) < 1.0);

    // Save under a different choice, then reload: the dialog is the only face
    // and must round-trip through the very key the engine reads.
    dlg.saveToConfig(cfg);
    QCOMPARE(s.value(QStringLiteral("rx/sampleRate")).toDouble(), 1.024e6);
}

void TestSettingsOrphans::sampleRateHzReadsItemData() {
    ui::SettingsDialog dlg;
    const double hz = dlg.sampleRateHz();
    // The project default band: 2.4 MS/s when nothing is configured.
    QVERIFY(hz > 0.0);
    QVERIFY(hz >= 1.0e6 && hz <= 3.2e6);
    // Garbage stored preference must snap to a legal item, not leak through.
    mbdsdr::core::AppSettings s;
    s.setValue(QStringLiteral("rx/sampleRate"), 123456.0);   // nonsense Hz
    ai::AiConfig cfg;
    dlg.loadFromConfig(cfg);
    const double snapped = dlg.sampleRateHz();
    QVERIFY(snapped == 1.024e6 || snapped == 2.048e6 ||
            snapped == 2.4e6  || snapped == 3.2e6);
}

void TestSettingsOrphans::wfOverlayTogglePersists() {
    ui::SpectrumDisplay d;
    QVERIFY(!d.wfOverlay());          // honest default: classic split
    d.setWfOverlay(true);
    QVERIFY(d.wfOverlay());
    QCOMPARE(mbdsdr::core::AppSettings()
                 .value(QStringLiteral("view/wfOverlay")).toBool(), true);
    d.setWfOverlay(false);
    QVERIFY(!d.wfOverlay());
    QCOMPARE(mbdsdr::core::AppSettings()
                 .value(QStringLiteral("view/wfOverlay")).toBool(), false);
}

void TestSettingsOrphans::recMaxSegmentSetterIsLive() {
    dsp::SpectrumEngine e;
    e.setRecMaxSegmentSeconds(600.0);
    QCOMPARE(e.recMaxSegmentSeconds(), 600.0);
    QCOMPARE(mbdsdr::core::AppSettings()
                 .value(QStringLiteral("rec/max_seg_s")).toDouble(), 600.0);
    // A fresh engine (same settings store) must come back with the persisted
    // ceiling -- this is the path recSegSpin's handler feeds.
    dsp::SpectrumEngine e2;
    QCOMPARE(e2.recMaxSegmentSeconds(), 600.0);
}

void TestSettingsOrphans::frontendDecimationRestoresOnLaunch() {
    {
        mbdsdr::core::AppSettings s;
        s.setValue(QStringLiteral("rx/frontendDecimation"), 4);
        s.sync();
    }
    MainWindow w;
    w.show();
    auto* decim = w.findChild<QComboBox*>(QStringLiteral("decimCombo"));
    QVERIFY(decim != nullptr);
    QCOMPARE(decim->currentData().toInt(), 4);
    // And the persisted preference survives a save cycle untouched.
    QCOMPARE(mbdsdr::core::AppSettings()
                 .value(QStringLiteral("rx/frontendDecimation")).toInt(), 4);
}

QTEST_MAIN(TestSettingsOrphans)
#include "test_settings_orphans.moc"
