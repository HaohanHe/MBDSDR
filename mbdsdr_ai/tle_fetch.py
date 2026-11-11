# SPDX-License-Identifier: MIT
"""TLE 数据生命周期：刷新调度、失败退避、离线降级与来源健康度。

设计立场
========

网络不稳时，TLE 获取最常见的坏行为不是「报错」，而是三种静默假象：

1. **重复打点**：启动时逐星刷新、UI 每帧又访问一次，同一对象一轮内被
   请求多次，把源打成 429，最后所有人都拿不到数据；
2. **失败狂重试**：DNS 黑洞/超时后每步都重试，整个进程被网络调用拖死；
3. **假实时**：拿内置合成 TLE 或过期缓存冒充「刚拉到的实时轨道」。

因此本模块把「什么时候该刷、能不能刷、失败后等多久、拿不到时给什么」
显式化。所有判定都接受注入的时钟与 HTTP 打开器，CI 无网络即可全覆盖。

刷新调度规则（三条触发，共用同一套门槛）
----------------------------------------

* **启动时（reason="startup"）**：对传入目录逐条检查，只把「无缓存」或
  「缓存年龄 > fresh_max_hours（默认 24h）」的条目列入 due；
* **访问时（reason="access"）**：只传入当前要用的对象，仍按同一 24h 门槛
  判断，不做批量预热；
* **定时（reason="scheduled"）**：建议每 ``SCHEDULED_INTERVAL_S``（6h）
  巡检一次，但单个对象仍受 24h 门槛 + 退避 + 限速约束。

``refresh_plan`` 对同一轮内的 CATNR 去重；``refresh_round`` 再以 seen 集合
兜底，保证同一对象一轮最多一次网络请求。

失败分类与退避
--------------

分类见 :data:`FAILURE_LABELS`：网络不可达 / 超时 / 4xx / 5xx / 429 限流 /
解析失败 / 空响应 / 其他。可重试类按指数退避
``min(backoff_max, backoff_base * 2**(n-1))`` 增长；429 若有 Retry-After
则取该值（封顶 ``retry_after_max_s``）。4xx（除 429）视为对该对象的永久
拒绝，直接给到最大退避。退避期内**不发起网络请求**，有旧缓存就标
``not_updated`` 并写明原因，无缓存就诚实抛 ``TLEUnavailableError``。

离线降级
--------

``offline_provider()`` 为真时永不联网：有缓存则用缓存并在记录里给
``offline=True`` / ``offline_label="离线·缓存"``；无缓存则抛诚实错误。
**任何情况下都不用内置合成 TLE 冒充实时轨道**（策略见
:func:`builtin_tle_meta.synthetic_tle_policy`）。
"""
from __future__ import annotations

import os
import socket
import time
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .error_catalog import (
    ERRORS,
    retryable as catalog_retryable,
    suggestion as catalog_suggestion,
    user_message,
)

__all__ = [
    "DEFAULT_SOURCE",
    "SCHEDULED_INTERVAL_S",
    "GROUP_SCOPE_KEY",
    "FAILURE_LABELS",
    "FetchPolicy",
    "SourceHealth",
    "TleFetcher",
    "classify_failure",
    "failure_copy",
    "tle_refresh_status_text",
]

# 群组拉取与单星拉取共用同一「源」限速/退避账本；群组本身没有 CATNR，
# 用保留键参与对象级退避判定（不会与真实 NORAD 编号冲突）。
GROUP_SCOPE_KEY = -1

DEFAULT_SOURCE = "celestrak"
SCHEDULED_INTERVAL_S = 6 * 3600.0
DEFAULT_URL_TEMPLATE = (
    "https://celestrak.org/NORAD/elements/gp.php?CATNR={catnr}&FORMAT=tle"
)

# 失败分类（kind -> 人话标签）。标签与可重试性统一取自错误目录，
# 本模块不再自己维护第二份话术（详见 docs/error-catalog.md）。
_TLE_FAILURE_KINDS = (
    "rate_limited", "http_4xx", "http_5xx", "network_timeout",
    "network_unreachable", "network_other", "parse_failure",
    "empty_response", "tle_unknown",
)
FAILURE_LABELS: Dict[str, str] = {
    kind: ERRORS[kind].meaning for kind in _TLE_FAILURE_KINDS
}


@dataclass
class FetchPolicy:
    """刷新/退避/限速策略。默认值面向 Celestrak 的单源小目录。"""

    fresh_max_hours: float = 24.0        # 超过即视为「该刷新」，不是可用门槛
    min_interval_s: float = 1.0          # 同源两次请求的最小间隔
    backoff_base_s: float = 60.0         # 首次失败退避
    backoff_max_s: float = 3600.0         # 指数退避上限
    max_consecutive_failures: int = 6    # 连续失败达到此数判「降级」
    request_timeout_s: float = 15.0
    hard_deadline_s: float = 20.0
    retry_after_max_s: float = 86400.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "fresh_max_hours": self.fresh_max_hours,
            "min_interval_s": self.min_interval_s,
            "backoff_base_s": self.backoff_base_s,
            "backoff_max_s": self.backoff_max_s,
            "max_consecutive_failures": self.max_consecutive_failures,
        }


def classify_failure(exc: Optional[BaseException] = None,
                     status: Optional[int] = None,
                     body: Optional[str] = None) -> Dict[str, Any]:
    """把异常/状态码/响应体归到稳定的失败类别（不冒充成功）。"""
    status = status if status is not None else getattr(exc, "code", None)
    if status is not None:
        try:
            code = int(status)
        except (TypeError, ValueError):
            code = 0
        if code == 429:
            kind = "rate_limited"
        elif 400 <= code < 500:
            kind = "http_4xx"
        elif 500 <= code < 600:
            kind = "http_5xx"
        else:
            kind = "network_other"
        detail = f"HTTP {code}"
    elif isinstance(exc, (TimeoutError, socket.timeout)):
        kind, detail = "network_timeout", f"{type(exc).__name__}: {exc}"
    elif isinstance(exc, urllib.error.URLError):
        kind, detail = "network_unreachable", f"{type(exc).__name__}: {exc}"
    elif exc is not None:
        kind, detail = "network_other", f"{type(exc).__name__}: {exc}"
    elif body is not None:
        if not str(body).strip():
            kind, detail = "empty_response", user_message("empty_response")
        else:
            kind, detail = ("parse_failure",
                            user_message("parse_failure", detail="响应体不含合法 TLE"))
    else:
        # 调用方没给状态码/异常/响应体：如实标注「缺上下文」，不用空话糊弄。
        kind, detail = "tle_unknown", user_message("tle_unknown")
    return {
        "kind": kind,
        "label": FAILURE_LABELS.get(kind, kind),
        "retryable": catalog_retryable(kind),
        "status": status,
        "detail": detail,
    }


def failure_copy(kind: str, detail: str = "",
                 status: Optional[int] = None) -> Dict[str, str]:
    """失败分类 → 用户可见的 code/label/reason/action（三种典型失败分话术）。

    面板与状态栏只该展示这里产出的文案：``code`` 是稳定错误码（与
    docs/error-catalog.md 一致），``reason`` 是原因原文（截断到可读长度），
    ``action`` 是用户下一步能做的事。绝不再写「与 code 无关的固定文案」。
    """
    kind = str(kind or "tle_unknown")
    detail = str(detail or "").strip()
    if len(detail) > 120:
        detail = detail[:117] + "…"
    if kind == "offline":
        label = "离线未联网"
        action = "连接网络后重试，或用 TLE 文件导入离线星历。"
    elif kind == "empty_response" or (kind == "http_4xx" and status == 404):
        # 编号在源里没有对应对象：不是网络问题，别让用户反复重试
        label = "星历不存在（该 NORAD 编号在源中无数据）"
        action = "核对卫星编号/目录名；该对象可能已再入或不在源目录中。"
    elif kind in ("network_unreachable", "network_timeout", "network_other"):
        label = FAILURE_LABELS.get(kind, kind)
        action = catalog_suggestion(kind)
    elif kind == "parse_failure":
        label = "返回数据损坏/解析失败"
        action = catalog_suggestion(kind)
    elif kind == "rate_limited":
        # 目录里的建议是 "{retry_in} 秒后再试。"；抓取链路拿不到那个占位符的
        # 实际秒数（退避由 Retry-After/策略决定），原样渲染等于把模板给用户看。
        label = FAILURE_LABELS.get(kind, kind)
        action = "等退避结束（首次约 1 分钟，源给了 Retry-After 就按它延长）后再重试。"
    else:
        spec = ERRORS.get(kind) or ERRORS["tle_unknown"]
        label = spec.meaning
        action = spec.suggestion
    return {
        "code": kind,
        "label": label,
        "reason": detail or label,
        "action": action,
    }


def tle_refresh_status_text(count: int,
                            failure: Optional[Dict[str, Any]] = None) -> str:
    """状态栏文案：成功才说成功；失败必须带 code + 原因 + 建议动作。"""
    if count > 0 and not failure:
        return f"TLE 已更新（{count} 颗刷新）"
    if not failure:
        if count == 0:
            # 请求成功但源目录没有匹配对象：既不是成功，也不能冒充失败码
            return ("TLE 在线刷新完成，但源目录未匹配到本地卫星（0 颗更新）；"
                    "建议：核对本地目录与源中的 NORAD 编号是否一致。")
        return "TLE 在线刷新失败（未记录 code/原因，请查抓取链路日志）"
    copy = failure_copy(str(failure.get("code") or "tle_unknown"),
                        str(failure.get("reason") or ""),
                        failure.get("status"))
    return (f"TLE 在线更新失败（{copy['code']}：{copy['label']}）："
            f"{copy['reason']}；建议：{copy['action']}")


@dataclass
class SourceHealth:
    """单个 TLE 源的最近成功/失败、退避状态（全部真实，不造数）。"""

    source: str = DEFAULT_SOURCE
    last_success_at: Optional[float] = None
    last_attempt_at: Optional[float] = None
    last_failure_at: Optional[float] = None
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_error: str = ""
    last_error_kind: str = ""
    backoff_until: float = 0.0
    next_allowed_at: float = 0.0

    def report(self, now: Optional[float] = None,
               policy: Optional[FetchPolicy] = None,
               offline: bool = False) -> Dict[str, Any]:
        ref = time.time() if now is None else float(now)
        policy = policy or FetchPolicy()
        if offline:
            state = "offline"
        elif self.backoff_until > ref:
            state = "backoff"
        elif self.consecutive_failures >= policy.max_consecutive_failures:
            state = "degraded"
        elif self.consecutive_failures > 0:
            state = "degraded"
        elif self.last_success_at is None:
            state = "unknown"
        else:
            state = "healthy"

        def _iso(ts: Optional[float]) -> Optional[str]:
            if ts is None:
                return None
            return datetime.fromtimestamp(float(ts), tz=timezone.utc) \
                .strftime("%Y-%m-%dT%H:%M:%SZ")

        return {
            "source": self.source,
            "state": state,
            "offline": bool(offline),
            "last_success_utc": _iso(self.last_success_at),
            "last_attempt_utc": _iso(self.last_attempt_at),
            "last_failure_utc": _iso(self.last_failure_at),
            "consecutive_failures": int(self.consecutive_failures),
            "total_failures": int(self.total_failures),
            "total_successes": int(self.total_successes),
            "last_error": self.last_error,
            "last_error_kind": self.last_error_kind,
            "backoff_until_utc": _iso(self.backoff_until if self.backoff_until > ref else None),
            "retry_in_s": max(0.0, round(self.backoff_until - ref, 1)),
            "policy": policy.as_dict(),
        }


def _parse_tle_body(body: Any, catnr: int) -> Tuple[Optional[Tuple[str, str, str]], Optional[Dict[str, Any]]]:
    """解析响应体 -> (name, line1, line2)；失败返回 (None, 失败分类)。"""
    text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, classify_failure(body="")
    idx = None
    for i in range(len(lines) - 1):
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
            idx = i
            break
    if idx is None:
        return None, classify_failure(body=text)
    line1, line2 = lines[idx], lines[idx + 1]
    try:
        if int(line1[2:7].strip()) != int(catnr) \
                or int(line2[2:7].strip()) != int(catnr):
            return None, classify_failure(body=text)
        name = (lines[idx - 1] if idx > 0
                and not lines[idx - 1].startswith(("1 ", "2 ")) else "")
    except (ValueError, IndexError):
        return None, classify_failure(body=text)
    return (name or f"NORAD {catnr}", line1, line2), None


def _parse_tle_group(body: Any) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """解析多星群组响应 -> ([{catnr,name,line1,line2}], 失败分类)。

    只做结构解析（名称 + "1 "/"2 " 成对且编号一致）；校验和等业务校验由
    调用方决定。无任何可解析块时给出空响应/解析失败分类，绝不假装成功。
    """
    text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return [], classify_failure(body="")
    entries: List[Dict[str, Any]] = []
    seen: set = set()
    i, n = 0, len(lines)
    while i < n - 1:
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
            try:
                catnr = int(lines[i + 1][2:7].strip())
                if int(lines[i][2:7].strip()) != catnr:
                    raise ValueError("line1/line2 编号不一致")
            except (ValueError, IndexError):
                i += 1
                continue
            if catnr in seen:
                i += 2
                continue
            seen.add(catnr)
            name = (lines[i - 1] if i > 0
                    and not lines[i - 1].startswith(("1 ", "2 ")) else "")
            entries.append({
                "catnr": catnr,
                "name": name or f"NORAD {catnr}",
                "line1": lines[i],
                "line2": lines[i + 1],
            })
            i += 2
            continue
        i += 1
    if not entries:
        return [], classify_failure(body=text)
    return entries, None


class TleFetcher:
    """单源 TLE 获取器：内存/磁盘缓存 + 刷新调度 + 退避 + 健康度。

    所有外部能力均可注入（``opener``、``clock``、``sleep``、缓存目录/内存
    字典 provider、离线判定），因此测试可完全离线复现超时/429/坏响应。
    """

    def __init__(self,
                 opener: Callable[[str, float, float], Any],
                 cache_dir_provider: Callable[[], str],
                 memory_provider: Optional[Callable[[], Dict[str, Tuple[float, Tuple[str, str]]]]] = None,
                 offline_provider: Optional[Callable[[], bool]] = None,
                 policy: Optional[FetchPolicy] = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 source: str = DEFAULT_SOURCE,
                 url_template: str = DEFAULT_URL_TEMPLATE,
                 error_cls: type = RuntimeError):
        self._opener = opener
        self._error_cls = error_cls
        self._cache_dir = cache_dir_provider
        self._memory = memory_provider
        self._offline = offline_provider or (lambda: False)
        self.policy = policy or FetchPolicy()
        self._clock = clock
        self._sleep = sleep
        self.source = source
        self._url_template = url_template
        self.health = SourceHealth(source=source)
        self._object_backoff: Dict[int, float] = {}

    # ------------------------------------------------------------------ 缓存
    def _cache_path(self, catnr: int) -> str:
        return os.path.join(self._cache_dir(), f"{int(catnr)}.tle")

    def _mem_key(self, catnr: int) -> str:
        return str(int(catnr))

    def _memory_get(self, catnr: int, now: float,
                    max_age_hours: float) -> Optional[Tuple[float, Tuple[str, str]]]:
        if self._memory is None:
            return None
        rec = self._memory().get(self._mem_key(catnr))
        if rec is None:
            return None
        if now - rec[0] >= max_age_hours * 3600.0:
            return None
        return rec

    def _memory_set(self, catnr: int, ts: float, line1: str, line2: str) -> None:
        if self._memory is not None:
            self._memory()[self._mem_key(catnr)] = (float(ts), (line1, line2))

    def _read_disk(self, catnr: int) -> Optional[Dict[str, Any]]:
        path = self._cache_path(catnr)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = [ln.strip() for ln in fh.read().splitlines()]
            lines = [ln for ln in lines if ln]
            if len(lines) < 3 or not lines[1].startswith("1 ") \
                    or not lines[2].startswith("2 "):
                return None
            if lines[1][2:7].strip() != lines[2][2:7].strip():
                return None
            return {
                "catnr": int(catnr),
                "name": lines[0],
                "line1": lines[1],
                "line2": lines[2],
                "fetched_at": os.path.getmtime(path),
                "cache_path": path,
            }
        except (OSError, ValueError, IndexError):
            return None

    def _write_disk(self, catnr: int, name: str, line1: str,
                    line2: str, fetched_at: float) -> str:
        path = self._cache_path(catnr)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join([name, line1, line2]))
        try:
            os.utime(path, (fetched_at, fetched_at))
        except OSError:
            pass
        return path

    # ------------------------------------------------------------ 调度/限速
    def _backoff_seconds(self, failures: int, kind: str) -> float:
        base = self.policy.backoff_base_s
        cap = self.policy.backoff_max_s
        if kind == "http_4xx":
            return cap
        exp = base * (2.0 ** max(0, failures - 1))
        return min(cap, exp)

    def attempt_decision(self, catnr: int, now: float) -> Dict[str, Any]:
        """当前是否允许对该对象发起网络请求（退避/限速统一入口）。"""
        obj_until = self._object_backoff.get(int(catnr), 0.0)
        if now < obj_until:
            return {"allowed": False, "reason": "object_backoff",
                    "retry_in_s": round(obj_until - now, 1)}
        if now < self.health.backoff_until:
            return {"allowed": False, "reason": "source_backoff",
                    "retry_in_s": round(self.health.backoff_until - now, 1)}
        if now < self.health.next_allowed_at:
            return {"allowed": False, "reason": "rate_limited",
                    "retry_in_s": round(self.health.next_allowed_at - now, 1)}
        return {"allowed": True, "reason": "", "retry_in_s": 0.0}

    def _record_success(self, now: float) -> None:
        self.health.last_success_at = now
        self.health.total_successes += 1
        self.health.consecutive_failures = 0
        self.health.backoff_until = 0.0
        self.health.next_allowed_at = now + self.policy.min_interval_s

    def _record_failure(self, catnr: int, now: float,
                        failure: Dict[str, Any],
                        retry_after_s: Optional[float] = None) -> float:
        self.health.backoff_until = 0.0
        self.health.total_failures += 1
        self.health.consecutive_failures += 1
        self.health.last_failure_at = now
        self.health.last_error = failure["detail"]
        self.health.last_error_kind = failure["kind"]
        self.health.next_allowed_at = now + self.policy.min_interval_s
        wait = self._backoff_seconds(self.health.consecutive_failures,
                                     failure["kind"])
        if retry_after_s is not None and retry_after_s > 0:
            wait = min(float(retry_after_s), self.policy.retry_after_max_s)
        if not failure.get("retryable"):
            wait = self.policy.backoff_max_s
        until = now + wait
        if failure["kind"] == "http_4xx":
            self._object_backoff[int(catnr)] = until
        else:
            self.health.backoff_until = until
            self._object_backoff[int(catnr)] = until
        return wait

    @staticmethod
    def _retry_after_s(exc: Optional[BaseException]) -> Optional[float]:
        headers = getattr(exc, "headers", None)
        if headers is None:
            return None
        try:
            value = headers.get("Retry-After")
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError, AttributeError):
            return None

    def _wait_for_slot(self, now: float) -> float:
        """遵守同源最小请求间隔（最多等一个 min_interval）。"""
        wait = max(0.0, self.health.next_allowed_at - now)
        if wait <= 0.0:
            return now
        wait = min(wait, self.policy.min_interval_s)
        self._sleep(wait)
        return now + wait

    # ------------------------------------------------------------- 取数主链
    def fetch_record(self, catnr: int, max_age_hours: Optional[float] = None,
                     now: Optional[float] = None) -> Dict[str, Any]:
        """取一条 TLE 记录；始终写明来源、是否离线、失败原因与健康度。"""
        catnr = int(catnr)
        max_age = (self.policy.fresh_max_hours if max_age_hours is None
                   else float(max_age_hours))
        ref = self._clock() if now is None else float(now)
        offline = bool(self._offline())
        health = lambda: self.health.report(ref, self.policy, offline=offline)  # noqa: E731

        # 1) 内存缓存（未过期）
        mem = self._memory_get(catnr, ref, max_age)
        if mem is not None:
            cached = self._read_disk(catnr)
            if cached is not None and cached["fetched_at"] > float(mem[0]) + 1e-6:
                # 磁盘比内存新：别处（执行侧刷新）已落盘，内存只是旧快照。
                # 不比对就会拿旧根数冒充最新值，最长可拖到 max_age（默认 24h）。
                self._memory_set(catnr, cached["fetched_at"],
                                 cached["line1"], cached["line2"])
                return dict(cached, source="cache", refresh_status="cache",
                            refresh_error="", offline=offline,
                            offline_label="离线·缓存" if offline else "",
                            failure_kind="", failure_label="", health=health())
            return {
                "catnr": catnr,
                "name": cached["name"] if cached else "",
                "line1": mem[1][0],
                "line2": mem[1][1],
                "source": "memory",
                "fetched_at": float(mem[0]),
                "refresh_status": "memory",
                "refresh_error": "",
                "cache_path": self._cache_path(catnr),
                "offline": offline,
                "offline_label": "离线·缓存" if offline else "",
                "failure_kind": "",
                "failure_label": "",
                "health": health(),
            }

        # 2) 磁盘缓存（未过期）
        disk = self._read_disk(catnr)
        if disk is not None and ref - disk["fetched_at"] < max_age * 3600.0:
            self._memory_set(catnr, disk["fetched_at"],
                             disk["line1"], disk["line2"])
            return dict(disk, source="cache", refresh_status="cache",
                        refresh_error="", offline=offline,
                        offline_label="离线·缓存" if offline else "",
                        failure_kind="", failure_label="", health=health())

        # 3) 离线：只用缓存，明示「离线·缓存」；无缓存诚实报错
        if offline:
            if disk is not None:
                return dict(disk, source="cache", refresh_status="not_updated",
                            refresh_error="离线模式（MBDSDR_OFFLINE），未联网刷新，沿用旧缓存",
                            offline=True, offline_label="离线·缓存（未刷新）",
                            failure_kind="offline", failure_label="离线",
                            health=health())
            _err = self._error_cls(
                f"离线模式（MBDSDR_OFFLINE）且无 CATNR {catnr} 的 TLE 缓存；"
                "拒绝编造轨道数据，也不以内置合成 TLE 冒充实时"
            )
            _err.failure = failure_copy(
                "offline", "离线模式（MBDSDR_OFFLINE）且该编号无本地缓存")
            raise _err

        # 4) 退避：不发起请求，不刷屏重试；最小间隔则排队等待
        decision = self.attempt_decision(catnr, ref)
        if not decision["allowed"] and decision["reason"] == "rate_limited":
            ref = self._wait_for_slot(ref)
        elif not decision["allowed"]:
            reason = (f"退避中（{decision['reason']}，还剩 "
                      f"{decision['retry_in_s']:.0f}s），不重试")
            if disk is not None:
                return dict(disk, source="cache", refresh_status="not_updated",
                            refresh_error=reason, offline=False,
                            offline_label="", failure_kind=decision["reason"],
                            failure_label=FAILURE_LABELS.get(
                                self.health.last_error_kind or "", ""),
                            health=health())
            _err = self._error_cls(
                f"无 CATNR {catnr} 的 TLE 缓存，且{reason}"
            )
            _err.failure = failure_copy(
                self.health.last_error_kind or "tle_unknown", reason)
            raise _err

        # 5) 联网取新（遵守最小间隔）
        ref = self._wait_for_slot(ref)
        self.health.last_attempt_at = ref
        url = self._url_template.format(catnr=catnr)
        retry_after: Optional[float] = None
        try:
            body = self._opener(url, self.policy.request_timeout_s,
                                self.policy.hard_deadline_s)
            parsed, failure = _parse_tle_body(body, catnr)
        except Exception as e:  # noqa: BLE001 - 网络/DNS/超时统一分类
            parsed, failure = None, classify_failure(exc=e)
            retry_after = self._retry_after_s(e)
        if parsed is None:
            wait = self._record_failure(catnr, ref, failure, retry_after)
            err = (f"{failure['label']}（{failure['detail']}）；"
                   f"退避 {wait:.0f}s")
            if disk is not None:
                # 网络不可达（DNS/连接失败）视为事实离线，明示「离线·缓存」；
                # 超时/5xx 只标「缓存回退」——可能只是暂时抖动，不冒充离线。
                is_offline = failure["kind"] == "network_unreachable"
                label = ("离线·缓存（未刷新）" if is_offline
                         else "缓存回退（未刷新）")
                return dict(disk, source="cache", refresh_status="not_updated",
                            refresh_error=err, offline=is_offline,
                            offline_label=label,
                            failure_kind=failure["kind"],
                            failure_label=failure["label"], health=health())
            _err = self._error_cls(
                f"获取 CATNR {catnr} 的 TLE 失败：{err}；且无缓存，拒绝编造"
            )
            _err.failure = failure_copy(
                failure["kind"], failure.get("detail", err),
                failure.get("status"))
            raise _err

        name, line1, line2 = parsed
        self._record_success(ref)
        path = self._write_disk(catnr, name, line1, line2, ref)
        self._memory_set(catnr, ref, line1, line2)
        return {
            "catnr": catnr, "name": name,
            "line1": line1, "line2": line2,
            "source": self.source, "fetched_at": ref,
            "refresh_status": "network", "refresh_error": "",
            "cache_path": path,
            "offline": False, "offline_label": "",
            "failure_kind": "", "failure_label": "",
            "health": health(),
        }

    # ------------------------------------------------------------- 群组取数
    def fetch_group(self, url: str,
                    now: Optional[float] = None) -> Dict[str, Any]:
        """按群组 URL 拉取整组 TLE，与单星共用限速/退避/健康度账本。

        离线时**明确拒绝**（群组没有本地缓存索引，无法标注时效），不做
        「静默返回空」；退避期内也不发请求，直接给出剩余秒数。成功返回
        ``refresh_status="network"`` 与解析出的条目；失败返回
        ``refresh_status="unavailable"`` + 分类 + 退避秒数。
        """
        ref = self._clock() if now is None else float(now)
        offline = bool(self._offline())
        health = lambda: self.health.report(ref, self.policy, offline=offline)  # noqa: E731

        if offline:
            return {
                "entries": [], "refresh_status": "offline",
                "refresh_error": ("离线模式（MBDSDR_OFFLINE），未发起群组拉取；"
                                  "群组无本地缓存，单星数据请走单星缓存路径"),
                "failure_kind": "offline", "failure_label": "离线",
                "retry_in_s": 0.0, "offline": True, "health": health(),
            }

        decision = self.attempt_decision(GROUP_SCOPE_KEY, ref)
        if not decision["allowed"] and decision["reason"] == "rate_limited":
            ref = self._wait_for_slot(ref)
        elif not decision["allowed"]:
            return {
                "entries": [], "refresh_status": "unavailable",
                "refresh_error": (f"群组拉取退避中（{decision['reason']}，还剩 "
                                  f"{decision['retry_in_s']:.0f}s），不重试"),
                "failure_kind": decision["reason"], "failure_label": "",
                "retry_in_s": decision["retry_in_s"], "offline": False,
                "health": health(),
            }

        ref = self._wait_for_slot(ref)
        self.health.last_attempt_at = ref
        retry_after: Optional[float] = None
        try:
            body = self._opener(url, self.policy.request_timeout_s,
                                self.policy.hard_deadline_s)
            entries, failure = _parse_tle_group(body)
        except Exception as e:  # noqa: BLE001 - 网络/DNS/超时统一分类
            entries, failure = [], classify_failure(exc=e)
            retry_after = self._retry_after_s(e)

        if failure is not None:
            wait = self._record_failure(GROUP_SCOPE_KEY, ref, failure, retry_after)
            return {
                "entries": [], "refresh_status": "unavailable",
                "refresh_error": (f"群组拉取失败：{failure['label']}"
                                  f"（{failure['detail']}）；退避 {wait:.0f}s"),
                "failure_kind": failure["kind"],
                "failure_label": failure["label"],
                "retry_in_s": round(wait, 1), "offline": False,
                "health": health(),
            }

        self._record_success(ref)
        return {
            "entries": entries, "refresh_status": "network",
            "refresh_error": "", "failure_kind": "", "failure_label": "",
            "retry_in_s": 0.0, "offline": False, "fetched_at": ref,
            "url": url, "health": health(),
        }

    # ------------------------------------------------------------- 刷新调度
    def refresh_plan(self, entries: Iterable[Any], reason: str = "startup",
                     now: Optional[float] = None) -> Dict[str, Any]:
        """按规则给出本轮该刷新的 CATNR（去重；退避中的条目移出 due）。"""
        ref = self._clock() if now is None else float(now)
        due: List[int] = []
        skipped: List[Dict[str, Any]] = []
        seen: set = set()
        for entry in entries:
            if isinstance(entry, dict):
                catnr = entry.get("catnr")
                fetched_at = entry.get("fetched_at")
            else:
                catnr = getattr(entry, "catnr", None)
                fetched_at = getattr(entry, "fetched_at", None)
            try:
                catnr = int(catnr)
            except (TypeError, ValueError):
                continue
            if catnr in seen:
                continue
            seen.add(catnr)
            age_h = (None if fetched_at is None else
                     (ref - float(fetched_at)) / 3600.0)
            if age_h is not None and age_h <= self.policy.fresh_max_hours:
                skipped.append({"catnr": catnr, "why": "fresh",
                                "cache_age_hours": round(age_h, 2)})
                continue
            decision = self.attempt_decision(catnr, ref)
            # 仅「限速」不排除：fetch_record 会按最小间隔排队等待；
            # 真退避（源级/对象级）期间绝不再入队，避免刷屏重试。
            if not decision["allowed"] and decision["reason"] != "rate_limited":
                skipped.append({"catnr": catnr, "why": decision["reason"],
                                "retry_in_s": decision["retry_in_s"]})
                continue
            due.append(catnr)
        return {"reason": reason, "due": due, "skipped": skipped,
                "deduped_total": len(seen)}

    def refresh_round(self, entries: Iterable[Any], reason: str = "startup",
                      now: Optional[float] = None) -> Dict[str, Any]:
        """执行一轮刷新：同一对象本轮最多请求一次（seen 兜底）。"""
        plan = self.refresh_plan(entries, reason=reason, now=now)
        results: Dict[str, Any] = {}
        attempted: set = set()
        for catnr in plan["due"]:
            if catnr in attempted:
                continue
            attempted.add(catnr)
            try:
                results[str(catnr)] = self.fetch_record(catnr)
            except self._error_cls as e:
                results[str(catnr)] = {
                    "catnr": catnr, "refresh_status": "unavailable",
                    "error": str(e), "health": self.health_report(now),
                }
        return {"plan": plan, "results": results,
                "attempted": sorted(attempted),
                "health": self.health_report(now)}

    def health_report(self, now: Optional[float] = None) -> Dict[str, Any]:
        ref = self._clock() if now is None else float(now)
        return self.health.report(ref, self.policy, offline=bool(self._offline()))
