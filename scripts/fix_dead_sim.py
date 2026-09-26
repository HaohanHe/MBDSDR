# -*- coding: utf-8 -*-
"""Remove dead sim branches in rf_sky_view/status_panel; rename sky demo."""
import io

# ---------- rf_sky_view.py ----------
R = "/home/user/Doubao/chats/38438160041798146/desktop/rf_sky_view.py"
s = io.open(R, encoding="utf-8").read()

s = s.replace(
    '        # "none" = 无观测站位置; "real" = 真实GNSS或手动配置; "sim" = 模拟\n',
    '        # "none" = 无观测站位置; "real" = 真实GNSS或手动配置\n')

s = s.replace(
    '        """设置数据来源标注："none" / "real" / "sim"。"""\n'
    '        if source not in ("none", "real", "sim"):',
    '        """设置数据来源标注："none" / "real"。"""\n'
    '        if source not in ("none", "real"):')

badge = '''        # 模拟模式角标
        if self._data_source == "sim":
            badge = "[模拟]"
            f = QFont(self._font)
            f.setBold(True)
            f.setPointSize(9)
            painter.setFont(f)
            fm = QFontMetrics(f)
            bw = fm.horizontalAdvance(badge) + 20
            bh = fm.height() + 10
            bx, by = self.width() - bw - 12, 12
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#C4845C")))
            painter.drawRoundedRect(QRectF(bx, by, bw, bh), 4, 4)
            painter.setPen(QColor("#F5F3EF"))
            painter.drawText(QRectF(bx, by, bw, bh), Qt.AlignCenter, badge)
'''
assert s.count(badge) == 1, "badge"
s = s.replace(badge, "")

# docstring overlay priority mention
s = s.replace(
    "          3. 模拟模式 -> 橙色 [模拟] 角标\n", "")

io.open(R, "w", encoding="utf-8", newline="").write(s)
print("rf_sky_view sim branches removed")

# ---------- status_panel.py ----------
P = "/home/user/Doubao/chats/38438160041798146/desktop/status_panel.py"
p = io.open(P, encoding="utf-8").read()

p = p.replace(
    '''        三要素：来源（real/sim/none）、定位状态、数据时间戳。
        source=="none" 或（无 fix 且非 sim）时一律显示 "--"，绝不用 0 伪装。''',
    '''        两要素：来源（real/none）、定位状态、数据时间戳。
        source=="none" 或无 fix 时一律显示 "--"，绝不用 0 伪装。''')

p = p.replace(
    '        if source == "none" or (not fix and source != "sim"):',
    '        if source == "none" or not fix:')

simblock = '''        elif source == "sim":
            # 模拟模式：橙色"模拟定位"角标，坐标加 [模拟] 前缀
            self.gps_fix.setText("模拟定位")
            self.gps_fix.setStyleSheet("color: #C4845C;")
            self.gps_group.setTitle("GPS / 北斗 [模拟]")
            lat = gps.get("lat")
            lon = gps.get("lon")
            alt = gps.get("alt")
            hdop = gps.get("hdop")
            self.gps_sats.setText(str(gps.get("sats", 0)))
            self.gps_lat.setText(f"[模拟] {lat:.6f}" if lat is not None else "--")
            self.gps_lon.setText(f"[模拟] {lon:.6f}" if lon is not None else "--")
            self.gps_alt.setText(f"[模拟] {alt:.1f} m" if alt is not None else "--")
            self.gps_hdop.setText(f"[模拟] {hdop:.1f}" if hdop is not None else "--")
'''
assert p.count(simblock) == 1, "simblock"
p = p.replace(simblock, "")

io.open(P, "w", encoding="utf-8", newline="").write(p)
print("status_panel sim branch removed")

# ---------- rename _init_sky_view_demo ----------
M = "/home/user/Doubao/chats/38438160041798146/desktop/main_window.py"
m = io.open(M, encoding="utf-8").read()
assert m.count("_init_sky_view_demo") == 2
m = m.replace("_init_sky_view_demo", "_init_sky_view")
io.open(M, "w", encoding="utf-8", newline="").write(m)
print("renamed _init_sky_view_demo -> _init_sky_view")
