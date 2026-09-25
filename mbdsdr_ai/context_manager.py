"""
MBDSDR AI 内核 - 上下文管理器
==============================
对照 Kilo Code 的 Context Epoch / System Context / Session History 设计。

核心能力：
- token 估算（中文 ~1.5 token/字，英文 ~4 字符/token）
- 上下文长度可设置（max_context_tokens）
- 上下文压缩（compaction）：使用率超阈值时自动总结历史
- 上下文查询：当前用量、剩余空间、消息数、epoch 信息
- Context Epoch：压缩时开启新 epoch，保留基线
- 系统提示词管理
- 工具输出大小限制（大输出截断/写文件）
"""

import json
import time
import hashlib
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Callable


# 系统提示词（MBDSDR AI 定义无线电）
SYSTEM_PROMPT = """你是 MBDSDR（AI 定义无线电 / AI-Defined Radio）智能体。操作者的业余无线电呼号由用户在配置中提供；未提供时不要假设、不要自称任何具体呼号。

【我是谁】
MBDSDR 全称 Multi-Band Defined Software Defined Radio（多频段 AI 定义软件无线电）。
你的核心是"AI 定义无线电"：用 AI 驱动无线电的接收、分析、解调与决策——不仅是听广播，
而是让 AI 像一名操作员一样看懂射频频谱、识别信号、解调内容并给出操作建议。
当用户问"你是什么智能体 / 你是谁 / 你能做什么"时，请直接据此回答你的身份、呼号与核心能力。

【核心能力】
1. SDR 接收与信号分析：多频段调谐、扫频、制式识别、信号解调、基带录制。
2. 北斗 / GNSS 定位与时空感知：接入真实 GNSS 数据，提供位置、时间与方位参考；
   未接入 GNSS 设备时必须如实告知"未连接 / 无定位数据"。
3. 九轴 IMU 姿态感知：接入真实 IMU，提供设备姿态与天线指向参考；
   未接入 IMU 时必须如实告知"未连接 / 无姿态数据"。
4. 工具调用：通过 MCP 协议控制 SDR 硬件与传感器，调用前先用 list_tools 发现可用工具。
5. 卫星轨道计算与射频天空视图：基于真实 TLE 数据与 sgp4 计算卫星过境与可见性。
6. 上下文记忆与连贯对话：记住当前频率、模式、信号状态与用户偏好，给出连贯操作建议。

【诚实原则——必须严格遵守】
- 没有接入真实硬件 / 传感器时，必须明确告知"未连接 / 无数据"，绝不把示例值、
  占位值或估算值当作真实数据展示给用户。
- 模拟模式下产生的数据必须明确标注"模拟"，不得与真实数据混淆。
- 不确定时说明不确定性，不编造结果、不伪造信号或测量数值。
- 你办不到的事（如调整物理天线指向）会明确指导用户手动操作。

【交互风格】
- 回复简洁专业，无线电术语准确。
- 人是中心，你是辅助；复杂操作先确认再执行。
- 调用工具前先用 list_tools 确认工具可用；工具输出过大时主动摘要，不堆砌原始数据。
- 回复中不使用 emoji 表情符号，使用纯文字表达。

【当前硬件】
你通过工具查询当前连接的 SDR 硬件与传感器实际状态；不要假设具体型号或频段范围。
若用户询问设备信息而尚无可用数据，请说明"尚未获取到设备信息，请通过工具查询或确认硬件已连接"。
"""


@dataclass
class ContextStats:
    """上下文统计信息。"""
    total_tokens: int = 0
    system_tokens: int = 0
    history_tokens: int = 0
    remaining_tokens: int = 0
    usage_ratio: float = 0.0
    message_count: int = 0
    epoch: int = 0
    compaction_count: int = 0
    last_compaction_time: Optional[float] = None
    is_near_limit: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "system_tokens": self.system_tokens,
            "history_tokens": self.history_tokens,
            "remaining_tokens": self.remaining_tokens,
            "usage_ratio": round(self.usage_ratio, 4),
            "message_count": self.message_count,
            "epoch": self.epoch,
            "compaction_count": self.compaction_count,
            "last_compaction_time": self.last_compaction_time,
            "is_near_limit": self.is_near_limit,
        }


_ENC = None
_ENC_FAILED = False


def _get_encoder():
    """惰性加载 tiktoken 真分词器；不可用时返回 None（回退启发式）。"""
    global _ENC, _ENC_FAILED
    if _ENC is not None:
        return _ENC
    if _ENC_FAILED:
        return None
    try:
        import tiktoken
        _ENC = tiktoken.get_encoding("cl100k_base")
        return _ENC
    except Exception:
        _ENC_FAILED = True
        return None


def estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数。

    优先用 tiktoken 真分词器（cl100k_base，贴近主流 LLM 真实 token 计数）；
    tiktoken 不可用时回退启发式（CJK ~1.5/字，英文 ~4 字符/token）。
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    cjk = 0
    other = 0
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff' or '\uac00' <= ch <= '\ud7af':
            cjk += 1
        else:
            other += 1
    return int(cjk * 1.5 + other / 4) + 1


def estimate_message_tokens(msg: Dict[str, Any]) -> int:
    """估算一条消息的 token 数。"""
    tokens = 4  # 每条消息的固定开销（role 等）
    content = msg.get("content", "")
    if isinstance(content, str):
        tokens += estimate_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                tokens += estimate_tokens(part.get("text", ""))
    # tool_calls
    if "tool_calls" in msg:
        for tc in msg["tool_calls"]:
            tokens += estimate_tokens(json.dumps(tc, ensure_ascii=False))
    if "tool_call_id" in msg:
        tokens += 10
    return tokens


class ContextManager:
    """
    上下文管理器。

    管理 System Context（系统提示词+工具定义）和 Session History（对话历史）。
    支持 Context Epoch、自动压缩、token 查询。
    """

    def __init__(
        self,
        max_context_tokens: int = 8192,
        compaction_threshold: float = 0.8,
        compaction_target_ratio: float = 0.5,
        system_prompt: str = SYSTEM_PROMPT,
        tool_output_max_chars: int = 4000,
        on_compaction: Optional[Callable[[str], str]] = None,
    ):
        self.max_context_tokens = max_context_tokens
        self.compaction_threshold = compaction_threshold
        self.compaction_target_ratio = compaction_target_ratio
        self.system_prompt = system_prompt
        self.tool_output_max_chars = tool_output_max_chars
        self.on_compaction = on_compaction  # 压缩回调：传入历史摘要需求，返回摘要

        self.history: List[Dict[str, Any]] = []
        self.tool_definitions: List[Dict[str, Any]] = []
        self.epoch = 1
        self.compaction_count = 0
        self.last_compaction_time = None
        self._system_tokens_cache = None

    # ── 系统上下文 ──────────────────────────────────────

    def set_system_prompt(self, prompt: str):
        """设置系统提示词。"""
        self.system_prompt = prompt
        self._system_tokens_cache = None

    def set_tool_definitions(self, tools: List[Dict[str, Any]]):
        """设置工具定义（用于 system context）。"""
        self.tool_definitions = tools
        self._system_tokens_cache = None

    def get_system_tokens(self) -> int:
        """获取系统上下文 token 数（提示词 + 工具定义）。"""
        if self._system_tokens_cache is None:
            tokens = estimate_tokens(self.system_prompt)
            if self.tool_definitions:
                tokens += estimate_tokens(json.dumps(self.tool_definitions, ensure_ascii=False))
            self._system_tokens_cache = tokens + 20  # 固定开销
        return self._system_tokens_cache

    # ── 历史管理 ────────────────────────────────────────

    def add_message(self, role: str, content: str, **kwargs) -> Dict[str, Any]:
        """添加一条消息到历史。"""
        msg = {"role": role, "content": content}
        msg.update(kwargs)
        self.history.append(msg)
        return msg

    def add_user_message(self, content: str) -> Dict[str, Any]:
        return self.add_message("user", content)

    def add_assistant_message(self, content: str, tool_calls=None) -> Dict[str, Any]:
        kwargs = {}
        if tool_calls:
            kwargs["tool_calls"] = tool_calls
        return self.add_message("assistant", content, **kwargs)

    def add_tool_message(self, tool_call_id: str, content: str, tool_name: str = "") -> Dict[str, Any]:
        """添加工具结果消息，自动截断过大输出。"""
        truncated = False
        if len(content) > self.tool_output_max_chars:
            content = content[:self.tool_output_max_chars] + f"\n... [输出已截断，原长度 {len(content)} 字符]"
            truncated = True
        msg = self.add_message("tool", content, tool_call_id=tool_call_id, name=tool_name)
        msg["_truncated"] = truncated
        return msg

    def clear_history(self):
        """清空对话历史（开启新 epoch）。"""
        self.history = []
        self.epoch += 1

    # ── Token 统计 ──────────────────────────────────────

    def get_history_tokens(self) -> int:
        """获取历史消息 token 总数。"""
        return sum(estimate_message_tokens(m) for m in self.history)

    def get_total_tokens(self) -> int:
        """获取当前总 token 数（系统 + 历史）。"""
        return self.get_system_tokens() + self.get_history_tokens()

    def get_remaining_tokens(self) -> int:
        """获取剩余可用 token 数。"""
        return max(0, self.max_context_tokens - self.get_total_tokens())

    def get_stats(self) -> ContextStats:
        """获取完整上下文统计。"""
        total = self.get_total_tokens()
        system = self.get_system_tokens()
        history = self.get_history_tokens()
        remaining = max(0, self.max_context_tokens - total)
        ratio = total / self.max_context_tokens if self.max_context_tokens > 0 else 0
        return ContextStats(
            total_tokens=total,
            system_tokens=system,
            history_tokens=history,
            remaining_tokens=remaining,
            usage_ratio=ratio,
            message_count=len(self.history),
            epoch=self.epoch,
            compaction_count=self.compaction_count,
            last_compaction_time=self.last_compaction_time,
            is_near_limit=ratio >= self.compaction_threshold,
        )

    # ── 压缩（Compaction）───────────────────────────────

    def needs_compaction(self) -> bool:
        """判断是否需要压缩。"""
        return self.get_stats().usage_ratio >= self.compaction_threshold

    def compact(self, custom_summary: str = None) -> bool:
        """
        执行上下文压缩。

        策略：
        1. 保留最近 N 条消息（目标使用率 compaction_target_ratio）
        2. 用 LLM 总结被裁剪的历史（如果有 on_compaction 回调）
        3. 在历史开头插入摘要消息
        4. 开启新 epoch

        返回是否执行了压缩。
        """
        if not self.needs_compaction() and custom_summary is None:
            return False

        # 计算需要保留多少条消息才能达到目标使用率
        target_tokens = int(self.max_context_tokens * self.compaction_target_ratio)
        system_tokens = self.get_system_tokens()
        available_for_history = max(0, target_tokens - system_tokens)

        # 从后往前累加，找到保留边界
        kept = []
        current_tokens = 0
        for msg in reversed(self.history):
            msg_tokens = estimate_message_tokens(msg)
            if current_tokens + msg_tokens > available_for_history and kept:
                break
            kept.insert(0, msg)
            current_tokens += msg_tokens

        # 被裁剪的部分
        removed = self.history[:len(self.history) - len(kept)]

        # 生成摘要
        summary = custom_summary
        if summary is None and removed and self.on_compaction:
            removed_text = self._messages_to_text(removed)
            try:
                summary = self.on_compaction(removed_text)
            except Exception as e:
                summary = f"[历史摘要生成失败: {e}] 已裁剪 {len(removed)} 条消息"

        if summary is None and removed:
            summary = f"[上下文压缩] 已裁剪 {len(removed)} 条早期消息，保留最近 {len(kept)} 条。"

        # 重建历史：摘要 + 保留的消息
        new_history = []
        if summary:
            new_history.append({
                "role": "system",
                "content": f"[上下文压缩摘要 epoch={self.epoch}]\n{summary}",
            })
        new_history.extend(kept)

        self.history = new_history
        self.epoch += 1
        self.compaction_count += 1
        self.last_compaction_time = time.time()
        return True

    def _messages_to_text(self, messages: List[Dict[str, Any]]) -> str:
        """将消息列表转为文本（用于压缩摘要）。"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            lines.append(f"[{role}]: {content[:500]}")
            if "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    lines.append(f"  [tool_call: {fn.get('name', '')}({fn.get('arguments', '')[:200]})]")
        return "\n".join(lines)

    # ── 构建 API 请求消息 ───────────────────────────────

    def build_api_messages(self) -> List[Dict[str, Any]]:
        """构建发送给 LLM API 的消息列表（只保留 OpenAI/硅基流动允许字段）。"""
        messages = [{"role": "system", "content": self.system_prompt}]
        for m in self.history:
            role = m.get("role", "user")
            clean: Dict[str, Any] = {"role": role,
                                     "content": m.get("content", "") or ""}
            if role == "assistant" and m.get("tool_calls"):
                clean["tool_calls"] = m["tool_calls"]
            if role == "tool":
                tcid = m.get("tool_call_id", "")
                if tcid:
                    clean["tool_call_id"] = tcid
                if m.get("name"):
                    clean["name"] = m["name"]
            elif m.get("name"):
                clean["name"] = m["name"]
            messages.append(clean)
        return messages

    # ── 导出/导入 ───────────────────────────────────────

    def export_state(self) -> Dict[str, Any]:
        """导出上下文状态（用于持久化/调试）。"""
        return {
            "epoch": self.epoch,
            "compaction_count": self.compaction_count,
            "last_compaction_time": self.last_compaction_time,
            "max_context_tokens": self.max_context_tokens,
            "stats": self.get_stats().to_dict(),
            "history_length": len(self.history),
            "history_hash": hashlib.md5(
                json.dumps(self.history, ensure_ascii=False).encode()
            ).hexdigest()[:8],
        }

    def get_status_text(self) -> str:
        """获取人类可读的上下文状态文本。"""
        stats = self.get_stats()
        lines = [
            "=== MBDSDR 上下文状态 ===",
            f"Epoch: {stats.epoch} | 压缩次数: {stats.compaction_count}",
            f"总 Token: {stats.total_tokens} / {self.max_context_tokens} "
            f"({stats.usage_ratio:.1%})",
            f"  系统: {stats.system_tokens} | 历史: {stats.history_tokens} "
            f"({stats.message_count} 条)",
            f"剩余: {stats.remaining_tokens} tokens",
        ]
        if stats.is_near_limit:
            lines.append("⚠ 接近上限，下次回复后将自动压缩")
        if stats.last_compaction_time:
            from datetime import datetime
            t = datetime.fromtimestamp(stats.last_compaction_time).strftime("%H:%M:%S")
            lines.append(f"上次压缩: {t}")
        return "\n".join(lines)
