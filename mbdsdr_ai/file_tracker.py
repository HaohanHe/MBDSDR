"""
MBDSDR AI 内核 - 文件变更跟踪器
================================
FileChangeTracker：跟踪所有文件变更，记录变更历史，支持回滚和审计。

对照白皮书第四章 4.6.6 文件变更跟踪器。

核心概念：
- FileChange：单次文件变更记录（修改前/修改后/变更类型/原因）
- ChangeTracker：变更跟踪器，记录所有变更，支持查询和回滚

与守护者（Guardian）的区别：
- Guardian：在修改前自动备份，失败自动回滚（防御性）
- FileChangeTracker：记录所有变更历史，支持审计和任意回滚（审计性）

用途：
- 自进化时记录所有代码变更
- 审计 AI 做了哪些修改
- 回滚到任意历史版本
- 生成变更日志（changelog）
"""

import json
import time
import os
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from enum import Enum


class ChangeType(str, Enum):
    """变更类型。"""
    CREATE = "create"  # 新建文件
    MODIFY = "modify"  # 修改文件
    DELETE = "delete"  # 删除文件
    RENAME = "rename"  # 重命名文件
    REVERT = "revert"  # 回滚到历史版本


@dataclass
class FileChange:
    """单次文件变更记录。"""
    change_id: str
    file_path: str  # 文件路径
    change_type: ChangeType  # 变更类型
    content_before: str = ""  # 修改前内容（文本文件）
    content_after: str = ""  # 修改后内容
    hash_before: str = ""  # 修改前哈希
    hash_after: str = ""  # 修改后哈希
    reason: str = ""  # 变更原因
    actor: str = "agent"  # 变更执行者（agent/user/system）
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    reverted: bool = False  # 是否已被回滚
    reverted_to: str = ""  # 回滚到哪个 change_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "change_id": self.change_id,
            "file_path": self.file_path,
            "change_type": self.change_type.value,
            "hash_before": self.hash_before,
            "hash_after": self.hash_after,
            "reason": self.reason,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "reverted": self.reverted,
            "reverted_to": self.reverted_to,
            # 不保存完整内容（太大），只保存哈希和元数据
        }


class FileChangeTracker:
    """
    文件变更跟踪器。

    记录所有文件变更，支持查询、审计、回滚。
    """

    def __init__(self, storage_dir: str = None, max_content_size: int = 100000):
        if storage_dir is None:
            storage_dir = os.path.expanduser("~/.mbdsdr/file_changes")
        self.storage_dir = storage_dir
        self.max_content_size = max_content_size  # 最大保存内容大小（字节）
        os.makedirs(storage_dir, exist_ok=True)

        self.changes: Dict[str, FileChange] = {}  # change_id -> change
        self.file_changes: Dict[str, List[str]] = {}  # file_path -> [change_id...]
        self._counter = 0

        # 加载历史变更
        self._load_changes()

    def track_change(
        self,
        file_path: str,
        change_type: ChangeType,
        content_before: str = "",
        content_after: str = "",
        reason: str = "",
        actor: str = "agent",
        metadata: Dict[str, Any] = None,
    ) -> FileChange:
        """
        记录一次文件变更。

        这是跟踪器的核心方法：每次文件变更后调用。
        """
        self._counter += 1
        change_id = f"chg_{int(time.time() * 1000)}_{self._counter}"

        # 计算哈希
        hash_before = self._compute_hash(content_before) if content_before else ""
        hash_after = self._compute_hash(content_after) if content_after else ""

        change = FileChange(
            change_id=change_id,
            file_path=file_path,
            change_type=change_type,
            content_before=content_before if len(content_before) < self.max_content_size else "",
            content_after=content_after if len(content_after) < self.max_content_size else "",
            hash_before=hash_before,
            hash_after=hash_after,
            reason=reason,
            actor=actor,
            metadata=metadata or {},
        )

        self.changes[change_id] = change
        if file_path not in self.file_changes:
            self.file_changes[file_path] = []
        self.file_changes[file_path].append(change_id)

        # 保存到文件
        self._save_change(change)

        return change

    def track_modify(
        self,
        file_path: str,
        content_before: str,
        content_after: str,
        reason: str = "",
        actor: str = "agent",
    ) -> FileChange:
        """记录文件修改。"""
        return self.track_change(
            file_path=file_path,
            change_type=ChangeType.MODIFY,
            content_before=content_before,
            content_after=content_after,
            reason=reason,
            actor=actor,
        )

    def track_create(
        self,
        file_path: str,
        content: str,
        reason: str = "",
        actor: str = "agent",
    ) -> FileChange:
        """记录文件创建。"""
        return self.track_change(
            file_path=file_path,
            change_type=ChangeType.CREATE,
            content_after=content,
            reason=reason,
            actor=actor,
        )

    def track_delete(
        self,
        file_path: str,
        content_before: str,
        reason: str = "",
        actor: str = "agent",
    ) -> FileChange:
        """记录文件删除。"""
        return self.track_change(
            file_path=file_path,
            change_type=ChangeType.DELETE,
            content_before=content_before,
            reason=reason,
            actor=actor,
        )

    def revert_to(self, change_id: str, file_writer=None) -> Optional[FileChange]:
        """
        回滚到某个变更之前的状态。

        file_writer: (file_path, content) -> None，用于实际写入文件
        返回回滚变更记录。
        """
        change = self.changes.get(change_id)
        if not change:
            return None

        # 找到该文件在 change_id 之前的最后一个变更
        file_history = self.file_changes.get(change.file_path, [])
        target_content = change.content_before

        # 标记为已回滚
        change.reverted = True

        # 创建回滚变更记录
        revert_change = self.track_change(
            file_path=change.file_path,
            change_type=ChangeType.REVERT,
            content_before=change.content_after,
            content_after=target_content,
            reason=f"回滚到 {change_id} 之前的状态",
            actor="system",
            metadata={"reverted_from": change_id},
        )
        change.reverted_to = revert_change.change_id

        # 实际写入文件
        if file_writer and target_content:
            file_writer(change.file_path, target_content)

        return revert_change

    def get_file_history(self, file_path: str, limit: int = 50) -> List[Dict[str, Any]]:
        """获取某个文件的变更历史。"""
        change_ids = self.file_changes.get(file_path, [])
        result = []
        for cid in reversed(change_ids[-limit:]):
            change = self.changes.get(cid)
            if change:
                result.append(change.to_dict())
        return result

    def get_change(self, change_id: str) -> Optional[FileChange]:
        return self.changes.get(change_id)

    def list_recent_changes(self, limit: int = 50, actor: str = None) -> List[Dict[str, Any]]:
        """列出最近的变更。"""
        all_changes = sorted(self.changes.values(), key=lambda c: c.timestamp, reverse=True)
        if actor:
            all_changes = [c for c in all_changes if c.actor == actor]
        return [c.to_dict() for c in all_changes[:limit]]

    def get_stats(self) -> Dict[str, Any]:
        """获取跟踪器统计信息。"""
        total_changes = len(self.changes)
        total_files = len(self.file_changes)
        by_type = {}
        by_actor = {}
        for change in self.changes.values():
            by_type[change.change_type.value] = by_type.get(change.change_type.value, 0) + 1
            by_actor[change.actor] = by_actor.get(change.actor, 0) + 1

        return {
            "total_changes": total_changes,
            "total_files_tracked": total_files,
            "changes_by_type": by_type,
            "changes_by_actor": by_actor,
            "reverted_changes": sum(1 for c in self.changes.values() if c.reverted),
            "storage_dir": self.storage_dir,
        }

    def generate_changelog(self, since_timestamp: float = 0) -> str:
        """生成变更日志（changelog）。"""
        changes = [
            c for c in self.changes.values()
            if c.timestamp >= since_timestamp and c.change_type != ChangeType.REVERT
        ]
        changes.sort(key=lambda c: c.timestamp, reverse=True)

        lines = ["# 变更日志", ""]
        for change in changes:
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(change.timestamp))
            lines.append(f"## [{time_str}] {change.change_type.value}: {change.file_path}")
            if change.reason:
                lines.append(f"- 原因: {change.reason}")
            lines.append(f"- 执行者: {change.actor}")
            lines.append(f"- 哈希: {change.hash_before[:8]} -> {change.hash_after[:8]}")
            lines.append("")

        return "\n".join(lines)

    def _compute_hash(self, content: str) -> str:
        """计算内容的 SHA256 哈希。"""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def _save_change(self, change: FileChange):
        """保存变更到文件。"""
        # 按日期分目录
        date_str = time.strftime("%Y-%m-%d", time.localtime(change.timestamp))
        day_dir = os.path.join(self.storage_dir, date_str)
        os.makedirs(day_dir, exist_ok=True)

        path = os.path.join(day_dir, f"{change.change_id}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(change.to_dict(), f, ensure_ascii=False, indent=2)

    def _load_changes(self):
        """加载历史变更（只加载元数据，不加载完整内容）。"""
        if not os.path.exists(self.storage_dir):
            return
        for date_dir in os.listdir(self.storage_dir):
            full_dir = os.path.join(self.storage_dir, date_dir)
            if not os.path.isdir(full_dir):
                continue
            for filename in os.listdir(full_dir):
                if not filename.endswith('.json'):
                    continue
                path = os.path.join(full_dir, filename)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    change = FileChange(
                        change_id=data["change_id"],
                        file_path=data["file_path"],
                        change_type=ChangeType(data["change_type"]),
                        hash_before=data.get("hash_before", ""),
                        hash_after=data.get("hash_after", ""),
                        reason=data.get("reason", ""),
                        actor=data.get("actor", "agent"),
                        timestamp=data.get("timestamp", 0),
                        metadata=data.get("metadata", {}),
                        reverted=data.get("reverted", False),
                        reverted_to=data.get("reverted_to", ""),
                    )
                    self.changes[change.change_id] = change
                    if change.file_path not in self.file_changes:
                        self.file_changes[change.file_path] = []
                    self.file_changes[change.file_path].append(change.change_id)
                    self._counter = max(self._counter, int(change.change_id.split('_')[-1]))
                except Exception:
                    pass
