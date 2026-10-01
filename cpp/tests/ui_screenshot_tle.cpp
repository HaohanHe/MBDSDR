// SPDX-License-Identifier: MIT
// Offscreen self-check: TLE freshness panel (category / epoch / days / status).
//   cpp/scratch/ui_tle_freshness.png
// NOT in ctest; built as ui_shot_tle, run offscreen.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QLabel>
#include <QSettings>
#include <QDir>

#include "core/tokens.h"
#include "ui/main_window.h"

using namespace mbdsdr;

static const QString kOut =
    "/home/user/Doubao/chats/38438160041798146/cpp/scratch/";

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_tle");
    app.setStyleSheet(tokens::buildDarkQss());

    MainWindow win;
    win.resize(tokens::scaled(1280), tokens::scaled(800));
    win.show();
    QTimer::singleShot(1200, [&]() {
        QLabel* fresh = win.findChild<QLabel*>("tleFreshLabel");
        if (fresh) {
            fresh->setText(QStringLiteral(
                "TLE 类别 GNSS/气象/空间站/业余 · 历元 2026-09-28 · 3 天前 · 新鲜"));
            fresh->resize(760, fresh->sizeHint().height());
            QPixmap pm = fresh->grab();
            pm.save(kOut + "ui_tle_freshness.png", "PNG");
            qInfo("ui_tle_freshness.png %dx%d", pm.width(), pm.height());
        } else {
            qWarning("no freshness label found");
        }
        app.quit();
    });
    return app.exec();
}
