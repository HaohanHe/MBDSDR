// SPDX-License-Identifier: MIT
#include "core/app_settings.h"

#include <cstdlib>

namespace mbdsdr {
namespace core {
namespace {

// 进程级 format/路径决策：测试隔离 env 首见时一次性把 IniFormat 用户作用域
// 根目录切到隔离目录（setPath 是全局状态，重复调用无害但只认第一次的目录，
// 与测试 initTestCase 的一次性设置约定一致）。
QSettings::Format pickFormat() {
    const char* dir = std::getenv("MBDSDR_SETTINGS_DIR");
    if (dir && *dir) {
        static const bool redirected = [] {
            QSettings::setPath(QSettings::IniFormat, QSettings::UserScope,
                               QString::fromLocal8Bit(std::getenv("MBDSDR_SETTINGS_DIR")));
            return true;
        }();
        (void)redirected;
        return QSettings::IniFormat;
    }
    return QSettings::NativeFormat;
}

} // namespace

AppSettings::AppSettings()
    : QSettings(pickFormat(), QSettings::UserScope,
                QStringLiteral("MBDSDR"), QStringLiteral("MBDSDR")) {}

} // namespace core
} // namespace mbdsdr
