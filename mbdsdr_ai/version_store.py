# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 版本存储
==========================
轻量级 git-like 版本存储，用于自进化的版本管理和一键恢复。

核心能力：
- 快照（snapshot）：保存当前状态
- 提交（commit）：记录修改
- 回滚（rollback）：恢复到任意历史版本
- 差异（diff）：比较两个版本的差异
- 分支（branch）：实验性修改在分支中进行
- 防幻觉变砖：任何修改前自动快照，失败自动回滚
"""

import hashlib
import json
import logging
import os
import time

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

from .corrupt_evidence import write_reason

logger = logging.getLogger(__name__)


def backup_corrupt_versions(path: str) -> Optional[str]:
    """把损坏的版本索引改名成 ``*.corrupt-时间戳``，返回备份路径（失败 None）。

    这是损坏留证的**公共接缝**：测试模拟「备份失败」时替换本函数返回
    ``None``，不再改模块私有名。
    """
    backup = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(path, backup)
    except OSError as exc:
        logger.error("版本索引损坏备份失败：%s（%s）", path, exc)
        return None
    return backup


@dataclass
class Version:
    """一个版本快照。"""
    id: str
    timestamp: float
    message: str
    parent_id: Optional[str]
    files: Dict[str, str]  # filename -> content hash
    author: str = "ai"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "message": self.message,
            "parent_id": self.parent_id,
            "files": self.files,
            "author": self.author,
            "metadata": self.metadata,
        }


class VersionStore:
    """
    轻量级版本存储。

    目录结构：
        store_path/
        ├── versions.json       # 版本元数据
        ├── objects/            # 内容寻址存储（hash -> content）
        └── HEAD                # 当前版本指针
    """

    def __init__(self, store_path: str = "~/.mbdsdr/evolution"):
        self.store_path = os.path.expanduser(store_path)
        self.objects_path = os.path.join(self.store_path, "objects")
        self.versions_file = os.path.join(self.store_path, "versions.json")
        self.head_file = os.path.join(self.store_path, "HEAD")

        os.makedirs(self.objects_path, exist_ok=True)
        self._versions: Dict[str, Version] = {}
        self._head: Optional[str] = None
        #: 上次读取失败的人话说明（None = 正常）。目前没有独立界面消费，
        #: 完整提示接线等 main_window.py 那一笔。
        self.last_error: Optional[str] = None
        #: 损坏索引的备份路径（None = 未损坏或备份失败）。
        self.corrupt_backup: Optional[str] = None
        #: 读失败/损坏且备份失败时拒绝写盘：snapshot 会 _save，一旦放行
        #: 就会把「无版本」写成唯一索引。
        self._save_blocked = False
        self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """加载版本数据：缺失 = 空库；损坏先备份；读不了不写。

        注意：``objects/`` 只有「内容 hash → 内容」，没有文件名、版本分组、
        时间戳与父链。索引一旦丢失，这些对象文件无法反推出属于哪个版本、
        叫什么名字——所以这里必须保住原件、禁止用空索引覆盖。
        """
        if not os.path.exists(self.versions_file):
            return
        try:
            with open(self.versions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except OSError as exc:
            self.last_error = (
                f"版本索引读取失败（{exc.__class__.__name__}）："
                f"{self.versions_file}；本次不写入，避免覆盖可能仍可恢复的索引")
            self._save_blocked = True
            logger.warning("版本索引读取失败（非损坏，不备份）：%s", exc)
            return
        except ValueError as exc:
            self._mark_corrupt(f"解析失败（{exc.__class__.__name__}: {exc}）")
            return
        try:
            if not isinstance(data, dict):
                raise ValueError("根节点不是对象")
            items = data.get("versions", [])
            if not isinstance(items, list):
                raise ValueError("versions 不是列表")
            loaded: Dict[str, Version] = {}
            for item in items:
                if not isinstance(item, dict):
                    raise TypeError("版本条目不是对象")
                version = Version(**item)
                loaded[version.id] = version
            head = data.get("head")
            if head is not None and head not in loaded:
                raise ValueError(f"head 指向不存在的版本 {head}")
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            self._mark_corrupt(f"条目非法（{exc.__class__.__name__}: {exc}）")
            return
        # 全部解析成功才提交：中途失败不留半截（部分版本 + head=None）
        self._versions = loaded
        self._head = head
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False

    def _object_count(self) -> int:
        """objects/ 里现有对象文件数（只用于错误文本，读不了按 0 计）。"""
        try:
            return len(os.listdir(self.objects_path))
        except OSError:
            return 0

    def _mark_corrupt(self, reason: str) -> None:
        """损坏：先备份原件再留人话错误；备份失败则禁止后续写盘。"""
        self.corrupt_backup = backup_corrupt_versions(self.versions_file)
        objects_count = self._object_count()
        objects_note = (
            f"磁盘上的对象文件还在 objects/（{objects_count} 个），"
            "但索引里没有「版本 → 文件」映射，无法从对象目录重建，"
            "已无法通过界面找回")
        if self.corrupt_backup:
            self.last_error = (
                f"版本索引损坏（{reason}）：{self.versions_file}；"
                f"原件已备份为 {self.corrupt_backup}，本次按无版本启动。"
                + objects_note)
            write_reason(self.corrupt_backup, self.last_error, logger)
        else:
            self.last_error = (
                f"版本索引损坏（{reason}）：{self.versions_file}；"
                "备份失败，本次不写入，避免覆盖唯一证据。" + objects_note)
        self._save_blocked = self.corrupt_backup is None
        logger.error(self.last_error)

    def _save(self) -> bool:
        """保存版本数据。读失败/损坏未备份时拒绝写盘。"""
        if self._save_blocked:
            self.last_error = (
                f"版本索引未保存：{self.versions_file} 读取失败或已损坏且无法备份；"
                "为避免覆盖唯一证据，本次不写入")
            logger.error("版本索引写入被拒绝（文件不可用且未备份）：%s",
                         self.versions_file)
            return False
        data = {
            "head": self._head,
            "versions": [v.to_dict() for v in self._versions.values()],
        }
        with open(self.versions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True

    # ── 内容寻址存储 ────────────────────────────────────

    def _hash_content(self, content: str) -> str:
        """计算内容的 SHA256 哈希。"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _store_object(self, content: str) -> str:
        """存储内容，返回哈希。"""
        h = self._hash_content(content)
        obj_path = os.path.join(self.objects_path, h)
        if not os.path.exists(obj_path):
            with open(obj_path, "w", encoding="utf-8") as f:
                f.write(content)
        return h

    def _get_object(self, h: str) -> Optional[str]:
        """根据哈希获取内容。"""
        obj_path = os.path.join(self.objects_path, h)
        if os.path.exists(obj_path):
            with open(obj_path, "r", encoding="utf-8") as f:
                return f.read()
        return None

    # ── 快照与提交 ──────────────────────────────────────

    def snapshot(self, message: str, files: Dict[str, str], author: str = "ai",
                 metadata: Dict[str, Any] = None) -> Version:
        """
        创建快照（提交一个版本）。

        files: {filename: content}
        返回创建的 Version。

        索引读不了且无法备份时拒绝新建：写下去只会得到一份磁盘上没有、
        重启就消失的「假快照」。
        """
        if self._save_blocked:
            raise RuntimeError(self.last_error or "版本存储不可写（索引损坏且无法备份）")

        # 存储所有文件内容
        file_hashes = {}
        for filename, content in files.items():
            h = self._store_object(content)
            file_hashes[filename] = h

        version_id = f"v_{int(time.time()*1000)}_{hashlib.md5(message.encode()).hexdigest()[:8]}"
        # 同毫秒 + 同 message 会撞 ID：必须消歧，否则后写覆盖前写、静默丢历史
        n = 0
        while version_id in self._versions:
            n += 1
            version_id = (
                f"v_{int(time.time()*1000)}_"
                f"{hashlib.md5(message.encode()).hexdigest()[:8]}_{n}"
            )
        version = Version(
            id=version_id,
            timestamp=time.time(),
            message=message,
            parent_id=self._head,
            files=file_hashes,
            author=author,
            metadata=metadata or {},
        )

        self._versions[version_id] = version
        self._head = version_id
        self._save()
        return version

    def get_version(self, version_id: str = None) -> Optional[Version]:
        """获取指定版本（默认当前 HEAD）。"""
        vid = version_id or self._head
        return self._versions.get(vid)

    def get_file(self, filename: str, version_id: str = None) -> Optional[str]:
        """获取指定版本中的文件内容。"""
        version = self.get_version(version_id)
        if not version or filename not in version.files:
            return None
        return self._get_object(version.files[filename])

    def get_all_files(self, version_id: str = None) -> Dict[str, str]:
        """获取指定版本的所有文件。

        对象库缺块时抛 ValueError——不返回空串冒充"文件为空"，
        否则回滚/比对会把丢失内容当成真空内容写出去（伪造结果）。
        """
        version = self.get_version(version_id)
        if not version:
            return {}
        result = {}
        for filename, h in version.files.items():
            content = self._get_object(h)
            if content is None:
                raise ValueError(f"对象缺失: {filename} (hash {h})，拒绝用空内容冒充")
            result[filename] = content
        return result

    # ── 回滚 ────────────────────────────────────────────

    def rollback(self, version_id: str) -> Tuple[bool, str]:
        """
        回滚到指定版本。

        实际上是创建一个新版本，内容等于目标版本。
        这样历史不会丢失，可以再次回滚。
        """
        version = self.get_version(version_id)
        if not version:
            return False, f"版本 {version_id} 不存在"

        try:
            files = self.get_all_files(version_id)
        except ValueError as e:
            # 对象库缺块：回滚必须失败，绝不能把空内容写成"已回滚"
            return False, f"无法回滚到 {version_id}: {e}"
        new_version = self.snapshot(
            message=f"回滚到 {version_id}: {version.message}",
            files=files,
            author="system",
            metadata={"rollback_from": version_id},
        )
        return True, f"已回滚到 {version_id}，新版本 {new_version.id}"

    def rollback_to_parent(self) -> Tuple[bool, str]:
        """回滚到上一个版本（一键恢复）。"""
        current = self.get_version()
        if not current or not current.parent_id:
            return False, "没有父版本可回滚"
        return self.rollback(current.parent_id)

    # ── 差异比较 ────────────────────────────────────────

    def diff(self, version_id1: str, version_id2: str = None) -> Dict[str, Any]:
        """
        比较两个版本的差异。

        返回：
        {
            "added": [...],
            "modified": [...],
            "deleted": [...],
            "unchanged": [...],
        }
        """
        v1 = self.get_version(version_id1)
        v2 = self.get_version(version_id2)  # None = HEAD

        if not v1:
            return {"error": f"版本 {version_id1} 不存在"}
        if version_id2 is not None and not v2:
            # 显式指定的 v2 缺失必须报错：静默当空集会把全部文件报成 deleted
            return {"error": f"版本 {version_id2} 不存在"}

        files1 = set(v1.files.keys())
        files2 = set(v2.files.keys()) if v2 else set()

        added = files2 - files1
        deleted = files1 - files2
        common = files1 & files2

        modified = []
        unchanged = []
        for f in common:
            if v1.files[f] != (v2.files.get(f) if v2 else None):
                modified.append(f)
            else:
                unchanged.append(f)

        return {
            "version1": version_id1,
            "version2": version_id2 or "HEAD",
            "added": sorted(added),
            "modified": sorted(modified),
            "deleted": sorted(deleted),
            "unchanged": sorted(unchanged),
        }

    # ── 历史 ────────────────────────────────────────────

    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取版本历史。"""
        versions = sorted(self._versions.values(), key=lambda v: v.timestamp, reverse=True)
        return [
            {
                "id": v.id,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v.timestamp)),
                "message": v.message,
                "author": v.author,
                "files": len(v.files),
                "parent": v.parent_id,
            }
            for v in versions[:limit]
        ]

    def get_stats(self) -> Dict[str, Any]:
        """获取版本存储统计。"""
        return {
            "total_versions": len(self._versions),
            "head": self._head,
            "store_path": self.store_path,
            "objects_count": len(os.listdir(self.objects_path)) if os.path.exists(self.objects_path) else 0,
        }
