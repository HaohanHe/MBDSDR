// SPDX-License-Identifier: MIT
#include "ai_config.h"

#include <QStandardPaths>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <cstdlib>

namespace mbdsdr {
namespace ai {

QString AiConfig::configPath() const {
    auto path = QStandardPaths::writableLocation(QStandardPaths::AppConfigLocation);
    QDir().mkpath(path);
    return path + "/ai_config.json";
}

bool AiConfig::load() {
    // Priority order: environment variable > config file > built-in default.
    // The env var (MBDSDR_API_KEY) wins when set; the config file only fills
    // in fields the env does not provide. Do NOT let an empty/absent file
    // value clobber an already-set env value.
    auto envKey = qgetenv("MBDSDR_API_KEY");
    if (!envKey.isEmpty()) apiKey = QString::fromUtf8(envKey);

    QFile f(configPath());
    if (f.open(QIODevice::ReadOnly)) {
        auto doc = QJsonDocument::fromJson(f.readAll());
        auto obj = doc.object();
        // Only read the file's api_key when the env var did not already provide
        // one. Previously this branch inverted the priority by overwriting the
        // env-derived value with whatever the file held (including empty).
        if (envKey.isEmpty() && obj.contains("api_key"))
            apiKey = obj["api_key"].toString();
        if (obj.contains("base_url")) baseUrl = obj["base_url"].toString();
        if (obj.contains("model")) model = obj["model"].toString();
        if (obj.contains("audio_device")) audioDevice = obj["audio_device"].toString();
        if (obj.contains("station_lat")) { stationLat = obj["station_lat"].toDouble(); stationSet = true; }
        if (obj.contains("station_lon")) { stationLon = obj["station_lon"].toDouble(); stationSet = true; }
        if (obj.contains("station_alt")) stationAlt = obj["station_alt"].toDouble();
        f.close();
    }
    return isConfigured();
}

bool AiConfig::save() const {
    QJsonObject obj;
    obj["api_key"] = apiKey;
    obj["base_url"] = baseUrl;
    obj["model"] = model;
    obj["audio_device"] = audioDevice;
    if (stationSet) {
        obj["station_lat"] = stationLat;
        obj["station_lon"] = stationLon;
        obj["station_alt"] = stationAlt;
    }
    QFile f(configPath());
    if (!f.open(QIODevice::WriteOnly)) return false;
    f.write(QJsonDocument(obj).toJson());
    f.close();
    return true;
}

} // namespace ai
} // namespace mbdsdr
