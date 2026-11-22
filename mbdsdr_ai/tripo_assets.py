# SPDX-License-Identifier: MIT
"""Tripo 文生 3D 资产客户端（离线可测、默认拒绝联网）。

只做三件事：提交 ``text-to-model`` 任务、轮询任务状态、把成功响应的
``model_url`` 下载成 ``.glb``。网络策略复用 :mod:`mbdsdr_ai.net_policy`：
默认离线优先，未显式设置 ``MBDSDR_ALLOW_NETWORK=1`` 时**不发起任何请求**；
``MBDSDR_OFFLINE`` 优先级更高，置位后即使传入 ``allow_network=True`` 也拒绝。

诚实纪律
--------

* 无 Key、离线、提交失败、任务失败、轮询超时、下载失败、响应不是
  JSON/缺字段——一律返回结构化失败（``ok=False`` + 稳定错误码 + 中文原因），
  不返回「差不多成功」；
* 下载先写同目录临时文件，校验非空后再 :func:`os.replace` 原子替换；
  任何失败都清掉临时文件，不留下半截 ``.glb``；
* API Key 只从参数或环境变量 ``TRIPO_API_KEY`` 读取，不写日志、不进返回值、
  不出现在失败原因里。

注入点
------

``transport`` 需提供三个可调用属性：``post_json(url, headers, payload)``、
``get_json(url, headers)``、``download(url, dest)``。缺省实现
:class:`UrllibTransport` 走 :mod:`urllib.request`；测试可传假 transport，
完全不联网、不真正等待轮询。
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import shutil
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import net_policy

#: Tripo 官方 API 根地址（大赛教程已核实）。
BASE_URL = "https://openapi.tripo3d.ai/v3"
SUBMIT_URL = BASE_URL + "/generation/text-to-model"
TASK_URL_TEMPLATE = BASE_URL + "/tasks/{task_id}"

#: 默认模型版本。
DEFAULT_MODEL = "v3.1-20260211"

#: API Key 环境变量名。
API_KEY_ENV = "TRIPO_API_KEY"

#: 任务成功状态与终态失败状态。
SUCCESS_STATUS = "success"
PENDING_STATUSES = frozenset({"queued", "running"})
FAILED_STATUSES = frozenset({"failed", "banned", "cancelled"})

#: 稳定错误码（CLI 与调用方按这些码分流，不解析中文原因）。
ERROR_MISSING_API_KEY = "missing_api_key"
ERROR_OFFLINE = "offline"
ERROR_INVALID_PROMPT = "invalid_prompt"
ERROR_INVALID_OUT_PATH = "invalid_out_path"
ERROR_INVALID_MODEL = "invalid_model"
ERROR_INVALID_POLL_INTERVAL = "invalid_poll_interval"
ERROR_INVALID_TIMEOUT = "invalid_timeout"
ERROR_INVALID_TRANSPORT = "invalid_transport"
ERROR_SUBMIT_FAILED = "submit_failed"
ERROR_SUBMIT_INVALID_RESPONSE = "submit_invalid_response"
ERROR_POLL_FAILED = "poll_failed"
ERROR_POLL_INVALID_RESPONSE = "poll_invalid_response"
ERROR_POLL_TIMEOUT = "poll_timeout"
ERROR_TASK_FAILED = "task_failed"
ERROR_MISSING_MODEL_URL = "missing_model_url"
ERROR_INVALID_MODEL_URL = "invalid_model_url"
ERROR_DOWNLOAD_FAILED = "download_failed"
ERROR_WRITE_FAILED = "write_failed"
ERROR_INVALID_MANIFEST = "invalid_manifest"
ERROR_INVALID_OUT_DIR = "invalid_out_dir"
ERROR_INVALID_MAX_MODELS = "invalid_max_models"
ERROR_SKIPPED = "skipped"

#: 批量清单里 ``name`` 允许的字符集：不含 ``.`` 与路径分隔符，杜绝穿越。
_MANIFEST_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")

_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')
_SPACE_RE = re.compile(r"\s+")
_REPEAT_UNDERSCORE_RE = re.compile(r"_+")


class _TransportError(Exception):
    """网络/HTTP/本地 I/O 失败（交给 generate_model 分类）。"""


class _ResponseError(Exception):
    """响应不是合法 JSON 或结构不符。"""


def _failure(code: str, reason: str) -> Dict[str, Any]:
    return {"ok": False, "error": code, "reason": reason}


def _number(value: Any) -> Optional[float]:
    """接受有限实数；bool 不算数值，非法返回 None。"""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def slugify_prompt(prompt: Any, max_length: int = 64) -> str:
    """把 prompt 变成安全文件名（保留中文/字母/数字，去掉路径与控制字符）。

    空 prompt 或清理后为空时返回 ``"tripo_model"``；Windows 保留名加
    ``tripo_`` 前缀，避免 ``CON.glb`` 之类无法创建的文件。
    """
    text = prompt if isinstance(prompt, str) else ""
    text = _CONTROL_RE.sub("_", text)
    text = _UNSAFE_RE.sub("_", text)
    text = _SPACE_RE.sub("_", text.strip())
    text = _REPEAT_UNDERSCORE_RE.sub("_", text)
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch in "._-")
    cleaned = cleaned.strip("._-")
    try:
        limit = max(1, int(max_length))
    except (TypeError, ValueError):
        limit = 64
    cleaned = cleaned[:limit].strip("._-")
    if not cleaned:
        return "tripo_model"
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = "tripo_" + cleaned
    return cleaned


def unique_output_path(path: Any) -> str:
    """不覆盖已有文件：存在时在扩展名前加 ``-1``、``-2``……"""
    target = os.path.abspath(os.fspath(path))
    if not os.path.exists(target):
        return target
    stem, suffix = os.path.splitext(target)
    for index in range(1, 10000):
        candidate = f"{stem}-{index}{suffix}"
        if not os.path.exists(candidate):
            return candidate
    raise OSError(f"同名文件过多，无法为 {target} 找到可用名称")


def _is_https_url(url: Any) -> bool:
    """只做语法校验（不发 DNS）：https + 有主机名 + 无空白/控制字符。"""
    if not isinstance(url, str) or not url.strip():
        return False
    if any(ch.isspace() or ord(ch) < 0x20 for ch in url):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parsed.scheme.lower() == "https" and bool(parsed.hostname)


def _ensure_public_https(url: str) -> None:
    """网络层安全门：只允许 https，拒绝解析到内网/回环/链路本地的 host。"""
    if not _is_https_url(url):
        raise _TransportError("只允许 https URL")
    host = urllib.parse.urlparse(url).hostname
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise _TransportError(f"无法解析主机 {host}：{exc}") from exc
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise _TransportError(f"安全限制：拒绝访问内网/回环地址 {host}")


def _describe_error(exc: BaseException) -> str:
    """网络异常的人话摘要；不回显完整 URL（下载直链可能带签名）。"""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {int(getattr(exc, 'code', 0) or 0)}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "请求超时（含 DNS 解析）"
    if isinstance(exc, urllib.error.URLError):
        return f"网络不可达：{getattr(exc, 'reason', exc)}"
    return f"{type(exc).__name__}: {exc}"


def _bounded_urlopen(req: urllib.request.Request, timeout_s: float,
                     hard_deadline_s: float):
    """复用 orbit 的硬截止 urlopen（线程 + join，覆盖 DNS 阶段）。"""
    try:
        from .orbit import _bounded_urlopen as opener
    except ImportError:  # 独立脚本运行
        from orbit import _bounded_urlopen as opener
    return opener(req, timeout_s, hard_deadline_s)


class UrllibTransport:
    """缺省 transport：urllib + 硬截止 + https/私网地址校验。"""

    def __init__(self, request_timeout_s: float = 30.0,
                 hard_deadline_s: float = 40.0) -> None:
        self.request_timeout_s = float(request_timeout_s)
        self.hard_deadline_s = float(hard_deadline_s)

    def _open(self, req: urllib.request.Request):
        _ensure_public_https(req.full_url)
        try:
            resp = _bounded_urlopen(req, self.request_timeout_s,
                                    self.hard_deadline_s)
        except Exception as exc:  # noqa: BLE001 - 统一交给上层分类
            raise _TransportError(_describe_error(exc)) from exc
        try:
            _ensure_public_https(resp.geturl())
        except Exception:
            resp.close()
            raise
        return resp

    def _read_json(self, req: urllib.request.Request) -> Any:
        try:
            with self._open(req) as resp:
                raw = resp.read()
        except (_TransportError, _ResponseError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _TransportError(_describe_error(exc)) from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _ResponseError("响应不是合法 JSON") from exc

    def post_json(self, url: str, headers: Dict[str, str],
                  payload: Dict[str, Any]) -> Any:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        merged = dict(headers)
        merged.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=merged,
                                     method="POST")
        return self._read_json(req)

    def get_json(self, url: str, headers: Dict[str, str]) -> Any:
        req = urllib.request.Request(url, headers=dict(headers), method="GET")
        return self._read_json(req)

    def download(self, url: str, dest: str) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "MBDSDR/1.0"})
        resp = self._open(req)
        try:
            with resp, open(dest, "wb") as fh:
                shutil.copyfileobj(resp, fh, length=256 * 1024)
        except (OSError, _TransportError, _ResponseError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _TransportError(_describe_error(exc)) from exc
        if os.path.getsize(dest) <= 0:
            raise _ResponseError("下载内容为空")


class _MappingTransport:
    """把 ``{"post_json": fn, ...}`` 字典适配成 transport 对象。"""

    def __init__(self, mapping: Dict[str, Any]) -> None:
        self._mapping = dict(mapping)

    def post_json(self, *args: Any, **kwargs: Any) -> Any:
        return self._mapping["post_json"](*args, **kwargs)

    def get_json(self, *args: Any, **kwargs: Any) -> Any:
        return self._mapping["get_json"](*args, **kwargs)

    def download(self, *args: Any, **kwargs: Any) -> Any:
        return self._mapping["download"](*args, **kwargs)


def _validate_call(prompt: Any, out_path: Any, model: Any,
                   poll_interval_s: Any, timeout_s: Any,
                   transport: Any) -> Optional[Dict[str, Any]]:
    """参数校验（在任何网络/Key 判定之前）。"""
    if not isinstance(prompt, str) or not prompt.strip():
        return _failure(ERROR_INVALID_PROMPT, "prompt 必须是非空字符串")

    try:
        raw_out = os.fspath(out_path)
    except TypeError:
        return _failure(ERROR_INVALID_OUT_PATH,
                        "out_path 必须是文件路径字符串或 Path")
    if isinstance(raw_out, bytes) or not str(raw_out).strip():
        return _failure(ERROR_INVALID_OUT_PATH, "out_path 必须是非空文件路径")
    if str(raw_out).replace("\\", "/").endswith("/"):
        return _failure(ERROR_INVALID_OUT_PATH, "out_path 必须是文件路径，不能是目录")
    absolute_out = os.path.abspath(str(raw_out))
    if os.path.isdir(absolute_out):
        return _failure(ERROR_INVALID_OUT_PATH,
                        f"out_path 指向已存在的目录：{absolute_out}")

    if not isinstance(model, str) or not model.strip():
        return _failure(ERROR_INVALID_MODEL, "model 必须是非空字符串")

    interval = _number(poll_interval_s)
    if interval is None or interval < 0.0:
        return _failure(ERROR_INVALID_POLL_INTERVAL,
                        "poll_interval_s 必须是 >= 0 的有限数值")

    timeout = _number(timeout_s)
    if timeout is None or timeout <= 0.0:
        return _failure(ERROR_INVALID_TIMEOUT,
                        "timeout_s 必须是 > 0 的有限数值")

    if transport is not None:
        if isinstance(transport, dict):
            for name in ("post_json", "get_json", "download"):
                if not callable(transport.get(name)):
                    return _failure(ERROR_INVALID_TRANSPORT,
                                    f"transport 缺可调用函数 {name}()")
        else:
            for name in ("post_json", "get_json", "download"):
                if not callable(getattr(transport, name, None)):
                    return _failure(ERROR_INVALID_TRANSPORT,
                                    f"transport 缺少可调用方法 {name}()")
    return None


def _resolve_api_key(api_key: Optional[str]) -> str:
    raw = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _network_permitted(allow_network: Optional[bool]) -> bool:
    if net_policy.network_disabled():
        return False
    if allow_network is not None:
        return bool(allow_network)
    return net_policy.network_allowed()


def _api_data(response: Any) -> Any:
    """取出 ``data``；非对象/错误码非 0 抛 :class:`_ResponseError`。"""
    if not isinstance(response, dict):
        raise _ResponseError("响应不是 JSON 对象")
    code = response.get("code")
    if code is not None:
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            raise _ResponseError("响应 code 不是整数") from None
        if code_int != 0:
            raise _ResponseError(f"API 返回错误码 {code_int}")
    data = response.get("data")
    if not isinstance(data, dict):
        raise _ResponseError("响应缺少 data 对象")
    return data


def _download_model(transport: Any, model_url: str, out_path: str,
                    task_id: str, model: str, prompt: str) -> Dict[str, Any]:
    """下载到同目录临时文件，校验非空后原子替换；失败不留半文件。"""
    parent = os.path.dirname(out_path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        return _failure(ERROR_WRITE_FAILED, f"无法创建输出目录：{exc}")

    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=".tripo-", suffix=".part", dir=parent)
        os.close(fd)
    except OSError as exc:
        return _failure(ERROR_WRITE_FAILED, f"无法创建临时文件：{exc}")

    try:
        try:
            transport.download(model_url, tmp_path)
        except _ResponseError as exc:
            return _failure(ERROR_DOWNLOAD_FAILED, f"下载模型失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - 全部如实分类
            return _failure(ERROR_DOWNLOAD_FAILED,
                            f"下载模型失败：{type(exc).__name__}: {exc}")

        try:
            size = os.path.getsize(tmp_path)
        except OSError:
            size = 0
        if size <= 0:
            return _failure(ERROR_DOWNLOAD_FAILED, "下载结果为空文件")

        try:
            final_path = unique_output_path(out_path)
        except OSError as exc:
            return _failure(ERROR_WRITE_FAILED, f"无法确定输出文件名：{exc}")
        try:
            os.replace(tmp_path, final_path)
        except OSError as exc:
            return _failure(ERROR_WRITE_FAILED, f"写入输出文件失败：{exc}")
        tmp_path = None
        return {
            "ok": True,
            "path": os.path.abspath(final_path),
            "task_id": task_id,
            "model": model,
            "prompt": prompt,
        }
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _submit(transport: Any, headers: Dict[str, str], prompt: str,
            model: str) -> Any:
    return transport.post_json(
        SUBMIT_URL, headers, {"prompt": prompt, "model": model})


def generate_model(
    prompt: str,
    *,
    out_path: Any,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    poll_interval_s: float = 2.0,
    timeout_s: float = 300.0,
    transport: Any = None,
    allow_network: Optional[bool] = None,
) -> Dict[str, Any]:
    """生成一个 3D 资产并下载为 ``.glb``。

    参数与 Key/网络判定顺序固定为：**先参数校验，再 API Key，最后网络策略**，
    保证「无 Key/离线」时 transport 一次都不会被调用。

    返回成功 ``{"ok": True, "path", "task_id", "model", "prompt"}``，
    失败 ``{"ok": False, "error", "reason"}``。
    """
    invalid = _validate_call(prompt, out_path, model, poll_interval_s,
                             timeout_s, transport)
    if invalid is not None:
        return invalid

    key = _resolve_api_key(api_key)
    if not key:
        return _failure(
            ERROR_MISSING_API_KEY,
            "未提供 Tripo API Key：请设置环境变量 TRIPO_API_KEY"
            "（在 https://developers.tripo3d.ai 控制台创建，Key 只显示一次）")

    if not _network_permitted(allow_network):
        return _failure(
            ERROR_OFFLINE,
            "生成 3D 资产需要联网访问 Tripo API，当前未允许联网。"
            "请设置环境变量 MBDSDR_ALLOW_NETWORK=1 后重试"
            "（若已设置 MBDSDR_OFFLINE=1，请先取消离线模式）")

    if transport is None:
        transport = UrllibTransport()
    elif isinstance(transport, dict):
        transport = _MappingTransport(transport)

    prompt_text = prompt.strip()
    model_text = model.strip()
    headers = {"Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}

    try:
        submitted = _submit(transport, headers, prompt_text, model_text)
    except _ResponseError as exc:
        return _failure(ERROR_SUBMIT_INVALID_RESPONSE,
                        f"提交响应无法解析：{exc}")
    except Exception as exc:  # noqa: BLE001
        return _failure(ERROR_SUBMIT_FAILED,
                        f"提交任务失败：{type(exc).__name__}: {exc}")
    try:
        data = _api_data(submitted)
    except _ResponseError as exc:
        return _failure(ERROR_SUBMIT_INVALID_RESPONSE,
                        f"提交响应无效：{exc}")
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        return _failure(ERROR_SUBMIT_INVALID_RESPONSE,
                        "提交响应缺少 data.task_id")
    task_id = task_id.strip()

    deadline = time.monotonic() + float(timeout_s)
    last_status = "unknown"
    last_progress: Optional[int] = None
    task_url = TASK_URL_TEMPLATE.format(task_id=task_id)

    while True:
        try:
            polled = transport.get_json(task_url, headers)
        except _ResponseError as exc:
            return _failure(ERROR_POLL_INVALID_RESPONSE,
                            f"轮询响应无法解析：{exc}")
        except Exception as exc:  # noqa: BLE001
            return _failure(ERROR_POLL_FAILED,
                            f"轮询任务失败：{type(exc).__name__}: {exc}")
        try:
            data = _api_data(polled)
        except _ResponseError as exc:
            return _failure(ERROR_POLL_INVALID_RESPONSE,
                            f"轮询响应无效：{exc}")

        status = data.get("status")
        if not isinstance(status, str) or not status.strip():
            return _failure(ERROR_POLL_INVALID_RESPONSE,
                            "轮询响应缺少 data.status")
        status = status.strip().lower()
        last_status = status
        progress = data.get("progress")
        try:
            if progress is not None:
                last_progress = int(progress)
        except (TypeError, ValueError):
            last_progress = None

        if status == SUCCESS_STATUS:
            output = data.get("output")
            model_url = (output.get("model_url")
                         if isinstance(output, dict) else None)
            if not isinstance(model_url, str) or not model_url.strip():
                return _failure(
                    ERROR_MISSING_MODEL_URL,
                    "任务成功但响应缺少 data.output.model_url")
            model_url = model_url.strip()
            if not _is_https_url(model_url):
                return _failure(
                    ERROR_INVALID_MODEL_URL,
                    "模型下载地址不是 https URL，已拒绝下载")
            return _download_model(transport, model_url,
                                   os.path.abspath(os.fspath(out_path)),
                                   task_id, model_text, prompt_text)

        if status in FAILED_STATUSES:
            return _failure(ERROR_TASK_FAILED,
                            f"Tripo 任务未成功（status={status}）")

        if status not in PENDING_STATUSES:
            return _failure(ERROR_POLL_INVALID_RESPONSE,
                            f"未知任务状态：{status}")

        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            detail = f"（最后状态 {last_status}"
            if last_progress is not None:
                detail += f"，进度 {last_progress}%"
            detail += "）"
            return _failure(
                ERROR_POLL_TIMEOUT,
                f"轮询超时（{float(timeout_s):g}s）{detail}")
        interval = float(poll_interval_s)
        if interval > 0.0:
            time.sleep(min(interval, remaining))


def _manifest_failure(reason: str) -> Dict[str, Any]:
    return _failure(ERROR_INVALID_MANIFEST, reason)


def _entry_from_manifest(item: Any, index: int,
                         seen: set) -> Tuple[Optional[Dict[str, Any]],
                                            Optional[Dict[str, Any]]]:
    """校验单条清单条目；返回 ``(entry, error)``，二者恰有一个非 None。"""
    position = f"第 {index + 1} 条"
    if not isinstance(item, dict):
        return None, _manifest_failure(f"{position}不是 JSON 对象")

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, _manifest_failure(f"{position}缺少非空 name")
    name = name.strip()
    if not _MANIFEST_NAME_RE.fullmatch(name):
        return None, _manifest_failure(
            f"{position} name 只能含字母/数字/下划线/短横线：{name!r}")
    if name in seen:
        return None, _manifest_failure(f"{position} name 重复：{name}")

    prompt = item.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None, _manifest_failure(f"{position}（{name}）缺少非空 prompt")

    raw_model = item.get("model")
    if raw_model is None:
        raw_model = DEFAULT_MODEL
    if not isinstance(raw_model, str) or not raw_model.strip():
        return None, _manifest_failure(
            f"{position}（{name}）model 必须是非空字符串")

    seen.add(name)
    return {"name": name, "prompt": prompt.strip(),
            "model": raw_model.strip()}, None


def load_manifest(
    path: Any,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """读取并校验批量生成清单，返回 ``(entries, error)``。

    清单格式::

        {"version": 1,
         "items": [{"name": "mars", "prompt": "...", "model": "v3.1-20260211"}]}

    校验规则：顶层必须是 JSON 对象且 ``version == 1``；``items`` 必须是非空
    数组；``name`` 非空、在清单内唯一、只含 ``[A-Za-z0-9_-]``（防路径穿越）；
    ``prompt`` 非空字符串；``model`` 可省略（缺省 :data:`DEFAULT_MODEL`）。
    顶层其它键（如 ``_note``）一律忽略。

    任一条不合法都返回 ``([], {"ok": False, "error": "invalid_manifest",
    "reason": 中文原因})``——**整份清单要么全可用，要么不返回任何条目**，
    调用方不会拿到「一半能跑」的清单。
    """
    try:
        raw_path = os.fspath(path)
    except TypeError:
        return [], _manifest_failure("清单路径必须是字符串或 Path")
    if isinstance(raw_path, bytes) or not str(raw_path).strip():
        return [], _manifest_failure("清单路径必须是非空文件路径")

    try:
        with open(os.path.abspath(str(raw_path)), "r",
                  encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except OSError as exc:
        return [], _manifest_failure(f"无法读取清单文件：{exc}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], _manifest_failure(f"清单不是合法的 UTF-8 JSON：{exc}")

    if not isinstance(data, dict):
        return [], _manifest_failure("清单顶层必须是 JSON 对象")
    version = data.get("version")
    if isinstance(version, bool) or version != 1:
        return [], _manifest_failure("清单 version 必须为 1")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return [], _manifest_failure("清单 items 必须是非空数组")

    entries: List[Dict[str, Any]] = []
    seen: set = set()
    for index, item in enumerate(items):
        entry, error = _entry_from_manifest(item, index, seen)
        if error is not None:
            return [], error
        entries.append(entry)
    return entries, None


def _validate_batch(entries: Any, out_dir: Any, model: Any, max_models: Any,
                    poll_interval_s: Any, timeout_s: Any,
                    transport: Any) -> Tuple[Optional[str],
                                             List[Dict[str, Any]],
                                             Optional[Dict[str, Any]]]:
    """批量总校验：返回 ``(out_dir_abs, entries, error)``。

    任何一条非法都返回 error，调用方在**开始生成前**就停下。
    """
    try:
        raw_dir = os.fspath(out_dir)
    except TypeError:
        return "", [], _failure(ERROR_INVALID_OUT_DIR,
                                "out_dir 必须是目录路径字符串或 Path")
    if isinstance(raw_dir, bytes) or not str(raw_dir).strip():
        return "", [], _failure(ERROR_INVALID_OUT_DIR,
                                "out_dir 必须是非空目录路径")
    absolute_dir = os.path.abspath(str(raw_dir))
    if os.path.exists(absolute_dir) and not os.path.isdir(absolute_dir):
        return "", [], _failure(
            ERROR_INVALID_OUT_DIR, f"out_dir 已存在但不是目录：{absolute_dir}")

    if isinstance(max_models, bool) or not isinstance(max_models, int) \
            or max_models < 1:
        return "", [], _failure(ERROR_INVALID_MAX_MODELS,
                                "max_models 必须是 >= 1 的整数")

    if not isinstance(model, str) or not model.strip():
        return "", [], _failure(ERROR_INVALID_MODEL, "model 必须是非空字符串")

    if not isinstance(entries, (list, tuple)) or not entries:
        return "", [], _manifest_failure("条目必须是非空数组")

    interval = _number(poll_interval_s)
    if interval is None or interval < 0.0:
        return "", [], _failure(ERROR_INVALID_POLL_INTERVAL,
                                "poll_interval_s 必须是 >= 0 的有限数值")
    timeout = _number(timeout_s)
    if timeout is None or timeout <= 0.0:
        return "", [], _failure(ERROR_INVALID_TIMEOUT,
                                "timeout_s 必须是 > 0 的有限数值")

    if transport is not None:
        if isinstance(transport, dict):
            for name in ("post_json", "get_json", "download"):
                if not callable(transport.get(name)):
                    return "", [], _failure(
                        ERROR_INVALID_TRANSPORT,
                        f"transport 缺可调用函数 {name}()")
        else:
            for name in ("post_json", "get_json", "download"):
                if not callable(getattr(transport, name, None)):
                    return "", [], _failure(
                        ERROR_INVALID_TRANSPORT,
                        f"transport 缺少可调用方法 {name}()")

    normalized: List[Dict[str, Any]] = []
    seen: set = set()
    for index, item in enumerate(entries):
        entry, error = _entry_from_manifest(item, index, seen)
        if error is not None:
            return "", [], error
        normalized.append(entry)

    return absolute_dir, normalized, None


def generate_batch(
    entries: Any,
    *,
    out_dir: Any,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    transport: Any = None,
    allow_network: Optional[bool] = None,
    max_models: int = 200,
    poll_interval_s: float = 2.0,
    timeout_s: float = 300.0,
) -> Dict[str, Any]:
    """按清单逐条生成 3D 资产（**串行，不并发**）。

    串行是刻意的：Tripo 对并发任务数与积分扣减有限流，批量抢跑只会换来
    一堆 ``429``/额度浪费；串行还能让 ``results`` 的顺序与清单一致，便于
    明天对着清单核对。

    流程：

    1. **先做总校验**（输出目录、``max_models``、每条 name/prompt/model），
       任何一条非法就返回错误，一条都不生成；
    2. 逐条调用 :func:`generate_model`，输出 ``<out_dir>/<name>.glb``；
       单条失败**不打断**后续条目（不因一颗坏苹果中断整批预算）；
    3. 超出 ``max_models`` 的条目**不生成**，但在 ``results`` 里保留记录，
       标 ``error="skipped"`` 并计入 ``skipped``——截断如实标出，不静默。

    返回::

        {"ok": 全部成功才 True（有失败或有跳过即 False）,
         "results": [{"name", "prompt", "ok", "path"/"error", "reason"}...],
         "succeeded": n, "failed": n, "skipped": n}

    ``results`` 与输入条目一一对应（含被截断的条目）。
    """
    absolute_dir, normalized, error = _validate_batch(
        entries, out_dir, model, max_models, poll_interval_s, timeout_s,
        transport)
    if error is not None:
        return {"ok": False, "results": [], "succeeded": 0, "failed": 0,
                "skipped": 0, **error}

    results: List[Dict[str, Any]] = []
    succeeded = 0
    failed = 0
    skipped = 0
    for index, entry in enumerate(normalized):
        name = entry["name"]
        prompt = entry["prompt"]
        if index >= max_models:
            skipped += 1
            results.append({
                "name": name,
                "prompt": prompt,
                "ok": False,
                "error": ERROR_SKIPPED,
                "reason": f"跳过：超出 max_models={max_models} 上限，未生成",
            })
            continue

        result = generate_model(
            prompt,
            out_path=os.path.join(absolute_dir, f"{name}.glb"),
            api_key=api_key,
            model=entry["model"] or model.strip(),
            poll_interval_s=poll_interval_s,
            timeout_s=timeout_s,
            transport=transport,
            allow_network=allow_network,
        )
        item = {"name": name, "prompt": prompt, "ok": bool(result.get("ok"))}
        if item["ok"]:
            succeeded += 1
            item["path"] = result.get("path")
        else:
            failed += 1
            item["error"] = str(result.get("error") or "unknown")
            item["reason"] = str(result.get("reason") or "未知原因")
        results.append(item)

    return {
        "ok": failed == 0 and skipped == 0,
        "results": results,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
    }


__all__ = [
    "API_KEY_ENV",
    "BASE_URL",
    "DEFAULT_MODEL",
    "ERROR_INVALID_MANIFEST",
    "ERROR_INVALID_MAX_MODELS",
    "ERROR_INVALID_OUT_DIR",
    "ERROR_SKIPPED",
    "SUBMIT_URL",
    "TASK_URL_TEMPLATE",
    "UrllibTransport",
    "generate_batch",
    "generate_model",
    "load_manifest",
    "slugify_prompt",
    "unique_output_path",
]
