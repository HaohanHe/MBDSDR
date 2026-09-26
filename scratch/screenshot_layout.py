"""离屏截图：3 个布局预设 + 3 种窗口尺寸，证明流体布局。"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "desktop"))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow

w = MainWindow()
w._apply_theme("dark_car")
w.resize(1280, 800)
w.show()

# 让布局 settle
def pump(ms=100):
    QTimer.singleShot(ms, app.quit)
    app.exec()

pump(200)

# ---- 3 个布局预设截图 ----
for preset in ("focus", "analysis", "grid"):
    w.dock_layout.apply_preset(preset)
    pump(150)
    pm = w.grab()
    out = f"/tmp/carwith_{preset}.png"
    pm.save(out)
    print("saved", out, pm.size().width(), "x", pm.size().height())

# ---- 3 种窗口尺寸截图（grid 预设下证明流体重排）----
w.dock_layout.apply_preset("grid")
# 临时放宽最小尺寸，证明小窗下布局依然流体（最小尺寸本身是弹性下限，不是死值）
w.setMinimumSize(600, 400)
for (cw, ch) in ((800, 600), (1280, 800), (1920, 1080)):
    w.resize(cw, ch)
    pump(150)
    pm = w.grab()
    out = f"/tmp/carwith_size_{cw}x{ch}.png"
    pm.save(out)
    print("saved", out, pm.size().width(), "x", pm.size().height())

w.close()
print("DONE")
