"""分离噪声源：读数据两次（不重新触发） vs 触发后再读。"""
import sys, time
sys.path.insert(0, "../..")
from mbdsdr_ai.nanovna_client import NanoVNAClient

cli = NanoVNAClient(port="COM11")
cli.connect(do_handshake=False)
cli.set_sweep(100_000_000, 200_000_000, 101)
cli.set_bandwidth(1000)

cli._transport.write(b"scan 0\r")
time.sleep(0.5)
cli._transport.reset_input_buffer()

d1 = cli.read_data(0)
d2 = cli.read_data(0)
print("连读两次(无重触发) 最大偏差:", max(abs(x-y) for x, y in zip(d1, d2)))

cli._transport.write(b"scan 0\r"); time.sleep(0.5); cli._transport.reset_input_buffer()
d3 = cli.read_data(0)
print("重触发后 vs 前次 最大偏差:", max(abs(x-y) for x, y in zip(d2, d3)))

# 再连读一次确认
d4 = cli.read_data(0)
print("连读(第3次) vs d3 最大偏差:", max(abs(x-y) for x, y in zip(d3, d4)))

# 检查是否数据行数完整
print("d1 行数:", len(d1))
cli.close()
