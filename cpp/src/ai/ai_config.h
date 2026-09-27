// SPDX-License-Identifier: MIT
#pragma once

#include <QString>
#include <limits>

namespace mbdsdr {
namespace ai {

struct AiConfig {
    QString apiKey;
    QString baseUrl = "https://api.siliconflow.cn/v1";
    QString model = "Qwen/Qwen2.5-7B-Instruct";
    double stationLat = std::numeric_limits<double>::quiet_NaN();
    double stationLon = std::numeric_limits<double>::quiet_NaN();
    bool stationSet = false;

    bool load();
    bool save() const;
    bool isConfigured() const { return !apiKey.isEmpty(); }
    QString configPath() const;
};

} // namespace ai
} // namespace mbdsdr
