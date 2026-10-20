// SPDX-License-Identifier: MIT
#include "core/freq_input.h"

#include <QCoreApplication>
#include <QRegularExpression>

#include "core/tokens.h"

namespace mbdsdr {
namespace core {

// 错误文案逐处走字面量 QCoreApplication::translate("FreqInput", …)——
// lupdate 只认字面量调用（包装函数传变量抽不出来），翻译管线才看得见。

bool parseHumanFrequency(const QString& text, double* hzOut, QString* errOut) {
    const QString t = text.trimmed();
    if (t.isEmpty()) {
        if (errOut)
            *errOut = QCoreApplication::translate(
                "FreqInput", "请输入频率（例：137.62M）");
        return false;
    }

    // 数值 + 可选 k/m/g 倍率 + 可选 hz 词尾；点号小数，逗号不收（诚实拒绝）。
    static const QRegularExpression re(
        QStringLiteral("^(\\d+(?:\\.\\d+)?)\\s*([kKmMgG]?)\\s*(?:[hH][zZ])?$"));
    const QRegularExpressionMatch m = re.match(t);
    if (!m.hasMatch()) {
        if (errOut)
            *errOut = QCoreApplication::translate(
                "FreqInput",
                "无法识别的频率格式（支持 k/M/G 与 hz，如 137.62M、7050k、7050000hz）");
        return false;
    }

    bool ok = false;
    const double value = m.captured(1).toDouble(&ok);
    if (!ok || value <= 0.0) {
        if (errOut)
            *errOut = QCoreApplication::translate("FreqInput", "频率必须是正数");
        return false;
    }

    // 倍率语义：k/M/G 盖过 hz 词尾；只有 hz 词尾 = Hz；两者皆无 = MHz。
    double mul = 1e6;
    const QString suf = m.captured(2).toLower();
    if (suf == QLatin1String("k"))      mul = 1e3;
    else if (suf == QLatin1String("m")) mul = 1e6;
    else if (suf == QLatin1String("g")) mul = 1e9;
    else if (t.endsWith(QLatin1String("hz"), Qt::CaseInsensitive)) mul = 1.0;

    const double hz = value * mul;
    if (hz < tokens::kFreqMinHz || hz > tokens::kFreqMaxHz) {
        if (errOut)
            *errOut = QCoreApplication::translate(
                "FreqInput", "频率超出接收范围，未调谐");
        return false;
    }

    if (hzOut) *hzOut = hz;
    return true;
}

} // namespace core
} // namespace mbdsdr
