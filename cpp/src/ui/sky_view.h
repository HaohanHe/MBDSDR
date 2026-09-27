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
    double azStart;  // degrees
    double elStart;
    double azMax;
    double elMax;
    double azEnd;
    double elEnd;
    bool isDemo = false;
};

class SkyView : public QWidget {
    Q_OBJECT
public:
    explicit SkyView(QWidget* parent = nullptr);
    void setPasses(QList<PassArc> passes);

protected:
    void paintEvent(QPaintEvent*) override;

private:
    QList<PassArc> passes_;
};

} // namespace ui
} // namespace mbdsdr
