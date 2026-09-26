# -*- coding: utf-8 -*-
"""Remove unconditional HackRF/bladeRF/LimeSDR device entries.
These must only appear when a real device is enumerated (SoapySDR/osmosdr)."""
import io
F = "/home/user/Doubao/chats/38438160041798146/mbdsdr_ai/sdr_backend.py"
s = io.open(F, encoding="utf-8").read()

start_marker = "    # 3) HackRF：始终列出一个条目"
end_marker = "    # 3.7) PlutoSDR (Analog Devices ADALM-PLUTO / AD9361)："
i = s.find(start_marker)
j = s.find(end_marker)
assert i != -1 and j != -1 and i < j, (i, j)

replacement = (
    "    # 3) HackRF / bladeRF / LimeSDR：不再无条件列出。\n"
    "    #    这些设备只有在被真实枚举到时才出现——SoapySDR 总线(步骤1)或\n"
    "    #    gr-osmosdr(步骤4)探测到即列出；无对应库/无设备时不产生任何条目。\n"
)
s = s[:i] + replacement + s[j:]

# tidy: renumber the now-following Pluto comment 3.7 -> 3 (comment only)
s = s.replace("    # 3.7) PlutoSDR (Analog Devices ADALM-PLUTO / AD9361)：",
              "    # 4) PlutoSDR (Analog Devices ADALM-PLUTO / AD9361)：", 1)
s = s.replace("    # 4) gr-osmosdr 通用后端枚举",
              "    # 5) gr-osmosdr 通用后端枚举", 1)

io.open(F, "w", encoding="utf-8", newline="").write(s)
print("removed unconditional HackRF/bladeRF/LimeSDR blocks")
