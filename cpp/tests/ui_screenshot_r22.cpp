// SPDX-License-Identifier: MIT
#include <QApplication>
#include <QPixmap>
#include <QTimer>
#include "core/tokens.h"
#include "ui/main_window.h"
#include "ui/settings_dialog.h"
#include "ai/ai_config.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());
    mbdsdr::MainWindow win;
    win.resize(1280, 800);
    win.show();
    QTimer::singleShot(1500, [&]() {
        mbdsdr::ui::SettingsDialog dlg(&win);
        mbdsdr::ai::AiConfig cfg; cfg.load();
        dlg.loadFromConfig(cfg);
        dlg.setWindowTitle("设置");
        dlg.show();
        QTimer::singleShot(600, [&]() {
            win.grab().save("/tmp/mbdsdr_r22_settings.png", "PNG");
            app.quit();
        });
    });
    return app.exec();
}
