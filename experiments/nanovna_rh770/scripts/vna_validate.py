"""恢复后验证安全配方：resume + sweep换段 + 连读多次是否每次都是新扫（用于平均）。"""
import time
import serial

ser = serial.Serial("COM11", 115200, timeout=0.05)
time.sleep(0.3)
ser.reset_input_buffer()

def send(cmd):
    ser.reset_input_buffer()
    ser.write((cmd + "\r").encode("ascii"))

def read_n(want, hard=3.0):
    out = []
    t0 = time.time()
    while len(out) < want and time.time() - t0 < hard:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("ascii", errors="replace").strip()
        if not line or line.startswith("ch>") or line == "data 0":
            continue
        p = line.split()
        if len(p) < 2:
            continue
        try:
            out.append(complex(float(p[0]), float(p[1])))
        except ValueError:
            pass
    return out

def md(a, b):
    return max(abs(x - y) for x, y in zip(a, b)) if a and b else -1

# 恢复扫描循环
send("resume")
time.sleep(0.5)
ser.reset_input_buffer()

# 段1
send("sweep 100000000 200000000 101")
time.sleep(1.0)
send("data 0")
d1 = read_n(101)
print(f"段1 100-200MHz: {len(d1)} 行, [0]={d1[0]:.6f}")

# 段2（换段）
send("sweep 400000000 500000000 101")
time.sleep(1.0)
send("data 0")
d2 = read_n(101)
print(f"段2 400-500MHz: {len(d2)} 行, [0]={d2[0]:.6f}, 与段1差={md(d1,d2):.6f}")

# 连读5次：每次是否都是新扫（用于平均）
diffs = []
prev = d2
for i in range(5):
    send("data 0")
    d = read_n(101)
    diffs.append(md(prev, d))
    prev = d
print("连读相邻差:", [f"{x:.5f}" for x in diffs])
print(f"判定: {'每次都是新扫，可平均' if all(x > 1e-5 for x in diffs) else '读数重复，需触发'}")
ser.close()
print("VALIDATE_DONE")
