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
import time
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


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
        self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """从文件加载记忆。"""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data.get("memories", []):
                    mem = Memory(**item)
                    self.memories[mem.id] = mem
                self._next_id = data.get("next_id", len(self.memories) + 1)
            except Exception as e:
                print(f"警告: 记忆文件加载失败: {e}")

    def _save(self):
        """保存记忆到文件。"""
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        data = {
            "next_id": self._next_id,
            "memories": [m.to_dict() for m in self.memories.values()],
        }
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

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
