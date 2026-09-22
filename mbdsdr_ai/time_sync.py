"""
MBDSDR 授时（纯 socket，无第三方依赖）
======================================
NTP 校时：查询 NTP 服务器，返回本地时钟偏差。
新时空主线的授时能力（与仰角/轨道配套）。
"""
from __future__ import annotations
import socket
import struct
import time
from typing import Dict


# NTP 时间戳：1900-01-01 到 1970-01-01 的秒数
_NTP_DELTA = 2208988800


def ntp_offset(server: str = "ntp.aliyun.com", port: int = 123, timeout: float = 3.0) -> Dict:
    """查询 NTP 服务器，返回本地时钟与 NTP 时间的偏差（秒，正=本地慢）。"""
    pkt = b"\x1b" + 47 * b"\0"
    t0 = time.time()  # 发送前本地时间
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(pkt, (server, port))
        data, _ = s.recvfrom(1024)
        s.close()
        t1 = time.time()  # 收到后本地时间
    except Exception as e:
        return {"ntp_server": server, "reachable": False, "error": str(e)}

    # NTP 传输时间在字节 40-47
    recv_ts = struct.unpack("!12I", data)[8]
    recv_frac = struct.unpack("!12I", data)[9]
    ntp_sec = recv_ts - _NTP_DELTA + recv_frac / 2 ** 32
    # 单程延迟
    delay = (t1 - t0) / 2.0
    local_now = (t0 + t1) / 2.0
    offset = ntp_sec - local_now
    return {
        "ntp_server": server,
        "reachable": True,
        "local_clock_skew_s": round(offset, 4),
        "round_trip_s": round(t1 - t0, 4),
        "ntp_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ntp_sec)),
    }


if __name__ == "__main__":
    r = ntp_offset()
    print(r)
