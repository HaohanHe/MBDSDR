// SPDX-License-Identifier: MIT
#include "ai/arguments_validator.h"

#include <QJsonDocument>
#include <QJsonArray>
#include <cmath>

namespace mbdsdr {
namespace ai {
namespace {

bool valueIsNumber(const QJsonValue& v) {
    return v.isDouble(); // this Qt6 build stores every number (int/double) as Double
}

// "integer" demands an integral number: accepted only when the numeric value
// carries no fractional part.
bool valueIsIntegral(const QJsonValue& v) {
    if (!v.isDouble()) return false;
    double d = v.toDouble();
    return std::isfinite(d) && std::trunc(d) == d;
}

bool enumContains(const QJsonArray& en, const QJsonValue& v) {
    for (const QJsonValue& e : en) {
        if (e == v) return true;
    }
    return false;
}

QString describeJsonType(const QJsonValue& v) {
    switch (v.type()) {
    case QJsonValue::Null:      return QString::fromUtf8("null");
    case QJsonValue::Bool:      return QString::fromUtf8("boolean");
    case QJsonValue::Double:    return QString::fromUtf8("number");
    case QJsonValue::String:    return QString::fromUtf8("string");
    case QJsonValue::Array:     return QString::fromUtf8("array");
    case QJsonValue::Object:    return QString::fromUtf8("object");
    default:                    return QString::fromUtf8("other");
    }
}

} // namespace

ValidationResult validateArguments(const QString& toolName,
                                   const QJsonObject& args,
                                   const QJsonObject& schema) {
    ValidationResult r;

    // ① schema 必须是对象且携带 properties 对象。
    if (!schema.contains("properties") ||
        schema.value("properties").type() != QJsonValue::Object) {
        r.reasons << QString::fromUtf8("schema 无效：缺少 properties 对象");
    }
    const QJsonObject props = schema.value("properties").toObject();
    const QJsonArray required = schema.value("required").toArray();

    // ② required 数组中的键必须存在；⑦ 属性值为 null 视为缺失。
    for (const QJsonValue& req : required) {
        const QString key = req.toString();
        const auto it = args.constFind(key);
        if (it == args.constEnd() || it->isNull()) {
            r.reasons << QString::fromUtf8("缺少必填参数：%1").arg(key);
        }
    }

    // ③④⑤ 对每个已声明且实际提供（非 null）的属性做类型 / enum / 边界校验。
    // QJsonObject 按键字典序迭代，结果确定。
    for (auto propIt = props.constBegin(); propIt != props.constEnd(); ++propIt) {
        const QString key = propIt.key();
        const auto argIt = args.constFind(key);
        if (argIt == args.constEnd() || argIt->isNull()) continue; // 未提供或 null → 跳过
        const QJsonValue v = *argIt;
        const QJsonObject p = propIt->toObject();
        const QString type = p.value("type").toString();

        // ③ 值类型匹配。
        bool typeOk;
        if (type == QLatin1String("number")) {
            typeOk = valueIsNumber(v);
        } else if (type == QLatin1String("integer")) {
            typeOk = valueIsIntegral(v);
        } else if (type == QLatin1String("string")) {
            typeOk = v.isString();
        } else if (type == QLatin1String("boolean")) {
            typeOk = v.isBool();
        } else {
            r.reasons << QString::fromUtf8("参数 %1 的 schema 类型未知：%2").arg(key, type);
            continue;
        }
        if (!typeOk) {
            r.reasons << QString::fromUtf8("参数 %1 类型错误：期望 %2，实得 %3")
                             .arg(key, type, describeJsonType(v));
            continue; // 类型错则该属性不再做 enum / 边界
        }

        // ④ enum 成员校验（schema 属性含 enum 时）。
        if (p.contains("enum")) {
            const QJsonArray en = p.value("enum").toArray();
            if (!enumContains(en, v)) {
                r.reasons << QString::fromUtf8("参数 %1 取值不在枚举中").arg(key);
            }
        }

        // ⑤ minimum / maximum 边界（仅对数值生效；非数值已在 ③ 报错）。
        if (valueIsNumber(v)) {
            const double d = v.toDouble();
            if (p.contains("minimum") && valueIsNumber(p.value("minimum"))) {
                const double lo = p.value("minimum").toDouble();
                if (d < lo) {
                    r.reasons << QString::fromUtf8("参数 %1 超出下界：%2 < %3").arg(key).arg(d).arg(lo);
                }
            }
            if (p.contains("maximum") && valueIsNumber(p.value("maximum"))) {
                const double hi = p.value("maximum").toDouble();
                if (d > hi) {
                    r.reasons << QString::fromUtf8("参数 %1 超出上界：%2 > %3").arg(key).arg(d).arg(hi);
                }
            }
        }
    }

    // ⑥ 白名单：args 中任何不在 properties 里的键一律拒绝（幻觉参数）。
    for (auto argIt = args.constBegin(); argIt != args.constEnd(); ++argIt) {
        const QString key = argIt.key();
        if (!props.contains(key)) {
            r.reasons << QString::fromUtf8("未知参数（schema 外）：%1").arg(key);
        }
    }

    if (r.reasons.isEmpty()) {
        r.ok = true;
        r.errorJson.clear();
        return r;
    }

    r.ok = false;
    QJsonObject err;
    err["ok"] = false;
    err["tool"] = toolName;
    err["error"] = QString::fromUtf8("参数校验失败：%1").arg(r.reasons.first());
    QJsonArray ra;
    for (const QString& s : r.reasons) ra.append(s);
    err["reasons"] = ra;
    r.errorJson = QString::fromUtf8(QJsonDocument(err).toJson(QJsonDocument::Compact));
    return r;
}

} // namespace ai
} // namespace mbdsdr
