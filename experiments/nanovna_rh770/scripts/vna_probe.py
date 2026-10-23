"""NanoVNA 探测：握手 + 能力 + 校准状态。一次性脚本。"""
import sys, time
sys.path.insert(0, "../..")
from mbdsdr_ai.nanovna_client import NanoVNAClient, find_nanovna_ports

ports = find_nanovna_ports()
print("候选端口:", ports)

PORT = "COM11"
cli = NanoVNAClient(port=PORT)
cli.connect(do_handshake=True)
print("version:", cli.version)
print("info:", cli.info_lines)
print("features:", cli.features)
print("sn:", repr(cli.sn))
print("cal:", cli.read_cal_status())

# 裸协议探测：scan 触发 + 频点表
t = cli._transport
t.write(b"scan 0\r")
time.sleep(0.3)
t.reset_input_buffer()
freqs = cli.read_frequencies()
print("frequencies 前5:", freqs[:5], "共", len(freqs))
data0 = cli.read_data(0)
print("S11 前3点:", data0[:3], "共", len(data0))
cli.close()
print("PROBE_OK")
