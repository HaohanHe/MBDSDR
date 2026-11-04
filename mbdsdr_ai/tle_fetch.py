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

__all__ = [
    "DEFAULT_SOURCE",
    "SCHEDULED_INTERVAL_S",
    "FAILURE_LABELS",
    "FetchPolicy",
    "SourceHealth",
    "TleFetcher",
    "classify_failure",
]

DEFAULT_SOURCE = "celestrak"
SCHEDULED_INTERVAL_S = 6 * 3600.0
DEFAULT_URL_TEMPLATE = (
    "https://celestrak.org/NORAD/elements/gp.php?CATNR={catnr}&FORMAT=tle"
)

# 失败分类（kind -> 人话标签）。retryable 由 classify_failure 给出。
FAILURE_LABELS: Dict[str, str] = {
    "rate_limited": "429 限流",
    "http_4xx": "4xx 拒绝",
    "http_5xx": "5xx 服务端错误",
    "network_timeout": "网络超时",
    "network_unreachable": "网络不可达/DNS 失败",
    "network_other": "其他网络错误",
    "parse_failure": "返回内容解析失败",
    "empty_response": "空响应",
    "unknown": "未知失败",
}

_RETRYABLE = {
    "rate_limited", "http_5xx", "network_timeout",
    "network_unreachable", "network_other",
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
            kind, detail = "empty_response", "响应体为空"
        else:
            kind, detail = "parse_failure", "响应体不含合法 TLE"
    else:
        kind, detail = "unknown", "未知失败"
    return {
        "kind": kind,
        "label": FAILURE_LABELS.get(kind, kind),
        "retryable": kind in _RETRYABLE,
        "status": status,
        "detail": detail,
    }


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
            raise self._error_cls(
                f"离线模式（MBDSDR_OFFLINE）且无 CATNR {catnr} 的 TLE 缓存；"
                "拒绝编造轨道数据，也不以内置合成 TLE 冒充实时"
            )

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
            raise self._error_cls(
                f"无 CATNR {catnr} 的 TLE 缓存，且{reason}"
            )

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
            raise self._error_cls(
                f"获取 CATNR {catnr} 的 TLE 失败：{err}；且无缓存，拒绝编造"
            )

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
