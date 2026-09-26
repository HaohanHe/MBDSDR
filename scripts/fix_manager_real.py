# -*- coding: utf-8 -*-
"""Make SDRBackendManager register only real devices; drop unused WS constant."""
import io

# ---- sdr_backend.py manager ----
F = "/home/user/Doubao/chats/38438160041798146/mbdsdr_ai/sdr_backend.py"
s = io.open(F, encoding="utf-8").read()

mini = '''        # 自研 ai-sdr Mini（注册但不自动连接，用户手动连接）
        ai_mini = AISDRMiniBackend()
        self.backends[ai_mini.device.device_id] = ai_mini

'''
assert s.count(mini) == 1, "mini block"
s = s.replace(mini, "")

pluto = '''        # PlutoSDR（注册但不自动连接；无 libiio/SoapySDR 时 connect 返回 False，
        # enumerate() 返回 []，不伪造硬件）
        try:
            pluto = PlutoSDRBackend()
            self.backends[pluto.device.device_id] = pluto
        except Exception as e:
            logger.debug(f"PlutoSDRBackend 注册失败(忽略): {e}")'''
pluto_new = '''        # PlutoSDR：仅当真实 enumerate() 探测到设备时才注册（libiio USB/网络）。
        try:
            for _pd in PlutoSDRBackend.enumerate():
                _uri = _pd.get("uri")
                _pl = PlutoSDRBackend(uri=_uri, device_info=_pd) if _uri \\
                    else PlutoSDRBackend(device_info=_pd)
                self.backends[_pl.device.device_id] = _pl
        except Exception as e:
            logger.debug(f"PlutoSDR 枚举失败(忽略): {e}")'''
assert s.count(pluto) == 1, "pluto block"
s = s.replace(pluto, pluto_new)

io.open(F, "w", encoding="utf-8", newline="").write(s)
print("manager now registers only real devices")

# ---- main_window.py: drop unused _WS_SPECIAL ----
M = "/home/user/Doubao/chats/38438160041798146/desktop/main_window.py"
m = io.open(M, encoding="utf-8").read()
ws = '''    # 设备选择对话框中"ai-sdr Mini WebSocket"特殊条目的 data 标记
    _WS_SPECIAL = "__ai_sdr_mini_ws__"
'''
assert m.count(ws) == 1, "ws const"
m = m.replace(ws, "")
io.open(M, "w", encoding="utf-8", newline="").write(m)
print("dropped unused _WS_SPECIAL")
