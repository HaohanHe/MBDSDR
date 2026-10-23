"""紧急恢复：停掉卡死态，验证 shell 活性。硬超时，绝不挂死。"""
import sys, time
import serial

ser = serial.Serial("COM11", 115200, timeout=0.05)
time.sleep(0.3)
ser.reset_input_buffer()

def send(cmd):
    ser.reset_input_buffer()
    ser.write((cmd + "\r").encode("ascii"))

def raw_read(seconds):
    buf = b""
    t0 = time.time()
    while time.time() - t0 < seconds:
        chunk = ser.read(8192)
        if chunk:
            buf += chunk
    return buf

# 1) 停扫描循环
send("pause")
r = raw_read(1.5)
print(f"pause 后收到 {len(r)} 字节")
if r:
    print("  尾部:", r[-120:].decode("ascii", errors="replace").replace("\r", "|"))

# 2) Ctrl+C 打断可能的流模式
ser.write(b"\x03")
time.sleep(0.3)
r = raw_read(1.0)
print(f"Ctrl+C 后收到 {len(r)} 字节")
if r:
    print("  尾部:", r[-120:].decode("ascii", errors="replace").replace("\r", "|"))

# 3) 活性探测
send("version")
r = raw_read(2.0)
txt = r.decode("ascii", errors="replace")
alive = "1.2" in txt or "ch>" in txt
print(f"version 探测: {'存活' if alive else '无响应'} ({len(r)} 字节)")
if r:
    print("  尾部:", txt[-150:].replace("\r", "|"))

# 4) 若存活，验证取数链路（只用 sweep+wait+data，不发 scan）
if alive:
    send("sweep 100000000 200000000 101")
    time.sleep(1.0)
    send("data 0")
    r = raw_read(2.5)
    lines = [l for l in r.decode("ascii", errors="replace").split("\n") if l.strip() and "ch>" not in l]
    print(f"data 0 读到 {len(lines)} 行; 首行: {lines[0] if lines else '(空)'}")
ser.close()
print("RECOVERY_DONE")
