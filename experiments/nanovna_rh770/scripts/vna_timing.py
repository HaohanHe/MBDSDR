"""测单次 scan+read 周期时长，定平均策略。"""
import sys, time
sys.path.insert(0, "../..")
from mbdsdr_ai.nanovna_client import NanoVNAClient

cli = NanoVNAClient(port="COM11")
cli.connect(do_handshake=False)
cli.set_sweep(100_000_000, 200_000_000, 101)
cli.set_bandwidth(1000)

# 预热
cli._transport.write(b"scan 0\r"); time.sleep(0.4); cli._transport.reset_input_buffer()
cli.read_data(0)

# 计时：不同 wait 下周期
for wait in (0.05, 0.1, 0.2):
    t0 = time.time()
    for _ in range(5):
        cli._transport.write(b"scan 0\r")
        time.sleep(wait)
        cli._transport.reset_input_buffer()
        cli.read_data(0)
    dt = (time.time() - t0) / 5
    print(f"wait={wait}s -> 周期 {dt*1000:.0f}ms")

# 小 wait 下数据是否稳定（扫描是否已完成）
cli._transport.write(b"scan 0\r"); time.sleep(0.05); cli._transport.reset_input_buffer()
a = cli.read_data(0)
time.sleep(0.5)
cli._transport.reset_input_buffer()
b = cli.read_data(0)
print("wait=0.05 后立即读 vs 再等0.5s读 偏差:", max(abs(x-y) for x, y in zip(a, b)))
cli.close()
