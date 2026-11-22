// SPDX-License-Identifier: MIT
// UI text helpers shared by the desktop widgets.
#pragma once

#include <QChar>
#include <QString>

namespace mbdsdr::ui {

// QLabel 的 wordWrap 只在词边界折行；驱动溯源/设备名里的长 ASCII 标识
// （rtlsdr_get_tuner_type / librtlsdr 等）会整段溢出左侧列宽（2026-11-22
// 960×720 便携包快照实测右侧被裁）。插入零宽空格让它在标识符处可折行，
// 原文另存 tooltip，信息不丢。
inline QString softBreakable(const QString& text) {
    QString out;
    out.reserve(text.size() + 16);
    for (const QChar ch : text) {
        out.append(ch);
        if (ch == '_' || ch == '/' || ch == '.' || ch == '-')
            out.append(QChar(0x200B));
    }
    return out;
}

} // namespace mbdsdr::ui
