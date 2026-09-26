"""路A 桌面 UI offscreen 截图自证。

生成 6 张：
  /tmp/ui_focus.png       focus 预设（全屏频谱 + 右侧窄条）
  /tmp/ui_analysis.png    analysis 预设（频谱 + 右侧天空 + 底部控制）
  /tmp/ui_grid.png        grid 预设（多区分栏）
  /tmp/ui_disconnected.png  断连态（无设备，全部 -- / 置灰）
  /tmp/ui_small.png       800x600 小窗口（QScrollArea 不裁切）
  /tmp/ui_floating.png    浮动 dock（24px 圆角 + 半透明）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QTimer, Qt  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def pump(ms=120):
    QTimer.singleShot(ms, app.quit)
    app.exec()


def grab(w, path):
    pump(80)
    pm = w.grab()
    pm.save(path)
    print("saved", path, pm.width(), "x", pm.height())


def main():
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    pump(300)  # 让自动枚举/定时器各跑一轮，稳定布局

    # 切到 dark_car 设计体系
    w._apply_theme("dark_car")
    pump(60)

    dlm = w.dock_layout

    def to(size=(1400, 900)):
        w.resize(size[0], size[1])
        pump(120)

    # 1) focus
    dlm.apply_preset("focus")
    to((1400, 900))
    grab(w, "/tmp/ui_focus.png")

    # 2) analysis
    dlm.apply_preset("analysis")
    to((1400, 900))
    grab(w, "/tmp/ui_analysis.png")

    # 3) grid
    dlm.apply_preset("grid")
    to((1400, 900))
    grab(w, "/tmp/ui_grid.png")

    # 4) disconnected（无硬件默认即断连态；显式 _disconnect 确保全部 -- / 置灰）
    dlm.apply_preset("focus")
    pump(60)
    try:
        w._disconnect()
    except Exception:
        pass
    to((1400, 900))
    grab(w, "/tmp/ui_disconnected.png")

    # 5) small window 800x600 —— 证明 dock 内 QScrollArea 不裁切。
    #    临时放宽最小尺寸（正式最小窗是 1200x800，这里只为验证小窗滚动不裁切）。
    #    直接把控制坞停靠在右侧并可见：窄窗下面板内容在 QScrollArea 内滚动。
    w.setMinimumSize(0, 0)
    w.control_dock.setFloating(False)
    w.addDockWidget(Qt.RightDockWidgetArea, w.control_dock)
    w.control_dock.setVisible(True)
    w.status_dock.setVisible(False)
    w.ai_dock.setVisible(False)
    w.control_dock.raise_()
    to((800, 600))
    grab(w, "/tmp/ui_small.png")

    # 6) floating dock（控制面板浮出，套 24px 圆角 + 半透明）。
    #    浮动 dock 是独立顶层窗口，主窗口 grab 抓不到它——直接 grab dock 本体。
    w.setMinimumSize(1200, 800)
    to((1400, 900))
    w.control_dock.setFloating(True)
    w.control_dock.resize(380, 640)
    w.control_dock.move(80, 40)
    w.control_dock.show()
    pump(150)
    pm = w.control_dock.grab()
    pm.save("/tmp/ui_floating.png")
    print("saved /tmp/ui_floating.png", pm.width(), "x", pm.height())

    w.close()
    print("ALL SHOTS DONE")


if __name__ == "__main__":
    main()
