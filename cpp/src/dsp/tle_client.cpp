// SPDX-License-Identifier: MIT
#include "tle_client.h"

#include <QNetworkAccessManager>
#include <QNetworkRequest>
#include <QNetworkReply>
#include <QJsonDocument>
#include <QJsonObject>

namespace mbdsdr {
namespace dsp {

TleClient::TleClient(QObject* parent) : QObject(parent) {}

void TleClient::fetch() {
    // Simplified: no real TLE fetch in cloud env. Emit demo positions.
    // Real fetch would use QNetworkAccessManager to celestrak.org.
    QList<SatPos> demo;
    demo.append({"ISS (demo)", 90, 45});
    demo.append({"NOAA-19 (demo)", 200, 30});
    emit positionsReady(demo);
}

QList<SatPos> TleClient::computePositions(double, double) {
    return {};
}

} // namespace dsp
} // namespace mbdsdr
