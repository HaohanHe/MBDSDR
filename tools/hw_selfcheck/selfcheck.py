#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MBDSDR 真机环境一键自检工具 (hardware self-check).

只读探测本机是否具备运行 MBDSDR（cpp/ Qt 桌面端、mobile/ Flutter 端、
mbdsdr_ai/ Python 原型）所需的 SDR / 声卡 / GNSS 串口 / 依赖环境。

设计原则：
  * 仅依赖 Python 3 标准库；必要时调用系统命令，命令缺失一律降级为 WARN，
    绝不抛 traceback。
  * 只读设备与系统：不安装系统包、不改 udev、不写 /etc；rtl_sdr 采集仅写
    临时目录，且限时 <= 3 秒。
  * 无硬件时诚实报告，禁止假造“检测到设备”。

用法：
  python3 selfcheck.py                # 人类可读报告
  python3 selfcheck.py --json         # 机器可读 JSON
  python3 selfcheck.py --timeout 15   # 子进程默认超时（秒）

退出码：
  0  全部检查无 FAIL（允许 WARN）
  1  存在至少一项 FAIL
  2  脚本自身参数/运行错误
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import grp
import importlib.util
import json
import os
import platform
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
# SKIP 仅在“上游前置检查未通过、本项不具备执行条件”时使用，仍如实说明原因。
SKIP = "SKIP"

# 目标 RTL-SDR USB VID:PID（真机为 0bda:2838 RTL2838UHIDIR）。
# 0bda:2832 是同源 RTL2832U OEM，作为兼容命中一并提示。
TARGET_USB_IDS = {
    "0bda:2838": "RTL2838UHIDIR (目标真机)",
    "0bda:2832": "RTL2832U OEM (兼容)",
}

GNSS_BAUDS = (9600, 115200)
NMEA_PREFIXES = (b"$GP", b"$GN", b"$BD", b"$GL", b"$GA", b"$G")

RTL_SDR_SAMPLE_RATE = 2_400_000
# 3 秒 * 2.4 MS/s = 7.2e6 采样点
RTL_SDR_SAMPLES = 7_200_000
RTL_SDR_MAX_SECONDS = 3.0


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    status: str = WARN
    evidence: list[str] = field(default_factory=list)
    fix: list[str] = field(default_factory=list)
    # 机器可读的结构化明细（json 输出用），人类报告里默认折叠
    detail: dict[str, Any] = field(default_factory=dict)

    def add_evidence(self, line: str) -> None:
        self.evidence.append(line)

    def add_fix(self, line: str) -> None:
        self.fix.append(line)

    def worst(self) -> None:
        """不主动改状态；状态由各检查函数显式设置。占位以便扩展。"""
        return


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def run_cmd(
    cmd: list[str],
    timeout: float = 10.0,
    cwd: str | None = None,
) -> tuple[int | None, str, str, str | None]:
    """安全地跑一个外部命令。

    返回 (returncode, stdout_text, stderr_text, error_string)。
    任何异常（命令不存在/超时/被杀）都吞掉并转成 error_string，绝不抛。
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or "", None
    except FileNotFoundError:
        return None, "", "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return None, "", "", f"timeout after {timeout}s: {' '.join(cmd)}"
    except PermissionError as e:
        return None, "", "", f"permission denied: {e}"
    except Exception as e:  # noqa: BLE001 - 自检脚本必须绝不崩
        return None, "", "", f"unexpected error: {type(e).__name__}: {e}"


def read_text_file(path: str, max_bytes: int = 4096) -> str | None:
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes).decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        return None


def find_rtl_tool(name: str) -> str | None:
    """优先 PATH，再找 ~/.local/bin/<name>。"""
    p = shutil.which(name)
    if p:
        return p
    local = Path.home() / ".local" / "bin" / name
    if local.exists() and os.access(local, os.X_OK):
        return str(local)
    return None


def current_user_groups() -> set[str]:
    """返回当前用户所属的所有组名。失败返回空集。"""
    names: set[str] = set()
    try:
        uid = os.getuid()
        pw = pwd.getpwuid(uid)
        names.add(pw.pw_name)
        for g in grp.getgrall():
            if pw.pw_name in g.gr_mem or g.gr_gid == pw.pw_gid:
                names.add(g.gr_name)
    except Exception:  # noqa: BLE001
        pass
    return names


# ---------------------------------------------------------------------------
# 检查 1：USB / udev / 权限
# ---------------------------------------------------------------------------

def scan_usb_via_sys(root: str = "/sys/bus/usb/devices") -> list[dict[str, str]]:
    """扫描 <root>/*/{idVendor,idProduct,product}。只读。root 可注入用于测试。"""
    found: list[dict[str, str]] = []
    root_path = Path(root)
    if not root_path.is_dir():
        return found
    for child in sorted(root_path.iterdir()):
        try:
            vid = read_text_file(str(child / "idVendor"))
            pid = read_text_file(str(child / "idProduct"))
            if not vid or not pid:
                continue
            prod = read_text_file(str(child / "product")) or ""
            # 过滤掉 USB 主控制器本身（hub 等仍保留，命中目标才记录）
            found.append(
                {
                    "sysdir": child.name,
                    "vid": vid.lower(),
                    "pid": pid.lower(),
                    "product": prod,
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return found


def scan_usb_via_lsusb() -> tuple[list[dict[str, str]], str | None]:
    """跑 lsusb 解析；返回 (设备列表, 错误说明)。"""
    if not shutil.which("lsusb"):
        return [], "lsusb not found"
    rc, out, err, errstr = run_cmd(["lsusb"], timeout=5)
    if rc != 0 or errstr:
        return [], errstr or f"lsusb rc={rc} stderr={err.strip()[:200]}"
    devs: list[dict[str, str]] = []
    pat = re.compile(r"ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})", re.I)
    for line in out.splitlines():
        m = pat.search(line)
        if not m:
            continue
        vid, pid = m.group(1).lower(), m.group(2).lower()
        # 取 ID 后面的描述串作为 product 名
        desc = line.split("ID", 1)[-1]
        desc = re.sub(r"^[0-9a-fA-F:]+", "", desc).strip()
        devs.append({"vid": vid, "pid": pid, "product": desc, "raw": line.strip()})
    return devs, None


def check_usb_udev() -> CheckResult:
    r = CheckResult("1. USB/udev：RTL-SDR 枚举与权限")

    # 1) lsusb
    lsusb_devs, lsusb_err = scan_usb_via_lsusb()
    # 2) /sys 扫描（永远做，作为兜底与交叉验证）
    sys_devs = scan_usb_via_sys()

    if lsusb_err:
        r.add_evidence(f"lsusb 不可用：{lsusb_err}（降级为 /sys 扫描）")
    else:
        r.add_evidence(f"lsusb 枚举到 {len(lsusb_devs)} 个 USB 设备")

    # 合并命中
    hits: list[dict[str, str]] = []
    for d in sys_devs:
        if f"{d['vid']}:{d['pid']}" in TARGET_USB_IDS:
            hits.append(d)
    for d in lsusb_devs:
        if f"{d['vid']}:{d['pid']}" in TARGET_USB_IDS and not any(
            h["vid"] == d["vid"] and h["pid"] == d["pid"] for h in hits
        ):
            hits.append(d)

    r.detail["sys_scanned"] = [f"{d['vid']}:{d['pid']} {d['product']}" for d in sys_devs]
    r.detail["target_hits"] = [
        {**h, "known_as": TARGET_USB_IDS.get(f"{h['vid']}:{h['pid']}", "?")}
        for h in hits
    ]

    if hits:
        r.status = PASS
        for h in hits:
            label = TARGET_USB_IDS.get(f"{h['vid']}:{h['pid']}", "?")
            r.add_evidence(f"  命中 USB 设备 {h['vid']}:{h['pid']} — {label} ({h.get('product','')})")
    else:
        r.status = FAIL
        r.add_evidence("  未在 lsusb / /sys/bus/usb 中找到 0bda:2838 (RTL2838UHIDIR) 或 0bda:2832")
        r.add_fix("  确认 RTL-SDR 已插入 USB（尽量直连主板后置 USB2.0 口，避免用 USB3.0 Hub）")
        r.add_fix("  重新插拔后执行 `lsusb | grep -E '0bda:(2838|2832)'` 复核")
        r.add_fix("  若 `dmesg | tail` 出现 USB 复位/枚举失败，换线或换口")

    # 3) 当前用户组
    groups = current_user_groups()
    r.detail["user_groups"] = sorted(groups)
    needed = {"plugdev", "dialout"}
    have = groups & needed
    missing = needed - groups
    if have:
        r.add_evidence(f"当前用户组：{sorted(groups)}（含 {sorted(have)}）")
    else:
        r.add_evidence(f"当前用户组：{sorted(groups)}（不含 plugdev/dialout）")

    if hits and missing:
        r.status = FAIL
        r.add_fix(f"  把当前用户加入 {'/'.join(sorted(missing))} 组：sudo usermod -aG {','.join(sorted(missing))} $USER（重新登录生效）")
    elif not hits:
        # 没设备时组问题降级为 WARN（反正也用不上）
        if missing:
            r.add_evidence(f"  提示：当前用户不在 {sorted(missing)} 组，真机接入后会影响设备权限")
            r.add_fix(f"  建议提前执行：sudo usermod -aG plugdev,dialout $USER && 重新登录")

    # 4) udev 规则
    rule_dirs = ["/lib/udev/rules.d", "/etc/udev/rules.d"]
    found_rules: list[str] = []
    for d in rule_dirs:
        if not os.path.isdir(d):
            continue
        for rp in glob.glob(os.path.join(d, "*.rules")):
            try:
                txt = read_text_file(rp, max_bytes=200_000) or ""
            except Exception:  # noqa: BLE001
                continue
            if "0bda" in txt and ("2838" in txt or "2832" in txt):
                found_rules.append(rp)
    r.detail["udev_rules_hit"] = found_rules
    if found_rules:
        r.add_evidence(f"已存在 RTL-SDR udev 规则：{found_rules}")
    else:
        r.add_evidence("未在 /lib/udev/rules.d 或 /etc/udev/rules.d 找到含 0bda:2838/2832 的规则")
        if r.status != FAIL:
            r.status = WARN
        r.add_fix("  安装 librtlsdr 自带的 99-rtl-sdr.rules 到 /etc/udev/rules.d/（本工具只读，不自动写入）")
        r.add_fix("  参考：仓库 repos/librtlsdr/rtl-sdr.rules，安装后执行 sudo udevadm control --reload && sudo udevadm trigger")

    return r


# ---------------------------------------------------------------------------
# 检查 2：rtl_test 设备枚举（tuner 型号 / 增益档数）
# ---------------------------------------------------------------------------

_RTL_TEST_DEVICE_RE = re.compile(r"^\s*(\d+):\s+([^,]+),\s*([^,]+),\s+SN:\s*(\S+)\s*$")
_RTL_TEST_TUNER_RE = re.compile(r"Found\s+(.+?)\s+tuner", re.I)
_RTL_TEST_GAINS_RE = re.compile(r"Supported gain values\s*\((\d+)\)\s*:\s*(.*)", re.I)
_RTL_TEST_NO_DEV_RE = re.compile(r"No supported devices found", re.I)


def parse_rtl_test_output(combined: str) -> dict[str, Any]:
    """从 rtl_test 合并输出（stdout+stderr）里抽 tuner / gains / 设备列表。"""
    out: dict[str, Any] = {}
    devs: list[dict[str, str]] = []
    for line in combined.splitlines():
        m = _RTL_TEST_DEVICE_RE.match(line)
        if m:
            devs.append({"idx": m.group(1), "vendor": m.group(2).strip(),
                         "product": m.group(3).strip(), "serial": m.group(4).strip()})
    out["devices"] = devs

    m = _RTL_TEST_TUNER_RE.search(combined)
    if m:
        out["tuner"] = m.group(1).strip()

    m = _RTL_TEST_GAINS_RE.search(combined)
    if m:
        out["gain_count"] = int(m.group(1))
        gains = [g for g in re.split(r"\s+", m.group(2).strip()) if g]
        out["gain_list_db"] = gains

    if _RTL_TEST_NO_DEV_RE.search(combined):
        out["no_supported_devices"] = True
    return out


def check_device_enum(usb_hits: list[dict]) -> CheckResult:
    r = CheckResult("2. 设备枚举：rtl_test -t（tuner 型号 / 增益档数）")

    exe = find_rtl_tool("rtl_test")
    if not exe:
        r.status = WARN
        r.add_evidence("未找到 rtl_test（PATH 与 ~/.local/bin 均无）")
        r.add_fix("  编译安装 librtlsdr：见 repos/librtlsdr/，或发行版包 apt install rtl-sdr")
        return r

    r.add_evidence(f"rtl_test 路径：{exe}")

    if not usb_hits:
        r.status = SKIP
        r.add_evidence("USB 层未命中 0bda:2838/2832，跳过 rtl_test（运行也会报 No supported devices）")
        r.detail["skipped_reason"] = "no_usb_device"
        return r

    rc, out, err, errstr = run_cmd([exe, "-t"], timeout=15)
    combined = (out or "") + "\n" + (err or "")
    r.detail["rc"] = rc
    r.detail["output_tail"] = combined.strip()[-2000:]
    if errstr:
        r.add_evidence(f"rtl_test 运行异常：{errstr}")

    parsed = parse_rtl_test_output(combined)
    r.detail["parsed"] = parsed

    if parsed.get("no_supported_devices"):
        r.status = FAIL
        r.add_evidence("rtl_test 报告 No supported devices found")
        r.add_fix("  USB 已枚举但 librtlsdr 打不开设备：检查是否被 dvb 内核驱动占用（sudo modprobe -r dvb_usb_rtl28xxu 仅建议，本工具不执行）")
        return r

    tuner = parsed.get("tuner")
    n_gains = parsed.get("gain_count")
    if tuner:
        r.add_evidence(f"  tuner 型号：{tuner}")
    else:
        r.add_evidence("  未在输出里解析到 tuner 型号（可能是 E4000 分支或固件未就绪）")
    if n_gains is not None:
        r.add_evidence(f"  增益档数：{n_gains}（示例：{parsed.get('gain_list_db', [])[:6]}）")
    for d in parsed.get("devices", []):
        r.add_evidence(f"  设备[{d['idx']}] {d['vendor']} {d['product']} SN={d['serial']}")

    if tuner and n_gains is not None:
        r.status = PASS
    else:
        r.status = WARN
        r.add_fix("  重跑 `rtl_test -t` 观察完整 stderr；若一直拿不到 tuner，换一根 USB 线/口")
    return r


# ---------------------------------------------------------------------------
# 检查 3：rtl_sdr 实读 ≤3 秒，统计丢包
# ---------------------------------------------------------------------------

_LOST_RE = re.compile(r"lost at least\s+(\d+)\s+bytes", re.I)


def check_stream_read(usb_hits: list[dict]) -> CheckResult:
    r = CheckResult("3. 流读取：rtl_sdr 实读 ≤3s（2.4 MS/s）丢包统计")

    exe = find_rtl_tool("rtl_sdr")
    if not exe:
        r.status = WARN
        r.add_evidence("未找到 rtl_sdr（PATH 与 ~/.local/bin 均无）")
        r.add_fix("  编译安装 librtlsdr 或 apt install rtl-sdr")
        return r

    if not usb_hits:
        r.status = SKIP
        r.add_evidence("USB 层未命中设备，跳过实读（rtl_sdr 会直接报错退出）")
        r.detail["skipped_reason"] = "no_usb_device"
        return r

    r.add_evidence(f"rtl_sdr 路径：{exe}，采样率 {RTL_SDR_SAMPLE_RATE} Hz，-n {RTL_SDR_SAMPLES}（≈{RTL_SDR_MAX_SECONDS:.0f}s）")

    with tempfile.NamedTemporaryFile(prefix="mbdsdr_selfcheck_", suffix=".bin", delete=False) as tf:
        tmp_path = tf.name

    try:
        # 子进程硬超时：3s 采集 + 启动/收尾余量
        hard_timeout = RTL_SDR_MAX_SECONDS + 8.0
        rc, out, err, errstr = run_cmd(
            [exe, "-s", str(RTL_SDR_SAMPLE_RATE), "-n", str(RTL_SDR_SAMPLES), tmp_path],
            timeout=hard_timeout,
        )
        combined = (out or "") + "\n" + (err or "")
        r.detail["rc"] = rc
        r.detail["output_tail"] = combined.strip()[-1500:]
        if errstr:
            r.add_evidence(f"rtl_sdr 运行异常：{errstr}")

        size = 0
        try:
            size = os.path.getsize(tmp_path)
        except OSError:
            pass
        r.detail["tmp_file_bytes"] = size
        r.add_evidence(f"临时文件：{tmp_path} 写入 {size} 字节（理论上限 ≈ {RTL_SDR_SAMPLES * 2} 字节，IQ 各 8bit）")

        lost_total = sum(int(m.group(1)) for m in _LOST_RE.finditer(combined))
        r.detail["lost_bytes_total"] = lost_total
        hits = _LOST_RE.findall(combined)
        if hits:
            r.add_evidence(f"  检测到丢包报告：共 {len(hits)} 次，累计 lost at least {lost_total} bytes")
        else:
            r.add_evidence("  输出中未出现 'lost at least N bytes' 提示")

        if size <= 0:
            r.status = FAIL
            r.add_evidence("  未采到任何 IQ 数据（临时文件为空）")
            r.add_fix("  检查设备是否被其他进程占用（rtl_tcp/gqrx/dump1090）；拔出重插")
        elif lost_total > 0:
            r.status = WARN
            r.add_evidence(f"  有 {lost_total} 字节丢包：USB 带宽/调度压力，建议改用 USB2.0 口、降低采样率")
        else:
            r.status = PASS
            r.add_evidence("  3 秒实读无丢包上报")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return r


# ---------------------------------------------------------------------------
# 检查 4：声卡
# ---------------------------------------------------------------------------

_CARD_RE = re.compile(r"card\s+(\d+):\s+(\S+)\s+\[([^\]]+)\]")


def _parse_cards(text: str) -> list[dict[str, str]]:
    out = []
    for m in _CARD_RE.finditer(text):
        out.append({"index": m.group(1), "short": m.group(2), "name": m.group(3)})
    return out


def check_soundcard() -> CheckResult:
    r = CheckResult("4. 声卡：aplay -l / arecord -l")

    has_aplay = shutil.which("aplay")
    has_arecord = shutil.which("arecord")

    if not has_aplay and not has_arecord:
        r.status = WARN
        r.add_evidence("aplay / arecord 均不可用（alsa-utils 未安装），且 /dev/snd 存在性见下")
        r.add_fix("  sudo apt install alsa-utils（仅真机推荐，本工具不安装）")
    else:
        if has_aplay:
            rc, out, err, _ = run_cmd(["aplay", "-l"], timeout=5)
            cards = _parse_cards((out or "") + (err or ""))
            r.detail["playback_cards"] = cards
            r.add_evidence(f"aplay -l：{len(cards)} 个播放卡")
            for c in cards:
                r.add_evidence(f"  [{c['index']}] {c['short']} — {c['name']}")
        if has_arecord:
            rc, out, err, _ = run_cmd(["arecord", "-l"], timeout=5)
            cards = _parse_cards((out or "") + (err or ""))
            r.detail["capture_cards"] = cards
            r.add_evidence(f"arecord -l：{len(cards)} 个录音卡")
            for c in cards:
                r.add_evidence(f"  [{c['index']}] {c['short']} — {c['name']}")

    # 无论命令在不在，都看一眼 /dev/snd
    snd_dir = "/dev/snd"
    if os.path.isdir(snd_dir):
        entries = sorted(os.listdir(snd_dir))
        r.detail["dev_snd_entries"] = entries
        r.add_evidence(f"/dev/snd 存在，条目：{entries}")
    else:
        r.add_evidence("/dev/snd 不存在（容器/无头云主机常见，真机一般会有）")

    has_playback = bool(r.detail.get("playback_cards")) or os.path.isdir("/dev/snd")
    if r.status == WARN:
        # 命令缺失但 /dev/snd 存在也保留 WARN（无法确认具体卡）
        pass
    elif has_playback:
        r.status = PASS
    else:
        r.status = WARN
        r.add_fix("  无音频设备：SSTV/SSB 监听与回放需要声卡；确认内核 snd-usb-audio/snd-hda-intel 已加载")
    return r


# ---------------------------------------------------------------------------
# 检查 5：GNSS 串口
# ---------------------------------------------------------------------------

def _try_read_nmea(dev_path: str, baud: int, read_timeout: float = 1.2) -> dict[str, Any]:
    """以指定波特率打开串口，读少量字节，判断是否 NMEA。只读。"""
    info: dict[str, Any] = {"device": dev_path, "baud": baud, "ok": False, "sample": None, "reason": None}
    fd = -1
    try:
        import termios  # 仅 Unix
        import select
    except ImportError:
        info["reason"] = "termios/select 不可用"
        return info

    try:
        fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    except PermissionError as e:
        info["reason"] = f"PermissionError: {e}"
        return info
    except OSError as e:
        info["reason"] = f"OSError: {e}"
        return info

    try:
        # 配置 raw 8N1
        try:
            attrs = termios.tcgetattr(fd)
            baud_const = getattr(termios, f"B{baud}", None)
            if baud_const is None:
                info["reason"] = f"unsupported baud {baud}"
                return info
            termios.cfmakeraw(attrs)
            termios.cfsetispeed(attrs, baud_const)
            termios.cfsetospeed(attrs, baud_const)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except termios.error as e:
            info["reason"] = f"termios error: {e}"
            return info

        end = time.time() + read_timeout
        buf = b""
        while time.time() < end and len(buf) < 512:
            r, _, _ = select.select([fd], [], [], 0.2)
            if not r:
                continue
            try:
                chunk = os.read(fd, 256)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk

        info["sample"] = buf[:200].decode("ascii", errors="replace")
        for line in buf.splitlines():
            line = line.strip()
            if line.startswith(NMEA_PREFIXES):
                info["ok"] = True
                info["reason"] = f"matched NMEA line: {line[:60].decode('ascii', errors='replace')}"
                break
        if not info["ok"] and not info["reason"]:
            info["reason"] = "read ok but no NMEA $-line within timeout"
        return info
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def check_gnss_serial() -> CheckResult:
    r = CheckResult("5. GNSS 串口：/dev/ttyUSB*,/dev/ttyACM*,/dev/serial/by-id")

    candidates: list[str] = []
    for pat in ("/dev/ttyUSB*", "/dev/ttyACM*"):
        candidates.extend(sorted(glob.glob(pat)))
    by_id = "/dev/serial/by-id"
    if os.path.isdir(by_id):
        try:
            for name in sorted(os.listdir(by_id)):
                p = os.path.join(by_id, name)
                rp = os.path.realpath(p)
                candidates.append(f"{p} -> {rp}")
        except OSError:
            pass

    r.detail["candidates"] = candidates
    if not candidates:
        r.status = WARN
        r.add_evidence("未发现任何 /dev/ttyUSB* /dev/ttyACM* 或 /dev/serial/by-id 条目")
        r.add_fix("  插入 GNSS 模块（USB-TTL 桥）后执行 ls /dev/ttyUSB* 复核")
        r.add_fix("  若出现但权限不足：sudo usermod -aG dialout $USER && 重新登录")
        return r

    r.add_evidence(f"候选串口 {len(candidates)} 个：{[c.split(' -> ')[0] for c in candidates]}")

    found_nmea = 0
    for cand in candidates:
        dev_path = cand.split(" -> ")[0]
        for baud in GNSS_BAUDS:
            res = _try_read_nmea(dev_path, baud, read_timeout=1.0)
            r.detail.setdefault("probes", []).append(res)
            tag = "NMEA-OK" if res["ok"] else "no-nmea"
            r.add_evidence(f"  {dev_path} @{baud}: {tag} — {res.get('reason')}")
            if res["ok"]:
                found_nmea += 1
                break

    if found_nmea > 0:
        r.status = PASS
        r.add_evidence(f"共 {found_nmea} 个串口输出 NMEA 语句")
    else:
        r.status = WARN
        r.add_evidence("存在串口设备但未读到 NMEA（可能模块未定位/未接线/波特率不对）")
        r.add_fix("  试 sudo cat /dev/ttyUSB0（Ctrl-C 退出）肉眼看是否有 $ 开头行")
        r.add_fix("  常见 GNSS 模块波特率：9600（NMEA 默认）/ 38400 / 115200")
    return r


# ---------------------------------------------------------------------------
# 检查 6：依赖（Python 绑定 + gpsd）
# ---------------------------------------------------------------------------

def check_dependencies() -> CheckResult:
    r = CheckResult("6. 依赖：pyrtlsdr / SoapySDR / gpsd")

    def _has_mod(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except Exception:  # noqa: BLE001
            return False

    py_rtlsdr = _has_mod("rtlsdr")
    py_soapy = _has_mod("SoapySDR")
    gpsd_bin = shutil.which("gpsd")
    gpsd_client = shutil.which("cgps") or shutil.which("gpsmon")

    r.detail["py_rtlsdr"] = py_rtlsdr
    r.detail["py_soapy"] = py_soapy
    r.detail["gpsd"] = gpsd_bin
    r.detail["gpsd_client"] = gpsd_client

    r.add_evidence(f"python3 版本：{platform.python_version()} ({sys.executable})")
    r.add_evidence(f"import rtlsdr     : {'OK' if py_rtlsdr else 'MISSING'}")
    r.add_evidence(f"import SoapySDR   : {'OK' if py_soapy else 'MISSING'}")
    r.add_evidence(f"which gpsd        : {gpsd_bin or 'MISSING'}")
    r.add_evidence(f"which cgps/gpsmon : {gpsd_client or 'MISSING'}")

    problems: list[str] = []
    if not py_rtlsdr:
        problems.append("  pip install pyrtlsdr（或 apt install python3-rtlsdr）")
    if not py_soapy:
        problems.append("  pip install SoapySDR（可选，mbdsdr_ai 原型用到时再装）")
    if not gpsd_bin:
        problems.append("  sudo apt install gpsd gpsd-clients（GNSS PPS/NMEA 守护，真机推荐）")

    if problems:
        r.status = WARN
        r.add_evidence(f"缺 {len(problems)} 项依赖（不影响 C++/Qt 桌面端，影响 mbdsdr_ai Python 原型与 GNSS 守护）")
        for p in problems:
            r.add_fix(p)
    else:
        r.status = PASS
    return r


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def summarize(results: list[CheckResult]) -> dict[str, Any]:
    counts = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    no_sdr = all(
        r.detail.get("skipped_reason") == "no_usb_device" or "未在 lsusb" in " ".join(r.evidence)
        for r in results
        if "USB" in r.name
    )

    conclusion: str
    todo: list[str] = []
    if counts[FAIL] == 0 and counts[PASS] > 0:
        conclusion = "环境基本就绪：未发现阻断性问题。"
    elif counts[FAIL] == 0:
        conclusion = "环境可跑，但有若干 WARN 项建议修复（见上）。"
    else:
        # 判定是否是“无硬件”场景
        usb_check = next((r for r in results if r.name.startswith("1.")), None)
        if usb_check is not None and not usb_check.detail.get("target_hits"):
            conclusion = "未检测到 RTL-SDR 真机设备；本机适合做软件/CI 自检，真机联调请在插好硬件的机器上重跑。"
            todo = [
                "插入 RTL-SDR (0bda:2838) 到 USB2.0 口，确认 lsusb 可见",
                "插入 GNSS 模块到 USB-TTL，确认 /dev/ttyUSB* 出现",
                "把当前用户加入 plugdev,dialout 组并重新登录",
                "安装 librtlsdr udev 规则（参考 repos/librtlsdr/rtl-sdr.rules）",
                "重跑本脚本，期待第 1/2/3 项变 PASS",
            ]
        else:
            conclusion = "存在阻断性 FAIL，请按上面“修复建议”逐项处理。"

    return {
        "counts": counts,
        "conclusion": conclusion,
        "real_machine_todo": todo,
    }


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

_STATUS_COLOR = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", SKIP: "\033[90m"}
_RESET = "\033[0m"


def print_human(results: list[CheckResult], summary: dict[str, Any]) -> None:
    print("=" * 72)
    print(f"MBDSDR 真机环境自检  {_now_iso()}")
    print(f"主机：{platform.node()}  OS：{platform.platform()}  Python：{platform.python_version()}")
    print("=" * 72)
    for r in results:
        color = _STATUS_COLOR.get(r.status, "")
        print(f"\n[{color}{r.status}{_RESET}] {r.name}")
        for e in r.evidence:
            print(f"  {e}")
        if r.fix:
            print("  修复建议：")
            for f in r.fix:
                print(f"   - {f}")
    print("\n" + "=" * 72)
    c = summary["counts"]
    print(f"汇总：PASS={c[PASS]}  WARN={c[WARN]}  FAIL={c[FAIL]}  SKIP={c[SKIP]}")
    print(f"结论：{summary['conclusion']}")
    if summary["real_machine_todo"]:
        print("\n真机待办清单：")
        for i, t in enumerate(summary["real_machine_todo"], 1):
            print(f"  {i}. {t}")
    print("=" * 72)


def build_json(results: list[CheckResult], summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": "mbdsdr-hw-selfcheck",
        "version": "0.1.0",
        "timestamp": _now_iso(),
        "host": platform.node(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "checks": [asdict(r) for r in results],
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MBDSDR 真机环境一键自检（只读）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--timeout", type=float, default=15.0, help="子进程默认超时（秒）")
    args = ap.parse_args(argv)

    results: list[CheckResult] = []
    try:
        # 1. USB/udev（先跑，后续检查依赖其命中结果）
        usb = check_usb_udev()
        results.append(usb)
        usb_hits = usb.detail.get("target_hits", [])

        results.append(check_device_enum(usb_hits))
        results.append(check_stream_read(usb_hits))
        results.append(check_soundcard())
        results.append(check_gnss_serial())
        results.append(check_dependencies())
    except Exception as e:  # noqa: BLE001
        # 最后兜底：任何意外都不允许 traceback 给用户看
        print(f"[FATAL] selfcheck 内部错误（已捕获，不影响其他检查）：{type(e).__name__}: {e}",
              file=sys.stderr)
        fatal = CheckResult("0. 内部兜底", status=FAIL,
                             evidence=[f"{type(e).__name__}: {e}"],
                             fix=["请把本行输出反馈给 mbdsdr_ai 维护者"])
        results.append(fatal)

    summary = summarize(results)

    if args.json:
        print(json.dumps(build_json(results, summary), ensure_ascii=False, indent=2))
    else:
        print_human(results, summary)

    return 1 if summary["counts"].get(FAIL, 0) > 0 else 0


if __name__ == "__main__":
    # 让 SIGTERM/SIGINT 干净退出
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    sys.exit(main())
