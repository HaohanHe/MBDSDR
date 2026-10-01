// SPDX-License-Identifier: MIT
// Offscreen visual self-check for round-11 task-2:
//   cpp/scratch/ui_timing_panel.png -- timing-service three-state chip + clock
//                                      readout (honest empty: no GNSS module).
//   cpp/scratch/ui_nav_sats.png     -- "在视导航卫星（预测）" table.
// NOT in ctest; built as ui_shot_timing, run offscreen.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QLabel>
#include <QTableWidget>
#include <QHeaderView>
#include <QSettings>
#include <QDir>
#include <QPainter>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "ui/sky_view.h"

using namespace mbdsdr;

static const QString kOut =
    "/home/user/Doubao/chats/38438160041798146/cpp/scratch/";

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_timing");
    app.setStyleSheet(tokens::buildDarkQss());

    MainWindow win;
    win.resize(tokens::scaled(1280), tokens::scaled(800));
    win.show();

    QTimer::singleShot(1200, [&]() {
        // ---- Timing panel: three-state chip + clock readout ----------------
        QLabel* state = nullptr;
        QLabel* clock = nullptr;
        for (QLabel* l : win.findChildren<QLabel*>()) {
            if (l->objectName() == "timingState") state = l;
            if (l->objectName() == "monoInfo" &&
                l->text().contains(QStringLiteral("时钟域"))) clock = l;
        }
        if (state && clock) {
            QPixmap pm = clock->grab();
            pm.save(kOut + "ui_timing_panel.png", "PNG");
            qInfo("ui_timing_panel.png %dx%d  state=%s  clock=%s", pm.width(), pm.height(),
                  qPrintable(state->text()), qPrintable(clock->text()));
        }

        // ---- Visible nav satellites (prediction) table ---------------------
        for (QTableWidget* t : win.findChildren<QTableWidget*>())
            qInfo("table: %s", qPrintable(t->objectName()));
        if (QTableWidget* tbl = win.findChild<QTableWidget*>("navSatTable")) {
                // Seed two labelled sample rows to illustrate the populated
                // layout (offline has no GNSS TLE -> otherwise empty state).
                tbl->setRowCount(0);
                auto add = [&](const QString& cat, const QString& name,
                               double az, double el, double rng) {
                    int row = tbl->rowCount();
                    tbl->insertRow(row);
                    tbl->setItem(row, 0, new QTableWidgetItem(cat));
                    tbl->setItem(row, 1, new QTableWidgetItem(name + "  (预测)"));
                    tbl->setItem(row, 2, new QTableWidgetItem(QString::number(az, 'f', 1)));
                    tbl->setItem(row, 3, new QTableWidgetItem(QString::number(el, 'f', 1)));
                    tbl->setItem(row, 4, new QTableWidgetItem(QString::number(rng, 'f', 0)));
                };
                add("28471", "GPS BIIR-4 (PRN 2)", 312.4, 43.7, 23412);
                add("37753", "GPS IIF-1 (PRN 1)",   88.1, 61.2, 20158);
                tbl->resizeColumnsToContents();
                tbl->resize(640, tbl->sizeHint().height());
                QPixmap pm = tbl->grab();
                pm.save(kOut + "ui_nav_sats.png", "PNG");
                qInfo("ui_nav_sats.png %dx%d", pm.width(), pm.height());
        }
        app.quit();
    });
    return app.exec();
}
