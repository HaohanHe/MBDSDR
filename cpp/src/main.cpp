// SPDX-License-Identifier: MIT
// MBDSDR native C++ -- entry point.
// No Python runtime, no embeddings, no subprocesses.
#include <QApplication>
#include <QCoreApplication>
#include <QDebug>
#include <QPixmap>
#include <QSettings>
#include <QString>
#include <QStringList>
#include <QTimer>

#include "core/tokens.h"
#include "ui/main_window.h"

static void printHelp() {
    qInfo().noquote() <<
        "MBDSDR C++ -- native SDR desktop application\n"
        "\n"
        "Usage: mbdsdr [options]\n"
        "\n"
        "Options:\n"
        "  --help            Show this help and exit.\n"
        "  --offscreen       Hint: run with Qt offscreen platform.\n"
        "                    Equivalent: QT_QPA_PLATFORM=offscreen ./mbdsdr\n"
        "  --scale <factor>  Override the UI scale factor (0.7 .. 2.5) for this\n"
        "                    run, ignoring the saved QSettings value. Intended\n"
        "                    for automation / DPI sweep verification.\n"
        "  --snapshot <path> Run the UI, wait for the first render, save a PNG\n"
        "                    screenshot to <path> and exit. Lets scripts verify\n"
        "                    the UI headlessly (use with --offscreen).\n"
        "\n"
        "Data sources: real SDR hardware via rtl_tcp / SoapySDR when connected;\n"
        "otherwise an OFFLINE TEST SIGNAL generator runs and all on-screen data\n"
        "is labeled \"非硬件 / NOT HARDWARE\". No fake hardware is ever shown.\n"
        "\n"
        "Build: Qt6 Widgets + C++17 CMake project. Single executable.\n"
        "No Python runtime is required at runtime.";
}

int main(int argc, char** argv) {
    QApplication::setAttribute(Qt::AA_EnableHighDpiScaling);
    QApplication::setAttribute(Qt::AA_UseHighDpiPixmaps);

    double  cliScale    = 0.0;   // 0 = unset; only a positive value overrides
    QString cliSnapshot;         // empty = unset

    // Parse simple CLI flags
    for (int i = 1; i < argc; ++i) {
        QString a = QString::fromLatin1(argv[i]);
        if (a == "--help" || a == "-h") {
            printHelp();
            return 0;
        }
        if (a == "--offscreen") {
            qInfo() << "[mbdsdr] Hint: use QT_QPA_PLATFORM=offscreen for headless runs.";
            qInfo() << "[mbdsdr] e.g. QT_QPA_PLATFORM=offscreen ./mbdsdr";
            // We don't force-set it here; the user (or the test harness)
            // exports QT_QPA_PLATFORM=offscreen externally.
        } else if (a == "--scale" && i + 1 < argc) {
            bool ok = false;
            const double v = QString::fromLatin1(argv[++i]).toDouble(&ok);
            if (ok && v > 0.0) cliScale = v;
            else qWarning() << "[mbdsdr] --scale: expected a positive number, got" << argv[i];
        } else if (a == "--snapshot" && i + 1 < argc) {
            cliSnapshot = QString::fromLocal8Bit(argv[++i]);
        } else if (a.startsWith(QLatin1String("--"))) {
            qWarning() << "[mbdsdr] unknown option:" << a;
        }
    }

    QApplication app(argc, argv);
    QApplication::setApplicationName("mbdsdr");
    QApplication::setApplicationVersion("0.2.0");

    // Restore persisted UI scale before building the stylesheet; a --scale on
    // the command line wins for this run (automation hooks only, no write-back).
    {
        QSettings s("MBDSDR", "MBDSDR");
        double scale = s.value("ui/scaleFactor", 1.0).toDouble();
        if (cliScale > 0.0) scale = cliScale;
        mbdsdr::tokens::setUserScale(scale);
        if (cliScale > 0.0) qInfo() << "[mbdsdr] UI scale" << scale
                                    << "(CLI override)";
    }
    // Apply dark QSS translated from desktop/tokens.py
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    mbdsdr::MainWindow win;
    win.show();

    if (!cliSnapshot.isEmpty()) {
        // Offscreen-verifiable render: wait one paint cycle, save, exit.
        QTimer::singleShot(1500, [&win, cliSnapshot]() {
            const QPixmap pm = win.grab();
            if (pm.save(cliSnapshot, "PNG"))
                qInfo() << "[mbdsdr] snapshot saved to" << cliSnapshot;
            else
                qWarning() << "[mbdsdr] snapshot FAILED:" << cliSnapshot;
            QCoreApplication::quit();
        });
    }

    return app.exec();
}
