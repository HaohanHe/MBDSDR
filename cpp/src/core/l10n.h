// SPDX-License-Identifier: MIT
//
// core/l10n.h -- UI 语言选择的纯逻辑层（i18n 管线，Qt Linguist）。
//
// 约定：源语言 = 简体中文（代码与默认 UI 文案中文，AGENTS.md 文化不变）。
// 翻译文件 cpp/i18n/mbdsdr_en.ts / mbdsdr_ja.ts 经 lrelease 产出 .qm，
// 构建期复制到可执行文件旁 i18n/ 目录；加载失败时诚实回退源语言。
// ui/language 设置值域：zh_CN / en / ja（存储写全量标签）；未知值
// 诚实回退 zh_CN——不抛异常、不猜用户意图。
#pragma once

#include <QString>
#include <QStringList>

namespace mbdsdr {
namespace core {

/// 设置值 -> BCP-47 语言标签。"zh*" -> zh_CN，"en*" -> en，"ja*" -> ja，
/// 其余（含空值）-> zh_CN（源语言）。
QString uiLanguageTag(const QString& setting);

/// .qm 搜索目录（优先 appDir/i18n，其次 appDir 自身）。
QStringList qmSearchDirs(const QString& appDir);

/// 对应 .qm 文件主名："zh_CN" 无翻译文件（源语言即中文，不装翻译器），
/// "en" -> "mbdsdr_en"，"ja" -> "mbdsdr_ja"；源语言返回空串表示"无需加载"。
QString qmFileBase(const QString& tag);

} // namespace core
} // namespace mbdsdr
