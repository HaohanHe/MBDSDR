"""测 sweep 点数上限 + 测量稳定性（空口连扫3次看噪声）。"""
import sys, time
sys.path.insert(0, "../..")
from mbdsdr_ai.nanovna_client import NanoVNAClient

cli = NanoVNAClient(port="COM11")
cli.connect(do_handshake=False)

for pts in (101, 201, 501):
    try:
        cli.set_sweep(80_000_000, 500_000_000, pts)
        cli._transport.write(b"scan 0\r")
        time.sleep(0.5)
        cli._transport.reset_input_buffer()
        f = cli.read_frequencies()
        print(f"请求 {pts} 点 -> 实得 {len(f)} 点, 范围 {f[0]/1e6:.1f}-{f[-1]/1e6:.1f}MHz")
    except Exception as e:
        print(f"请求 {pts} 点 -> 失败: {type(e).__name__} {e}")

# 定 501 点（若支持），连扫3次看重复性
cli.set_sweep(80_000_000, 500_000_000, 501)
cli._transport.write(b"scan 0\r"); time.sleep(0.5); cli._transport.reset_input_buffer()
cli.read_frequencies()
snaps = []
for i in range(3):
    cli._transport.write(b"scan 0\r")
    time.sleep(0.6)
    cli._transport.reset_input_buffer()
    d = cli.read_data(0)
    snaps.append(d)
    print(f"scan{i}: 101点@144MHz附近值 {[round(abs(v),4) for v in d[140:143]]}")
import math
def md(a, b):
    return max(abs(x - y) for x, y in zip(a, b))
print("重复性 scan0-scan1 最大偏差:", md(snaps[0], snaps[1]))
print("重复性 scan1-scan2 最大偏差:", md(snaps[1], snaps[2]))
cli.close()
