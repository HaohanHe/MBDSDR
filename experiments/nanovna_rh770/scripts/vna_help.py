"""拿 help 全量文本 + 尝试清固件校准。"""
import sys, time
sys.path.insert(0, "../..")
from mbdsdr_ai.nanovna_client import NanoVNAClient

cli = NanoVNAClient(port="COM11")
cli.connect(do_handshake=False)
help_lines = cli._exec("help")
print("HELP_START")
for l in help_lines:
    print(l)
print("HELP_END")
print("cal before:", cli.read_cal_status())
for cmd in ("cal_reset", "calreset", "reset"):
    out = cli._exec(cmd)
    print(f"try {cmd!r} ->", out[:3])
print("cal after:", cli.read_cal_status())
cli.close()
