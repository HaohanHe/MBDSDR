// SPDX-License-Identifier: MIT
//
// core/app_settings.h -- 统一 QSettings 入口（全仓唯一合法构造点）。
//
// 动机（2026-10-20 探针实测，scratch/qset_probe.cpp）：QSettings("MBDSDR",
// "MBDSDR") 两参构造在 Windows 上固定走 NativeFormat（注册表），
// **无视 QSettings::setDefaultFormat** —— 测试侧 setPath/setDefaultFormat
// 重定向全部失效，测试一直在读写用户真实配置（HKCU\Software\MBDSDR\MBDSDR
// 曾被测试污染出隐藏 tab / CTCSS 状态，跨跑回读导致断言连环挂）。
//
// 行为契约：
//   * env MBDSDR_SETTINGS_DIR 非空（测试隔离）：IniFormat + 进程级重定向到
//     该目录，三平台一致、真实隔离；
//   * 否则 NativeFormat 用户作用域 "MBDSDR"/"MBDSDR"——与历史行为逐字节一致
//     （Windows 注册表 / Linux ~/.config / macOS plist）。
//
// 纪律：产品与测试代码一律经 mbdsdr::core::AppSettings 读写设置；再直接写
// QSettings("MBDSDR", ...) 构造视为 bug（有本注释作判据）。
#pragma once

#include <QSettings>

namespace mbdsdr {
namespace core {

class AppSettings : public QSettings {
public:
    AppSettings();
};

} // namespace core
} // namespace mbdsdr
