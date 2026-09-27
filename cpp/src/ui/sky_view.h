// SPDX-License-Identifier: MIT
#pragma once

#include <QWidget>
#include <QList>
#include <QString>
#include <QPointF>

namespace mbdsdr {
namespace ui {

struct PassArc {
    QString name;
    QList<QPair<double,double>> track; // sampled (az, el) degrees; horizon -> horizon
};

class SkyView : public QWidget {
    Q_OBJECT
public:
    explicit SkyView(QWidget* parent = nullptr);
    void setPasses(QList<PassArc> passes);
    // Highlight the pass at the given list index (different colour/width).
    // Pass -1 to clear the highlight.
    void setHighlightedPass(int index);
    // Honest empty-state caption shown when there are no arcs.
    void setEmptyText(const QString& text);
    // Draw (or update) the live satellite position as a glowing dot. Call
    // clearLiveSatellite() to hide it.
    void setLiveSatellite(double az, double el, const QString& name);
    void clearLiveSatellite();

protected:
    void paintEvent(QPaintEvent*) override;

private:
    QList<PassArc> passes_;
    int highlighted_ = -1;
    QString emptyText_ = QStringLiteral("无过境数据");
    bool liveValid_ = false;
    double liveAz_ = 0.0;
    double liveEl_ = 0.0;
    QString liveName_;
};

} // namespace ui
} // namespace mbdsdr
