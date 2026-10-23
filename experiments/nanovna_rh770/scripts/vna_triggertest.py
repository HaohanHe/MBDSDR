"""验证裸 scan 是否能真正切换扫描段。用位级差异判别（空口下各段都应≈+1，真假扫描的区别在噪声模式）。"""
import sys, time
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

def sweep_and_read(start, stop, pts, trigger):
    send(f"sweep {start} {stop} {pts}")
    time.sleep(0.3)
    if trigger:
        send(trigger)
        time.sleep(0.4)
    send("data 0")
    return read_n(pts)

# 先清态：pause 再 resume
send("pause"); time.sleep(0.2); send("resume"); time.sleep(0.3)
ser.reset_input_buffer()

for trigger in ("scan", "scan 0", None):
    d1 = sweep_and_read(100_000_000, 200_000_000, 101, trigger)
    d2 = sweep_and_read(400_000_000, 500_000_000, 101, trigger)
    if len(d1) != 101 or len(d2) != 101:
        print(f"trigger={trigger!r}: 读数不足 d1={len(d1)} d2={len(d2)}")
        continue
    same = all(abs(a - b) == 0 for a, b in zip(d1, d2))
    maxdiff = max(abs(a - b) for a, b in zip(d1, d2))
    print(f"trigger={trigger!r}: d1[0]={d1[0]:.6f} d2[0]={d2[0]:.6f} "
          f"位级相同={same} 最大差={maxdiff:.6f}")
ser.close()
