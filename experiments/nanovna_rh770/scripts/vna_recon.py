"""侦察扫描：任意频段裸采 |S11| 找谐振坑位（不做校准，坑位频率准、深度糙）。"""
import sys, time, json
import serial

PORT = "COM11"
start_hz = int(float(sys.argv[1]) * 1e6)
stop_hz = int(float(sys.argv[2]) * 1e6)
pts = int(sys.argv[3]) if len(sys.argv) > 3 else 101
avg = int(sys.argv[4]) if len(sys.argv) > 4 else 8
WAIT = 1.0

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

ser = serial.Serial(PORT, 115200, timeout=0.05)
time.sleep(0.3)
ser.reset_input_buffer()
acc = [0j] * pts
ok = 0
for i in range(avg):
    send(ser, f"sweep {start_hz} {stop_hz} {pts}")
    time.sleep(WAIT)
    send(ser, "data 0")
    d = read_n(ser, pts)
    if len(d) == pts:
        for j, v in enumerate(d):
            acc[j] += v
        ok += 1
data = [a / ok for a in acc] if ok else []
freqs = [start_hz + (stop_hz - start_hz) * i / (pts - 1) for i in range(pts)]
print(f"{ok}/{avg} 有效")
if ok:
    mags = [abs(v) for v in data]
    for i in range(0, pts, 2):
        print(f"  {freqs[i]/1e6:7.1f}MHz  |S11|={mags[i]:.4f}")
    iw = mags.index(min(mags))
    print(f"最浅反射(最深坑): {freqs[iw]/1e6:.1f}MHz |S11|={mags[iw]:.4f}")
ser.close()
