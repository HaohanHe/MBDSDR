# SPDX-License-Identifier: MIT
"""卫星发射机机器数据源：SatNOGS DB 拉取 + 磁盘缓存 + 离线回退。

背景（W92）
===========

``data/sat_links.json`` 是人工维护的「点星即听」下行目录：8 颗星、9 条
链路，字段只有 ``direction/freq_hz/mode/baud/note``。机器库能补上人工
表没有的东西——上下行频率边界（``downlink_low/high``）、发射机状态
（``active/inactive``）与业务类别（``service``）。

本模块只做「取数 + 落盘 + 给状态」，不改人工表：

* **运行时优先读缓存**：:func:`load_cache` 只读本地文件，零网络；
* **无缓存/断网照常用人工表**：缓存读不到就返回诚实状态，上层
  ``sat_links`` 继续用人工目录，绝不把目录变成空表；
* **网络在后台**：:func:`start_background_refresh` 起 daemon 线程，
  不阻塞启动与首帧；失败只记日志并给出状态文本；
* **不覆盖人工值**：:func:`merge_entry` 只把机器字段作为可选增量挂到
  链路上（``low_hz/high_hz/status/service``），``freq_hz/mode/baud/note``
  原样保留，频率对不上时记 ``conflicts`` 供人工核对。

数据源与字段（已核实）
----------------------

SatNOGS DB 发射机接口一次返回该星全部发射机，字段含
``downlink_low`` / ``downlink_high`` / ``mode`` / ``baud`` / ``status`` /
``service`` / ``uuid`` / ``sat_id``（对照 SDRangel
``f4exb/sdrangel@1ec7cea:plugins/feature/satellitetracker/satnogs.h:28-41``）。
本模块按原字段名原样缓存，便于逐条回查；合并进链路时才改名为
``low_hz/high_hz``。

诚实纪律
--------

* 响应不是发射机数组、条目缺关键字段、JSON 解析失败、HTTP 错误、
  超时、离线——全部如实分类并写进状态，不编造频率；
* 拉取全失败时**不写缓存**，旧缓存原样保留；
* 接口给了 ``downlink_low`` 但 ``downlink_high`` 为空时，如实只记单点，
  不当成「带宽为 0」也不猜边界。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:                                    # 跨进程缓存锁（Windows）
    import msvcrt
except ImportError:                     # pragma: no cover - 非 Windows
    msvcrt = None                       # type: ignore
try:                                    # 跨进程缓存锁（POSIX）
    import fcntl
except ImportError:                     # pragma: no cover - Windows
    fcntl = None                        # type: ignore

logger = logging.getLogger(__name__)

__all__ = [
    "CACHE_SCHEMA",
    "DEFAULT_FRESH_HOURS",
    "DEFAULT_TX_URL",
    "FAILURE_LABELS",
    "cache_path",
    "offline",
    "load_cache",
    "clear_cache_memo",
    "refresh",
    "refresh_in_progress",
    "last_refresh_result",
    "start_background_refresh",
    "catalog_status",
    "status_text",
    "age_text",
    "merge_entry",
    "machine_only_links",
]

SERVICE = "satnogs"
CACHE_SCHEMA = "mbdsdr sat_links machine cache v1"
DEFAULT_SOURCE_URL = "https://db.satnogs.org/api/transmitters/"
DEFAULT_TX_URL = (DEFAULT_SOURCE_URL
                  + "?format=json&satellite__norad_cat_id={norad}")
DEFAULT_FRESH_HOURS = 24.0 * 7.0      # 发射机库变化慢，7 天算新鲜
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_HARD_DEADLINE_S = 20.0
MATCH_TOL_HZ = 5000.0                 # 人工频点与机器边界的匹配容差
CACHE_FILENAME = "sat_links_machine.json"
OFFLINE_ENV = "MBDSDR_OFFLINE"

FAILURE_LABELS: Dict[str, str] = {
    "rate_limited": "请求被限流（HTTP 429）",
    "http_4xx": "请求被源拒绝（HTTP 4xx）",
    "http_5xx": "源服务端错误（HTTP 5xx）",
    "network_timeout": "网络超时",
    "network_unreachable": "网络不可达",
    "network_other": "网络错误",
    "parse_failure": "返回内容不是合法 JSON",
    "empty_response": "返回内容为空",
    "schema_mismatch": "库结构变了（不是发射机数组/缺关键字段）",
    "offline": "离线模式（MBDSDR_OFFLINE），未联网",
    "write_failed": "本地缓存写入失败（只读目录/文件被占用/权限）",
}


# --------------------------------------------------------------------- 工具
def _now(now: Optional[float] = None) -> float:
    return time.time() if now is None else float(now)


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_text(seconds: Optional[float]) -> str:
    if seconds is None:
        return "未知"
    s = max(0.0, float(seconds))
    if s < 3600.0:
        return f"{s / 60.0:.0f} 分钟"
    if s < 86400.0:
        return f"{s / 3600.0:.1f} 小时"
    return f"{s / 86400.0:.1f} 天"


def age_text(seconds: Optional[float]) -> str:
    """数据龄人话（分钟/小时/天）；供界面与状态文本共用。"""
    return _age_text(seconds)


def _offline() -> bool:
    return (os.environ.get(OFFLINE_ENV, "").strip().lower()
            in ("1", "true", "yes", "on"))


def offline() -> bool:
    """当前是否离线模式（测试/CI 默认置位，界面据此不起后台拉库）。"""
    return _offline()


def cache_path() -> Optional[str]:
    """机器缓存路径；``MBDSDR_CACHE_DIR`` 优先（测试隔离），否则用户目录。"""
    base = os.environ.get("MBDSDR_CACHE_DIR")
    if not base:
        home = os.path.expanduser("~")
        if not home or home == "~":
            return None
        base = os.path.join(home, ".mbdsdr", "cache")
    return os.path.join(base, CACHE_FILENAME)


# ----------------------------------------------------------------- 失败分类
def _classify_exc(exc: BaseException) -> Dict[str, Any]:
    if isinstance(exc, urllib.error.HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        kind = ("rate_limited" if code == 429
                else "http_4xx" if 400 <= code < 500
                else "http_5xx" if 500 <= code < 600
                else "network_other")
        detail = f"HTTP {code}: {exc}"
    elif isinstance(exc, (TimeoutError, socket.timeout)):
        kind = "network_timeout"
        detail = f"{type(exc).__name__}: {exc}"
    elif isinstance(exc, urllib.error.URLError):
        kind = "network_unreachable"
        detail = f"{type(exc).__name__}: {exc}"
    elif isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        kind = "parse_failure"
        detail = f"{type(exc).__name__}: {exc}"
    else:
        kind = "network_other"
        detail = f"{type(exc).__name__}: {exc}"
    return _failure(kind, detail)


def _failure(kind: str, detail: str = "") -> Dict[str, Any]:
    return {"kind": kind, "label": FAILURE_LABELS.get(kind, kind),
            "detail": detail or FAILURE_LABELS.get(kind, kind)}


def _bounded_opener(url: str, timeout_s: float, deadline_s: float) -> bytes:
    """工作线程内 urlopen + 硬截止（urllib 的 timeout 不覆盖 DNS 阶段）。"""
    import ssl

    box: Dict[str, Any] = {}
    req = urllib.request.Request(url, headers={"User-Agent": "MBDSDR/1.0"})

    def _run() -> None:
        try:
            box["r"] = urllib.request.urlopen(
                req, timeout=timeout_s, context=ssl.create_default_context())
        except Exception as e:  # noqa: BLE001 - 原样交给分类器
            box["e"] = e

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        raise TimeoutError(
            f"SatNOGS 拉取超时（>{deadline_s:.0f}s，含 DNS）：{url}")
    if "e" in box:
        raise box["e"]
    return box["r"].read()


# ----------------------------------------------------------------- 缓存读写
_MEMO: Dict[str, Any] = {"key": None, "payload": None, "state": None}


def clear_cache_memo() -> None:
    """清掉进程内缓存快照（测试/刷新写盘后调用）。"""
    _MEMO["key"] = None
    _MEMO["payload"] = None
    _MEMO["state"] = None


def _empty_state(path: Optional[str], state: str, detail: str,
                 reason: str = "") -> Dict[str, Any]:
    return {
        "state": state,
        "path": path,
        "detail": detail,
        "reason": reason,
        "fetched_at": None,
        "fetched_at_utc": "",
        "age_s": None,
        "sat_count": 0,
        "failure_count": 0,
    }


def _validate(payload: Any) -> Tuple[bool, str]:
    """校验缓存结构；结构不符返回 (False, 原因)，不抛异常。"""
    if not isinstance(payload, dict):
        return False, "顶层不是对象"
    if payload.get("_schema") != CACHE_SCHEMA:
        return False, (f"_schema 不符（找到 {payload.get('_schema')!r}，"
                       f"期望 {CACHE_SCHEMA!r}）")
    sats = payload.get("satellites")
    if not isinstance(sats, dict):
        return False, "satellites 不是对象"
    for key, rec in sats.items():
        if not isinstance(rec, dict):
            return False, f"satellites[{key}] 不是对象"
        if not isinstance(rec.get("transmitters"), list):
            return False, f"satellites[{key}].transmitters 不是数组"
    return True, ""


def load_cache(path: Optional[str] = None,
               now: Optional[float] = None) -> Tuple[Optional[Dict[str, Any]],
                                                      Dict[str, Any]]:
    """读本地机器缓存 -> ``(payload|None, state)``；零网络、零异常。"""
    ref = _now(now)
    target = path or cache_path()
    if not target:
        return None, _empty_state(None, "missing", "取不到用户缓存目录")
    try:
        st = os.stat(target)
        key = (target, st.st_mtime_ns, st.st_size)
    except OSError:
        return None, _empty_state(target, "missing", "无本地缓存")
    if _MEMO["key"] == key:
        return _MEMO["payload"], dict(_MEMO["state"],
                                      age_s=(None if _MEMO["state"]["fetched_at"]
                                             is None else
                                             ref - _MEMO["state"]["fetched_at"]))
    payload = None
    last_os_error: Optional[OSError] = None
    for attempt in range(3):
        try:
            with open(target, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            last_os_error = None
            break
        except ValueError as e:
            state = _empty_state(target, "invalid",
                                 f"内容不是合法 JSON：{e}", "unreadable")
            _MEMO.update(key=key, payload=None, state=state)
            return None, state
        except OSError as e:
            # Windows 上另一进程正在 os.replace 时会短暂共享冲突；
            # 退避重试，仍失败也不写 memo（下次调用会重新读盘）。
            last_os_error = e
            if attempt < 2:
                time.sleep(0.03 * (attempt + 1))
    if last_os_error is not None:
        return None, _empty_state(target, "invalid",
                                  f"缓存读取失败：{last_os_error}",
                                  "unreadable")
    ok, why = _validate(payload)
    if not ok:
        state = _empty_state(target, "invalid", why, "schema")
        _MEMO.update(key=key, payload=None, state=state)
        return None, state
    fetched_at = payload.get("fetched_at")
    try:
        fetched_at = float(fetched_at) if fetched_at is not None else None
    except (TypeError, ValueError):
        fetched_at = None
    state = {
        "state": "ok",
        "path": target,
        "detail": "",
        "fetched_at": fetched_at,
        "fetched_at_utc": str(payload.get("fetched_at_utc") or ""),
        "age_s": None if fetched_at is None else max(0.0, ref - fetched_at),
        "sat_count": len(payload.get("satellites") or {}),
        "failure_count": len(payload.get("failures") or {}),
    }
    _MEMO.update(key=key, payload=payload, state=state)
    return payload, state


def _satellite_record(payload: Optional[Dict[str, Any]],
                      norad_cat_id: Any) -> Optional[Dict[str, Any]]:
    if not payload:
        return None
    sats = payload.get("satellites") or {}
    rec = sats.get(str(norad_cat_id))
    if rec is None:
        rec = sats.get(norad_cat_id)  # 兼容以 int 为键的手写缓存
    return rec if isinstance(rec, dict) else None


# ------------------------------------------------------------------- 拉取写盘
def _norm_targets(targets: Iterable[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in targets or []:
        if isinstance(item, dict):
            norad = item.get("norad_cat_id")
            key = item.get("key") or item.get("norad_name") or ""
        else:
            seq = list(item)
            norad = seq[0] if seq else None
            key = seq[1] if len(seq) > 1 else ""
        try:
            norad = int(norad)
        except (TypeError, ValueError):
            continue
        if norad in seen:
            continue
        seen.add(norad)
        out.append({"norad_cat_id": norad, "key": str(key)})
    return out


def _parse_tx_payload(body: Any) -> Tuple[Optional[List[Dict[str, Any]]],
                                         Optional[Dict[str, Any]]]:
    """解析 SatNOGS 响应 -> (发射机列表, 失败)；结构变了如实报。"""
    if body is None:
        return None, _failure("empty_response", "响应为空")
    if isinstance(body, (bytes, bytearray)):
        text = body.decode("utf-8", "replace")
        if not text.strip():
            return None, _failure("empty_response", "响应体为空")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return None, _failure("parse_failure", f"JSON 解析失败：{e}")
    elif isinstance(body, str):
        if not body.strip():
            return None, _failure("empty_response", "响应体为空")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return None, _failure("parse_failure", f"JSON 解析失败：{e}")
    else:
        data = body
    if not isinstance(data, list):
        return None, _failure(
            "schema_mismatch",
            f"响应顶层是 {type(data).__name__}，不是发射机数组")
    if not data:
        return [], None
    valid: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        # 结构哨兵：机器库至少要有下行业务字段之一
        if not any(k in item for k in ("downlink_low", "downlink_high",
                                       "mode", "status", "uuid")):
            continue
        valid.append(item)
    if not valid:
        return None, _failure(
            "schema_mismatch",
            f"返回 {len(data)} 条但没有一条含 downlink/mode/status 字段")
    return valid, None


def _trim_tx(item: Dict[str, Any]) -> Dict[str, Any]:
    """保留源字段名原样落盘（便于回查），只取本模块用到的键。"""
    keys = ("uuid", "description", "uplink_low", "uplink_high",
            "downlink_low", "downlink_high", "downlink_drift", "mode",
            "uplink_mode", "baud", "sat_id", "norad_cat_id", "status",
            "updated", "service", "alive")
    return {k: item.get(k) for k in keys if k in item}


def _cleanup_tmp(tmp: str) -> None:
    try:
        os.unlink(tmp)
    except OSError:
        pass


def _write_cache(payload: Dict[str, Any], path: str) -> Tuple[bool, str]:
    """原子写盘 -> ``(成功?, 失败原因)``；失败绝不装作成功。"""
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as e:
        logger.warning("卫星发射机缓存写入失败：%s", e)
        _cleanup_tmp(tmp)
        return False, f"{type(e).__name__}: {e}"
    # Windows：读者/杀软短暂持有目标文件时 os.replace 会抛共享冲突，
    # 退避重试；仍失败就保留旧缓存并把原因交给上层如实上报。
    last = ""
    for attempt in range(3):
        try:
            os.replace(tmp, path)
            clear_cache_memo()
            return True, ""
        except OSError as e:
            last = f"{type(e).__name__}: {e}"
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
    logger.warning("卫星发射机缓存替换失败：%s", last)
    _cleanup_tmp(tmp)
    return False, last


class _CacheLock:
    """跨进程缓存锁：只罩住「重读 + 合并 + 写盘」，不罩网络请求。

    拿不到锁不阻断刷新（结果里 ``lock_acquired`` 如实为 False），
    但写盘仍走原子替换，最坏是后写者覆盖，不会写出半个 JSON。
    """

    def __init__(self, path: Optional[str], deadline_s: float = 10.0):
        self.path = f"{path}.lock" if path else None
        self.deadline_s = deadline_s
        self._fh = None

    def acquire(self) -> bool:
        if not self.path:
            return False
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fh = open(self.path, "a+b")
            if fh.seek(0, os.SEEK_END) == 0:
                fh.write(b"\0")
                fh.flush()
        except OSError as e:
            logger.warning("卫星发射机缓存锁打不开：%s", e)
            return False
        end = time.monotonic() + self.deadline_s
        while True:
            try:
                fh.seek(0)
                if msvcrt is not None:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh = fh
                return True
            except OSError:
                if time.monotonic() >= end:
                    fh.close()
                    logger.warning("卫星发射机缓存锁超时（%.0fs）",
                                   self.deadline_s)
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.seek(0)
            if msvcrt is not None:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            fh.close()


def refresh(targets: Iterable[Any],
            path: Optional[str] = None,
            opener: Optional[Callable[[str, float, float], Any]] = None,
            now: Optional[float] = None,
            offline: Optional[bool] = None,
            fresh_hours: float = DEFAULT_FRESH_HOURS) -> Dict[str, Any]:
    """拉取目标星的发射机并写盘；失败分类、保留旧缓存、绝不静默清空。"""
    del fresh_hours  # 由状态展示层使用；刷新本身不做年龄门槛
    ref = _now(now)
    target_path = path or cache_path()
    is_offline = _offline() if offline is None else bool(offline)
    todo = _norm_targets(targets)
    result: Dict[str, Any] = {
        "state": "no_targets",
        "fetched_at": ref,
        "fetched_at_utc": _iso_utc(ref),
        "satellites": {},
        "failures": {},
        "cache_path": target_path,
        "written": False,
        "detail": "",
    }
    if not todo:
        result["detail"] = "没有可拉取的目标（人工目录未声明 norad_cat_id）"
        return result
    if is_offline:
        result["state"] = "offline"
        result["detail"] = FAILURE_LABELS["offline"]
        return result

    old_payload, _old_state = load_cache(target_path)
    old_sats = dict((old_payload or {}).get("satellites") or {})
    satellites: Dict[str, Any] = {}
    failures: Dict[str, Any] = {}
    fetch = opener or _bounded_opener
    fresh_written = False
    for entry in todo:
        norad = entry["norad_cat_id"]
        url = DEFAULT_TX_URL.format(norad=norad)
        try:
            body = fetch(url, DEFAULT_TIMEOUT_S, DEFAULT_HARD_DEADLINE_S)
            txs, failure = _parse_tx_payload(body)
        except Exception as e:  # noqa: BLE001 - 统一分类，不逃逸
            txs, failure = None, _classify_exc(e)
        if failure is not None:
            failures[str(norad)] = dict(failure, norad_cat_id=norad,
                                        at_utc=_iso_utc(ref))
            logger.warning("卫星发射机库拉取失败 NORAD %s：%s（%s）",
                           norad, failure["label"], failure["detail"])
            old = old_sats.get(str(norad))
            if isinstance(old, dict):
                kept = dict(old)
                kept["last_refresh_failure"] = dict(
                    failure, at_utc=_iso_utc(ref))
                satellites[str(norad)] = kept
            continue
        txs = [_trim_tx(t) for t in (txs or [])]
        first = txs[0] if txs else {}
        satellites[str(norad)] = {
            "norad_cat_id": norad,
            "key": entry["key"],
            "satnogs_name": None,       # 发射机接口不带卫星名，留空不编造
            "satnogs_sat_id": first.get("sat_id"),
            "fetched_at": ref,
            "fetched_at_utc": _iso_utc(ref),
            "state": "ok" if txs else "empty",
            "transmitters": txs,
        }
        fresh_written = True

    if not fresh_written:
        # 旧缓存里未被本轮目标覆盖的星原样保留，避免刷新把目录缩水。
        for key, rec in old_sats.items():
            satellites.setdefault(key, rec)
        result["satellites"] = satellites
        result["failures"] = failures
        result["state"] = "failed"
        result["detail"] = ("本轮全部目标拉取失败；"
                            + ("旧缓存原样保留" if old_payload else "且无旧缓存"))
        logger.warning("卫星发射机库刷新全失败：%s", result["detail"])
        return result

    # 只锁「重读 + 合并 + 写盘」：网络请求已经做完，锁内不留 I/O 等待。
    lock = _CacheLock(target_path)
    acquired = lock.acquire()
    result["lock_acquired"] = acquired
    try:
        if acquired:
            clear_cache_memo()
            current, _ = load_cache(target_path)
            current_sats = dict(((current or {}).get("satellites")) or {})
            target_keys = {str(e["norad_cat_id"]) for e in todo}
            # 失败的星：优先采用临界区内较新的旧记录，只把失败原因挂上去。
            for norad_s, failure in failures.items():
                rec = current_sats.get(norad_s)
                if isinstance(rec, dict):
                    satellites[norad_s] = dict(
                        rec, last_refresh_failure=dict(failure))
            # 本轮没碰的星：以临界区内的当前缓存为准，避免覆盖并发写入。
            for key, rec in current_sats.items():
                if key not in target_keys:
                    satellites.setdefault(key, rec)
        result["satellites"] = satellites
        result["failures"] = failures
        payload = {
            "_schema": CACHE_SCHEMA,
            "_source": "SatNOGS DB",
            "_source_url": DEFAULT_SOURCE_URL,
            "fetched_at": ref,
            "fetched_at_utc": _iso_utc(ref),
            "satellites": satellites,
            "failures": failures,
        }
        written, write_err = ((False, "无缓存路径") if not target_path
                              else _write_cache(payload, target_path))
    finally:
        lock.release()

    result["written"] = written
    if not written:
        result["state"] = "write_failed"
        result["detail"] = (f"拉取成功但本地缓存写入失败（{write_err}）；"
                            "旧缓存保留"
                            + (f"，另有 {len(failures)} 颗拉取失败"
                               if failures else ""))
        logger.warning("卫星发射机库刷新写盘失败：%s", result["detail"])
        return result
    result["state"] = "ok" if not failures else "partial"
    result["detail"] = ("" if not failures
                        else f"{len(failures)} 颗拉取失败，其余已更新")
    return result


# --------------------------------------------------------------- 后台刷新
_BG_LOCK = threading.Lock()
_BG_THREAD: Optional[threading.Thread] = None
_BG_LAST: Dict[str, Any] = {}


def refresh_in_progress() -> bool:
    with _BG_LOCK:
        return _BG_THREAD is not None and _BG_THREAD.is_alive()


def last_refresh_result() -> Dict[str, Any]:
    with _BG_LOCK:
        return dict(_BG_LAST)


def start_background_refresh(targets: Iterable[Any],
                             path: Optional[str] = None,
                             opener: Optional[Callable[..., Any]] = None
                             ) -> Optional[threading.Thread]:
    """后台拉库（daemon）；已有在途任务时直接复用，不叠线程、不阻塞调用方。"""
    global _BG_THREAD
    with _BG_LOCK:
        if _BG_THREAD is not None and _BG_THREAD.is_alive():
            return _BG_THREAD
        todo = _norm_targets(targets)

        def _work() -> None:
            try:
                res = refresh(todo, path=path, opener=opener)
            except Exception as e:  # noqa: BLE001 - 后台线程不允许静默死
                logger.exception("卫星发射机后台刷新异常")
                res = {"state": "failed",
                       "detail": f"{type(e).__name__}: {e}",
                       "fetched_at": time.time()}
            with _BG_LOCK:
                _BG_LAST.clear()
                _BG_LAST.update(res)

        thread = threading.Thread(target=_work, name="sat-links-refresh",
                                  daemon=True)
        _BG_THREAD = thread
        thread.start()
        return thread


# ----------------------------------------------------------- 状态与合并视图
def catalog_status(payload: Optional[Dict[str, Any]] = None,
                   path: Optional[str] = None,
                   now: Optional[float] = None,
                   fresh_hours: float = DEFAULT_FRESH_HOURS) -> Dict[str, Any]:
    """缓存状态（供界面/工具读）：永远返回结构，不抛异常。"""
    if payload is None:
        payload, state = load_cache(path=path, now=now)
        if payload is None and _offline():
            state = dict(state, state="offline",
                         detail=FAILURE_LABELS["offline"])
    else:
        state = {"state": "ok", "path": path or cache_path(), "detail": "",
                 "fetched_at": payload.get("fetched_at"),
                 "fetched_at_utc": str(payload.get("fetched_at_utc") or ""),
                 "age_s": None, "sat_count": len(payload.get("satellites") or {}),
                 "failure_count": len(payload.get("failures") or {})}
        if state["fetched_at"] is not None:
            state["age_s"] = max(0.0, _now(now) - float(state["fetched_at"]))
    state = dict(state)
    state["fresh_hours"] = float(fresh_hours)
    state["stale"] = (state["age_s"] is not None
                      and state["age_s"] > fresh_hours * 3600.0)
    state["source"] = SERVICE
    state["text"] = status_text(state)
    return state


def status_text(state: Dict[str, Any]) -> str:
    """一句人话状态；界面只展示这一处，不在别处另写一套。"""
    kind = str(state.get("state") or "missing")
    if kind == "ok":
        age = _age_text(state.get("age_s"))
        n = int(state.get("sat_count") or 0)
        text = f"SatNOGS 发射机库：机器缓存 {age}前（{n} 颗）"
        if state.get("stale"):
            text += f"，已超 {float(state.get('fresh_hours') or 0):.0f}h 未刷新"
        if state.get("failure_count"):
            text += f"，{int(state['failure_count'])} 颗最近拉取失败"
        return text
    if kind == "offline":
        return ("SatNOGS 发射机库：未取到（"
                f"{FAILURE_LABELS['offline']}），沿用人工目录")
    if kind == "write_failed":
        return ("SatNOGS 发射机库：拉取成功但本地缓存写入失败"
                "（只读目录/文件被占用/权限），沿用旧缓存")
    if kind == "missing":
        return "SatNOGS 发射机库：未取到（无本地缓存），沿用人工目录"
    if kind == "invalid":
        why = state.get("detail") or "未知原因"
        if state.get("reason") == "schema":
            return ("SatNOGS 发射机库：未取到（缓存结构不符："
                    f"{why}），沿用人工目录")
        return ("SatNOGS 发射机库：未取到（缓存不可用："
                f"{why}），沿用人工目录")
    return "SatNOGS 发射机库：未取到，沿用人工目录"


def _tx_low_high(tx: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    low = tx.get("downlink_low")
    high = tx.get("downlink_high")
    try:
        low = float(low) if low is not None else None
    except (TypeError, ValueError):
        low = None
    try:
        high = float(high) if high is not None else None
    except (TypeError, ValueError):
        high = None
    if low is None and high is None:
        return None, None
    if low is None:
        low = high
    if high is None:
        high = None          # 源只给单点：如实保留「高频边界未取到」
    return low, high


def _tx_distance(freq: float, tx: Dict[str, Any]) -> Optional[float]:
    low, high = _tx_low_high(tx)
    if low is None:
        return None
    if high is not None and low <= freq <= high:
        return 0.0
    return min(abs(freq - low),
               abs(freq - high) if high is not None else abs(freq - low))


def _mode_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _mode_compatible(manual: Any, machine: Any) -> bool:
    a, b = _mode_key(manual), _mode_key(machine)
    if not a or not b:
        return False
    if a == b:
        return True
    fm_like = {"nfm", "fm", "fmn"}
    return a in fm_like and b in fm_like


def _machine_link_view(tx: Dict[str, Any]) -> Dict[str, Any]:
    low, high = _tx_low_high(tx)
    return {
        "freq_hz": low,
        "low_hz": low,
        "high_hz": high,
        "mode": tx.get("mode"),
        "baud": tx.get("baud"),
        "status": tx.get("status"),
        "service": tx.get("service"),
        "uuid": tx.get("uuid"),
        "description": tx.get("description"),
        "updated": tx.get("updated"),
        "source": SERVICE,
    }


def merge_entry(entry: Dict[str, Any],
                payload: Optional[Dict[str, Any]] = None,
                now: Optional[float] = None) -> Dict[str, Any]:
    """人工条目 + 机器字段的只读合并视图（人工频率永远优先、不被覆盖）。"""
    merged: Dict[str, Any] = dict(entry)
    merged["links"] = [dict(link) for link in entry.get("links", [])]
    norad = entry.get("norad_cat_id")
    basis: Dict[str, Any] = {
        "source": SERVICE,
        "state": "absent",
        "detail": "人工条目未声明 norad_cat_id",
        "satnogs_name": None,
        "fetched_at": None,
        "fetched_at_utc": "",
        "age_s": None,
        "transmitter_count": 0,
        "matched_links": 0,
        "conflicts": [],
        "machine_only": [],
    }
    if norad is None:
        merged["_machine"] = basis
        return merged

    payload = payload if payload is not None else load_cache()[0]
    rec = _satellite_record(payload, norad)
    if rec is None:
        basis["state"] = "absent"
        if payload is None and _offline():
            basis["detail"] = "离线模式，未取到机器库"
        else:
            basis["detail"] = ("无机器缓存" if payload is None
                               else "机器缓存里没有这颗星")
        merged["_machine"] = basis
        return merged

    basis["state"] = str(rec.get("state") or "ok")
    basis["detail"] = ""
    basis["satnogs_name"] = rec.get("satnogs_name")
    basis["satnogs_sat_id"] = rec.get("satnogs_sat_id")
    basis["fetched_at"] = rec.get("fetched_at")
    basis["fetched_at_utc"] = str(rec.get("fetched_at_utc") or "")
    if rec.get("fetched_at") is not None:
        basis["age_s"] = max(0.0, _now(now) - float(rec["fetched_at"]))
    basis["last_refresh_failure"] = rec.get("last_refresh_failure")

    txs = [tx for tx in (rec.get("transmitters") or [])
           if isinstance(tx, dict) and _tx_low_high(tx)[0] is not None]
    basis["transmitter_count"] = len(txs)
    used: set = set()
    for link in merged["links"]:
        try:
            freq = float(link.get("freq_hz"))
        except (TypeError, ValueError):
            continue
        best = None
        best_key = None
        for idx, tx in enumerate(txs):
            dist = _tx_distance(freq, tx)
            if dist is None or dist > MATCH_TOL_HZ:
                continue
            # 同频多发射机：体制一致优先（NOAA-18 同频有 FSK「IQ Recording」
            # 与 APT「Downlink」，不能因为前者 active 就配错体制），
            # 其次状态 active，再按距离与条目顺序。
            key = (0 if _mode_compatible(link.get("mode"), tx.get("mode")) else 1,
                   0 if str(tx.get("status")) == "active" else 1,
                   dist, idx)
            if best_key is None or key < best_key:
                best, best_key = tx, key
        if best is None:
            continue
        used.add(id(best))
        low, high = _tx_low_high(best)
        link["low_hz"] = low
        link["high_hz"] = high
        link["status"] = best.get("status")
        link["service"] = best.get("service")
        link["source"] = SERVICE
        link["machine"] = {
            "uuid": best.get("uuid"),
            "mode": best.get("mode"),
            "baud": best.get("baud"),
            "updated": best.get("updated"),
            "description": best.get("description"),
        }
        basis["matched_links"] += 1
        tx_freq = low
        if tx_freq is not None and abs(float(tx_freq) - freq) > 1e-9:
            basis["conflicts"].append({
                "link_freq_hz": freq,
                "machine_freq_hz": tx_freq,
                "delta_hz": float(tx_freq) - freq,
                "machine_mode": best.get("mode"),
                "machine_status": best.get("status"),
            })

    basis["machine_only"] = [
        _machine_link_view(tx) for tx in txs if id(tx) not in used]
    merged["_machine"] = basis
    return merged


def machine_only_links(entry: Dict[str, Any],
                       payload: Optional[Dict[str, Any]] = None,
                       now: Optional[float] = None) -> List[Dict[str, Any]]:
    """机器库里存在、人工链路未收录的发射机（只读增量，不写回人工表）。"""
    merged = merge_entry(entry, payload=payload, now=now)
    return list((merged.get("_machine") or {}).get("machine_only") or [])
