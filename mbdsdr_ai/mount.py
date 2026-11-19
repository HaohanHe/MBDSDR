# SPDX-License-Identifier: MIT
"""MBDSDR AI - 赤道仪/望远镜设备抽象（ASCOM Alpaca 客户端，第一层）

只做「设备抽象 + 能力位门 + 位置来源诚实标注」，不含 UI、不注册 AI 工具、
不负责图像解算。指向闭环的三级承诺里，本模块只承担第二级的**入口**：

    云台粗指向（度级）
        → 赤道仪精调/跟踪（角分级，仅当设备声明能力位时）
        → 图像解算残差（唯一「已对准」证据，本模块不判定）

三个必须守住的诚实点：

1. **能力位门**：``can_slew_radec`` / ``can_slew_altaz`` / ``can_abort`` /
   ``can_track`` / ``can_park`` 为 False 时，客户端在发任何 ``PUT`` 之前就拒绝，
   不会「先发出去再说」。探测失败一律抛 ``MountError``，绝不猜 True。
2. **位置来源**：``MountPosition.source`` 只有三种取值。设备未声明编码器
   （``has_encoder=False``）时回读只能标 ``"model"``——这是模型推算，
   不是机械真值，上层不得拿它宣称角分级精度。
3. **读失败不冒充**：``position()`` 任一读失败即抛错，不返回 0/None 当坐标。

Alpaca 信封口径：HTTP 200 且 ``ErrorNumber == 0`` 才算成功；``ErrorNumber != 0``
抛 ``bad_envelope``（带服务端 ``ErrorMessage``）；非 200 抛 ``http_<code>``；
连接失败/超时抛 ``timeout``。成员名按 Alpaca REST 路径解析，JSON 键大小写不敏感。

只用标准库（``urllib.request`` / ``json`` / ``dataclasses`` / ``datetime``）。
模拟替身与用例在 ``tests/test_mount_alpaca.py``：假服务通过只证明协议与门禁，
**不证明真机可用**。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


#: ``MountPosition.source`` 的合法取值。
POSITION_SOURCES = ("encoder", "model", "commanded")


class MountError(Exception):
    """设备侧失败。``code`` 是机读分类，``message`` 是人类可读说明。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass
class MountCapabilities:
    """设备能力位。任一探测失败都不得以 True 兜底。"""

    can_slew_radec: bool = False
    can_slew_altaz: bool = False
    can_abort: bool = False
    can_track: bool = False
    can_park: bool = False
    can_read_position: bool = False
    has_encoder: bool = False
    #: 原始能力位响应（键为小写成员名），保留给诊断，不让调用方猜。
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MountPosition:
    """一次位置回读。``ra_deg``/``dec_deg``/``az_deg``/``el_deg`` 为度。

    ``source`` 决定这条读数能不能当机械精度使用：
    ``"encoder"`` 是编码器读数；``"model"`` 是按指令/星表模型推算；
    ``"commanded"`` 只是最近一次下发值。后两者都不是「已在目标」的证据。
    """

    ra_deg: Optional[float] = None
    dec_deg: Optional[float] = None
    az_deg: Optional[float] = None
    el_deg: Optional[float] = None
    source: str = "model"
    read_at_utc: str = ""
    slewing: bool = False

    def __post_init__(self) -> None:
        if self.source not in POSITION_SOURCES:
            raise ValueError(f"source 只能是 {POSITION_SOURCES}，实得 {self.source!r}")


class AlpacaMount:
    """ASCOM Alpaca ``ITelescope`` 的最小客户端。

    只实现本层需要的方法：连接、能力位探测、RA/Dec 与 Alt/Az 异步指向、
    中止、位置回读、Park/Unpark、跟踪开关。所有写操作都先过能力位门。
    """

    #: 能力位字段 → Alpaca 成员路径。
    _CAPABILITY_MEMBERS = (
        ("can_slew_radec", "canslew"),
        ("can_slew_altaz", "canslewaltaz"),
        ("can_abort", "canabort"),
        ("can_track", "cantrack"),
        ("can_park", "canpark"),
        ("can_read_position", "canreadposition"),
    )

    def __init__(self, host: str, port: int, device_id: int = 0,
                 timeout_s: float = 3.0):
        self.host = host
        self.port = int(port)
        self.device_id = int(device_id)
        self.timeout_s = float(timeout_s)
        self._caps: Optional[MountCapabilities] = None
        self._client_id = 1
        self._txn = 0

    # ------------------------------------------------------------------
    # HTTP + Alpaca 信封
    # ------------------------------------------------------------------
    def _url(self, member: str) -> str:
        return (f"http://{self.host}:{self.port}"
                f"/api/v1/telescope/{self.device_id}/{member}")

    def _call(self, method: str, member: str,
              params: Optional[Dict[str, Any]] = None) -> Tuple[Any, Dict[str, Any]]:
        """发一次请求并解包。返回 ``(Value, 小写键信封)``。

        成功判据是 HTTP 200 + ``ErrorNumber == 0``；任何偏离都抛 ``MountError``。
        """
        data = None
        if method == "PUT":
            self._txn += 1
            payload: Dict[str, Any] = {
                "ClientID": self._client_id,
                "ClientTransactionID": self._txn,
            }
            payload.update(params or {})
            data = urlencode(payload).encode("utf-8")
        request = Request(self._url(member), data=data, method=method)
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                body = response.read()
        except HTTPError as exc:
            raise MountError(f"http_{exc.code}",
                             f"{method} {member} 返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise MountError("timeout",
                             f"{method} {member} 连接失败/超时（{self.timeout_s:g}s）："
                             f"{reason}") from exc

        try:
            decoded = json.loads(body.decode("utf-8", errors="replace"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MountError("bad_envelope",
                             f"{method} {member} 响应不是合法 JSON") from exc
        if not isinstance(decoded, dict):
            raise MountError("bad_envelope",
                             f"{method} {member} 响应不是 Alpaca 信封对象")

        envelope = {str(key).lower(): value for key, value in decoded.items()}
        if "errornumber" not in envelope:
            raise MountError("bad_envelope",
                             f"{method} {member} 信封缺少 ErrorNumber")
        try:
            error_number = int(envelope["errornumber"])
        except (TypeError, ValueError) as exc:
            raise MountError("bad_envelope",
                             f"{method} {member} 的 ErrorNumber 不是整数："
                             f"{envelope['errornumber']!r}") from exc
        if error_number != 0:
            server_message = envelope.get("errormessage") or "（服务端未给 ErrorMessage）"
            raise MountError("bad_envelope",
                             f"{method} {member} 服务端错误 {error_number}："
                             f"{server_message}")
        return envelope.get("value"), envelope

    @staticmethod
    def _as_bool(value: Any, member: str) -> bool:
        """严格布尔解析：认不出就抛错，不把非 0 当 True。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise MountError("bad_envelope", f"{member} 不是布尔值：{value!r}")

    # ------------------------------------------------------------------
    # 连接与能力位
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """先读 ``connected``，再 PUT 置 true。"""
        self._call("GET", "connected")
        self._call("PUT", "connected", {"Connected": "true"})
        return True

    def capabilities(self) -> MountCapabilities:
        """逐个探测能力位；任一项探测失败即抛 ``MountError``。

        结果在实例上缓存（能力位在会话内按设备声明使用）。``has_encoder``
        是 Alpaca 标准之外的可选扩展：读不到/读到非布尔时一律按 False，
        即「设备没声明编码器」——回读只能标 model。
        """
        if self._caps is not None:
            return self._caps
        raw: Dict[str, Any] = {}
        parsed: Dict[str, bool] = {}
        for attribute, member in self._CAPABILITY_MEMBERS:
            value, _ = self._call("GET", member)
            raw[member] = value
            parsed[attribute] = self._as_bool(value, member)
        parsed["has_encoder"] = self._probe_encoder(raw)
        self._caps = MountCapabilities(raw=raw, **parsed)
        return self._caps

    def _probe_encoder(self, raw: Dict[str, Any]) -> bool:
        """可选编码器声明。未提供（400/404）或非布尔 → False，不猜 True。

        连接类错误（超时/服务端 ErrorNumber）照常抛出：那说明设备本身没答上来，
        不能顺手把「读不到」解释成「没有编码器」。
        """
        try:
            value, _ = self._call("GET", "hasencoder")
        except MountError as exc:
            if exc.code in ("http_400", "http_404"):
                raw["hasencoder"] = None
                return False
            raise
        raw["hasencoder"] = value
        return value if isinstance(value, bool) else False

    def _caps_or_probe(self) -> MountCapabilities:
        return self._caps if self._caps is not None else self.capabilities()

    # ------------------------------------------------------------------
    # 指向（写操作，能力位先行）
    # ------------------------------------------------------------------
    def slew_to_radec(self, ra_hours: float, dec_deg: float) -> None:
        """异步指向赤道坐标。``ra_hours`` 为小时（0–24），``dec_deg`` 为度。"""
        if not self._caps_or_probe().can_slew_radec:
            raise MountError("capability_missing:canSlew",
                             "设备未声明 CanSlew，拒绝下发赤道坐标指向")
        self._call("PUT", "slewtocoordinatesasync", {
            "RightAscension": float(ra_hours),
            "Declination": float(dec_deg),
        })

    def slew_to_altaz(self, az_deg: float, el_deg: float) -> None:
        """异步指向地平坐标。``az_deg`` 方位角，``el_deg`` 高度角，单位度。"""
        if not self._caps_or_probe().can_slew_altaz:
            raise MountError("capability_missing:canSlewAltAz",
                             "设备未声明 CanSlewAltAz，拒绝下发地平坐标指向")
        self._call("PUT", "slewtoaltazasync", {
            "Azimuth": float(az_deg),
            "Altitude": float(el_deg),
        })

    def abort_slew(self) -> None:
        if not self._caps_or_probe().can_abort:
            raise MountError("capability_missing:canabort",
                             "设备未声明 AbortSlew 能力，拒绝下发中止")
        self._call("PUT", "abortslew")

    def park(self) -> None:
        if not self._caps_or_probe().can_park:
            raise MountError("capability_missing:canPark",
                             "设备未声明 CanPark，拒绝下发 Park")
        self._call("PUT", "park")

    def unpark(self) -> None:
        if not self._caps_or_probe().can_park:
            raise MountError("capability_missing:canPark",
                             "设备未声明 CanPark，拒绝下发 Unpark")
        self._call("PUT", "unpark")

    def tracking(self, enabled: bool) -> None:
        if not self._caps_or_probe().can_track:
            raise MountError("capability_missing:canTrack",
                             "设备未声明跟踪能力，拒绝切换 Tracking")
        self._call("PUT", "tracking",
                   {"Tracking": "true" if enabled else "false"})

    # ------------------------------------------------------------------
    # 位置回读
    # ------------------------------------------------------------------
    def position(self) -> MountPosition:
        """读 RA/Dec/跟踪状态。读失败抛错，绝不返回 0 冒充坐标。

        ``source``：设备声明 ``has_encoder`` → ``"encoder"``；否则
        ``"model"``（模型推算）。Alpaca 的 ``rightascension`` 单位是小时，
        这里换算成度填入 ``ra_deg``。
        """
        caps = self._caps_or_probe()
        ra_hours, _ = self._call("GET", "rightascension")
        dec_deg, _ = self._call("GET", "declination")
        slewing_raw, _ = self._call("GET", "slewing")
        return MountPosition(
            ra_deg=self._as_number(ra_hours, "rightascension") * 15.0,
            dec_deg=self._as_number(dec_deg, "declination"),
            source="encoder" if caps.has_encoder else "model",
            read_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            slewing=self._as_bool(slewing_raw, "slewing"),
        )

    @staticmethod
    def _as_number(value: Any, member: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MountError("bad_envelope",
                             f"{member} 不是数值：{value!r}")
        return float(value)
