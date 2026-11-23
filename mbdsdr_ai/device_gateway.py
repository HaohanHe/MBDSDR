# SPDX-License-Identifier: MIT
"""多设备网关（M2 内核）：配对角色的**只读**端点 + 位姿上报。

它解决什么
==========
用户要「两台手机挂望远镜、手机/电脑/头显同时互联」。在把任何东西暴露到
局域网之前，先把三件事做成可测的内核：

1. **配对**：权威端生成 6 位一次性配对码；设备用码换自己的 token；
   token 只在内存里，带 TTL，可吊销。
2. **角色**：`lens`（镜头粗指向）/ `eyepiece`（目镜认星）/ `handheld`（手持只读）
   / `headset`（空间化只读）。角色不符就拒绝对应动作。
3. **只读 + 位姿**：`/panorama`、`/passes`、`/status`、`/spectrum`、`/roster`、
   `/poses`、`/fuse` 是读；
   只有 `lens/eyepiece` 能 `POST /pose`。**没有任何写硬件/改站址/调谐端点**——
   那些仍归桌面权威端。
4. **多机融合**：`/fuse` 把两条位姿喂给 `sky_pointing.fuse_poses`（与
   `sky_pose_fuse` 工具同一内核）；不可用时 `valid=false` + 原因，不猜朝向。
   目镜可选带 `identification`（星名/HIP/角距），**只存不融合**，
   镜头机携带即拒绝。
5. **SDR 一次性授权（M4.2）**：`POST /sdr/lease` 只给 `headset` 签发独占租约
   （谁占用、多久、何时释放），`POST /sdr/release` 由持有者释放，
   `GET /sdr/lease` 查状态。桌面自报占用或已有他人租约时**拒绝**；
   本网关不启动 rtl_tcp、不碰 dongle，只维护授权。rtl_tcp 地址只回给持有者。

安全边界（与 `web_bridge_host.py` 的本地桥分开）
================================================
* 默认只绑回环；绑非回环必须显式 ``allow_lan=True``，且只允许私有网段
  （拒绝公网地址与 ``0.0.0.0``/``::`` 通配）。
* 所有局域网端点都要 ``Authorization: Bearer <token>``；只有 `/health` 与
  `/pair` 免 token。
* 配对失败 5 次/60 秒即锁 60 秒，防 6 位码被无限试。
* 无 CORS 头；不带 cookie；不把 token 写日志。
* 本文档不宣称「已实现多机互联」：头显接入、桌面接线与真实局域网鉴权
  仍待后续（见 `.trae/specs/multi-device-interconnect/`）。
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

__all__ = [
    "ROLES",
    "POSE_ROLES",
    "SDR_LEASE_SCHEMA",
    "SDR_LEASE_ROLES",
    "SPECTRUM_SCHEMA",
    "Device",
    "PairingRegistry",
    "SdrLeaseRegistry",
    "DeviceGateway",
    "build_default_panorama_provider",
    "build_desktop_spectrum_provider",
    "build_station_panorama_provider",
    "parse_tle_groups",
    "build_tle_passes_provider",
    "build_station_passes_provider",
]

#: 可配对角色：桌面是权威端，不通过本网关配对。
ROLES = ("lens", "eyepiece", "handheld", "headset")
#: 允许上报位姿的角色。
POSE_ROLES = ("lens", "eyepiece")
#: 允许申请直连 SDR 租约的角色（头显；手机不直连 dongle）。
SDR_LEASE_ROLES = ("headset",)

PAIR_FAIL_WINDOW_S = 60.0
PAIR_FAIL_LIMIT = 5
PAIR_LOCK_S = 60.0
DEFAULT_TOKEN_TTL_S = 12 * 3600.0

#: 过境预报信封 schema（头显/手机增量拉取用）。
PASSES_SCHEMA = "mbdsdr.sat-passes/1"
#: 权威端状态信封 schema（只读 /status）。
STATUS_SCHEMA = "mbdsdr.desktop-status/1"
#: 权威端频谱只读快照 schema（`mbdsdr.spectrum-snapshot/1`）。
SPECTRUM_SCHEMA = "mbdsdr.spectrum-snapshot/1"
#: SDR 一次性授权信封 schema。
SDR_LEASE_SCHEMA = "mbdsdr.sdr-lease/1"
#: 默认租约时长：到期自动失效，持有者可续租。
DEFAULT_SDR_LEASE_TTL_S = 300.0
POSE_MAX_AGE_S = 300.0
_POSES_LIMIT = 64


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_bind(host: str, allow_lan: bool) -> str:
    """绑定地址体检：默认只回环；显式 allow_lan 也只收私有网段。"""
    raw = str(host or "").strip()
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ValueError(f"绑定地址必须是 IP 字面量（{raw!r}）：{exc}") from exc
    if ip.is_loopback:
        return raw
    if not allow_lan:
        raise ValueError("非回环地址必须显式 allow_lan=True（默认只绑 127.0.0.1）")
    if ip.is_unspecified or not ip.is_private:
        raise ValueError(f"只允许回环或私有网段地址，拒绝 {raw}")
    return raw


@dataclass
class Device:
    token: str
    device_id: str
    name: str
    role: str
    paired_at: float
    last_seen: float
    expires_at: float

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.device_id,
            "name": self.name,
            "role": self.role,
            "paired_at": _iso(datetime.fromtimestamp(self.paired_at, timezone.utc)),
            "last_seen": _iso(datetime.fromtimestamp(self.last_seen, timezone.utc)),
        }


class PairingRegistry:
    """配对码 + 设备 token 台账（线程安全、内存态、可注入时钟）。"""

    def __init__(self, *, token_ttl_s: float = DEFAULT_TOKEN_TTL_S,
                 now: Callable[[], float] = time.time,
                 code_factory: Optional[Callable[[], str]] = None,
                 token_factory: Optional[Callable[[], str]] = None):
        self._lock = threading.Lock()
        self._now = now
        self._token_ttl_s = float(token_ttl_s)
        self._code_factory = code_factory or (
            lambda: f"{secrets.randbelow(1000000):06d}")
        self._token_factory = token_factory or (
            lambda: secrets.token_urlsafe(24))
        self._code = self._code_factory()
        self._devices: Dict[str, Device] = {}
        self._fail_times: List[float] = []
        self._lock_until = 0.0

    @property
    def code(self) -> str:
        """当前配对码（只给权威端控制台看，不进 API 回执）。"""
        return self._code

    def rotate_code(self) -> str:
        with self._lock:
            self._code = self._code_factory()
            return self._code

    def pair(self, code: str, device_name: str, role: str) -> Dict[str, Any]:
        """用配对码换 token；失败返回稳定原因，不做模糊提示。"""
        name = str(device_name or "").strip()
        role = str(role or "").strip()
        with self._lock:
            now = float(self._now())
            if now < self._lock_until:
                return {"ok": False, "code": "locked",
                        "reason": f"配对失败过多，已锁定到 "
                                  f"{_iso(datetime.fromtimestamp(self._lock_until, timezone.utc))}"}
            if role not in ROLES:
                return {"ok": False, "code": "bad_role",
                        "reason": f"角色必须是 {'/'.join(ROLES)} 之一，收到 {role!r}"}
            if not name:
                return {"ok": False, "code": "bad_name",
                        "reason": "device_name 不能为空"}
            if not hmac.compare_digest(str(code or ""), self._code):
                self._fail_times = [t for t in self._fail_times
                                    if now - t <= PAIR_FAIL_WINDOW_S]
                self._fail_times.append(now)
                if len(self._fail_times) >= PAIR_FAIL_LIMIT:
                    self._lock_until = now + PAIR_LOCK_S
                    self._fail_times = []
                return {"ok": False, "code": "bad_code",
                        "reason": "配对码不正确或已轮换"}
            token = self._token_factory()
            device = Device(
                token=token,
                device_id=f"dev-{secrets.token_hex(4)}",
                name=name,
                role=role,
                paired_at=now,
                last_seen=now,
                expires_at=now + self._token_ttl_s,
            )
            self._devices[token] = device
            return {"ok": True, "code": "paired", "token": token,
                    "device": device.public()}

    def authorize(self, token: str) -> Optional[Device]:
        """校验 token（Bearer 原样传入）；过期即删，返回 None。"""
        tok = str(token or "").strip()
        if not tok:
            return None
        with self._lock:
            device = self._devices.get(tok)
            if device is None:
                return None
            now = float(self._now())
            if now >= device.expires_at:
                self._devices.pop(tok, None)
                return None
            device.last_seen = now
            return device

    def revoke(self, token: str) -> bool:
        with self._lock:
            return self._devices.pop(str(token or "").strip(), None) is not None

    def roster(self) -> Dict[str, Any]:
        with self._lock:
            now = float(self._now())
            devices = [d.public() for d in self._devices.values()
                       if now < d.expires_at]
        devices.sort(key=lambda d: (d["role"], d["id"]))
        return {
            "schema": "mbdsdr.device-roster/1",
            "at_utc": _iso(datetime.fromtimestamp(now, timezone.utc)),
            "authority": "desktop",
            "devices": devices,
        }


class SdrLeaseRegistry:
    """SDR 独占租约（一次性授权）：谁占用 dongle、占多久、何时释放。

    只在内存里；到期自动失效；**同一**持有者可续租（延长到期时刻），
    其它设备一律拒绝。桌面自身占用由调用方通过 ``desktop_busy`` 声明，
    这里不碰硬件、不启动 rtl_tcp，只维护「谁被允许用」。
    """

    def __init__(self, *, ttl_s: float = DEFAULT_SDR_LEASE_TTL_S,
                 now: Callable[[], float] = time.time,
                 lease_factory: Optional[Callable[[], str]] = None):
        self._lock = threading.Lock()
        self._now = now
        self._ttl_s = float(ttl_s)
        self._lease_factory = lease_factory or (
            lambda: f"sdr-{secrets.token_hex(4)}")
        self._lease: Optional[Dict[str, Any]] = None

    @property
    def ttl_s(self) -> float:
        return self._ttl_s

    @staticmethod
    def _public(lease: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "lease_id": lease["lease_id"],
            "device_id": lease["device_id"],
            "name": lease["name"],
            "role": lease["role"],
            "purpose": lease.get("purpose"),
            "granted_utc": _iso(datetime.fromtimestamp(
                lease["granted_unix"], timezone.utc)),
            "expires_utc": _iso(datetime.fromtimestamp(
                lease["expires_unix"], timezone.utc)),
            "expires_unix": float(lease["expires_unix"]),
        }

    def _current_locked(self, now: float) -> Optional[Dict[str, Any]]:
        lease = self._lease
        if lease is not None and now >= lease["expires_unix"]:
            self._lease = lease = None
        return lease

    def status(self) -> Dict[str, Any]:
        with self._lock:
            now = float(self._now())
            lease = self._current_locked(now)
            return {
                "schema": SDR_LEASE_SCHEMA,
                "now_utc": _iso(datetime.fromtimestamp(now, timezone.utc)),
                "active": lease is not None,
                "lease": None if lease is None else self._public(lease),
                "ttl_s": self._ttl_s,
            }

    def acquire(self, device: Device, *, rtl_tcp_host: str, rtl_tcp_port: int,
                desktop_busy: bool = False,
                purpose: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        """申请/续租；返回 (HTTP 状态, 回执)。拒绝路径都带稳定 error/reason。"""
        with self._lock:
            now = float(self._now())
            lease = self._current_locked(now)
            if desktop_busy:
                return 503, {
                    "ok": False, "error": "sdr_busy_desktop",
                    "reason": "桌面权威端正在占用 SDR；先释放桌面占用再授权头显",
                }
            if lease is not None and lease["device_id"] != device.device_id:
                return 409, {
                    "ok": False, "error": "sdr_leased",
                    "reason": (f"SDR 已被 {lease['role']}（{lease['device_id']}）"
                               f"占用到 {_iso(datetime.fromtimestamp(lease['expires_unix'], timezone.utc))}"),
                    "lease": self._public(lease),
                }
            renewed = lease is not None
            expires = now + self._ttl_s
            if lease is None:
                lease = {
                    "lease_id": self._lease_factory(),
                    "device_id": device.device_id,
                    "name": device.name,
                    "role": device.role,
                    "purpose": purpose,
                    "granted_unix": now,
                    "expires_unix": expires,
                }
            else:
                lease["expires_unix"] = expires
                if purpose:
                    lease["purpose"] = purpose
            self._lease = lease
            return 200, {
                "schema": SDR_LEASE_SCHEMA,
                "ok": True,
                "renewed": renewed,
                "exclusive": True,
                "lease": self._public(lease),
                "rtl_tcp": {"host": str(rtl_tcp_host),
                            "port": int(rtl_tcp_port)},
                "note": "一次性授权：仅本设备可用；到期或释放后自动失效",
            }

    def release(self, device: Device) -> Tuple[int, Dict[str, Any]]:
        with self._lock:
            now = float(self._now())
            lease = self._current_locked(now)
            if lease is None:
                return 409, {
                    "ok": False, "error": "no_lease",
                    "reason": "当前没有生效的 SDR 授权",
                }
            if lease["device_id"] != device.device_id:
                return 403, {
                    "ok": False, "error": "not_holder",
                    "reason": "只有当前持有者可以释放 SDR 授权",
                    "lease": self._public(lease),
                }
            self._lease = None
            return 200, {
                "schema": SDR_LEASE_SCHEMA,
                "ok": True,
                "released": True,
                "lease_id": lease["lease_id"],
            }


def _bearer(headers: Dict[str, str]) -> str:
    hs = {k.lower(): v for k, v in (headers or {}).items()}
    raw = hs.get("authorization", "")
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def _finite(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _validate_pose(payload: Any, device: Device,
                   now: float) -> Tuple[Optional[Dict[str, Any]], str]:
    """`sky-pose/1` 体检；合法返回规范化字典，否则给稳定原因。"""
    if not isinstance(payload, dict):
        return None, "pose 必须是 JSON 对象"
    if str(payload.get("schema") or "") != "sky-pose/1":
        return None, "schema 必须是 sky-pose/1"
    source = str(payload.get("source") or "")
    if source != device.device_id:
        return None, "source 必须是本设备的 device_id（不能替别人上报）"
    role = str(payload.get("role") or "")
    if role != device.role:
        return None, f"role 与本设备配对角色不符（本设备是 {device.role}）"
    if device.role not in POSE_ROLES:
        return None, f"角色 {device.role} 只读，不能上报位姿"
    t = _finite(payload.get("t_utc"))
    if t is None:
        return None, "t_utc 必须是 Unix 秒"
    if abs(now - t) > POSE_MAX_AGE_S:
        return None, f"t_utc 与权威端时钟相差超过 {POSE_MAX_AGE_S:g}s"
    az = _finite(payload.get("az_deg"))
    alt = _finite(payload.get("alt_deg"))
    acc = _finite(payload.get("accuracy_deg"))
    if az is None or not (0.0 <= az < 360.0):
        return None, "az_deg 必须在 [0, 360)"
    if alt is None or not (-90.0 <= alt <= 90.0):
        return None, "alt_deg 必须在 [-90, 90]"
    if acc is None or not (0.0 < acc <= 30.0):
        return None, "accuracy_deg 必须在 (0, 30]"
    roll = _finite(payload.get("roll_deg"))
    roll = 0.0 if roll is None else roll
    fov = payload.get("fov_deg")
    fov_v = None if fov is None else _finite(fov)
    if fov is not None and (fov_v is None or not (0.0 < fov_v <= 180.0)):
        return None, "fov_deg 必须在 (0, 180]（或省略）"

    ident = payload.get("identification")
    ident_v = None
    if ident is not None:
        if device.role != "eyepiece":
            return None, "只有 eyepiece 角色可以携带识别结果"
        if not isinstance(ident, dict):
            return None, "identification 必须是对象（或省略）"
        name_raw = ident.get("name")
        name = None if name_raw is None else str(name_raw).strip()
        if name is not None and (not name or len(name) > 64):
            return None, "identification.name 必须非空且 ≤64 字符（或省略）"
        hip_raw = ident.get("hip")
        hip = None
        if hip_raw is not None:
            if (isinstance(hip_raw, bool) or not isinstance(hip_raw, int)
                    or not (1 <= hip_raw <= 10_000_000)):
                return None, "identification.hip 必须是正整数（或省略）"
            hip = int(hip_raw)
        sep_raw = ident.get("separation_deg")
        sep = None if sep_raw is None else _finite(sep_raw)
        if sep_raw is not None and (sep is None or not (0.0 <= sep <= 180.0)):
            return None, "identification.separation_deg 必须在 [0, 180]"
        if name is None and hip is None and sep is None:
            return None, "identification 至少要有 name/hip/separation_deg 之一"
        ident_v = {
            "name": name,
            "hip": hip,
            "separation_deg": None if sep is None else float(sep),
        }

    return {
        "schema": "sky-pose/1",
        "source": source,
        "role": role,
        "t_utc": float(t),
        "az_deg": float(az),
        "alt_deg": float(alt),
        "accuracy_deg": float(acc),
        "roll_deg": float(roll),
        "fov_deg": fov_v,
        "identification": ident_v,
    }, ""


class DeviceGateway:
    """局域网设备网关：配对 + 角色只读 + 位姿。socket 与路由解耦，便于单测。"""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0,
                 allow_lan: bool = False,
                 registry: Optional[PairingRegistry] = None,
                 panorama_provider: Optional[Callable[[], Dict[str, Any]]] = None,
                 passes_provider: Optional[Callable[[], Dict[str, Any]]] = None,
                 status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
                 spectrum_provider: Optional[
                     Callable[[], Optional[Dict[str, Any]]]] = None,
                 sdr_lease: Optional[SdrLeaseRegistry] = None,
                 rtl_tcp_provider: Optional[Callable[[], Tuple[str, int]]] = None,
                 desktop_sdr_busy: Optional[Callable[[], bool]] = None,
                 allow_live: bool = False):
        self.host = _validate_bind(host, allow_lan)
        self.port = int(port)
        self.allow_lan = bool(allow_lan)
        self.registry = registry or PairingRegistry()
        self._panorama_provider = panorama_provider
        self._passes_provider = passes_provider
        self._status_provider = status_provider
        #: 只读频谱快照（M2.6）：返回 None 表示桌面当前没有可读帧 → 503。
        self._spectrum_provider = spectrum_provider
        #: SDR 租约（M4.2）：只发「谁可以用」，不碰硬件；未配置 rtl_tcp 时 503。
        self.sdr_leases = sdr_lease or SdrLeaseRegistry()
        self._rtl_tcp_provider = rtl_tcp_provider
        self._desktop_sdr_busy = desktop_sdr_busy
        self._allow_live = bool(allow_live)
        self._poses: Dict[str, Dict[str, Any]] = {}
        self._poses_lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ 位姿
    @property
    def sdr_lease_enabled(self) -> bool:
        """是否已配置 rtl_tcp（决定能否签发 SDR 授权）。"""
        return self._rtl_tcp_provider is not None

    @property
    def sdr_lease_endpoint(self) -> Optional[Tuple[str, int]]:
        """当前 rtl_tcp 地址（未配置/非法为 None）。"""
        return self._rtl_tcp_endpoint()

    def recent_poses(self) -> List[Dict[str, Any]]:
        with self._poses_lock:
            rows = list(self._poses.values())
        rows.sort(key=lambda r: (r["role"], r["source"]))
        return [dict(r) for r in rows]

    def fuse_now(self, now_utc: Optional[float] = None) -> Dict[str, Any]:
        """把 lens/eyepiece 的最近位姿喂给 `fuse_poses`，输出融合信封。

        与 `sky_pose_fuse` 工具共用同一份纯数值融合内核：精度加权、逐条
        报告丢弃原因；不可用时 `valid=False` 且朝向字段全为 None，
        绝不返回猜测朝向。默认陈旧阈值沿用 `fuse_poses` 的 2 s。
        """
        from .sky_pointing import PoseObservation, fuse_poses

        now = time.time() if now_utc is None else float(now_utc)
        observations = []
        for row in self.recent_poses():
            if row.get("role") not in POSE_ROLES:
                continue
            fov = row.get("fov_deg")
            observations.append(PoseObservation(
                source=str(row.get("source") or ""),
                t_utc=float(row.get("t_utc") or 0.0),
                az_deg=float(row.get("az_deg") or 0.0),
                alt_deg=float(row.get("alt_deg") or 0.0),
                accuracy_deg=float(row.get("accuracy_deg") or 0.0),
                roll_deg=float(row.get("roll_deg") or 0.0),
                fov_deg=None if fov is None else float(fov),
            ))
        fused = fuse_poses(observations, now)
        return {
            "schema": "mbdsdr.pose-fusion/1",
            "now_utc": now,
            "observations": len(observations),
            **fused,
        }

    # ------------------------------------------------------------------ 路由
    def dispatch(self, method: str, path: str, body: bytes = b"",
                 headers: Optional[Dict[str, str]] = None
                 ) -> Tuple[int, bytes, str]:
        route = urlparse(path).path
        method = str(method or "").upper()
        hs = {k.lower(): v for k, v in (headers or {}).items()}

        if route == "/health":
            if method != "GET":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            return 200, self._json({
                "ok": True,
                "service": "mbdsdr-device-gateway",
                "schema": "mbdsdr.device-gateway/1",
                "pairing_required": True,
                "roles": list(ROLES),
                "lan": self.allow_lan,
                "sdr_lease": self.sdr_lease_enabled,
            }), "application/json"

        if route == "/pair":
            if method != "POST":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return 400, self._json({"ok": False, "error": "bad_json"}), "application/json"
            if not isinstance(payload, dict):
                return 400, self._json({"ok": False, "error": "bad_json"}), "application/json"
            res = self.registry.pair(
                payload.get("code"), payload.get("device_name"), payload.get("role"))
            if res.get("ok"):
                return 200, self._json(res), "application/json"
            status = 429 if res.get("code") == "locked" else 401
            return status, self._json(res), "application/json"

        device = self.registry.authorize(_bearer(hs))
        if device is None:
            return 401, self._json({
                "ok": False, "error": "unauthorized",
                "reason": "缺少或无效的设备 token；先用配对码 POST /pair",
            }), "application/json"

        if route == "/roster":
            if method != "GET":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            return 200, self._json(self.registry.roster()), "application/json"

        if route == "/panorama":
            if method != "GET":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            if self._panorama_provider is None:
                return 503, self._json({
                    "ok": False, "error": "panorama_unavailable",
                    "reason": "权威端尚未接入全景数据提供者",
                }), "application/json"
            try:
                data = self._panorama_provider()
            except Exception as exc:  # noqa: BLE001 - 失败要可见
                return 500, self._json({
                    "ok": False, "error": "panorama_failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                }), "application/json"
            if not isinstance(data, dict):
                return 500, self._json({
                    "ok": False, "error": "panorama_bad_shape",
                    "reason": "provider 必须返回对象",
                }), "application/json"
            return 200, self._json(data), "application/json"

        if route == "/pose":
            if method != "POST":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return 400, self._json({"ok": False, "error": "bad_json"}), "application/json"
            pose, why = _validate_pose(payload, device, time.time())
            if pose is None:
                status = 403 if "角色" in why or "source" in why else 400
                return status, self._json({
                    "ok": False, "error": "bad_pose", "reason": why,
                }), "application/json"
            pose["received_at"] = _iso(_utc_now())
            with self._poses_lock:
                self._poses[device.device_id] = pose
                if len(self._poses) > _POSES_LIMIT:
                    oldest = sorted(self._poses,
                                    key=lambda k: self._poses[k]["received_at"])
                    for key in oldest[:len(self._poses) - _POSES_LIMIT]:
                        self._poses.pop(key, None)
            return 200, self._json({"ok": True, "pose": pose}), "application/json"

        if route == "/poses":
            if method != "GET":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            return 200, self._json({
                "schema": "mbdsdr.sky-poses/1",
                "poses": self.recent_poses(),
            }), "application/json"

        if route == "/passes":
            if method != "GET":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            if self._passes_provider is None:
                return 503, self._json({
                    "ok": False, "error": "passes_unavailable",
                    "reason": "权威端尚未接入过境数据提供者",
                }), "application/json"
            try:
                data = self._passes_provider()
            except Exception as exc:  # noqa: BLE001 - 失败要可见
                return 500, self._json({
                    "ok": False, "error": "passes_failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                }), "application/json"
            if not isinstance(data, dict):
                return 500, self._json({
                    "ok": False, "error": "passes_bad_shape",
                    "reason": "provider 必须返回对象",
                }), "application/json"
            return 200, self._json(data), "application/json"

        if route == "/status":
            if method != "GET":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            if self._status_provider is None:
                return 503, self._json({
                    "ok": False, "error": "status_unavailable",
                    "reason": "权威端尚未接入状态提供者",
                }), "application/json"
            try:
                data = self._status_provider()
            except Exception as exc:  # noqa: BLE001 - 失败要可见
                return 500, self._json({
                    "ok": False, "error": "status_failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                }), "application/json"
            if not isinstance(data, dict):
                return 500, self._json({
                    "ok": False, "error": "status_bad_shape",
                    "reason": "provider 必须返回对象",
                }), "application/json"
            return 200, self._json(data), "application/json"

        if route == "/spectrum":
            if method != "GET":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            if self._spectrum_provider is None:
                return 503, self._json({
                    "ok": False, "error": "spectrum_unavailable",
                    "reason": "权威端尚未接入频谱快照提供者",
                }), "application/json"
            try:
                data = self._spectrum_provider()
            except Exception as exc:  # noqa: BLE001 - 失败要可见
                return 500, self._json({
                    "ok": False, "error": "spectrum_failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                }), "application/json"
            if data is None:
                return 503, self._json({
                    "ok": False, "error": "spectrum_unavailable",
                    "reason": "桌面当前没有可读的频谱帧（未连接 SDR 或已停止）",
                }), "application/json"
            if not isinstance(data, dict):
                return 500, self._json({
                    "ok": False, "error": "spectrum_bad_shape",
                    "reason": "provider 必须返回对象或 None",
                }), "application/json"
            return 200, self._json(data), "application/json"

        if route == "/fuse":
            if method != "GET":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            return 200, self._json(self.fuse_now()), "application/json"

        # ------------------------------------------------------------ SDR 租约
        if route == "/sdr/lease":
            if method == "GET":
                return 200, self._json(self._sdr_status(device)), "application/json"
            if method != "POST":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            if device.role not in SDR_LEASE_ROLES:
                return 403, self._json({
                    "ok": False, "error": "sdr_lease_role_denied",
                    "reason": (f"角色 {device.role} 不能直连 SDR；只有 "
                               f"{'/'.join(SDR_LEASE_ROLES)} 可申请一次性授权"),
                }), "application/json"
            purpose = None
            if body:
                try:
                    payload = json.loads(body.decode("utf-8") or "{}")
                except (ValueError, UnicodeDecodeError):
                    return 400, self._json({
                        "ok": False, "error": "bad_json"}), "application/json"
                if not isinstance(payload, dict):
                    return 400, self._json({
                        "ok": False, "error": "bad_json"}), "application/json"
                raw_purpose = payload.get("purpose")
                if raw_purpose is not None:
                    purpose = str(raw_purpose).strip()
                    if not purpose or len(purpose) > 128:
                        return 400, self._json({
                            "ok": False, "error": "bad_purpose",
                            "reason": "purpose 必须非空且 ≤128 字符（或省略）",
                        }), "application/json"
            endpoint = self._rtl_tcp_endpoint()
            if endpoint is None:
                return 503, self._json({
                    "ok": False, "error": "sdr_not_configured",
                    "reason": "权威端未配置 rtl_tcp 地址；不能签发 SDR 授权",
                }), "application/json"
            host, port = endpoint
            desktop_busy = False
            if self._desktop_sdr_busy is not None:
                try:
                    desktop_busy = bool(self._desktop_sdr_busy())
                except Exception as exc:  # noqa: BLE001 - 状态未知就不授权
                    return 503, self._json({
                        "ok": False, "error": "sdr_busy_unknown",
                        "reason": f"无法确认桌面是否占用 SDR：{type(exc).__name__}: {exc}",
                    }), "application/json"
            code, data = self.sdr_leases.acquire(
                device, rtl_tcp_host=host, rtl_tcp_port=port,
                desktop_busy=desktop_busy, purpose=purpose)
            return code, self._json(data), "application/json"

        if route == "/sdr/release":
            if method != "POST":
                return 405, self._json({"ok": False, "error": "method_not_allowed"}), "application/json"
            code, data = self.sdr_leases.release(device)
            return code, self._json(data), "application/json"

        return 404, self._json({"ok": False, "error": "not_found"}), "application/json"

    def _rtl_tcp_endpoint(self) -> Optional[Tuple[str, int]]:
        """取 rtl_tcp 地址；未配置/非法都返回 None（不外抛、不猜端口）。"""
        if self._rtl_tcp_provider is None:
            return None
        try:
            host, port = self._rtl_tcp_provider()
        except Exception:  # noqa: BLE001 - 配置失败按「未配置」处理
            return None
        host_s = str(host or "").strip()
        try:
            port_i = int(port)
        except (TypeError, ValueError):
            return None
        if not host_s or not (1 <= port_i <= 65535):
            return None
        return host_s, port_i

    def _sdr_status(self, device: Device) -> Dict[str, Any]:
        """租约状态：所有人可见「谁占着」；rtl_tcp 地址只回给当前持有者。"""
        data = self.sdr_leases.status()
        lease = data.get("lease")
        if lease is not None and lease.get("device_id") == device.device_id:
            endpoint = self._rtl_tcp_endpoint()
            if endpoint is not None:
                data["rtl_tcp"] = {"host": endpoint[0], "port": endpoint[1]}
            else:
                data["rtl_tcp"] = None
                data["note"] = "权威端未配置 rtl_tcp 地址"
        return data

    @staticmethod
    def _json(payload: Dict[str, Any]) -> bytes:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # ------------------------------------------------------------------ 服务
    def start(self) -> str:
        if self._server is not None:
            return f"http://{self.host}:{self.port}/"
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MBDSDRDeviceGateway/1"

            def log_message(self, *_args):  # 不把 token/查询串写日志
                return

            def _handle(self, method: str):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length > 0 else b""
                status, raw, ctype = gateway.dispatch(
                    method, self.path, body, dict(self.headers))
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                self._handle("GET")

            def do_POST(self):  # noqa: N802
                self._handle("POST")

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        return f"http://{self.host}:{self.port}/"

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None


def _panorama_now(lat: float, lon: float, alt_m: Optional[float],
                  allow_live: bool, *, site_note: str) -> Dict[str, Any]:
    """按给定站址构造一次全景（时钟/时基在这里统一处理）。"""
    from datetime import datetime, timezone

    from .new_spacetime import get_time_engine, utc_to_jd
    from .sky_tools import build_panorama

    try:
        jd = float(get_time_engine().tick())
        time_source = "time_engine"
    except Exception:
        jd = utc_to_jd(datetime.now(timezone.utc))
        time_source = "wall_clock"
    return build_panorama(
        float(lat), float(lon), jd=jd, allow_live=allow_live,
        time_source=time_source, site_note=site_note, alt_m=alt_m)


def build_station_panorama_provider(
        station_provider: Callable[[], Tuple[Optional[float], Optional[float],
                                             Optional[float]]],
        *, allow_live: bool = False,
        site_note: str = "设备网关（权威端）"
) -> Callable[[], Dict[str, Any]]:
    """每次调用都重新取站址：桌面换站/GNSS fix 后立刻反映到 `/panorama`。

    ``station_provider`` 返回 ``(lat, lon, alt_m)``；缺经纬度即抛错（500 带原因），
    不拿示例站或上一次的坐标顶替。
    """

    def provider() -> Dict[str, Any]:
        la, lo, alt = station_provider()
        if la is None or lo is None:
            raise RuntimeError(
                "权威端未配置站址：先设置观测位置（GNSS/手填）后再取全景")
        return _panorama_now(la, lo, alt, allow_live, site_note=site_note)

    return provider


def build_default_panorama_provider(
        *, lat: Optional[float] = None, lon: Optional[float] = None,
        allow_live: bool = False) -> Callable[[], Dict[str, Any]]:
    """构造 `/panorama` 的默认 provider：复用 `build_panorama` 单一实现。

    站址优先用显式参数；没有则读 ``~/.mbdsdr/config.json`` 的地面站坐标。
    都拿不到就抛错（网关回 500 并带原因），绝不拿示例站顶替。
    ``allow_live=False`` 时只读 15 分钟内缓存快照，不联网。
    """

    def station():
        from . import space_weather as sw

        la, lo = lat, lon
        if la is None or lo is None:
            la, lo = sw.default_station()
        return la, lo, None

    return build_station_panorama_provider(station, allow_live=allow_live)


def parse_tle_groups(text: str) -> List[List[str]]:
    """把 TLE 文本切成「可交给 SGP4 的组」：3 行（标题+1+2）或 2 行（1+2）。

    空行与注释（``#``/``//`` 开头）忽略；不完整/无法识别的行跳过而不是
    硬凑——凑出来的 TLE 只会让预报出错且看不出原因。保持文件内原始顺序。
    """
    groups: List[List[str]] = []
    lines: List[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        lines.append(line)
    i = 0
    while i < len(lines):
        cur = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        nxt2 = lines[i + 2] if i + 2 < len(lines) else ""
        if cur.startswith("1 ") and nxt.startswith("2 "):
            groups.append([cur, nxt])
            i += 2
        elif nxt.startswith("1 ") and nxt2.startswith("2 "):
            groups.append([cur, nxt, nxt2])
            i += 3
        else:
            i += 1
    return groups


def _pass_to_dict(p) -> Dict[str, Any]:
    """`SatellitePass` → JSON 友好字典（ISO 与 Unix 秒都给，消费端自选）。"""
    def _stamp(t) -> Tuple[str, float]:
        dt = t.utc_datetime()
        if dt.tzinfo is None:      # 兜底：视为 UTC，不按本机时区解释
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(), float(dt.timestamp())

    rise_iso, rise_unix = _stamp(p.rise_time)
    set_iso, set_unix = _stamp(p.set_time)
    peak_iso, peak_unix = _stamp(p.max_alt_time)
    return {
        "name": p.sat_name,
        "rise_utc": rise_iso,
        "set_utc": set_iso,
        "peak_utc": peak_iso,
        "rise_unix": rise_unix,
        "set_unix": set_unix,
        "peak_unix": peak_unix,
        "max_alt_deg": float(p.max_alt),
        "rise_az_deg": float(p.rise_az),
        "set_az_deg": float(p.set_az),
        "duration_s": float(p.duration),
        "sunlit": p.sunlit,
    }


def _compute_passes_payload(
        tle_file: str, *, lat: float, lon: float, alt_m: float,
        hours_v: float, min_alt_v: float,
        max_sats: Optional[int]) -> Dict[str, Any]:
    """读本地 TLE 现算 SGP4 并序列化（`/passes` 的单一实现）。"""
    with open(tle_file, "r", encoding="utf-8", errors="replace") as fh:
        groups = parse_tle_groups(fh.read())
    if not groups:
        raise ValueError(f"TLE 文件里没有可解析的 TLE：{tle_file}")
    from .sat_passes import GroundStation, predict_upcoming_passes

    station = GroundStation(float(lat), float(lon), float(alt_m))
    entries = predict_upcoming_passes(
        groups, station, hours=hours_v, min_alt=min_alt_v,
        max_sats=max_sats)
    passes = [_pass_to_dict(item["pass"]) for item in entries]
    passes.sort(key=lambda item: item["rise_unix"])
    return {
        "schema": PASSES_SCHEMA,
        "source": "sgp4",
        "count": len(passes),
        "hours": hours_v,
        "min_alt_deg": min_alt_v,
        "station": {"lat_deg": float(lat), "lon_deg": float(lon),
                    "alt_m": float(alt_m)},
        "passes": passes,
    }


def _validate_passes_args(hours: float, min_alt: float) -> Tuple[float, float]:
    hours_v = float(hours)
    min_alt_v = float(min_alt)
    if not math.isfinite(hours_v) or hours_v <= 0.0:
        raise ValueError(f"hours 必须是 > 0 的有限数，收到 {hours!r}")
    if not math.isfinite(min_alt_v) or not (0.0 <= min_alt_v < 90.0):
        raise ValueError(f"min_alt 必须在 [0, 90) 度，收到 {min_alt!r}")
    return hours_v, min_alt_v


def build_tle_passes_provider(
        tle_file: str, *, lat: float, lon: float, alt_m: float = 0.0,
        hours: float = 24.0, min_alt: float = 10.0,
        max_sats: Optional[int] = None,
) -> Callable[[], Dict[str, Any]]:
    """用本地 TLE 文件构造 `/passes` provider（不联网，站址固定）。

    每次请求重新读文件并现算：用户换了 TLE 文件内容，下一次请求即生效。
    文件缺失/无有效 TLE 都抛错（网关回 500 带原因），返回空列表也不伪造。
    """
    hours_v, min_alt_v = _validate_passes_args(hours, min_alt)

    def provider() -> Dict[str, Any]:
        return _compute_passes_payload(
            tle_file, lat=lat, lon=lon, alt_m=alt_m,
            hours_v=hours_v, min_alt_v=min_alt_v, max_sats=max_sats)

    return provider


def build_station_passes_provider(
        tle_file: str,
        station_provider: Callable[[], Tuple[Optional[float], Optional[float],
                                             Optional[float]]],
        *, hours: float = 24.0, min_alt: float = 10.0,
        max_sats: Optional[int] = None,
) -> Callable[[], Dict[str, Any]]:
    """同 [build_tle_passes_provider]，但**每次调用重取站址**（桌面权威端用）。

    桌面换站/GNSS fix 后 `/passes` 立刻用新坐标；缺站址抛 RuntimeError
    （网关回 500 带原因），不拿示例站或旧坐标顶替。
    """
    hours_v, min_alt_v = _validate_passes_args(hours, min_alt)

    def provider() -> Dict[str, Any]:
        la, lo, alt = station_provider()
        if la is None or lo is None:
            raise RuntimeError(
                "权威端未配置站址：先设置观测位置（GNSS/手填）后再取过境")
        return _compute_passes_payload(
            tle_file, lat=float(la), lon=float(lo),
            alt_m=float(alt or 0.0), hours_v=hours_v,
            min_alt_v=min_alt_v, max_sats=max_sats)

    return provider


def build_desktop_status_provider(
        station_provider: Callable[[], Tuple[Optional[float], Optional[float],
                                             Optional[float]]],
        *, sdr_status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Callable[[], Dict[str, Any]]:
    """`/status` 的默认实现：站址 + SDR 状态，缺什么就写进 `missing`。

    不把「配置了 rtl_tcp 地址」当成「dongle 已连接」；SDR 连接/调谐状态必须
    由调用方提供，否则该项为 null 且 `missing` 包含 `sdr`。
    """

    def provider() -> Dict[str, Any]:
        missing: List[str] = []
        station = None
        try:
            lat, lon, alt = station_provider()
        except Exception as exc:  # noqa: BLE001 - 状态失败要可见
            lat = lon = alt = None
            missing.append(f"station: {type(exc).__name__}: {exc}")
        if lat is None or lon is None:
            if not missing:
                missing.append("station")
        else:
            station = {
                "lat_deg": float(lat),
                "lon_deg": float(lon),
                "alt_m": None if alt is None else float(alt),
            }

        sdr = None
        if sdr_status_provider is None:
            missing.append("sdr")
        else:
            try:
                raw = sdr_status_provider()
            except Exception as exc:  # noqa: BLE001
                missing.append(f"sdr: {type(exc).__name__}: {exc}")
            else:
                if raw is None:
                    missing.append("sdr")
                elif isinstance(raw, dict):
                    sdr = raw
                else:
                    missing.append("sdr: provider 必须返回对象")

        return {
            "schema": STATUS_SCHEMA,
            "at_utc": _iso(_utc_now()),
            "authority": "desktop",
            "station": station,
            "sdr": sdr,
            "missing": missing,
        }

    return provider


def build_desktop_spectrum_provider(
        frame_provider: Callable[[], Optional[Dict[str, Any]]],
        *, max_age_s: float = 5.0,
) -> Callable[[], Optional[Dict[str, Any]]]:
    """`/spectrum` 的默认实现：把桌面「当前一帧」幅度谱转成只读快照。

    ``frame_provider`` 返回 ``None``（未连接/已停止）或一个帧对象：

    ``center_freq_hz``、``sample_rate_hz``、``powers_db``（必填）；
    ``fft_size``、``source``、``captured_monotonic``（可选，最后一项目的是
    进程内单调时钟，用于算 ``age_s``/``stale``）。

    这里**不造数据**：没有帧就返回 ``None``（网关回 503），全为 NaN/非有限值
    也按没有帧处理；绝不把旧谱线说成实时。非有限 bin 输出 ``null``，
    消费端不得当 0 dB 处理。
    """
    import statistics

    def provider() -> Optional[Dict[str, Any]]:
        frame = frame_provider()
        if frame is None:
            return None
        if not isinstance(frame, dict):
            raise RuntimeError("频谱帧必须是对象或 None")
        try:
            center_hz = float(frame["center_freq_hz"])
            sample_hz = float(frame["sample_rate_hz"])
            raw_obj = frame.get("powers_db")
            # 不用 `raw_obj or []`：numpy 数组的真值判断会抛异常。
            raw = [] if raw_obj is None else list(raw_obj)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"频谱帧字段非法：{exc}") from exc
        if not (math.isfinite(center_hz) and math.isfinite(sample_hz)
                and sample_hz > 0.0):
            raise RuntimeError("频谱帧的中心频率/采样率非法")

        powers: List[Optional[float]] = []
        finite: List[Tuple[int, float]] = []
        for idx, value in enumerate(raw):
            try:
                val = float(value)
            except (TypeError, ValueError):
                powers.append(None)
                continue
            if not math.isfinite(val):
                powers.append(None)
                continue
            powers.append(round(val, 2))
            finite.append((idx, val))
        if not finite:
            return None

        bins = len(powers)
        peak_idx, peak_db = max(finite, key=lambda pair: pair[1])
        span = sample_hz
        step = span / bins
        freq_start = center_hz - span / 2.0
        age_s: Optional[float] = None
        stale: Optional[bool] = None
        captured = frame.get("captured_monotonic")
        if captured is not None:
            try:
                age_s = max(0.0, time.monotonic() - float(captured))
                stale = age_s > float(max_age_s)
            except (TypeError, ValueError):
                age_s = None
        return {
            "schema": SPECTRUM_SCHEMA,
            "at_utc": _iso(_utc_now()),
            "authority": "desktop",
            "readonly": True,
            "realtime": False,
            "source": str(frame.get("source") or "unknown"),
            "center_freq_hz": center_hz,
            "sample_rate_hz": sample_hz,
            "span_hz": span,
            "fft_size": int(frame.get("fft_size") or bins),
            "bins": bins,
            "freq_start_hz": freq_start,
            "freq_step_hz": step,
            "powers_db": powers,
            "finite_bins": len(finite),
            "peak_freq_hz": freq_start + (peak_idx + 0.5) * step,
            "peak_power_db": round(peak_db, 2),
            "noise_floor_db": round(statistics.median(
                [v for _, v in finite]), 2),
            "age_s": None if age_s is None else round(age_s, 3),
            "stale": stale,
            "note": ("只读快照：桌面某一帧的幅度谱（相对 dBFS），"
                     "不是实时流；不调谐、不写回。"),
        }

    return provider


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="MBDSDR 多设备网关（配对 + 只读）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--allow-lan", action="store_true",
                    help="允许绑私有网段地址（默认只绑回环）")
    ap.add_argument("--lat", type=float, default=None,
                    help="可选：权威端纬度（缺省读 ~/.mbdsdr/config.json）")
    ap.add_argument("--lon", type=float, default=None,
                    help="可选：权威端经度")
    ap.add_argument("--allow-live", action="store_true",
                    help="允许 /panorama 现采网络数据（默认只读缓存快照）")
    ap.add_argument("--tle-file", default=None,
                    help="可选：本地 TLE 文件；提供则启用 GET /passes（离线 SGP4）")
    ap.add_argument("--rtl-tcp", default=None, metavar="HOST:PORT",
                    help="可选：rtl_tcp 地址；提供则允许头显申请 SDR 一次性授权")
    args = ap.parse_args(argv)
    provider = build_default_panorama_provider(
        lat=args.lat, lon=args.lon, allow_live=args.allow_live)
    passes_provider = None
    if args.tle_file:
        la, lo = args.lat, args.lon
        if la is None or lo is None:
            from . import space_weather as sw
            la, lo = sw.default_station()
        if la is None or lo is None:
            print("启用 /passes 需要站址：请给 --lat/--lon 或配置 "
                  "~/.mbdsdr/config.json")
            return 2
        passes_provider = build_tle_passes_provider(
            args.tle_file, lat=la, lon=lo)
    rtl_tcp_provider = None
    if args.rtl_tcp:
        raw_host, _, raw_port = str(args.rtl_tcp).rpartition(":")
        try:
            rtl_port = int(raw_port)
        except ValueError:
            rtl_port = 0
        rtl_host = raw_host.strip()
        if not rtl_host or not (1 <= rtl_port <= 65535):
            print(f"--rtl-tcp 必须是 HOST:PORT（1–65535），收到 {args.rtl_tcp!r}")
            return 2

        def _rtl_provider(host_fixed: str = rtl_host,
                          port_fixed: int = rtl_port
                          ) -> Tuple[str, int]:
            return host_fixed, port_fixed

        rtl_tcp_provider = _rtl_provider

    def _status_station():
        la, lo = args.lat, args.lon
        if la is None or lo is None:
            from . import space_weather as sw
            la, lo = sw.default_station()
        return la, lo, None

    sdr_status_provider = None
    if rtl_tcp_provider is not None:
        def sdr_status_provider() -> Dict[str, Any]:
            host_v, port_v = rtl_tcp_provider()
            return {
                "rtl_tcp": {"host": host_v, "port": port_v},
                "dongle_probed": False,
                "reason": "仅配置了 rtl_tcp 地址，未探测 dongle",
            }

    status_provider = build_desktop_status_provider(
        _status_station, sdr_status_provider=sdr_status_provider)
    try:
        gw = DeviceGateway(host=args.host, port=args.port,
                           allow_lan=args.allow_lan,
                           panorama_provider=provider,
                           passes_provider=passes_provider,
                           status_provider=status_provider,
                           rtl_tcp_provider=rtl_tcp_provider)
    except ValueError as exc:
        print(f"绑定被拒绝：{exc}")
        return 2
    url = gw.start()
    print(f"设备网关已启动：{url}")
    print(f"配对码：{gw.registry.code}（只给权威端看，不进 API 回执）")
    print("可用端点：GET /health、POST /pair、GET /roster、GET /panorama、"
          "GET /passes、GET /status、GET /spectrum、POST /pose、GET /poses、"
          "GET /fuse、GET/POST /sdr/lease、POST /sdr/release；没有写硬件端点。")
    print("频谱快照：CLI 无实时频谱帧，/spectrum 回 503；"
          "桌面 UI 启动网关且 SDR 在收时可读。")
    print("全景数据：" + ("允许联网现采" if args.allow_live
                        else "只读 15 分钟内缓存快照（--allow-live 可现采）"))
    print("过境数据：" + (f"本地 TLE 离线 SGP4（{args.tle_file}）"
                        if args.tle_file else "未启用（给 --tle-file 开启 /passes）"))
    print("SDR 授权：" + (f"可签发给 headset（rtl_tcp {args.rtl_tcp}，"
                          f"独占 {DEFAULT_SDR_LEASE_TTL_S:g}s）"
                        if args.rtl_tcp
                        else "未启用（给 --rtl-tcp HOST:PORT 开启 /sdr/lease）"))
    print("Ctrl+C 结束。")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        gw.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
