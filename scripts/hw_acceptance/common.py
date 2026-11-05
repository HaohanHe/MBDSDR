# SPDX-License-Identifier: MIT
"""真机七环验收套件公共层。

硬红线（写进脚本、也写进每条输出）
----------------------------------
1. 没有真实 SDR 设备时，硬件环一律返回 ``SKIPPED(no_device)``；
2. **严禁**用模拟数据、合成信号、``debug`` 调试源把硬件环判为 PASS；
3. 只有真实设备真实取值才允许 PASS；拿不到的字段标 ``UNVERIFIED`` 并写明原因；
4. 所有临时脚本与产物落在 D 盘 ``.tmp-tool-results/hw_acceptance/`` 下，
   不碰用户 C 盘目录；发射审计走 ``MBDSDR_TX_AUDIT_PATH`` 重定向到同一目录。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import sys
import time
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

ARTIFACT_ROOT = os.path.join(REPO_ROOT, ".tmp-tool-results", "hw_acceptance")

PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED(no_device)"
UNVERIFIED = "UNVERIFIED"

RED_LINE = (
    "红线：无真机 → SKIPPED(no_device)；禁止用模拟/合成/debug 源数据判 PASS。"
    "本结果的所有数字均来自真实设备或真实计算，未取到即标注 UNVERIFIED。"
)


# ---------------------------------------------------------------- 设备探测
def _dll_candidates() -> list[str]:
    """librtlsdr 动态库候选路径（环境变量优先，其次 rtl-sdr 官方安装位）。"""
    cands: list[str] = []
    env = (os.environ.get("MBDSDR_RTLSDR_DLL") or "").strip()
    if env:
        cands.append(env)
    cands.append(os.path.join(r"C:\rtl-sdr\bin", "librtlsdr.dll"))
    found = ctypes.util.find_library("rtlsdr")
    if found:
        cands.append(found)
    return [c for c in cands if c]


def _load_librtlsdr():
    last: Exception | None = None
    for path in _dll_candidates():
        try:
            return ctypes.CDLL(path)
        except OSError as e:  # 继续试下一个候选
            last = e
    return None


def rtl_device_count() -> int:
    """真实 RTL-SDR 枚举数量。加载失败/无设备一律返回 0，不猜。"""
    lib = _load_librtlsdr()
    if lib is None:
        return 0
    try:
        return int(lib.rtlsdr_get_device_count())
    except Exception:
        return 0


def rtl_device_strings() -> list[dict]:
    """枚举设备三元组（制造商/产品/序列），用于环 1 的识别证据。"""
    lib = _load_librtlsdr()
    if lib is None:
        return []
    out: list[dict] = []
    try:
        n = int(lib.rtlsdr_get_device_count())
        for i in range(n):
            man = ctypes.create_string_buffer(256)
            prod = ctypes.create_string_buffer(256)
            ser = ctypes.create_string_buffer(256)
            lib.rtlsdr_get_device_usb_strings(i, man, prod, ser)
            out.append({
                "index": i,
                "manufacturer": man.value.decode(errors="replace"),
                "product": prod.value.decode(errors="replace"),
                "serial": ser.value.decode(errors="replace"),
            })
    except Exception:
        return out
    return out


def real_devices() -> list:
    """非 debug 的真实设备列表（debug 调试源永远不算硬件）。"""
    try:
        from mbdsdr_ai.device_manager import DeviceManager

        return [d for d in DeviceManager().list_devices()
                if str(getattr(d, "driver", "")).lower() != "debug"]
    except Exception:
        return []


def require_device(ring: str, name: str, human_required: list[str] | None = None) -> dict | None:
    """无设备时直接返回 SKIPPED(no_device) 结果；有设备返回 None 继续跑。"""
    if rtl_device_count() > 0 and real_devices():
        return None
    return result(
        ring, name, SKIPPED,
        metrics={"device_count": rtl_device_count(), "real_devices": 0},
        checks=[check("device_present", False, "librtlsdr 未枚举到真实设备（无设备或驱动缺失）", skipped=True)],
        user_message_on_failure=USER_MSG["no_device"],
        human_required=human_required or [],
        device_required=True,
        notes="未接真实 SDR，按红线跳过；本环未产生任何数字。",
    )


# ---------------------------------------------------------------- 结果信封
USER_MSG = {
    "no_device": "未检测到 SDR 设备。请插入 RTL-SDR 后点「重新枚举」；若已插入仍看不到，检查 USB 口与 WinUSB/Zadig 驱动。",
    "device_busy": "设备被其他程序占用。请关闭 SDR++ / SDR# / rtl_tcp / 其他采集进程后重试（同一根棒子只能有一个客户端）。",
    "driver_missing": "找不到 SDR 运行库（librtlsdr）。请安装 rtl-sdr 驱动包，或设置环境变量 MBDSDR_RTLSDR_DLL 指向 librtlsdr.dll。",
    "freq_out_of_range": "该频率超出本机调谐器范围。FC0012 约 22–948.6 MHz：1090 MHz（ADS-B）无法接收，请换 R820T 系设备。",
    "no_signal": "当前频点没有足够强的信号。请换已知强台（FM 广播）或接天线后重试。",
    "tx_not_authorized": "发射未授权。所有发射必须由真人在界面确认（一次性 token）；模型自报 arm_manual=true 不构成确认，已被拒绝。",
}


def check(name: str, ok: bool, detail: str = "", skipped: bool = False) -> dict:
    return {"name": name, "ok": bool(ok), "detail": str(detail), "skipped": bool(skipped)}


def result(ring: str, name: str, status: str, metrics: dict | None = None,
           checks: list[dict] | None = None, user_message_on_failure: str = "",
           human_required: list[str] | None = None, device_required: bool = True,
           artifacts: list[str] | None = None, notes: str = "") -> dict:
    return {
        "ring": ring,
        "name": name,
        "status": status,
        "metrics": metrics or {},
        "checks": checks or [],
        "user_message_on_failure": user_message_on_failure,
        "human_required": human_required or [],
        "device_required": bool(device_required),
        "artifacts": artifacts or [],
        "notes": notes,
        "red_line": RED_LINE,
    }


def artifact_dir(ring_dir: str) -> str:
    """本次验收的证据目录（D 盘；run_all 通过环境变量统一时间戳）。"""
    root = os.environ.get("MBDSDR_HW_ACCEPTANCE_DIR") or ARTIFACT_ROOT
    if not os.path.isabs(root):
        root = os.path.join(REPO_ROOT, root)
    assert root.lower().startswith("d:\\"), f"产物必须在 D 盘：{root}"
    path = os.path.join(root, ring_dir)
    os.makedirs(path, exist_ok=True)
    return path


def emit(res: dict, out_dir: str, filename: str = "result.json") -> int:
    """写 JSON 产物 + stdout 打印；FAIL 退出码 1，其余 0（SKIPPED 不是失败）。"""
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    res["artifact"] = path
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 1 if res["status"] == FAIL else 0


def guarded(ring: str, name: str, fn, *, device_required: bool = True,
            human_required: list[str] | None = None) -> dict:
    """执行环主体；异常 = 本环 FAIL（不拖死全局），带可读原因。"""
    if device_required:
        skip = require_device(ring, name, human_required)
        if skip is not None:
            return skip
    try:
        return fn()
    except Exception as e:
        return result(ring, name, FAIL,
                      metrics={"exception": f"{type(e).__name__}: {e}"},
                      checks=[check("ring_completed", False, traceback.format_exc()[-1200:])],
                      user_message_on_failure=str(e),
                      human_required=human_required or [],
                      device_required=device_required,
                      notes="环内未捕获异常，按 FAIL 处理。")


def find_strong_fm(ctrl, nfft: int = 1024, fs: float = 2.4e6) -> tuple[float, float]:
    """扫 89–107 MHz 找最强 FM 台，返回 (频率 Hz, 峰底比 dB)。"""
    import numpy as np

    best_f, best_pf = 0.0, 0.0
    bin_hz = fs / nfft
    for center in range(89_000_000, 107_000_001, 2_000_000):
        ctrl.set_frequency(center)
        time.sleep(0.08)
        db = ctrl.read_spectrum(nfft)
        if db is None:
            continue
        db = np.fft.fftshift(np.asarray(db, dtype=np.float64))
        c = len(db) // 2
        edge = int(700e3 / bin_hz)
        win = db[c - edge:c + edge]
        pf = float(np.max(win) - np.median(win))
        if pf > best_pf:
            best_pf, best_f = pf, center + (float(np.argmax(win)) - edge) * bin_hz
        if best_pf > 30:
            break
    return best_f, best_pf


def common_args(desc: str) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--freq", type=float, default=0.0, help="指定频点 Hz（0=自动找强台）")
    ap.add_argument("--out", default="", help="证据根目录（默认 .tmp-tool-results/hw_acceptance）")
    args = ap.parse_args()
    if args.out:
        os.environ["MBDSDR_HW_ACCEPTANCE_DIR"] = args.out
    return args
