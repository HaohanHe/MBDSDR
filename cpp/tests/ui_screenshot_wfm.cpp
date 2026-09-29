// Offscreen WFM receive-area screenshot harness (NOT part of main build).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QComboBox>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication::setAttribute(Qt::AA_EnableHighDpiScaling);
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    mbdsdr::MainWindow win;
    win.show();

    QTimer::singleShot(300, [&]() {
        if (auto* cb = win.findChild<QComboBox*>("demodCombo"))
            cb->setCurrentIndex(2);  // WFM
    });

    // Default synthetic source has NO stereo pilot, so the badge must honestly
    // read "单声道" (mono). Give the engine time to lock up the chain.
    QTimer::singleShot(1500, [&]() {
        QPixmap pm = win.grab();
        pm.save("/tmp/mbdsdr_wfm.png", "PNG");
        qInfo("WFM screenshot saved to /tmp/mbdsdr_wfm.png (%dx%d)",
              pm.width(), pm.height());
        app.quit();
    });

    return app.exec();
}
