# SPDX-License-Identifier: MIT
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
import os
import time
import hashlib
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Callable, Iterable


# 常见模型的真实上下文窗口（参考各模型官方文档）
MODEL_CONTEXT_WINDOWS = {
    "Qwen3.6": 32768, "Qwen3.5": 32768, "Qwen3": 32768,
    "Qwen2.5": 32768, "Qwen2": 32768,
    "DeepSeek-V3": 128000, "DeepSeek-R1": 128000, "deepseek": 128000,
    "Llama-3.1": 128000, "Llama-3": 8192, "llama": 8192,
    "GLM-4": 128000, "glm": 32768,
    "gpt-4o": 128000, "gpt-4-turbo": 128000, "gpt-3.5": 16384,
}
DEFAULT_CONTEXT_WINDOW = 8192

# 压缩时为摘要消息预留的 token（硬上限里扣掉，避免「保留区刚好占满窗口、
# 再加一条摘要就出窗」）。实际摘要由模型生成，长度不受控，这里只是常见预留量；
# 小窗口按 5% 封顶，避免预留把「最近 N 轮」挤出窗口。
SUMMARY_RESERVE_TOKENS = 200


def summary_reserve_tokens(max_context_tokens: int) -> int:
    """摘要预留量：常见 200 token，小窗口按窗口 5% 收缩。"""
    return min(SUMMARY_RESERVE_TOKENS, max(0, int(max_context_tokens * 0.05)))


def get_model_context_window(model_name: str) -> int:
    """根据模型名返回上下文窗口大小；未知模型返回默认 8192。"""
    if not model_name:
        return DEFAULT_CONTEXT_WINDOW
    for pattern, window in MODEL_CONTEXT_WINDOWS.items():
        if pattern.lower() in model_name.lower():
            return window
    return DEFAULT_CONTEXT_WINDOW


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
5. 卫星轨道计算与天空模式视图：基于真实 TLE 数据与 sgp4 计算卫星过境与可见性。
6. 上下文记忆与连贯对话：记住当前频率、模式、信号状态与用户偏好，给出连贯操作建议。

【诚实原则——必须严格遵守】
- 没有接入真实硬件 / 传感器时，必须明确告知"未连接 / 无数据"，绝不把示例值、
  占位值或估算值当作真实数据展示给用户。
- 模拟模式下产生的数据必须明确标注"模拟"，不得与真实数据混淆。
- 不确定时说明不确定性，不编造结果、不伪造信号或测量数值。
- 你办不到的事（如调整物理天线指向）会明确指导用户手动操作。

【动手前先确认硬件现实】
- 在追踪卫星、调谐接收、驱动云台/旋转器等动手动作之前，先确认相关硬件现实；
  每类硬件只有三种状态：有 / 无 / 未知。
- 有 → 直接使用；无 → 明确说缺什么，并给出可行的替代方案（如手动转动天线、
  只做软件调谐）；未知 → 先用现有工具探测或自然语言问用户。
- 不得假设型号、不得假设设备已连接；不得为了"跑通流程"虚构硬件或用占位数据。
- 已在本会话确认过的硬件不要重复追问，除非用户改口；用户改口时以新回答覆盖。
- 能做的部分照做，不能做的部分如实说明，不要用一句"已完成"掩盖缺口。
- 问硬件时用自然语言对话，不要生成表单、不要按固定问卷逐项念题；用户一句话
  可以描述多件硬件，一次抽取即可。

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
        on_compaction: Optional[Callable[[str, Optional[str]], str]] = None,
        recent_turns_keep: int = 6,
        model_name: str = "",
    ):
        self.max_context_tokens = max_context_tokens
        self.compaction_threshold = compaction_threshold
        self.compaction_target_ratio = compaction_target_ratio
        self.system_prompt = system_prompt
        self.tool_output_max_chars = tool_output_max_chars
        self.on_compaction = on_compaction  # 压缩回调：(历史文本, 旧摘要) -> 新摘要
        self.recent_turns_keep = recent_turns_keep
        self.model_name = model_name
        # 天空模式硬件能力账本（由 Agent 挂入；None 表示尚未启用）
        self.capability_ledger = None

        # 模型感知上下文窗口：用户没显式改默认值时，按真实模型窗口覆盖
        if model_name and max_context_tokens == 8192:
            inferred = get_model_context_window(model_name)
            if inferred != 8192:
                self.max_context_tokens = inferred

        self.history: List[Dict[str, Any]] = []
        self.tool_definitions: List[Dict[str, Any]] = []
        self.epoch = 1
        self.compaction_count = 0
        self.last_compaction_time = None
        self._system_tokens_cache = None
        # P7.3：关键会话状态（Agent 在状态型工具成功后登记）。压缩时原样
        # 保留在摘要里，避免出现「压缩后不记得当前频率/模式却照常作答」。
        self.state_snapshot: Dict[str, Any] = {}
        self.state_meta: Dict[str, Dict[str, Any]] = {}
        self.last_compaction_report: Optional[Dict[str, Any]] = None

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
        self.state_snapshot = {}
        self.state_meta = {}
        self.epoch += 1

    # ── 关键状态（P7.3） ────────────────────────────────

    def set_state(self, key: str, value: Any, source: str = "") -> Any:
        """登记一条跨压缩保留的关键状态（如当前频率/模式/采样率）。

        值必须可 JSON 序列化或 repr 安全；只登记链路/设备真实生效的值，
        不要拿模型的口头承诺当状态。
        """
        key = str(key)
        prev = self.state_snapshot.get(key)
        self.state_snapshot[key] = value
        self.state_meta[key] = {
            "source": str(source),
            "updated_at": time.time(),
            "changed": prev != value,
        }
        return value

    def state_section(self) -> str:
        """把状态快照渲染成可注入摘要的文本（空快照返回空串）。"""
        if not self.state_snapshot:
            return ""
        parts = []
        for k in sorted(self.state_snapshot):
            v = self.state_snapshot[k]
            try:
                text = json.dumps(v, ensure_ascii=False)
            except Exception:
                text = repr(v)
            if len(text) > 200:
                text = text[:200] + "…"
            parts.append(f"{k}={text}")
        return "[会话状态（跨压缩保留）]\n" + "\n".join(parts)

    def state_health(self, required_keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """状态自检：哪些关键状态还在、哪些丢了、丢了该怎么重新获取。

        required_keys 省略时用已登记过的键集合。丢失时不假装记得——返回
        missing 与可直接展示给模型的 reacquire_hint。
        """
        required = list(required_keys) if required_keys is not None \
            else list(self.state_meta.keys())
        missing = [k for k in required if k not in self.state_snapshot]
        hint = ""
        if missing:
            hint = ("以下关键状态在压缩/故障后丢失：" + "、".join(missing)
                    + "。回答前必须先用对应工具重新读取真实值，不得凭上下文印象作答。")
        return {
            "tracked": sorted(self.state_snapshot),
            "missing": missing,
            "reacquire_hint": hint,
        }

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

    # 结构化摘要模板（参考 Kilo Code SUMMARY_TEMPLATE）
    SUMMARY_TEMPLATE = (
        "请把以下早期对话历史压缩成一份结构化摘要，保留对后续对话真正有用的信息。"
        "按以下小节输出：\n"
        "## 目标 (Objective)\n用户最初想达成什么。\n"
        "## 重要细节 (Important Details)\n"
        "关键的频率/模式/设置/设备状态/用户偏好/错误信息等事实。\n"
        "## 工作状态 (Work State)\n"
        "- 已完成 (Completed): ...\n"
        "- 进行中 (Active): ...\n"
        "- 受阻 (Blocked): ...\n"
        "## 下一步 (Next Move)\n下一步建议做什么。\n"
        "## 相关文件/资源 (Relevant Files)\n涉及的文件、设备、参数名。\n"
        "只输出摘要正文，不要复述本提示词。"
    )

    def needs_compaction(self) -> bool:
        """判断是否需要压缩。"""
        return self.get_stats().usage_ratio >= self.compaction_threshold

    def _find_existing_summary(self) -> Optional[str]:
        """
        检测历史开头是否已有压缩摘要消息。
        若有，提取其正文（去掉外层标记）作为 previous_summary 返回。
        """
        if not self.history:
            return None
        first = self.history[0]
        if first.get("role") == "system" and "上下文压缩摘要" in (first.get("content") or ""):
            content = first.get("content", "")
            # 去掉 "[上下文压缩摘要 epoch=N]\n" 前缀
            idx = content.find("\n")
            return content[idx + 1:] if idx != -1 else content
        return None

    def _select_recent_messages(self, available_for_history: int,
                                hard_limit_tokens: Optional[int] = None):
        """
        从后往前遍历历史，选出要原样保留的近期消息。

        约束（优先级从高到低）：
        1. **硬上限**：保留区 token 不得超过 hard_limit_tokens（默认 =
           窗口 - 系统开销）。这是防「单个用户轮次里连开几十轮工具、保留区
           越滚越大」的兜底，超了就停，哪怕还没凑够轮数。
        2. **轮数下限**：至少保留 recent_turns_keep 轮（一轮 = 一条 user
           消息及其后续 assistant 回复）。软预算内凑不够时，允许越过软预算
           继续向前，直到凑够或触到硬上限。
        3. **软预算**：available_for_history（压缩目标）。轮数已够且下一
           条更旧的消息会超软预算时停。
        4. 不割裂工具调用链：若保留了 role=tool 消息，必须连带保留其前面
           带 tool_calls 的 assistant 消息（此修正可能让保留区略超硬上限，
           最多一条 assistant 消息——链完整性优先，否则请求本身非法）。

        返回 (kept, removed) 两个消息列表。
        """
        if hard_limit_tokens is None:
            hard_limit_tokens = max(
                0, self.max_context_tokens - self.get_system_tokens()
                - summary_reserve_tokens(self.max_context_tokens))
        min_turns = max(1, int(self.recent_turns_keep))
        kept: List[Dict[str, Any]] = []
        current_tokens = 0
        user_turns_seen = 0
        n = len(self.history)

        for i in range(n - 1, -1, -1):
            msg = self.history[i]
            msg_tokens = estimate_message_tokens(msg)
            is_user = msg.get("role") == "user"

            if kept:
                if current_tokens + msg_tokens > hard_limit_tokens:
                    break
                if (current_tokens + msg_tokens > available_for_history
                        and user_turns_seen >= min_turns):
                    break

            kept.insert(0, msg)
            current_tokens += msg_tokens
            if is_user:
                user_turns_seen += 1

        # 工具链完整性修正：若保留区间最旧的一条是 tool 消息，
        # 向前扩展直到把对应的 assistant(tool_calls=...) 一起保留。
        while kept and kept[0].get("role") == "tool":
            # 找到 kept 在原 history 中的起始位置
            try:
                start_idx = self.history.index(kept[0])
            except ValueError:
                break
            if start_idx <= 0:
                break
            prev = self.history[start_idx - 1]
            # 前一条是带 tool_calls 的 assistant：必须一起保留
            if prev.get("role") == "assistant" and prev.get("tool_calls"):
                kept.insert(0, prev)
            else:
                break

        removed = self.history[:len(self.history) - len(kept)]
        return kept, removed

    def compact(self, custom_summary: str = None) -> bool:
        """
        执行上下文压缩（参考 Kilo Code select() + anchored summary 模式）。

        策略：
        1. 目标历史 token 预算 = max_context_tokens * compaction_target_ratio - system_tokens
        2. 从后往前选保留边界（至少保留 recent_turns_keep 轮）
        3. 被裁剪的 head 送 LLM 摘要；若已有旧摘要则传入让 LLM 合并（anchored summary）
        4. recent 部分原样保留，工具结果消息完整不截断
        5. 压缩后历史 = [结构化摘要 system 消息] + 保留的最近消息

        返回是否执行了压缩。
        """
        if not self.needs_compaction() and custom_summary is None:
            return False

        # 目标历史 token 预算（软预算）与窗口硬上限
        target_tokens = int(self.max_context_tokens * self.compaction_target_ratio)
        system_tokens = self.get_system_tokens()
        available_for_history = max(0, target_tokens - system_tokens)
        hard_limit_tokens = max(
            0, self.max_context_tokens - system_tokens
            - summary_reserve_tokens(self.max_context_tokens))

        # 检测已有摘要（anchored summary：合并而非重写）
        previous_summary = self._find_existing_summary()

        # 选保留区间
        kept, removed = self._select_recent_messages(
            available_for_history, hard_limit_tokens=hard_limit_tokens)
        kept_tokens = sum(estimate_message_tokens(m) for m in kept)
        recent_turns_kept = sum(1 for m in kept if m.get("role") == "user")

        # 生成摘要
        summary = custom_summary
        if summary is None and removed:
            removed_text = self._messages_to_text(removed)
            if self.on_compaction:
                try:
                    summary = self.on_compaction(removed_text, previous_summary)
                except TypeError:
                    # 旧回调只接受一个参数
                    try:
                        summary = self.on_compaction(removed_text)
                    except Exception as e:
                        summary = f"[历史摘要生成失败: {e}] 已裁剪 {len(removed)} 条消息"
                except Exception as e:
                    summary = f"[历史摘要生成失败: {e}] 已裁剪 {len(removed)} 条消息"
            if summary is None:
                summary = (f"[上下文压缩] 已裁剪 {len(removed)} 条早期消息，"
                           f"保留最近 {len(kept)} 条。")

        # 重建历史：摘要 + 保留的消息
        # P7.3：关键状态快照无条件拼进摘要（不依赖摘要模型是否记得），
        # 这样即使摘要生成失败/失真，频率、模式等状态也不会丢。
        state_text = self.state_section()
        if state_text:
            summary = (f"{summary}\n\n{state_text}" if summary else state_text)
        new_history: List[Dict[str, Any]] = []
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
        self.last_compaction_report = {
            "epoch": self.epoch - 1,
            "removed_messages": len(removed),
            "kept_messages": len(kept),
            "target_tokens": target_tokens,
            "available_for_history": available_for_history,
            "hard_limit_tokens": hard_limit_tokens,
            "kept_tokens": kept_tokens,
            "recent_turns_kept": recent_turns_kept,
            "recent_turns_required": max(1, int(self.recent_turns_keep)),
            # 软预算被越过（为满足轮数下限）时如实标记，便于诊断解释
            "kept_over_soft_budget": kept_tokens > available_for_history,
            "summary_chars": len(summary or ""),
            "state_preserved": sorted(self.state_snapshot),
            "state_missing": self.state_health()["missing"],
            "system_tokens": system_tokens,
            "history_tokens": self.get_history_tokens(),
        }
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
        system_content = self.system_prompt
        if self.capability_ledger is not None:
            try:
                from .sky_negotiation import build_capability_system_section
                system_content = (system_content + "\n\n"
                                  + build_capability_system_section(
                                      self.capability_ledger))
            except Exception:  # noqa: BLE001 —— 账本文案失败不得阻断对话
                system_content = self.system_prompt
        messages = [{"role": "system", "content": system_content}]
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

    def export_messages(self) -> List[Dict[str, Any]]:
        """返回可序列化的消息列表（去掉 _truncated 等内部字段）。"""
        out: List[Dict[str, Any]] = []
        for m in self.history:
            clean: Dict[str, Any] = {}
            for k, v in m.items():
                if k.startswith("_"):
                    continue
                clean[k] = v
            out.append(clean)
        return out

    def save_to_file(self, path: str) -> str:
        """将 history + epoch + compaction_count 存为 JSON，返回绝对路径。"""
        path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {
            "epoch": self.epoch,
            "compaction_count": self.compaction_count,
            "last_compaction_time": self.last_compaction_time,
            "max_context_tokens": self.max_context_tokens,
            "history": self.export_messages(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def load_from_file(self, path: str) -> int:
        """从 JSON 恢复 history + epoch + compaction_count，恢复的消息条数。"""
        path = os.path.expanduser(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.epoch = data.get("epoch", self.epoch)
        self.compaction_count = data.get("compaction_count", 0)
        self.last_compaction_time = data.get("last_compaction_time")
        self.history = data.get("history", [])
        return len(self.history)

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
