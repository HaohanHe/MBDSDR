"""判定裸 scan 的语义：是否把连续循环停成单次？重复 scan 是否每次出新扫？"""
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
        if not line or line.startswith("ch>"):
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

send("sweep 100000000 200000000 101")
time.sleep(0.3)
send("scan")
time.sleep(0.6)
a1 = read_n(101)
a2 = read_n(101)
print(f"scan后连读: a1[0]={a1[0]:.6f} a2[0]={a2[0]:.6f} 差={md(a1,a2):.6f} "
      f"({'循环已停' if md(a1,a2) < 1e-9 else '循环仍在跑'})")

send("scan"); time.sleep(0.6)
a3 = read_n(101)
print(f"再scan后: a3[0]={a3[0]:.6f} vs a2[0]={a2[0]:.6f} 差={md(a2,a3):.6f} "
      f"({'重复scan有新扫' if md(a2,a3) > 1e-6 else '重复scan无动作'})")

# 换段后再来一次
send("sweep 400000000 500000000 101"); time.sleep(0.3)
send("scan"); time.sleep(0.6)
b1 = read_n(101)
print(f"换段+scan: b1[0]={b1[0]:.6f} vs a3[0]={a3[0]:.6f} 差={md(a3,b1):.6f}")
send("scan"); time.sleep(0.6)
b2 = read_n(101)
print(f"新段重复scan: b2[0]={b2[0]:.6f} vs b1[0]={b1[0]:.6f} 差={md(b1,b2):.6f}")
ser.close()
