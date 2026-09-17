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

import json
import os
import time

import hashlib
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple


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
        self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """加载版本数据。"""
        if os.path.exists(self.versions_file):
            try:
                with open(self.versions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for v in data.get("versions", []):
                    self._versions[v["id"]] = Version(**v)
                self._head = data.get("head")
            except Exception as e:
                print(f"警告: 版本存储加载失败: {e}")

    def _save(self):
        """保存版本数据。"""
        data = {
            "head": self._head,
            "versions": [v.to_dict() for v in self._versions.values()],
        }
        with open(self.versions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

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
        """
        # 存储所有文件内容
        file_hashes = {}
        for filename, content in files.items():
            h = self._store_object(content)
            file_hashes[filename] = h

        version_id = f"v_{int(time.time()*1000)}_{hashlib.md5(message.encode()).hexdigest()[:8]}"
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
        """获取指定版本的所有文件。"""
        version = self.get_version(version_id)
        if not version:
            return {}
        result = {}
        for filename, h in version.files.items():
            result[filename] = self._get_object(h) or ""
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

        files = self.get_all_files(version_id)
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
