#!/usr/bin/env python3
"""
MBDSDR - 串口 GNSS 自检脚本
=============================

独立运行，扫描串口找 GNSS 模块并打印定位信息。

用法::

    python3 scripts/serial_gnss_probe.py                      # 自动扫描
    python3 scripts/serial_gnss_probe.py --port /dev/ttyUSB0 --baud 9600

行为：
- 找到模块后持续打印解析到的定位信息约 5 秒；
- 无设备 / 无合法 NMEA：打印“未发现GNSS模块”并 exit 0（绝不报错崩溃）；
- 指定 --port/--baud 时跳过自动扫描，直接打开该口。

退出码：正常 0；仅在参数错误时非 0。
"""

import sys
import time
import argparse

# 允许从仓库根直接运行
sys.path.insert(0, __file__.rsplit("/", 2)[0])

try:
    from mbdsdr_ai.serial_gnss import SerialGNSSReader, DEFAULT_BAUDRATES
except Exception as e:
    print(f"[错误] 无法导入 serial_gnss 模块: {e}")
    sys.exit(1)


def _print_fix(fix: dict):
    src = fix.get("source", "none")
    if src != "real":
        print("  ...等待定位（已连串口，但暂无有效 GGA/RMC）...")
        return
    lat = fix.get("latitude")
    lon = fix.get("longitude")
    alt = fix.get("altitude_m")
    sats = fix.get("satellites")
    hdop = fix.get("hdop")
    spd = fix.get("speed_kmh")
    crs = fix.get("course_deg")
    utc = fix.get("utc_time")
    print(f"  定位: lat={lat}  lon={lon}  alt={alt}m  "
          f"sats={sats}  hdop={hdop}  spd={spd}km/h  course={crs}  utc={utc}")


def main():
    ap = argparse.ArgumentParser(description="串口 GNSS 自检")
    ap.add_argument("--port", help="指定串口（如 /dev/ttyUSB0 或 COM3）")
    ap.add_argument("--baud", type=int, help="指定波特率")
    ap.add_argument("--seconds", type=float, default=5.0,
                    help="找到模块后打印时长（默认 5 秒）")
    args = ap.parse_args()

    reader = SerialGNSSReader()

    if args.port:
        # 指定端口：直接打开，跳过扫描
        print(f"[信息] 使用指定端口 {args.port} @ {args.baud or 9600} baud")
        ok = reader.start(args.port, args.baud or 9600)
        if not ok:
            print(f"[信息] 无法打开 {args.port}（被占用/无权限/无设备）。")
            print("未发现GNSS模块")
            sys.exit(0)
        port, baud = args.port, args.baud or 9600
    else:
        # 自动扫描
        print("[信息] 扫描串口与波特率 "
              f"{DEFAULT_BAUDRATES} ...")
        info = SerialGNSSReader.auto_detect()
        if info is None:
            print("未发现GNSS模块")
            sys.exit(0)
        port, baud = info["port"], info["baudrate"]
        print(f"[信息] 锁定 GNSS 模块: {port} @ {baud} baud")
        reader.start(port, baud)

    # 持续打印 N 秒
    print(f"[信息] 读取 {args.seconds:.0f} 秒定位数据 ...")
    deadline = time.time() + args.seconds
    had_fix = False
    try:
        while time.time() < deadline:
            fix = reader.get_fix()
            if fix.get("source") == "real":
                had_fix = True
            _print_fix(fix)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[信息] 中断。")
    finally:
        reader.stop()

    if not had_fix:
        print("[提示] 串口已连通但未拿到有效定位数据（室内/遮挡？）。")
    print("[完成] 自检结束。")


if __name__ == "__main__":
    main()
