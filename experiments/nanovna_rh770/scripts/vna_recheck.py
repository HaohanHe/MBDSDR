"""重启后重连，看 cal 状态是否已清。"""
import sys, time
sys.path.insert(0, "../..")
from mbdsdr_ai.nanovna_client import NanoVNAClient

time.sleep(4)  # 等 STM32 重启枚举
for attempt in range(5):
    try:
        cli = NanoVNAClient(port="COM11")
        cli.connect(do_handshake=False)
        break
    except Exception as e:
        print(f"attempt {attempt}: {type(e).__name__} {e}")
        time.sleep(2)
else:
    sys.exit("连不上")

print("version:", cli._exec("version"))
print("cal:", cli.read_cal_status())
cli.close()
