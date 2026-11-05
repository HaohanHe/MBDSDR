# SPDX-License-Identifier: MIT
"""真机七环验收套件公共层。

硬红线（写进脚本、也写进每条输出）
----------------------------------
1. 没有真实 SDR 设备时，硬件环一律返回 ``SKIP(NO_HW)``；
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
SKIP = "SKIP"
SKIPPED = SKIP  # 兼容旧名：统一为 SKIP + 原因码
UNVERIFIED = "UNVERIFIED"

#: SKIP 原因码——SKIP 必须带其中之一，否则视为口径缺陷。
SKIP_CODES = {
    "NO_HW": "无硬件/后端（SDR、云台、串口 GNSS 等）",
    "NO_DATA": "缺数据（TLE、星历、录制样本等）",
    "NEEDS_OPERATOR": "需真人在场做物理动作或授权",
    "NEEDS_TIME_WINDOW": "需特定时间窗（过境/天象/发射窗口）",
}

RED_LINE = (
    "红线：无真机 → SKIP(NO_HW) 且原因码可见；禁止用模拟/合成/debug 源数据判 PASS。"
    "本结果的所有数字均来自真实设备或真实计算，未取到即 SKIP/标 UNVERIFIED。"
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


def forced_offline() -> bool:
    """测试/演示用：强制「无硬件」路径。

    只会把硬件环降级为 SKIP + 离线替代，**不可能**把任何环判 PASS，
    因此不会被用来冒充实测；用于验证 SKIP 原因码与离线替代链路。
    """
    return (os.environ.get("MBDSDR_HW_FORCE_OFFLINE") or "").strip().lower() in (
        "1", "true", "yes", "on")


def rtl_device_count() -> int:
    """真实 RTL-SDR 枚举数量。加载失败/无设备一律返回 0，不猜。"""
    if forced_offline():
        return 0
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


def _exception_chain_text(e: BaseException) -> str:
    parts: list[str] = []
    cur: BaseException | None = e
    seen = 0
    while cur is not None and seen < 6:
        parts.append(f"{type(cur).__name__}: {cur}")
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return " | ".join(parts).lower()


def access_denied(e: BaseException) -> bool:
    """设备已枚举但打不开（权限/占用/驱动未绑定）——环境问题，不是环缺陷。"""
    text = _exception_chain_text(e)
    return any(k in text for k in (
        "access denied", "libusb_error_access", "insufficient permission",
        "permission denied", "busy", "占用", "另一个程序", "cannot open"))


def device_present() -> bool:
    return rtl_device_count() > 0 and bool(real_devices())


def transient_open_error(e: BaseException) -> bool:
    """打开失败的「瞬时」类：句柄未释放（access denied）或重枚举空窗（device not found）。

    串行跑多环时，上一环刚关掉 USB 句柄，下一环立刻重枚举可能瞬时看不到设备；
    这不是设备坏了，也不该判环 FAIL。真拔线时重试 4 次后仍会抛出。
    """
    from mbdsdr_ai.core.errors import DeviceNotFoundError

    return isinstance(e, DeviceNotFoundError) or access_denied(e)


def open_controller(device_id: str, attempts: int = 4):
    """打开 SDR；USB 从上一个进程/句柄释放需要一点时间，瞬时失败短暂重试。

    只对「瞬时」类错误重试（最多 4 次、累计约 1.8 s）；其他错误立即抛出，
    不掩盖真故障。ring1 的反复开关复用同一判定。
    """
    from mbdsdr_ai.core import SDRController

    last: Exception | None = None
    for i in range(attempts):
        ctrl = SDRController()
        try:
            ctrl.connect(device_id)
            return ctrl
        except Exception as e:  # noqa: BLE001 - 需要按错误类型分流
            last = e
            try:
                ctrl.disconnect()
            except Exception:
                pass
            if not transient_open_error(e):
                raise
            time.sleep(0.3 * (i + 1))
    assert last is not None
    raise last


def require_device(ring: str, name: str, human_required: list[str] | None = None) -> dict | None:
    """无设备时直接返回 SKIP(NO_HW) 结果；有设备返回 None 继续跑。"""
    if rtl_device_count() > 0 and real_devices():
        return None
    return skip_result(
        ring, name, "NO_HW",
        "librtlsdr 未枚举到真实设备（无设备或驱动缺失）；本环未产生任何数字。",
        metrics={"device_count": rtl_device_count(), "real_devices": 0},
        checks=[check("device_present", False,
                      "librtlsdr 未枚举到真实设备（无设备或驱动缺失）", skipped=True)],
        user_message_on_failure=USER_MSG["no_device"],
        human_required=human_required or [],
        device_required=True,
    )


# ---------------------------------------------------------------- 结果信封
USER_MSG = {
    "no_device": "未检测到 SDR 设备。请插入 RTL-SDR 后点「重新枚举」；若已插入仍看不到，检查 USB 口与 WinUSB/Zadig 驱动。",
    "device_busy": "设备被其他程序占用。请关闭 SDR++ / SDR# / rtl_tcp / 其他采集进程后重试（同一根棒子只能有一个客户端）。",
    "device_access_denied": "设备已枚举但打不开（LIBUSB_ERROR_ACCESS）。请关闭占用进程；仍失败则用 Zadig 把驱动换成 WinUSB，并检查当前用户是否有 USB 访问权限。",
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
           artifacts: list[str] | None = None, notes: str = "",
           skip_code: str = "") -> dict:
    return {
        "ring": ring,
        "name": name,
        "status": status,
        "skip_code": skip_code,
        "metrics": metrics or {},
        "checks": checks or [],
        "user_message_on_failure": user_message_on_failure,
        "human_required": human_required or [],
        "device_required": bool(device_required),
        "artifacts": artifacts or [],
        "notes": notes,
        "red_line": RED_LINE,
    }


def skip_result(ring: str, name: str, code: str, reason: str,
                metrics: dict | None = None, checks: list[dict] | None = None,
                user_message_on_failure: str = "",
                human_required: list[str] | None = None,
                device_required: bool = True,
                artifacts: list[str] | None = None) -> dict:
    """SKIP 信封：必须带原因码（NO_HW / NO_DATA / NEEDS_OPERATOR / NEEDS_TIME_WINDOW）。"""
    if code not in SKIP_CODES:
        raise ValueError(f"未知 SKIP 原因码：{code}（可选 {sorted(SKIP_CODES)}）")
    return result(ring, name, SKIP, metrics, checks,
                  user_message_on_failure=user_message_on_failure,
                  human_required=human_required, device_required=device_required,
                  artifacts=artifacts, notes=f"SKIP({code})：{reason}",
                  skip_code=code)


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
        if device_required and access_denied(e):
            return skip_result(
                ring, name, "NEEDS_OPERATOR",
                "设备已枚举但打不开（权限/占用/驱动未绑定），本环无法验证，不是环缺陷；"
                f"原始错误：{type(e).__name__}: {e}",
                metrics={"exception": f"{type(e).__name__}: {e}"},
                checks=[check("device_openable", False,
                              "设备存在但打开被拒（Access denied / busy）", skipped=True)],
                user_message_on_failure=USER_MSG["device_access_denied"],
                human_required=["关闭占用设备的程序 / 绑定 WinUSB 驱动后复跑"],
                device_required=True)
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


REAL_FM_FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "real_fm_88m1")


def real_fm_fixture() -> dict:
    """加载真实 OTA 录音夹具（88.1 MHz，RTL-SDR 实采），校验 sha256 与来历。

    这不是合成波形：``provenance.json`` 声明 ``external-truth / real_ota``，
    记录采集设备、时间、增益与 sha256。校验失败即 ``ok=False``——不拿来历不明
    或被篡改的数据判 PASS。
    """
    import hashlib
    import json

    import numpy as np

    data_p = os.path.join(REAL_FM_FIXTURE_DIR, "capture.sigmf-data")
    meta_p = os.path.join(REAL_FM_FIXTURE_DIR, "capture.sigmf-meta")
    prov_p = os.path.join(REAL_FM_FIXTURE_DIR, "provenance.json")
    for p in (data_p, meta_p, prov_p):
        if not os.path.isfile(p):
            return {"ok": False, "reason": f"缺真实 OTA 夹具文件：{p}"}

    with open(prov_p, encoding="utf-8") as f:
        prov = json.load(f)
    with open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    expected = str(prov["files"]["capture.sigmf-data"]["sha256"])
    h = hashlib.sha256()
    with open(data_p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != expected:
        return {"ok": False,
                "reason": f"夹具 sha256 不符（期望 {expected[:16]}…，实得 {actual[:16]}…）",
                "sha256": actual}

    raw = np.fromfile(data_p, dtype=np.uint8)
    iq = (raw.astype(np.float64) - 127.5) / 127.5
    iq = iq[0::2] + 1j * iq[1::2]
    g = meta.get("global", {})
    return {
        "ok": True,
        "iq": iq,
        "samples": int(len(iq)),
        "sample_rate": float(g.get("core:sample_rate", 0.0)),
        "center_hz": float(g.get("core:frequency", 0.0)),
        "datatype": str(g.get("core:datatype", "")),
        "provenance": str(g.get("mbdsdr:provenance", "")),
        "hardware": str(g.get("core:hw", "")),
        "capture_utc": str(meta.get("captures", [{}])[0].get("core:datetime", "")),
        "sha256": actual,
        "expected_sha256": expected,
        "source_note": str(prov.get("note", "")),
        "path": data_p,
    }


def common_args(desc: str) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--freq", type=float, default=0.0, help="指定频点 Hz（0=自动找强台）")
    ap.add_argument("--out", default="", help="证据根目录（默认 .tmp-tool-results/hw_acceptance）")
    args = ap.parse_args()
    if args.out:
        os.environ["MBDSDR_HW_ACCEPTANCE_DIR"] = args.out
    return args
