# -*- coding: utf-8 -*-
"""Remove hardcoded ai-sdr Mini entries from the device dialog."""
import io
F = "/home/user/Doubao/chats/38438160041798146/desktop/main_window.py"
s = io.open(F, encoding="utf-8").read()

# 1) initial combo
old1 = '''        # 保留原 ai-sdr Mini WebSocket 入口（自研板走 MCP/WebSocket）
        combo.addItem("ai-sdr Mini (WebSocket 192.168.4.1:81)", self._WS_SPECIAL)
        # 远程 rtl_tcp 源：连局域网/公网已跑 rtl_tcp 服务的机器，复用参数区
        combo.addItem("远程 rtl_tcp 源 (host:port)", self._RTL_TCP_SPECIAL)'''
new1 = '''        # 远程 rtl_tcp 源：连局域网/公网已跑 rtl_tcp 服务的机器，复用参数区
        combo.addItem("远程 rtl_tcp 源 (host:port)", self._RTL_TCP_SPECIAL)'''
assert s.count(old1) == 1, "old1"
s = s.replace(old1, new1)

# 2) reload combo
old2 = '''            combo.clear()
            combo.addItem("ai-sdr Mini (WebSocket 192.168.4.1:81)", self._WS_SPECIAL)
            combo.addItem("远程 rtl_tcp 源 (host:port)", self._RTL_TCP_SPECIAL)'''
new2 = '''            combo.clear()
            combo.addItem("远程 rtl_tcp 源 (host:port)", self._RTL_TCP_SPECIAL)'''
assert s.count(old2) == 1, "old2"
s = s.replace(old2, new2)

# 3) param state
old3 = "            enabled = d is not None and d != self._WS_SPECIAL\n"
new3 = "            enabled = d is not None\n"
assert s.count(old3) == 1, "old3"
s = s.replace(old3, new3)

# 4) dead WS branch
old4 = '''        # ai-sdr Mini WebSocket：走原 host/port 流程
        if data == self._WS_SPECIAL:
            host, ok = QInputDialog.getText(
                self, "连接 ai-sdr Mini", "设备 IP 地址:", text="192.168.4.1")
            if ok and host.strip():
                port, ok2 = QInputDialog.getInt(
                    self, "连接 ai-sdr Mini", "端口:",
                    value=81, min=1, max=65535)
                if ok2:
                    self._connect_real(host.strip(), port)
            return

'''
assert s.count(old4) == 1, "old4"
s = s.replace(old4, "")

io.open(F, "w", encoding="utf-8", newline="").write(s)
print("ai-sdr Mini entries removed from dialog")
