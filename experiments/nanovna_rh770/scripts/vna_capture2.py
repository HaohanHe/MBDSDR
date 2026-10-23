"""NanoVNA-H 裸串口稳健采集器 v2：硬超时 + 失败重发 + 端口重连。
用法: python vna_capture2.py <标签> [平均次数=24]
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
READ_HARD_TIMEOUT = 3.0   # 单次读 data 的硬墙（秒）

def reopen(ser):
    try:
        ser.close()
    except Exception:
        pass
    time.sleep(0.5)
    ser = serial.Serial(PORT, BAUD, timeout=0.05)
    time.sleep(0.3)
    ser.reset_input_buffer()
    ser.write(b"pause\r")
    time.sleep(0.2)
    ser.reset_input_buffer()
    return ser

def send(ser, cmd):
    ser.reset_input_buffer()
    ser.write((cmd + "\r").encode("ascii"))

def read_lines(ser, want, hard=READ_HARD_TIMEOUT):
    """读到 want 行或硬超时。返回 (行数, [complex])。"""
    out = []
    t0 = time.time()
    while len(out) < want and (time.time() - t0) < hard:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("ascii", errors="replace").strip()
        if not line or line.startswith("ch>"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            out.append(complex(float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return out

def read_freqs(ser, want):
    send(ser, "frequencies")
    out = []
    t0 = time.time()
    while len(out) < want and (time.time() - t0) < READ_HARD_TIMEOUT:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("ascii", errors="replace").strip()
        if not line or line.startswith("ch>"):
            continue
        try:
            out.append(int(float(line)))
        except ValueError:
            continue
    return out

def scan_once(ser, pts):
    """触发一次扫描并读 pts 个 S11 点；失败返回 None。"""
    send(ser, "scan 0")
    time.sleep(0.1)
    send(ser, "data 0")
    data = read_lines(ser, pts)
    if len(data) == pts:
        return data
    return None

ser = serial.Serial(PORT, BAUD, timeout=0.05)
time.sleep(0.3)
ser.reset_input_buffer()
ser.write(b"pause\r"); time.sleep(0.2); ser.reset_input_buffer()
ser.write(b"bandwidth 1000\r"); time.sleep(0.2); ser.reset_input_buffer()

result = {"label": LABEL, "avg": AVG, "segments": []}
t_start = time.time()
for (start, stop, pts) in SEGMENTS:
    send(ser, f"sweep {start} {stop} {pts}")
    time.sleep(0.2)
    freqs = read_freqs(ser, pts)
    if len(freqs) != pts:
        ser = reopen(ser)
        send(ser, f"sweep {start} {stop} {pts}")
        time.sleep(0.2)
        freqs = read_freqs(ser, pts)
    if len(freqs) != pts:
        print(f"!! 段 {start/1e6:.0f}MHz 频点表读取失败({len(freqs)}/{pts})，跳过")
        continue
    acc = [0j] * pts
    ok = 0
    fails = 0
    for i in range(AVG):
        got = scan_once(ser, pts)
        if got is None:
            fails += 1
            if fails >= 3:
                ser = reopen(ser)
                send(ser, f"sweep {start} {stop} {pts}")
                time.sleep(0.2)
                fails = 0
            continue
        for j, v in enumerate(got):
            acc[j] += v
        ok += 1
    if ok == 0:
        print(f"!! 段 {start/1e6:.0f}MHz 全部扫描失败")
        continue
    data = [a / ok for a in acc]
    result["segments"].append({
        "start_hz": start, "stop_hz": stop, "points": pts,
        "freqs_hz": freqs, "s11": [[v.real, v.imag] for v in data],
        "sweeps_ok": ok,
    })
    mags = [abs(v) for v in data]
    print(f"段 {start/1e6:.0f}-{stop/1e6:.0f}MHz: {ok}/{AVG} 有效, "
          f"|S11| {min(mags):.4f}-{max(mags):.4f}, 用时 {time.time()-t_start:.0f}s")

try:
    ser.close()
except Exception:
    pass
os.makedirs(OUT_DIR, exist_ok=True)
path = os.path.join(OUT_DIR, f"{LABEL}.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False)
print("SAVED:", path)
