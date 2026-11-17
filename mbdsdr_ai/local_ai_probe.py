# SPDX-License-Identifier: MIT
"""本地 LLM 端点体检：把「连不上」变成一句能核对的结论。

为什么单独做
============

本机没有 DeepSeek 权重、也没有常驻推理服务（见
``docs/learn/2026-11-17-local-ai-inventory.md``）。在此之前，用户切到
本地 ``base_url`` 只会看到一条通用异常或一个空模型列表，分不清是：

* 服务没启动（端口没人听）；
* 服务在、但没加载模型（``/v1/models`` 返回空）；
* 模型在、但名字不是当前配置的那个；
* 认证或权限不对；
* 本仓处于离线模式，根本没探测。

本模块只做一次**浅探测**（GET ``/v1/models``），不生成、不加载、不提权。
结论必须区分上面几种情况——「不可用」三个字不够，用户修不了。

设计口径
--------
* **不猜可用性**：探测失败就报失败，不拿内置模型列表冒充线上可用。
* **离线优先**：``MBDSDR_OFFLINE`` 生效时直接返回 ``offline``，不发起连接。
* **可注入**：``http_get`` 参数让测试无网络覆盖全部分支。
* 状态集合在 :data:`STATUSES` 里列全，避免调用方猜字符串。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "STATUSES",
    "DEFAULT_PROBE_TIMEOUT_S",
    "probe_endpoint",
    "format_probe",
]

#: 体检结论。前三态是用户最常遇到的「不能用」；后两态是诚实扩展。
STATUSES = (
    "offline",       # 本仓离线模式，未探测
    "not_running",   # 端点没监听 / 拒绝连接
    "unauthorized",  # 服务在，但 401/403
    "no_models",     # 服务在，模型列表为空
    "ready",         # 服务在且有模型
    "error",         # 其他失败（响应不是预期 JSON 等）
)

DEFAULT_PROBE_TIMEOUT_S = 2.0

#: ``http_get(url, headers, timeout) -> dict``。默认实现用 requests。
HttpGet = Callable[[str, Dict[str, str], float], Any]


def _default_http_get(url: str, headers: Dict[str, str], timeout: float) -> Any:
    import requests

    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _model_present(want: str, ids: List[str]) -> Optional[bool]:
    if not want:
        return None
    if want in ids:
        return True
    # Ollama / vLLM 常见写法：deepseek-r1:7b、deepseek-ai/DeepSeek-R1。
    # 只接受「tag 或命名空间」后缀，不做模糊包含匹配——包含匹配会把
    # 「deepseek-r1-distill」误判成「deepseek-r1」。
    return any(i.startswith(want + ":") or i.startswith(want + "/")
               for i in ids)


def probe_endpoint(
    base_url: str,
    api_key: str = "",
    model: str = "",
    timeout: float = DEFAULT_PROBE_TIMEOUT_S,
    http_get: Optional[HttpGet] = None,
) -> Dict[str, Any]:
    """探测一个 OpenAI 兼容端点，返回可核对的状态。

    ``base_url`` 应带版本段（如 ``http://127.0.0.1:11434/v1``），
    与 :class:`mbdsdr_ai.model_manager.ModelManager` 的口径一致。
    """
    base = (base_url or "").rstrip("/")
    result: Dict[str, Any] = {
        "base_url": base,
        "wanted_model": model or "",
        "checked_at_unix": time.time(),
        "status": "error",
        "reachable": False,
        "models": [],
        "model_present": None,
        "detail": "",
    }

    from .net_policy import network_disabled

    if network_disabled():
        result.update(
            status="offline", reachable=False,
            detail="离线模式（MBDSDR_OFFLINE）：未探测端点，结论不是「服务坏了」。")
        return result

    if not base:
        result.update(status="error", detail="base_url 为空，无法探测。")
        return result

    get = http_get or _default_http_get
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{base}/models"

    try:
        payload = get(url, headers, timeout)
    except Exception as e:  # 分类见下；不把异常栈丢给用户
        import requests

        if isinstance(e, requests.exceptions.HTTPError):
            status = getattr(getattr(e, "response", None), "status_code", 0)
            if status in (401, 403):
                result.update(
                    status="unauthorized", reachable=True,
                    detail=f"端点返回 HTTP {status}：服务在，但密钥或权限不对。")
            else:
                result.update(
                    status="error", reachable=True,
                    detail=f"端点返回 HTTP {status or '?'}，探测失败。")
        elif isinstance(e, (requests.exceptions.ConnectionError,
                            requests.exceptions.Timeout)):
            result.update(
                status="not_running", reachable=False,
                detail=(f"端点未响应（{type(e).__name__}）："
                        f"服务可能没启动，或地址/端口不对。"))
        else:
            result.update(
                status="error", reachable=False,
                detail=f"探测异常（{type(e).__name__}: {e}）。")
        return result

    items = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        result.update(
            status="error", reachable=True,
            detail="响应里没有 data 数组：这可能不是 OpenAI 兼容端点。")
        return result

    ids = [str(it.get("id", "")) for it in items
           if isinstance(it, dict) and it.get("id")]
    result.update(reachable=True, models=ids)

    if not ids:
        result.update(
            status="no_models",
            detail="服务已启动，但 /v1/models 返回空列表：没有加载任何模型。")
        return result

    present = _model_present(model, ids)
    if present is False:
        result.update(
            status="ready", model_present=False,
            detail=(f"服务可用（{len(ids)} 个模型），但 {model} 不在列表；"
                    f"可能是名字写错或还没 pull。"))
    elif present is True:
        result.update(
            status="ready", model_present=True,
            detail=f"可用：模型列表含 {model}。")
    else:
        result.update(
            status="ready", model_present=None,
            detail=f"服务可用（{len(ids)} 个模型）；未指定要检查的模型。")
    return result


_STATUS_ZH = {
    "offline": "离线模式，未探测",
    "not_running": "端点未启动",
    "unauthorized": "认证失败",
    "no_models": "服务在，但没有模型",
    "ready": "可用",
    "error": "探测失败",
}


def format_probe(result: Dict[str, Any]) -> str:
    """一行结论 + 一句细节，可直接进状态栏或诊断报告。"""
    head = _STATUS_ZH.get(str(result.get("status")), "未知状态")
    detail = str(result.get("detail") or "").strip()
    return f"本地模型端点：{head}" + (f"——{detail}" if detail else "")
