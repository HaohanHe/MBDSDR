"""
MBDSDR AI 内核 - 模型管理器
============================
管理 LLM 模型的列表查询、切换、参数设置、API 调用。

核心能力：
- 模型列表查询（GET /v1/models）
- 模型切换（弱模型/强模型）
- 模型参数设置（temperature, top_p, max_tokens）
- Chat Completions API 调用（支持 tool calling）
- 多 provider 支持（硅基流动、OpenAI 兼容接口）
- 错误处理、重试、超时
- 调用统计（token 用量、调用次数、延迟）
"""

import json
import time
import requests
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple


@dataclass
class ModelInfo:
    """模型信息。"""
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = ""
    description: str = ""
    context_window: int = 0
    is_weak: bool = False  # 弱模型标记（用于工具调用兼容性测试）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "owned_by": self.owned_by,
            "description": self.description,
            "context_window": self.context_window,
            "is_weak": self.is_weak,
        }


@dataclass
class CallStats:
    """API 调用统计。"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_latency_ms: float = 0.0
    last_call_time: Optional[float] = None
    last_latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "avg_latency_ms": round(self.total_latency_ms / max(1, self.successful_calls), 1),
            "last_latency_ms": self.last_latency_ms,
        }


# 内置模型列表（API 不可用时的 fallback）
BUILTIN_MODELS = [
    ModelInfo(id="Qwen/Qwen3.6-35B-A3B", owned_by="Qwen", description="通义千问 3.6 35B (MoE A3B 激活)", context_window=131072, is_weak=False),
    ModelInfo(id="Qwen/Qwen3.5-4B", owned_by="Qwen", description="通义千问 3.5 4B 轻量版（弱模型测试用）", context_window=32768, is_weak=True),
    ModelInfo(id="Qwen/Qwen3.5-9B", owned_by="Qwen", description="通义千问 3.5 9B", context_window=32768, is_weak=True),
    ModelInfo(id="deepseek-ai/DeepSeek-V3", owned_by="DeepSeek", description="深度求索 V3", context_window=131072, is_weak=False),
    ModelInfo(id="deepseek-ai/DeepSeek-R1", owned_by="DeepSeek", description="深度求索 R1 推理模型", context_window=131072, is_weak=False),
]


class ModelManager:
    """
    模型管理器。

    管理 LLM API 调用、模型列表、参数设置。
    兼容 OpenAI API 格式（硅基流动、OpenAI、本地 vLLM 等）。
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.siliconflow.cn/v1",
        model: str = "Qwen/Qwen3.6-35B-A3B",
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_output_tokens: int = 2048,
        timeout: int = 60,
        max_retries: int = 2,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.max_retries = max_retries

        self.available_models: List[ModelInfo] = list(BUILTIN_MODELS)
        self.stats = CallStats()
        self._session = requests.Session()
        self._last_error = None

    # ── 模型列表查询 ────────────────────────────────────

    def fetch_models(self, force_refresh: bool = False) -> List[ModelInfo]:
        """
        从 API 查询可用模型列表（GET /v1/models）。
        如果 API 不可用，返回内置列表。
        """
        if not force_refresh and self.available_models and len(self.available_models) > len(BUILTIN_MODELS):
            return self.available_models

        if not self.api_key:
            self._last_error = "api_key 为空，使用内置模型列表"
            return self.available_models

        try:
            url = f"{self.base_url}/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            resp = self._session.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()

            models = []
            for item in data.get("data", []):
                mid = item.get("id", "")
                models.append(ModelInfo(
                    id=mid,
                    object=item.get("object", "model"),
                    created=item.get("created", 0),
                    owned_by=item.get("owned_by", ""),
                    description=item.get("description", ""),
                    context_window=item.get("context_window", 0),
                    is_weak=self._is_weak_model(mid),
                ))

            if models:
                self.available_models = models
            return self.available_models

        except Exception as e:
            self._last_error = f"模型列表查询失败: {e}"
            return self.available_models

    def _is_weak_model(self, model_id: str) -> bool:
        """判断是否为弱模型（用于工具调用兼容性测试）。"""
        weak_keywords = ["1.5B", "1b", "0.5B", "tiny", "small", "7B"]
        return any(kw.lower() in model_id.lower() for kw in weak_keywords)

    def list_models(self) -> List[Dict[str, Any]]:
        """返回模型列表（字典格式，用于展示）。"""
        return [m.to_dict() for m in self.available_models]

    def get_model_info(self, model_id: str = None) -> Optional[ModelInfo]:
        """获取指定模型的信息。"""
        mid = model_id or self.model
        for m in self.available_models:
            if m.id == mid:
                return m
        return ModelInfo(id=mid, description="未知模型（未在列表中）")

    # ── 模型切换 ────────────────────────────────────────

    def switch_model(self, model_id: str) -> Tuple[bool, str]:
        """
        切换当前模型。
        返回 (成功, 消息)。
        """
        # 检查是否在可用列表中
        found = any(m.id == model_id for m in self.available_models)
        if not found:
            # 不在列表中也允许切换（可能是新模型），但给出警告
            self.available_models.append(ModelInfo(id=model_id, description="用户指定模型"))

        old_model = self.model
        self.model = model_id
        return True, f"模型已切换: {old_model} -> {model_id}"

    def set_params(self, **kwargs):
        """设置模型参数。"""
        if "temperature" in kwargs:
            self.temperature = max(0.0, min(2.0, kwargs["temperature"]))
        if "top_p" in kwargs:
            self.top_p = max(0.0, min(1.0, kwargs["top_p"]))
        if "max_output_tokens" in kwargs:
            self.max_output_tokens = max(1, kwargs["max_output_tokens"])
        if "timeout" in kwargs:
            self.timeout = max(1, kwargs["timeout"])

    # ── API 调用 ────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] = None,
        tool_choice: str = "auto",
        temperature: float = None,
        max_tokens: int = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """
        调用 Chat Completions API。

        返回标准响应字典：
        {
            "success": bool,
            "content": str,
            "tool_calls": list,
            "usage": {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int},
            "model": str,
            "latency_ms": float,
            "error": str (if failed),
        }
        """
        start_time = time.time()
        self.stats.total_calls += 1

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "top_p": self.top_p,
            "max_tokens": max_tokens if max_tokens is not None else self.max_output_tokens,
            "stream": stream,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        # 重试循环
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.post(url, headers=headers, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()

                latency_ms = (time.time() - start_time) * 1000
                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})

                result = {
                    "success": True,
                    "content": message.get("content", "") or "",
                    "tool_calls": message.get("tool_calls", []),
                    "usage": data.get("usage", {}),
                    "model": data.get("model", self.model),
                    "latency_ms": round(latency_ms, 1),
                    "finish_reason": choice.get("finish_reason", ""),
                }

                # 更新统计
                self.stats.successful_calls += 1
                self.stats.total_prompt_tokens += result["usage"].get("prompt_tokens", 0)
                self.stats.total_completion_tokens += result["usage"].get("completion_tokens", 0)
                self.stats.total_latency_ms += latency_ms
                self.stats.last_call_time = time.time()
                self.stats.last_latency_ms = latency_ms

                return result

            except requests.exceptions.HTTPError as e:
                last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                if e.response.status_code in (429, 500, 502, 503, 504):
                    # 可重试错误
                    if attempt < self.max_retries:
                        time.sleep(1 * (attempt + 1))
                        continue
            except requests.exceptions.Timeout:
                last_error = f"请求超时 ({self.timeout}s)"
                if attempt < self.max_retries:
                    time.sleep(1)
                    continue
            except requests.exceptions.ConnectionError as e:
                last_error = f"连接失败: {e}"
                if attempt < self.max_retries:
                    time.sleep(1)
                    continue
            except Exception as e:
                last_error = f"未知错误: {type(e).__name__}: {e}"
                break

        # 所有重试失败
        latency_ms = (time.time() - start_time) * 1000
        self.stats.failed_calls += 1
        self._last_error = last_error
        return {
            "success": False,
            "content": "",
            "tool_calls": [],
            "usage": {},
            "model": self.model,
            "latency_ms": round(latency_ms, 1),
            "error": last_error or "未知错误",
        }

    # ── 工具调用辅助 ────────────────────────────────────

    def extract_tool_calls(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 API 响应中提取工具调用。"""
        if not response.get("success"):
            return []
        return response.get("tool_calls", [])

    def parse_tool_call_args(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """解析工具调用的参数（处理 JSON 解析错误）。"""
        fn = tool_call.get("function", {})
        args_str = fn.get("arguments", "{}")
        try:
            return json.loads(args_str)
        except json.JSONDecodeError:
            # 尝试修复常见的 JSON 错误
            try:
                fixed = args_str.replace("'", '"').replace("True", "true").replace("False", "false")
                return json.loads(fixed)
            except Exception:
                return {"_raw": args_str, "_parse_error": True}

    # ── 状态查询 ────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """获取模型管理器状态。"""
        return {
            "current_model": self.model,
            "base_url": self.base_url,
            "api_key_set": bool(self.api_key),
            "api_key_masked": self.api_key[:8] + "..." + self.api_key[-4:] if self.api_key else "(未设置)",
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_output_tokens": self.max_output_tokens,
            "timeout": self.timeout,
            "available_models_count": len(self.available_models),
            "stats": self.stats.to_dict(),
            "last_error": self._last_error,
        }

    def get_status_text(self) -> str:
        """获取人类可读的状态文本。"""
        s = self.get_status()
        lines = [
            "=== MBDSDR 模型管理器 ===",
            f"当前模型: {s['current_model']}",
            f"API: {s['base_url']}",
            f"API Key: {s['api_key_masked']}",
            f"参数: temperature={s['temperature']}, top_p={s['top_p']}, max_tokens={s['max_output_tokens']}",
            f"可用模型: {s['available_models_count']} 个",
            f"调用统计: 成功 {s['stats']['successful_calls']}/{s['stats']['total_calls']}, "
            f"平均延迟 {s['stats']['avg_latency_ms']}ms",
        ]
        if s["last_error"]:
            lines.append(f"上次错误: {s['last_error']}")
        return "\n".join(lines)
