# SPDX-License-Identifier: MIT
"""结构化人机问答（HITL question/answer）——Python 扩展域「人机问答」。

来源（2026-10-14 kilocode 审计 Top-3）：kilocode 的 question 服务
（opencode/src/question/index.ts 多问题+选项+阻塞+dismiss）。MBDSDR 的
stdio 传输是请求-响应式，服务端不能阻塞等回答（会锁死协议循环），因此
取 request-response 友善形态：

    ask_question   -> 立即返回 question_pending 信封（不阻塞提问方）
    answer_question-> 人类/编排方把答案写进 store
    question_status-> 轮询单个/全部未决问题

要点：
- 会话内存态：重启即丢 pending（如实文档化；kilocode 的 SSE 断线恢复是
  后续候选，不假装有持久化）；
- 答案默认必须命中选项之一（防幻觉自由发挥）；确需自由文本须显式
  free_text=True（信封里标注）；
- 重复 answer 同一问题诚实报错（already_answered），除非 override=True；
- ask/answer 属于对话层动作，不碰 DSP/音频/硬件，因此不走 arm_manual
  门（门是硬件写安全，不是流程安全）——此判断写进工具 description。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class QuestionError(Exception):
    """问答 store 的诚实错误（消息可直接进错误信封）。"""


@dataclass
class Question:
    question_id: str
    question: str
    options: List[str]
    context: str = ""
    status: str = "pending"     # pending / answered
    answer: Optional[str] = None
    free_text: bool = False
    created_at: float = field(default_factory=time.time)
    answered_at: Optional[float] = None

    def to_dict(self, include_answer: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "question_id": self.question_id,
            "question": self.question,
            "options": list(self.options),
            "status": self.status,
            "created_at": self.created_at,
        }
        if self.context:
            out["context"] = self.context
        if include_answer and self.status == "answered":
            out["answer"] = self.answer
            out["answered_at"] = self.answered_at
            out["free_text"] = self.free_text
        return out


class QuestionStore:
    """会话内存问答 store（线程安全；无持久化，重启即丢）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: Dict[str, Question] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return "q%d" % self._counter

    def ask(self, question: str, options: List[str],
            context: str = "", question_id: str = "") -> Question:
        q = (question or "").strip()
        if not q:
            raise QuestionError("question 不能为空")
        opts = [str(o) for o in (options or [])]
        if len(opts) < 2:
            raise QuestionError(
                "options 至少 2 个（单选项不是提问，是通知）；收到 %d 个"
                % len(opts))
        if len(set(opts)) != len(opts):
            raise QuestionError("options 有重复项")
        with self._lock:
            if question_id:
                if question_id in self._items:
                    raise QuestionError(
                        "question_id 已存在：%s（换一个 id 或先 answer）"
                        % question_id)
                qid = question_id
            else:
                qid = self._next_id()
            item = Question(question_id=qid, question=q, options=opts,
                            context=(context or "").strip())
            self._items[qid] = item
            return item

    def answer(self, question_id: str, answer: str,
               free_text: bool = False, override: bool = False) -> Question:
        with self._lock:
            item = self._items.get(question_id)
            if item is None:
                raise QuestionError("未知 question_id：%s" % question_id)
            if item.status == "answered" and not override:
                raise QuestionError(
                    "该问题已回答过（%s）；确需改答案带 override=true"
                    % question_id)
            ans = "" if answer is None else str(answer)
            if free_text:
                if not ans.strip():
                    raise QuestionError("free_text=true 时 answer 不能为空")
            elif ans not in item.options:
                raise QuestionError(
                    "answer 必须命中选项之一 %s；确需自由文本带 free_text=true"
                    "（信封会标注）。收到 %r" % (item.options, ans))
            item.answer = ans
            item.free_text = free_text
            item.status = "answered"
            item.answered_at = time.time()
            return item

    def get(self, question_id: str) -> Question:
        with self._lock:
            item = self._items.get(question_id)
            if item is None:
                raise QuestionError("未知 question_id：%s" % question_id)
            return item

    def list(self, status: str = "") -> List[Question]:
        with self._lock:
            items = list(self._items.values())
        if status:
            items = [i for i in items if i.status == status]
        return items

    def pending_envelopes(self) -> List[Dict[str, Any]]:
        """未决问题的信封列表（供 agent 在任意工具结果里附带提醒）。"""
        return [i.to_dict(include_answer=False)
                for i in self.list(status="pending")]
