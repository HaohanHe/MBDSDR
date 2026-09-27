// SPDX-License-Identifier: MIT
#pragma once

#include <QObject>
#include <QString>
#include <QList>

namespace mbdsdr {
namespace dsp {

struct SatPos {
    QString name;
    double azDeg;
    double elDeg;
};

class TleClient : public QObject {
    Q_OBJECT
public:
    explicit TleClient(QObject* parent = nullptr);
    void fetch();  // async fetch from celestrak

signals:
    void positionsReady(QList<SatPos> positions);
    void fetchFailed(const QString& reason);

private:
    QList<SatPos> computePositions(double stationLat, double stationLon);
};

} // namespace dsp
} // namespace mbdsdr
