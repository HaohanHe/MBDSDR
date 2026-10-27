// SPDX-License-Identifier: MIT
// CJK font guard: fail loudly when the rendering environment cannot draw
// Chinese glyphs (tofu boxes) instead of silently shipping unreadable
// screenshots.
//
// Phase78 background: offscreen Qt/Flutter rendering on a box without any CJK
// font turns every Chinese character into a hollow box. Root-caused with a
// controlled experiment (isolated fontconfig): families 239 -> 3, hasCJK
// 1 -> 0, and a one-line label rendered as "口口口口...". CI runners
// (ubuntu-latest) ship no CJK font, so any screenshot-producing path was
// affected. This test turns that invisible quality bug into a visible red.
//
// The check is glyph-existence, not font-name matching: a tofu box still has
// an advance width, so metrics alone cannot detect it; QFontMetricsF::inFont
// reports whether the font actually carries a glyph for the codepoint.
#include <QtTest>
#include <QFontDatabase>
#include <QFontMetricsF>
#include <QStringList>

#ifdef Q_OS_WIN
// Windows 上的 offscreen 平台插件用 QBasicFontDatabase：只扫 QT_QPA_FONTDIR
// 指定目录（缺省指向 Qt 安装树内不存在的 lib/fonts），不枚举系统字体，
// families 恒为 0 —— 本守卫在 Windows 裸跑（ctest 基线口径）永远红。
// Linux CI 走 fontconfig 枚举系统字体，不受影响；此处只在 Windows 自举：
// 首次使用字体库前把目录指到系统字体库（微软雅黑等 CJK 字体在此），
// 让守卫在 Windows 上测的是真实字形覆盖，而不是平台插件的目录约定。
// SystemRoot 缺失时不动 env —— 让原有警告原样暴露，不发明目录。
#include <QtGlobal>
static void bootstrapWindowsFontDir() {
    if (!qEnvironmentVariableIsEmpty("QT_QPA_FONTDIR")) return;   // 尊重显式配置
    const QByteArray root = qgetenv("SystemRoot");
    if (!root.isEmpty()) qputenv("QT_QPA_FONTDIR", root + "/Fonts");
}
Q_CONSTRUCTOR_FUNCTION(bootstrapWindowsFontDir)
#endif

class TestFontCjkGuard : public QObject {
    Q_OBJECT

private slots:
    void someFamilyRendersChinese() {
        const QStringList families = QFontDatabase::families();
        // Characters spanning the UI's real vocabulary: waterfall label,
        // device-panel copy, common UI verbs.
        const QString probe = QString::fromUtf8("瀑布图未连接设备");
        QString bestFamily;
        int covered = 0;
        for (const QString& fam : families) {
            QFont f(fam);
            f.setPointSize(12);
            const QFontMetricsF fm(f);
            int hits = 0;
            for (const QChar ch : probe) {
                if (fm.inFont(ch)) ++hits;
            }
            if (hits > covered) {
                covered = hits;
                bestFamily = fam;
            }
            if (covered == probe.size()) break;
        }
        qInfo("font-cjk guard: families=%d bestCoverage=%d/%d via '%s'",
              families.size(), covered, probe.size(),
              qPrintable(bestFamily.isEmpty() ? QStringLiteral("<none>")
                                               : bestFamily));
        if (covered < probe.size()) {
            const QString fix =
                QString::fromUtf8(
                    "环境缺少中文字体：offscreen 渲染会把每个汉字画成方框。\n"
                    "修复（按平台）：\n"
                    "  Debian/Ubuntu:  sudo apt-get install -y fonts-noto-cjk\n"
                    "  Fedora/RHEL:    sudo dnf install google-noto-sans-cjk-fonts\n"
                    "  Arch:           sudo pacman -S noto-fonts-cjk\n"
                    "  Windows/macOS:  系统自带中文字体，若失败请检查字体服务\n"
                    "CI 侧已在 python/cpp/flutter 三个 job 安装 fonts-noto-cjk\n"
                    "（见 .github/workflows/ci.yml 与 docs/learn/capability-map\n"
                    "phase78 条目）。");
            QVERIFY2(false, qPrintable(fix));
        }
    }
};

QTEST_MAIN(TestFontCjkGuard)
#include "test_font_cjk_guard.moc"
