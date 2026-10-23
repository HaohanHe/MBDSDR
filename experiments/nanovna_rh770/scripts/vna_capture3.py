"""NanoVNA-H 采集器 v3（安全配方）：每次采样 = sweep(同段) + 等 + data 0。
禁发：scan / scan 0 / pause / reset（前两者卡死过设备）。
用法: python vna_capture3.py <标签> [平均次数=24]
"""
import sys, time, json, os
import serial

PORT = "COM11"
BAUD = 115200
OUT_DIR = "data"
SEGMENTS = [
    (100_000_000, 200_000_000, 101),
    (400_000_000, 500_000_000, 101),
]
AVG = int(sys.argv[2]) if len(sys.argv) > 2 else 24
LABEL = sys.argv[1] if len(sys.argv) > 1 else "unnamed"
WAIT = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25
PTS = 101

def read_n(ser, want, hard=3.0):
    out = []
    t0 = time.time()
    while len(out) < want and time.time() - t0 < hard:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("ascii", errors="replace").strip()
        if not line or line.startswith("ch>") or line.startswith("data "):
            continue
        p = line.split()
        if len(p) < 2:
            continue
        try:
            out.append(complex(float(p[0]), float(p[1])))
        except ValueError:
            pass
    return out

def send(ser, cmd):
    ser.reset_input_buffer()
    ser.write((cmd + "\r").encode("ascii"))

def sample(ser, start, stop):
    """一次完整采样：sweep 触发 + 读数。失败返回 None。"""
    send(ser, f"sweep {start} {stop} {PTS}")
    time.sleep(WAIT)
    send(ser, "data 0")
    d = read_n(ser, PTS)
    return d if len(d) == PTS else None

ser = serial.Serial(PORT, BAUD, timeout=0.05)
time.sleep(0.3)
ser.reset_input_buffer()

result = {"label": LABEL, "avg": AVG, "segments": []}
t_start = time.time()
for (start, stop, _) in SEGMENTS:
    # 频点表
    send(ser, f"sweep {start} {stop} {PTS}")
    time.sleep(0.4)
    send(ser, "frequencies")
    freqs = []
    t0 = time.time()
    while len(freqs) < PTS and time.time() - t0 < 3.0:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("ascii", errors="replace").strip()
        if not line or line.startswith("ch>") or line.startswith("frequencies"):
            continue
        try:
            freqs.append(int(float(line)))
        except ValueError:
            pass
    if len(freqs) != PTS:
        print(f"!! 段 {start/1e6:.0f}MHz 频点表失败({len(freqs)})，跳过")
        continue
    acc = [0j] * PTS
    ok = 0
    for i in range(AVG):
        got = sample(ser, start, stop)
        if got is None:
            # 重试一次（重发 sweep）
            got = sample(ser, start, stop)
        if got is None:
            continue
        for j, v in enumerate(got):
            acc[j] += v
        ok += 1
    if ok == 0:
        print(f"!! 段 {start/1e6:.0f}MHz 全部失败")
        continue
    data = [a / ok for a in acc]
    result["segments"].append({
        "start_hz": start, "stop_hz": stop, "points": PTS,
        "freqs_hz": freqs, "s11": [[v.real, v.imag] for v in data],
        "sweeps_ok": ok,
    })
    mags = [abs(v) for v in data]
    print(f"段 {start/1e6:.0f}-{stop/1e6:.0f}MHz: {ok}/{AVG} 有效, "
          f"|S11| {min(mags):.4f}-{max(mags):.4f}, 累计用时 {time.time()-t_start:.0f}s")

try:
    ser.close()
except Exception:
    pass
os.makedirs(OUT_DIR, exist_ok=True)
path = os.path.join(OUT_DIR, f"{LABEL}.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False)
print("SAVED:", path)
