/**
 * @file ui_screenshot_minimal.cpp
 * @brief Minimal UI screenshot tool for offscreen verification.
 *
 * Builds a minimal MainWindow, renders it offscreen, and saves PNG.
 * Usage: ui_shot_minimal [output.png]
 */

#include <QApplication>
#include <QMainWindow>
#include <QMenuBar>
#include <QStatusBar>
#include <QLabel>
#include <QPixmap>
#include <QPainter>
#include <QTimer>
#include <cstdio>

int main(int argc, char *argv[]) {
    // Force offscreen before QApplication
    qputenv("QT_QPA_PLATFORM", "offscreen");

    QApplication app(argc, argv);

    const char *output = argc > 1 ? argv[1] : "ui_shot_minimal.png";

    // Build a minimal window mimicking the app layout
    QMainWindow win;
    win.setWindowTitle("MBDSDR UI Test");
    win.resize(800, 600);

    win.menuBar()->addMenu("File")->addAction("Exit");
    win.menuBar()->addMenu("Radio")->addAction("Tune");
    win.menuBar()->addMenu("View")->addAction("Spectrum");

    auto *central = new QLabel("MBDSDR Main Window\nUI Rendering Test", &win);
    central->setAlignment(Qt::AlignCenter);
    win.setCentralWidget(central);

    win.statusBar()->showMessage("Ready");

    win.show();

    // Render and capture after event loop starts
    QTimer::singleShot(100, [&]() {
        QPixmap pixmap = win.grab();
        bool ok = pixmap.save(output);
        std::printf("Saved %s: %s (%dx%d)\n",
                    output, ok ? "OK" : "FAILED",
                    pixmap.width(), pixmap.height());
        app.quit();
    });

    return app.exec();
}
