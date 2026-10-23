"""NanoVNA-H 稳健采集器：分段扫描 + 多次平均 + 读空重试。
用法: python vna_capture.py <标签> [平均次数]
数据存 data/<标签>.json
"""
import sys, time, json, os
sys.path.insert(0, "../..")
from mbdsdr_ai.nanovna_client import NanoVNAClient

OUT_DIR = "data"
# RH770 钱段：100-200MHz(137/145/162) + 400-500MHz(435)，101点/段=1MHz步进
SEGMENTS = [
    (100_000_000, 200_000_000, 101),
    (400_000_000, 500_000_000, 101),
]
AVG = int(sys.argv[2]) if len(sys.argv) > 2 else 24
LABEL = sys.argv[1] if len(sys.argv) > 1 else "unnamed"

cli = NanoVNAClient(port="COM11")
cli.connect(do_handshake=False)
cli._transport.write(b"pause\r")   # 防连续扫描态
time.sleep(0.2)
cli._transport.reset_input_buffer()
cli.set_bandwidth(1000)

result = {"label": LABEL, "avg": AVG, "segments": []}
for (start, stop, pts) in SEGMENTS:
    cli.set_sweep(start, stop, pts)
    cli._transport.write(b"scan 0\r"); time.sleep(0.3)
    cli._transport.reset_input_buffer()
    freqs = cli.read_frequencies()
    if len(freqs) != pts:
        print(f"!! 段 {start/1e6:.0f}-{stop/1e6:.0f}MHz 频点表 {len(freqs)}/{pts}，重试")
        cli._transport.write(b"scan 0\r"); time.sleep(0.3)
        cli._transport.reset_input_buffer()
        freqs = cli.read_frequencies()
    acc = [0j] * pts
    ok = 0
    for i in range(AVG):
        got = None
        for retry in range(6):
            cli._transport.write(b"scan 0\r")
            time.sleep(0.1)
            cli._transport.reset_input_buffer()
            got = cli.read_data(0)
            if len(got) == pts:
                break
            time.sleep(0.2)
        if len(got) != pts:
            print(f"!! 段 {start/1e6:.0f} 第{i}次读失败({len(got)}行)")
            continue
        for j, v in enumerate(got):
            acc[j] += v
        ok += 1
    data = [a / ok for a in acc]
    result["segments"].append({
        "start_hz": start, "stop_hz": stop, "points": pts,
        "freqs_hz": freqs, "s11": [[v.real, v.imag] for v in data],
        "sweeps_ok": ok,
    })
    print(f"段 {start/1e6:.0f}-{stop/1e6:.0f}MHz: {ok}/{AVG} 次有效, "
          f"|S11|范围 {min(abs(v) for v in data):.4f}-{max(abs(v) for v in data):.4f}")

cli.close()
os.makedirs(OUT_DIR, exist_ok=True)
path = os.path.join(OUT_DIR, f"{LABEL}.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False)
print("SAVED:", path)
