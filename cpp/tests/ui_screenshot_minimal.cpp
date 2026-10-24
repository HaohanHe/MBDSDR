/**
 * @file ui_screenshot_minimal.cpp
 * @brief Qt offscreen 渲染管线最小自检工具（NOT an MBDSDR UI screenshot）。
 *
 * 诚实说明（phase75 补）：本工具刻意不链接 mbdsdr_core，因此它渲染的是
 * 一个临时构造的空壳 QMainWindow（菜单 + 居中标签），**画面里没有任何
 * MBDSDR 的真实界面元素**。它验证的只是「Qt offscreen 平台插件能渲染并
 * grab 出 PNG」，产物**不可**当作 MBDSDR UI 的渲染证据——真正的 UI 取证
 * 请用构建了 mbdsdr::MainWindow 的 ui_screenshot_r14.cpp（ui_shot_r27）
 * 等工具。文件名里的 ui_screenshot_ 前缀是历史沿袭，不代表它截的是本项目的 UI。
 *
 * Not in CMake（与多数 ui_screenshot_*.cpp 一样，按需手工构建）。
 * Usage: ui_shot_minimal [output.png]   # 默认 qt_offscreen_render_check.png
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

    const char *output = argc > 1 ? argv[1] : "qt_offscreen_render_check.png";

    // A bare-bones placeholder window: it mimics NOTHING of the real app layout,
    // because this tool does not link mbdsdr_core. It only proves the offscreen
    // platform plugin can render and grab a widget tree to PNG.
    QMainWindow win;
    win.setWindowTitle("Qt Offscreen Render Check (not an MBDSDR UI screenshot)");
    win.resize(800, 600);

    win.menuBar()->addMenu("File")->addAction("Exit");
    win.menuBar()->addMenu("Radio")->addAction("Tune");
    win.menuBar()->addMenu("View")->addAction("Spectrum");

    auto *central = new QLabel(
        "Qt offscreen render check\nNOT an MBDSDR UI screenshot", &win);
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
