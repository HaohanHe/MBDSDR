"""带宽 vs 重复性测试。"""
import sys, time
sys.path.insert(0, "../..")
from mbdsdr_ai.nanovna_client import NanoVNAClient

cli = NanoVNAClient(port="COM11")
cli.connect(do_handshake=False)
cli.set_sweep(100_000_000, 200_000_000, 101)

def snap():
    cli._transport.write(b"scan 0\r")
    time.sleep(0.4)
    cli._transport.reset_input_buffer()
    return cli.read_data(0)

for bw in (1000, 100, 10):
    try:
        cli.set_bandwidth(bw)
        a, b, c = snap(), snap(), snap()
        d1 = max(abs(x-y) for x, y in zip(a, b))
        d2 = max(abs(x-y) for x, y in zip(b, c))
        print(f"bw={bw}kHz 重复性: {d1:.5f} / {d2:.5f}  |S11|@101点中值 {abs(a[50]):.4f}")
    except Exception as e:
        print(f"bw={bw} 失败: {e}")
cli.close()
