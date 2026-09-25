#!/usr/bin/env python3
"""
MBDSDR - 串口 GNSS 自检脚本
=============================

独立运行，扫描串口找 GNSS 模块并打印定位信息。

用法::

    python3 scripts/serial_gnss_probe.py                      # 自动扫描
    python3 scripts/serial_gnss_probe.py --port /dev/ttyUSB0 --baud 9600
    python3 scripts/serial_gnss_probe.py --list-ports        # 只列串口不连接
    python3 scripts/serial_gnss_probe.py --baudscan /dev/ttyUSB0  # 扫波特率

行为：
- 找到模块后持续打印解析到的定位信息约 --seconds 秒；
- 已连串口但无定位（室内/遮挡）：持续显示“已连接串口，等待定位...”，不报错；
- 无设备 / 无合法 NMEA：打印友好提示并 exit 0（绝不报错崩溃）；
- 指定 --port/--baud 时跳过自动扫描，直接打开该口。

退出码：正常 0；仅在参数错误时非 0。
"""

import sys
import time
import argparse

# 允许从仓库根直接运行
sys.path.insert(0, __file__.rsplit("/", 2)[0])

try:
    import serial  # pyserial
    from mbdsdr_ai.serial_gnss import (
        SerialGNSSReader,
        NMEAParser,
        DEFAULT_BAUDRATES,
    )
except Exception as e:
    print(f"[错误] 无法导入 serial_gnss 模块（pyserial 是否安装？）: {e}")
    sys.exit(1)


def _print_fix(fix: dict):
    src = fix.get("source", "none")
    if src != "real":
        # 已连串口但暂无有效 GGA/RMC（室内常见），不报错
        print("  已连接串口，等待定位...")
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


def cmd_list_ports() -> int:
    """只列出所有可用串口及其描述，不尝试连接。"""
    ports = SerialGNSSReader.list_candidate_ports()
    if not ports:
        print("未发现串口设备。请检查 USB 连接。")
        return 0
    print(f"发现 {len(ports)} 个串口：")
    for p in ports:
        chip = p.get("usb_ttl_chip")
        chip_s = f"  [{chip}]" if chip else ""
        desc = p.get("description") or ""
        print(f"  {p['device']:24s}  {desc}{chip_s}")
    return 0


def cmd_baudscan(port: str, read_window: float = 1.5) -> int:
    """对指定端口扫描所有波特率，报告哪个能收到合法 NMEA。"""
    parser = NMEAParser()
    baudrates = DEFAULT_BAUDRATES
    print(f"[信息] 扫描 {port} 的波特率 {baudrates} ...")
    found = []
    for baud in baudrates:
        ser = None
        try:
            ser = serial.Serial(port, baud, timeout=0.5)
            got = 0
            deadline = time.time() + read_window
            while time.time() < deadline:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="ignore")
                if line.startswith("$") and parser.parse(line) is not None:
                    got += 1
                    if got >= 2:  # 收到 2 条合法 NMEA 即认为该波特率可用
                        break
            if got >= 2:
                print(f"  [OK] {baud:>6d} baud: 收到合法 NMEA ({got} 句)")
                found.append(baud)
            else:
                print(f"  [--] {baud:>6d} baud: 无合法 NMEA")
        except Exception as e:
            print(f"  [!!] {baud:>6d} baud: 打开失败 ({e})")
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
    if found:
        print(f"[完成] 可用波特率: {found}")
    else:
        print("[完成] 未在任何波特率收到合法 NMEA（模块未接/接线错/非 GNSS 口？）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="串口 GNSS 自检")
    ap.add_argument("--port", help="指定串口（如 /dev/ttyUSB0 或 COM3）")
    ap.add_argument("--baud", type=int, help="指定波特率")
    ap.add_argument("--seconds", type=float, default=5.0,
                    help="找到模块后打印时长（默认 5 秒）")
    ap.add_argument("--list-ports", action="store_true",
                    help="只列出所有可用串口及其描述，不尝试连接")
    ap.add_argument("--baudscan", metavar="PORT",
                    help="对指定端口扫描所有波特率，报告哪个能收到合法 NMEA")
    args = ap.parse_args()

    # --list-ports：只列举，不连接
    if args.list_ports:
        sys.exit(cmd_list_ports())

    # --baudscan：扫波特率，不进入常规定位循环
    if args.baudscan:
        sys.exit(cmd_baudscan(args.baudscan))

    reader = SerialGNSSReader()

    if args.port:
        # 指定端口：直接打开，跳过扫描
        print(f"[信息] 使用指定端口 {args.port} @ {args.baud or 9600} baud")
        ok = reader.start(args.port, args.baud or 9600)
        if not ok:
            print(f"[信息] 无法打开 {args.port}（被占用/无权限/无设备）。")
            print("未发现串口设备或 GNSS 模块。请检查 USB 连接。")
            sys.exit(0)
        port, baud = args.port, args.baud or 9600
    else:
        # 自动扫描
        print(f"[信息] 扫描串口与波特率 {DEFAULT_BAUDRATES} ...")
        info = SerialGNSSReader.auto_detect()
        if info is None:
            print("未发现串口设备或 GNSS 模块。请检查 USB 连接。")
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
            if fix.get("source") == "real" and fix.get("latitude") is not None:
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
