// SPDX-License-Identifier: MIT
#include "core/l10n.h"

namespace mbdsdr {
namespace core {

QString uiLanguageTag(const QString& setting) {
    const QString v = setting.trimmed().toLower();
    if (v.startsWith(QLatin1String("en"))) return QStringLiteral("en");
    if (v.startsWith(QLatin1String("ja"))) return QStringLiteral("ja");
    return QStringLiteral("zh_CN");   // 源语言：zh 前缀与一切未知值
}

QStringList qmSearchDirs(const QString& appDir) {
    QStringList dirs;
    if (!appDir.isEmpty()) {
        dirs << appDir + QStringLiteral("/i18n") << appDir;
    }
    return dirs;
}

QString qmFileBase(const QString& tag) {
    if (tag == QLatin1String("en")) return QStringLiteral("mbdsdr_en");
    if (tag == QLatin1String("ja")) return QStringLiteral("mbdsdr_ja");
    return QString();   // 源语言：不装翻译器，tr() 直接回源文
}

} // namespace core
} // namespace mbdsdr
