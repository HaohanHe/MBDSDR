// SPDX-License-Identifier: MIT
//
// 时空视图 (Spacetime View) tab: 状态 -> 文本 / 颜色角色 的纯函数映射。
// ============================================================================
// 与 status_format.h 同一套思路：原本要在 MainWindow 的各个槽里内联拼接的
// "设备/信号/解码/GNSS 四格 + 当前目标 + 时间源 + 多普勒补偿" 文案，全部抽成
// 无 Qt-widget 状态的 header-only 纯函数，可被 offscreen 单测直接断言。
//
// 诚实空态红线（无硬件时云内默认）：
//   * 设备 未连接 -> "未连接（非硬件）"（Neutral，不报警红）；
//   * 信号   rssi/snr 未知 -> "--"；
//   * 解码   无活动 -> "无解码"；
//   * GNSS   无 fix -> "无 fix"（绝不画一个假点 / 假坐标）；
//   * 时间源 上游已解析为 "gnss"|"system"；本函数绝不把 system 升级成 gnss，
//            system 一律带 "本机时钟，非 GNSS 授时" 的诚实标注（Warn）；
//   * 多普勒 无目标 -> "未补偿（无目标）"。
//
// 颜色只抽象成 widget 无关的 SpRole 枚举；tab 的 .cpp 再把角色映射到 tokens 色板。
// 这样本头文件只依赖 QtCore + QString，单测无需起 QApplication。
#pragma once

#include <QtCore/QString>

#include <cmath>

namespace mbdsdr {
namespace ui {

// Widget 无关的语义颜色角色。tab .cpp 用它查 tokens 色板；这里不碰 QColor。
enum class SpRole {
    Neutral,  // 空占位，不是错误（诚实的"还没有数据"）
    Info,     // 中性信息 / 实时真实读数
    Ok,       // 好 / 实时真实（绿）
    Warn,     // 诚实告诫 / 需注意（琥珀）
    Danger,   // 真实错误 / 链路断开（红）
};

// 角色 -> 稳定字符串 key（可测）；widget 再映射成具体 QColor。
inline const char* spRoleKey(SpRole r) {
    switch (r) {
    case SpRole::Ok:      return "ok";
    case SpRole::Warn:    return "warn";
    case SpRole::Danger:  return "danger";
    case SpRole::Info:    return "info";
    case SpRole::Neutral: return "neutral";
    }
    return "neutral";
}

// 一个四格 tile：标题 + 主文案 + 颜色角色。
struct SpTile {
    QString title;
    QString text;
    SpRole  role = SpRole::Neutral;
};

// 一行状态（当前目标 / 时间源 / 多普勒）：文案 + 颜色角色。
struct SpLine {
    QString text;
    SpRole  role = SpRole::Neutral;
};

// ---- 四格总览 --------------------------------------------------------------

// 设备连接。connected=true -> Ok + 真实源名；false -> Neutral "未连接（非硬件）"。
inline SpTile spTileDevice(bool connected, const QString& name) {
    if (connected) {
        return {QStringLiteral("设备连接"),
                name.isEmpty() ? QStringLiteral("已连接") : name,
                SpRole::Ok};
    }
    return {QStringLiteral("设备连接"),
            QStringLiteral("未连接（非硬件）"),
            SpRole::Neutral};
}

// 信号。rssi/snr 为 NaN（还没读到真实值）-> 诚实 "--"；有值 -> Info。
inline SpTile spTileSignal(float rssiDbfs, float snrDb) {
    const bool haveRssi = !std::isnan(rssiDbfs);
    const bool haveSnr  = !std::isnan(snrDb);
    if (!haveRssi && !haveSnr)
        return {QStringLiteral("信号"), QStringLiteral("--"), SpRole::Neutral};
    const QString r = haveRssi ? QString("%1 dBFS").arg(rssiDbfs, 0, 'f', 1)
                               : QStringLiteral("--");
    const QString s = haveSnr ? QString("SNR %1 dB").arg(snrDb, 0, 'f', 1)
                               : QStringLiteral("--");
    return {QStringLiteral("信号"), QString("%1 · %2").arg(r, s), SpRole::Info};
}

// 解码状态。模式为空或帧数<=0 -> 诚实 "无解码"（绝不编一个解码结果）。
inline SpTile spTileDecode(const QString& mode, int framesDecoded) {
    if (mode.isEmpty() || framesDecoded <= 0)
        return {QStringLiteral("解码状态"), QStringLiteral("无解码"), SpRole::Neutral};
    return {QStringLiteral("解码状态"),
            QString("%1 · %2 帧").arg(mode).arg(framesDecoded),
            SpRole::Ok};
}

// GNSS 定位。无 fix -> "无 fix"（Neutral，绝不画假点/假坐标）。
inline SpTile spTileGnss(bool hasFix, double lat, double lon,
                         int sats, double hdop) {
    if (!hasFix)
        return {QStringLiteral("GNSS 定位"), QStringLiteral("无 fix"), SpRole::Neutral};
    return {QStringLiteral("GNSS 定位"),
            QString("%1,%2 · 星%3 · HDOP %4")
                .arg(lat, 0, 'f', 5).arg(lon, 0, 'f', 5)
                .arg(sats).arg(hdop, 0, 'f', 1),
            SpRole::Ok};
}

// ---- 三行状态 --------------------------------------------------------------

// 时间源。`source` 已由上层按真实 NMEA 解析结果解析为 "gnss"|"system"；
// 本函数绝不把 system 升级成 gnss：gnss -> Ok；其它（含 system）-> Warn，
// 并显式标注"本机时钟，非 GNSS 授时"。utcIso 为空 -> 诚实 "--"。
inline SpLine spLineTimeSource(const QString& source, const QString& utcIso) {
    const QString utc = utcIso.isEmpty() ? QStringLiteral("--") : utcIso;
    if (source == QStringLiteral("gnss"))
        return {QString("时间源 GNSS · %1 UTC").arg(utc), SpRole::Ok};
    return {QString("时间源 system（本机时钟，非 GNSS 授时）· %1").arg(utc),
            SpRole::Warn};
}

// 当前接收目标。name 为空 -> 诚实 "无接收目标"（绝不编一个卫星名）。
inline SpLine spLineTarget(const QString& name, double centerHz) {
    if (name.isEmpty())
        return {QStringLiteral("当前接收目标：无"), SpRole::Neutral};
    const QString f = centerHz > 0.0
        ? QString(" · %1 MHz").arg(centerHz / 1.0e6, 0, 'f', 4)
        : QString();
    return {QString("当前接收目标：%1%2").arg(name, f), SpRole::Info};
}

// 多普勒补偿。armed && hasTarget -> Ok "补偿中"；armed 但无目标 -> Warn（状态
// 不一致）；未 armed -> 诚实 "未补偿（无目标）"。residualHz 非有限时不显示残差。
inline SpLine spLineDoppler(bool armed, bool hasTarget, double residualHz) {
    if (armed && hasTarget) {
        const QString r = std::isfinite(residualHz)
            ? QString(" · 残差 %1 Hz").arg(residualHz, 0, 'f', 1)
            : QString();
        return {QString("多普勒补偿：补偿中（轨道传播实时微调）%1").arg(r),
                SpRole::Ok};
    }
    if (armed && !hasTarget)
        return {QStringLiteral("多普勒补偿：补偿开但无目标（状态不一致）"),
                SpRole::Warn};
    return {QStringLiteral("多普勒补偿：未补偿（无目标）"), SpRole::Neutral};
}

} // namespace ui
} // namespace mbdsdr
