// SPDX-License-Identifier: MIT
// ft8_detector.h -- FT8 检测层（DSP 最小接入）。
//
// 干净室自写 C++（MIT），机制镜像 Python mbdsdr_ai/ft8_modem.py 的
// Ft8CostasSync：15 s 窗 FFT 能量 -> 符号谱（4x 时间 / 2x 频率过采样，
// 3.125 Hz bin）-> S7/D29/S7/D29/S7 二维 Costas 相关峰粗同步 ->
// 8-tone 能量 -> LLR 提取（逆格雷分组，正=bit0，镜像 ft8_codec.py:335）。
//
// 本轮为**检测层**：输出候选帧统计（sync_quality / 估计频偏/时偏 / SNR /
// 候选计数）。C++ BP 解码移植留第④步；本轮不编造解码消息，无信号 -> 零候选。
#pragma once

#include <complex>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace mbdsdr {

struct Ft8Candidate {
    bool    valid = false;
    double  freqOffsetHz = 0.0;
    double  timeOffsetSec = 0.0;
    double  syncQuality = 0.0;   // 相关峰/次峰比
    double  snrDb = 0.0;
    int     dataSymbolsDetected = 0;
};

class Ft8Detector {
public:
    Ft8Detector();

    // 注入一段 12 kS/s 复基带（complex float）。processWindow() 在 15 s 窗内
    // 做 Costas 二维搜索，返回最佳候选（无信号 -> valid=false，诚实空态）。
    void setEnabled(bool on) { enabled_ = on; }
    bool enabled() const { return enabled_; }

    // 处理 15 s 窗（180000 复样本）。返回最佳候选；无信号 valid=false。
    Ft8Candidate processWindow(const std::complex<float>* iq, std::size_t n);

    // 最近一次处理的候选数（诚实计数）。
    int lastCandidateCount() const { return lastCandidates_; }

    // 最近一次检出帧的 174 个 LLR（正=bit0，逆格雷分组，镜像 ft8_codec.py:335）。
    // 无检出 -> 空 vector（诚实空态）。
    const std::vector<double>& lastLlr174() const { return lastLlr_; }

    // SIC：谱减最强信号后二次检出的第二候选（无第二信号 -> valid=false）。
    const Ft8Candidate& lastSecondary() const { return secondary_; }

private:
    bool enabled_ = false;
    int  lastCandidates_ = 0;
    std::vector<double> lastLlr_;
    Ft8Candidate secondary_;
};

} // namespace mbdsdr
