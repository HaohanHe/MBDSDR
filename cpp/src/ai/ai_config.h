// SPDX-License-Identifier: MIT
#pragma once

#include <QString>

namespace mbdsdr {
namespace ai {

struct AiConfig {
    QString apiKey;
    QString baseUrl = "https://api.siliconflow.cn/v1";
    QString model = "Qwen/Qwen2.5-7B-Instruct";
    double stationLat = 39.9;   // default Beijing
    double stationLon = 116.4;

    bool load();
    bool save() const;
    bool isConfigured() const { return !apiKey.isEmpty(); }
    QString configPath() const;
};

} // namespace ai
} // namespace mbdsdr
