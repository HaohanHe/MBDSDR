# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 记忆系统
==========================
管理会话记忆、长期记忆、索引检索。

核心能力：
- 会话记忆：当前对话历史（由 ContextManager 管理）
- 长期记忆：跨会话的用户偏好、常用频率、常用设置
- 记忆索引：对历史对话/文件建立索引，快速检索
- 记忆写入：模型可以主动写入记忆
- 记忆检索：根据当前上下文检索相关记忆
- 记忆遗忘：过期/低重要性记忆自动清理
"""

import json
import logging
import time
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from .corrupt_evidence import write_reason

logger = logging.getLogger(__name__)


def _backup_corrupt_memory(path: str) -> Optional[str]:
    """把损坏的记忆文件改名成 ``*.corrupt-时间戳``，返回备份路径（失败 None）。

    改名保字节、不删除；失败时调用方拒绝写盘，避免空记忆覆盖唯一证据。
    """
    backup = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(path, backup)
    except OSError as exc:
        logger.error("记忆文件损坏备份失败：%s（%s）", path, exc)
        return None
    return backup


@dataclass
class Memory:
    """一条记忆。"""
    id: str
    content: str
    category: str = "general"  # general, preference, frequency, setting, fact, task
    importance: float = 0.5    # 0.0 - 1.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
            "metadata": self.metadata,
            "tags": self.tags,
        }


class MemoryStore:
    """
    记忆存储。

    管理长期记忆，支持写入、检索、遗忘。
    持久化到 JSON 文件。
    """

    def __init__(self, storage_path: str = "~/.mbdsdr/memory.json"):
        self.storage_path = os.path.expanduser(storage_path)
        self.memories: Dict[str, Memory] = {}
        self._next_id = 1
        #: 上次读取失败的人话说明（None = 正常）。目前**无任何界面消费**：
        #: 备份后用户仍看不到损坏，接线等 main_window.py 那一笔。
        self.last_error: Optional[str] = None
        #: 损坏文件的备份路径（None = 未损坏或备份失败）。
        self.corrupt_backup: Optional[str] = None
        #: 读失败/损坏且备份失败时拒绝写盘：add/update/forget 都会 _save，
        #: 一旦放行就会在下一次记忆写入时把空记忆写成唯一版本。
        self._save_blocked = False
        self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """从文件加载记忆：缺失给空态；损坏先备份；读不了不写。

        三种情况必须分开：
          * 文件不存在 —— 首次启动正常路径，空记忆，不算错误；
          * 文件损坏（解析失败 / 结构非法 / 条目缺字段）—— 备份原件 +
            人话 last_error；备份失败则置 _save_blocked，拒绝覆盖；
          * OSError（被占用 / 是目录 / 无权限）—— 不是损坏，不备份、
            不写入，同样置 block，避免临时读失败把文件当空记忆覆盖。
        """
        if not os.path.exists(self.storage_path):
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except OSError as exc:
            self.last_error = (
                f"记忆文件读取失败（{exc.__class__.__name__}）：{self.storage_path}；"
                "本次不写入，避免覆盖可能仍可恢复的文件")
            self._save_blocked = True
            logger.warning("记忆文件读取失败（非损坏，不备份）：%s", exc)
            return
        except ValueError as exc:
            self._mark_corrupt(f"解析失败（{exc.__class__.__name__}: {exc}）")
            return
        try:
            if not isinstance(data, dict):
                raise ValueError("根节点不是对象")
            items = data.get("memories", [])
            if not isinstance(items, list):
                raise ValueError("memories 不是列表")
            loaded: Dict[str, Memory] = {}
            for item in items:
                mem = Memory(**item)
                loaded[mem.id] = mem
            next_id = int(data.get("next_id", len(loaded) + 1))
        except (TypeError, KeyError, ValueError) as exc:
            self._mark_corrupt(f"条目非法（{exc.__class__.__name__}: {exc}）")
            return
        self.memories = loaded
        self._next_id = next_id
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False

    def _mark_corrupt(self, reason: str) -> None:
        """损坏：先备份原件再留人话错误；备份失败则禁止后续写盘。"""
        self.corrupt_backup = _backup_corrupt_memory(self.storage_path)
        self._save_blocked = self.corrupt_backup is None
        message = (
            f"记忆文件损坏（{reason}）：{self.storage_path}；"
            f"原文件已备份为 {self.corrupt_backup or '（备份失败）'}，"
            "本次按空记忆启动")
        if self.corrupt_backup:
            write_reason(self.corrupt_backup, message, logger)
        self.last_error = message
        logger.warning("记忆文件不可用（%s）：%s；备份=%s", self.storage_path,
                       reason, self.corrupt_backup or "（备份失败）")

    def _save(self) -> bool:
        """保存记忆到文件。读失败/损坏未备份时拒绝写盘。"""
        if self._save_blocked:
            self.last_error = (
                f"记忆未保存：{self.storage_path} 读取失败或已损坏且无法备份；"
                "为避免覆盖唯一证据，本次不写入")
            logger.error("记忆写入被拒绝（文件不可用且未备份）：%s", self.storage_path)
            return False
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        data = {
            "next_id": self._next_id,
            "memories": [m.to_dict() for m in self.memories.values()],
        }
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True

    # ── 写入 ────────────────────────────────────────────

    def add(
        self,
        content: str,
        category: str = "general",
        importance: float = 0.5,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
    ) -> Memory:
        """添加一条记忆。如果内容相似则更新。"""
        # 检查是否已有相似记忆（同 category + 内容包含关键词）
        existing = self._find_similar(content, category)
        if existing:
            existing.content = content
            existing.importance = max(existing.importance, importance)
            existing.updated_at = time.time()
            if tags:
                existing.tags = list(set(existing.tags + tags))
            if metadata:
                existing.metadata.update(metadata)
            self._save()
            return existing

        mem_id = f"mem_{self._next_id}"
        self._next_id += 1
        mem = Memory(
            id=mem_id,
            content=content,
            category=category,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
        )
        self.memories[mem_id] = mem
        self._save()
        return mem

    def _find_similar(self, content: str, category: str) -> Optional[Memory]:
        """查找相似记忆（简单关键词匹配）。"""
        content_words = set(content.lower().split())
        for mem in self.memories.values():
            if mem.category != category:
                continue
            mem_words = set(mem.content.lower().split())
            if content_words and mem_words:
                overlap = len(content_words & mem_words) / max(1, len(content_words | mem_words))
                if overlap > 0.5:
                    return mem
        return None

    def update(self, mem_id: str, **kwargs) -> Optional[Memory]:
        """更新记忆。"""
        if mem_id in self.memories:
            mem = self.memories[mem_id]
            for k, v in kwargs.items():
                if hasattr(mem, k):
                    setattr(mem, k, v)
            mem.updated_at = time.time()
            self._save()
            return mem
        return None

    def delete(self, mem_id: str) -> bool:
        """删除记忆。"""
        if mem_id in self.memories:
            del self.memories[mem_id]
            self._save()
            return True
        return False

    # ── 检索 ────────────────────────────────────────────

    def search(
        self,
        query: str,
        category: str = None,
        limit: int = 5,
        min_importance: float = 0.0,
    ) -> List[Memory]:
        """
        搜索相关记忆。

        简单评分：关键词匹配 + 重要性 + 最近访问。
        """
        query_words = set(query.lower().split())
        scored = []

        for mem in self.memories.values():
            if category and mem.category != category:
                continue
            if mem.importance < min_importance:
                continue

            # 关键词匹配分
            mem_words = set(mem.content.lower().split()) | set(mem.tags)
            if query_words and mem_words:
                keyword_score = len(query_words & mem_words) / max(1, len(query_words))
            else:
                keyword_score = 0.0

            # 重要性分
            importance_score = mem.importance * 0.3

            # 最近访问分（7天内满分，越久越低）
            days_since_access = (time.time() - mem.last_accessed) / 86400
            recency_score = max(0, 1.0 - days_since_access / 30) * 0.2

            total_score = keyword_score * 0.5 + importance_score + recency_score

            if total_score > 0 or not query_words:
                scored.append((total_score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [m for _, m in scored[:limit]]

        # 更新访问计数
        for mem in results:
            mem.access_count += 1
            mem.last_accessed = time.time()
        if results:
            self._save()

        return results

    def get_by_category(self, category: str) -> List[Memory]:
        """按类别获取记忆。"""
        return [m for m in self.memories.values() if m.category == category]

    def get_all(self) -> List[Memory]:
        """获取所有记忆。"""
        return list(self.memories.values())

    # ── 遗忘 ────────────────────────────────────────────

    def forget_old(self, max_age_days: int = 90, min_importance: float = 0.3) -> int:
        """
        遗忘过期且低重要性的记忆。
        返回删除的记忆数。
        """
        cutoff = time.time() - max_age_days * 86400
        to_delete = []
        for mem_id, mem in self.memories.items():
            if mem.updated_at < cutoff and mem.importance < min_importance and mem.access_count < 3:
                to_delete.append(mem_id)
        for mem_id in to_delete:
            del self.memories[mem_id]
        if to_delete:
            self._save()
        return len(to_delete)

    # ── 构建系统提示词 ──────────────────────────────────

    def build_memory_context(self, query: str, limit: int = 3) -> str:
        """
        构建记忆上下文文本（插入到系统提示词中）。
        """
        relevant = self.search(query, limit=limit)
        if not relevant:
            return ""

        lines = ["\n## 相关记忆（长期记忆）"]
        for mem in relevant:
            category_label = {
                "preference": "偏好",
                "frequency": "常用频率",
                "setting": "设置",
                "fact": "事实",
                "task": "任务",
                "general": "通用",
            }.get(mem.category, mem.category)
            lines.append(f"- [{category_label}] {mem.content}")
        return "\n".join(lines)

    # ── 统计 ────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """获取记忆统计。"""
        by_category = {}
        for mem in self.memories.values():
            by_category[mem.category] = by_category.get(mem.category, 0) + 1
        return {
            "total": len(self.memories),
            "by_category": by_category,
            "storage_path": self.storage_path,
        }

    def get_status_text(self) -> str:
        """获取人类可读的记忆状态。"""
        stats = self.get_stats()
        lines = [
            "=== MBDSDR 记忆系统 ===",
            f"总记忆数: {stats['total']}",
            f"存储路径: {stats['storage_path']}",
        ]
        for cat, count in sorted(stats["by_category"].items()):
            lines.append(f"  [{cat}] {count} 条")
        return "\n".join(lines)
