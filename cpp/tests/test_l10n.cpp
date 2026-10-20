// SPDX-License-Identifier: MIT
//
// i18n（Qt Linguist）管线契约测试：
//   * 语言标签归一（未知值诚实回退 zh_CN 源语言，不猜）
//   * .qm 主名与搜索目录口径（core/l10n 纯逻辑）
//   * ui/language 经 mbdsdr::core::AppSettings 的落盘往返（测试隔离 env）
//   * en 翻译器真实加载（.qm 存在时断言译文；缺失时诚实 QSKIP，不假绿）
#include <QtTest>
#include <QCoreApplication>
#include <QTemporaryDir>
#include <QTranslator>

#include "core/app_settings.h"
#include "core/l10n.h"

using namespace mbdsdr;

class TestL10n : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void languageTagNormalizesHonestly();
    void qmFileBaseByTag();
    void qmSearchDirsPreferBundled();
    void uiLanguageSettingRoundTrips();
    void englishTranslatorLoadsRealQm();
};

void TestL10n::initTestCase() {
    // 隔离进临时目录（Windows 上 NativeFormat 直落注册表——经 AppSettings 的
    // env 契约真实隔离，详见 core/app_settings.h）。
    static QTemporaryDir tmp;
    QVERIFY(tmp.isValid());
    qputenv("MBDSDR_SETTINGS_DIR", tmp.path().toLocal8Bit());
}

void TestL10n::languageTagNormalizesHonestly() {
    QCOMPARE(core::uiLanguageTag(QStringLiteral("zh_CN")), QStringLiteral("zh_CN"));
    QCOMPARE(core::uiLanguageTag(QStringLiteral("zh")), QStringLiteral("zh_CN"));
    QCOMPARE(core::uiLanguageTag(QStringLiteral("en")), QStringLiteral("en"));
    QCOMPARE(core::uiLanguageTag(QStringLiteral("en_US")), QStringLiteral("en"));
    QCOMPARE(core::uiLanguageTag(QStringLiteral("EN")), QStringLiteral("en"));
    QCOMPARE(core::uiLanguageTag(QStringLiteral("ja")), QStringLiteral("ja"));
    QCOMPARE(core::uiLanguageTag(QStringLiteral("ja_JP")), QStringLiteral("ja"));
    // 未知/空值：诚实回退源语言，不猜。
    QCOMPARE(core::uiLanguageTag(QString()), QStringLiteral("zh_CN"));
    QCOMPARE(core::uiLanguageTag(QStringLiteral("klingon")), QStringLiteral("zh_CN"));
}

void TestL10n::qmFileBaseByTag() {
    QVERIFY(core::qmFileBase(QStringLiteral("zh_CN")).isEmpty());   // 源语言不装翻译器
    QCOMPARE(core::qmFileBase(QStringLiteral("en")), QStringLiteral("mbdsdr_en"));
    QCOMPARE(core::qmFileBase(QStringLiteral("ja")), QStringLiteral("mbdsdr_ja"));
}

void TestL10n::qmSearchDirsPreferBundled() {
    const QStringList dirs = core::qmSearchDirs(QStringLiteral("/opt/mbdsdr"));
    QCOMPARE(dirs.size(), 2);
    QCOMPARE(dirs[0], QStringLiteral("/opt/mbdsdr/i18n"));
    QCOMPARE(dirs[1], QStringLiteral("/opt/mbdsdr"));
    QVERIFY(core::qmSearchDirs(QString()).isEmpty());   // 无目录不瞎猜
}

void TestL10n::uiLanguageSettingRoundTrips() {
    core::AppSettings s;
    s.setValue("ui/language", QStringLiteral("ja"));
    QCOMPARE(core::uiLanguageTag(s.value("ui/language").toString()),
             QStringLiteral("ja"));
    s.setValue("ui/language", QStringLiteral("zh_CN"));
    QCOMPARE(core::uiLanguageTag(s.value("ui/language").toString()),
             QStringLiteral("zh_CN"));
}

void TestL10n::englishTranslatorLoadsRealQm() {
    QTranslator translator;
    bool loaded = false;
    const QStringList dirs =
        core::qmSearchDirs(QCoreApplication::applicationDirPath());
    for (const QString& d : dirs) {
        if (translator.load(QStringLiteral("mbdsdr_en"), d)) { loaded = true; break; }
    }
    if (!loaded) {
        QSKIP("mbdsdr_en.qm 未生成（LinguistTools 缺失或未构建）——诚实跳过，不假绿");
    }
    // SettingsDialog 的"设置"应被译为 "Settings"（cpp/i18n/mbdsdr_en.ts 钉扎）。
    // moc 与 lupdate 对命名空间类的 context 口径在不同 Qt 版本间存在差异
    // （"SettingsDialog" vs "mbdsdr::ui::SettingsDialog"），两种口径任一命中
    // 即认为管线真实生效；都命不中说明 .ts context 与运行时脱节，必须报错。
    const QString qa = translator.translate("mbdsdr::ui::SettingsDialog", "设置");
    const QString qb = translator.translate("SettingsDialog", "设置");
    const bool hit = (qa == QStringLiteral("Settings")) ||
                     (qb == QStringLiteral("Settings"));
    QVERIFY2(hit, "neither tr context resolves the SettingsDialog translation");
    QVERIFY(!translator.translate("SettingsDialog", "音频").isEmpty() ||
            !translator.translate("mbdsdr::ui::SettingsDialog", "音频").isEmpty());
}

QTEST_MAIN(TestL10n)
#include "test_l10n.moc"
