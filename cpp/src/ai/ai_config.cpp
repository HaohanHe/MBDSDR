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
    // Env var takes priority
    auto envKey = qgetenv("MBDSDR_API_KEY");
    if (!envKey.isEmpty()) apiKey = QString::fromUtf8(envKey);

    QFile f(configPath());
    if (f.open(QIODevice::ReadOnly)) {
        auto doc = QJsonDocument::fromJson(f.readAll());
        auto obj = doc.object();
        if (!envKey.isEmpty()) apiKey = obj["api_key"].toString();
        if (obj.contains("base_url")) baseUrl = obj["base_url"].toString();
        if (obj.contains("model")) model = obj["model"].toString();
        f.close();
    }
    return isConfigured();
}

bool AiConfig::save() const {
    QJsonObject obj;
    obj["api_key"] = apiKey;
    obj["base_url"] = baseUrl;
    obj["model"] = model;
    QFile f(configPath());
    if (!f.open(QIODevice::WriteOnly)) return false;
    f.write(QJsonDocument(obj).toJson());
    f.close();
    return true;
}

} // namespace ai
} // namespace mbdsdr
