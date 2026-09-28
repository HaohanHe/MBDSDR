// SPDX-License-Identifier: MIT
// MBDSDR native C++ -- entry point.
// No Python runtime, no embeddings, no subprocesses.
#include <QApplication>
#include <QDebug>
#include <QSettings>
#include <QString>
#include <QStringList>

#include "core/tokens.h"
#include "ui/main_window.h"

static void printHelp() {
    qInfo().noquote() <<
        "MBDSDR C++ -- native SDR desktop application (Phase 1)\n"
        "\n"
        "Usage: mbdsdr [options]\n"
        "\n"
        "Options:\n"
        "  --help        Show this help and exit.\n"
        "  --offscreen   Hint: run with Qt offscreen platform.\n"
        "                Equivalent: QT_QPA_PLATFORM=offscreen ./mbdsdr\n"
        "\n"
        "Data sources: Phase 1 uses an OFFLINE TEST SIGNAL generator.\n"
        "              No SDR hardware is opened. All on-screen data is\n"
        "              labeled \"TEST SIGNAL - NOT HARDWARE\".\n"
        "\n"
        "Build: Qt6 Widgets + C++17 CMake project. Single executable.\n"
        "No Python runtime is required at runtime.";
}

int main(int argc, char** argv) {
    QApplication::setAttribute(Qt::AA_EnableHighDpiScaling);
    QApplication::setAttribute(Qt::AA_UseHighDpiPixmaps);
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
        }
    }

    QApplication app(argc, argv);
    QApplication::setApplicationName("mbdsdr");
    QApplication::setApplicationVersion("0.2.0");

    // Restore persisted UI scale before building the stylesheet.
    {
        QSettings s("MBDSDR", "MBDSDR");
        mbdsdr::tokens::setUserScale(s.value("ui/scaleFactor", 1.0).toDouble());
    }
    // Apply dark QSS translated from desktop/tokens.py
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    mbdsdr::MainWindow win;
    win.show();

    return app.exec();
}
