"""offscreen 自证截图：5 张 UI 面板截图。"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
app = QApplication.instance() or QApplication(sys.argv)

def grab(w, path, size=(520, 460)):
    w.resize(*size)
    w.show()
    app.processEvents()
    w.grab().save(path)
    print("saved", path)

# 1. 扫频面板（展开，注入合成扫描结果演示行渲染）
from scanner_panel import ScannerPanel
sp = ScannerPanel()
sp.setWindowTitle("扫频")
sp._on_done([
    {"start_freq": 88.1e6, "end_freq": 88.4e6, "peak_freq": 88.2e6,
     "peak_db": -38.2, "bandwidth": 300e3, "kind": "WFM", "suggested_mode": "WFM"},
    {"start_freq": 118.3e6, "end_freq": 118.35e6, "peak_freq": 118.32e6,
     "peak_db": -52.0, "bandwidth": 25e3, "kind": "NFM", "suggested_mode": "NFM"},
    {"start_freq": 145.0e6, "end_freq": 145.02e6, "peak_freq": 145.01e6,
     "peak_db": -60.5, "bandwidth": 12.5e3, "kind": "DIG", "suggested_mode": "DIG"},
])
sp.progress.setValue(42)
grab(sp, "/tmp/ui_scanner.png")

# 2. 调制识别面板（未连接态，按任务要求）
from modulation_panel import ModulationPanel
mp = ModulationPanel()
grab(mp, "/tmp/ui_modulation.png", (460, 460))

# 3. 书签管理器
from bookmark_panel import BookmarkPanel
bp = BookmarkPanel(config_dir=tempfile.mkdtemp())
from mbdsdr_ai.bookmark_manager import Bookmark
bp._mgr.add(Bookmark(frequency_hz=145000000, name="本地中继示例",
                     modulation="NFM", bandwidth_hz=12500, group="ham"))
bp._mgr.add(Bookmark(frequency_hz=121500000, name="航空监听到的频点",
                     modulation="AM", bandwidth_hz=8000, group="aviation"))
bp.refresh_list()
grab(bp, "/tmp/ui_bookmarks.png", (620, 420))

# 4. 服务设置面板
from settings_panel import ServiceSettingsPanel
stp = ServiceSettingsPanel()
grab(stp, "/tmp/ui_settings.png", (440, 420))

# 5. 天空视图选中卫星 + 调谐按钮
from rf_sky_view import RFSkyView, SkyObject
sv = RFSkyView()
sv.resize(800, 600); sv.show(); app.processEvents()
sv.set_observer(43.88, 125.32, 200.0)  # 长春附近观测站（演示用）
catalog = [{
    "name": "NOAA-19",
    "line1": "1 33591U 09005A   24273.50000000  .00000000  00000+0  00000+0 0 00010",
    "line2": "2 33591  99.1000 350.0000 0014000  90.0000 270.0000 14.12000000812345",
    "freq_mhz": 137.1,
}]
sv.set_satellite_catalog(catalog)
obj = SkyObject("NOAA-19", azimuth_deg=120.0, elevation_deg=45.0,
                obj_type="satellite", frequency_hz=137.1e6)
sv._objects.append(obj)
sv._build_selected_info(obj)
app.processEvents()
sv.grab().save("/tmp/ui_sat_tune.png")
print("saved /tmp/ui_sat_tune.png  tune btn enabled:", sv._tune_btn.isEnabled(),
      "|", sv._doppler_label.text())
