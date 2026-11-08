# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 会话事件日志（append-only JSONL）
====================================================
参照 DeepSeek Harness 的 session log：**"Model-visible ⟺ logged"** 不变量——
凡是进了模型上下文的东西（用户消息 / 助手消息 / 工具调用 / 工具结果 / 压缩），
都在此追加一条事件；事件只追加、不改写、不删除，可离线重放与审计。

与 :mod:`conversation`（整份 JSON 快照，用于加载/续聊）互补：
- ConversationStore = 当前状态快照（可覆盖）
- SessionLog        = 不可变事件流（只追加，含时序 / 轮次 / 耗时 / 错误码）

设计红线：
- 写日志绝不能拖垮主循环——任何 IO/序列化异常都吞掉，chat 照常返回。
- daemon 友好：每次 emit 即 flush，进程崩溃也不丢已发生的事件。
"""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

SESSION_DIR = os.path.expanduser("~/.mbdsdr/sessions")

# 事件类型常量（模型可见的东西都要有对应事件）
EV_TURN_START = "turn_start"
EV_USER_MESSAGE = "user_message"
EV_ASSISTANT_MESSAGE = "assistant_message"
EV_TOOL_CALL = "tool_call"
EV_TOOL_RESULT = "tool_result"
EV_COMPACTION = "compaction"
EV_ERROR = "error"
EV_TURN_END = "turn_end"


class SessionLog:
    """单个会话的 append-only 事件日志。

    每条事件是一行 JSON：
    ``{"ts": <float>, "seq": <int>, "conv": <id>, "type": <event>, ...fields}``

    ``seq`` 单调递增，可用于检测丢行；``ts`` 为 wall-clock（time.time()）。
    """

    def __init__(self, conversation_id: str, directory: str = SESSION_DIR,
                 enabled: bool = True):
        self.conversation_id = conversation_id
        self.enabled = enabled
        self.directory = os.path.expanduser(directory)
        self._lock = threading.Lock()
        self._seq = 0
        self._path: Optional[str] = None
        self._broken = False  # 一旦写失败就停手，别反复尝试拖慢主循环
        if self.enabled:
            try:
                os.makedirs(self.directory, exist_ok=True)
                self._path = os.path.join(
                    self.directory, f"{conversation_id}.jsonl")
            except Exception:
                self._broken = True

    @property
    def path(self) -> Optional[str]:
        return self._path

    def emit(self, event_type: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """追加一条事件。返回写入的事件 dict（失败/禁用返回 None）。

        绝不抛异常：任何错误就地吞掉并标记 ``_broken``，后续 emit 变 no-op。
        """
        if not self.enabled or self._broken or not self._path:
            return None
        with self._lock:
            self._seq += 1
            event = {
                "ts": time.time(),
                "seq": self._seq,
                "conv": self.conversation_id,
                "type": event_type,
            }
            event.update(fields)
            try:
                line = json.dumps(event, ensure_ascii=False, default=str)
            except Exception:
                self._broken = True
                return None
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
            except Exception:
                self._broken = True
                return None
            return event

    def read_events(self) -> List[Dict[str, Any]]:
        """读回全部事件（重放/审计/测试用）。文件缺失返回空表。"""
        if not self._path or not os.path.exists(self._path):
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue  # 半截行（崩溃时）跳过，不影响其余
        except Exception:
            return out
        return out

    def event_types(self) -> List[str]:
        """已记录事件的类型序列（便于断言不变量）。"""
        return [e.get("type", "") for e in self.read_events()]
