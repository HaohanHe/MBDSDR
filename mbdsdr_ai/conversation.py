# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 多会话对话存储
================================
管理多个对话会话的持久化：保存、加载、列表、删除。
每个会话是一个 JSON 文件，存在 ~/.mbdsdr/conversations/ 下。
"""
import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)

CONVERSATION_DIR = os.path.expanduser("~/.mbdsdr/conversations")


@dataclass
class ConversationMeta:
    """一条已保存会话的元信息。"""
    conversation_id: str
    title: str
    created_at: float
    updated_at: float
    message_count: int
    file_path: str


class ConversationStore:
    """
    多会话持久化存储。

    每个会话一个 JSON 文件，结构：
    {
        "conversation_id": ...,
        "title": ...,
        "created_at": ...,
        "updated_at": ...,
        "messages": [ ... ]
    }
    """

    def __init__(self, directory: str = CONVERSATION_DIR):
        self.directory = os.path.expanduser(directory)
        os.makedirs(self.directory, exist_ok=True)
        # 降级可见：读不出来的会话记在这里（path + 原因），调用方据此区分
        # 「本来就没有会话」与「会话文件坏了」。list_conversations() 每次重填。
        self.load_errors: List[Dict[str, str]] = []
        # 保存时发现原件损坏 → 先改名备份为 *.corrupt-<时间> 再写新内容；
        # 备份路径与原因记在这里，避免「创建时间被静默重置、原内容被覆写」。
        self.save_warnings: List[Dict[str, str]] = []

    def _record_load_error(self, path: str, exc: Exception) -> None:
        """记录一次读取降级（不抛：列表视图仍需返回其它可读会话）。"""
        reason = f"{type(exc).__name__}: {exc}"
        self.load_errors.append({"path": path, "error": reason})
        log.warning("会话读取失败（已记入 load_errors）：%s | %s", path, reason)

    def _quarantine_corrupt(self, path: str, exc: Exception) -> None:
        """把读不出来的原件改名保留，避免随后的整文件覆写抹掉用户数据。"""
        backup = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        reason = f"{type(exc).__name__}: {exc}"
        try:
            os.replace(path, backup)
        except OSError as backup_exc:
            backup_error = f"{type(backup_exc).__name__}: {backup_exc}"
            self.save_warnings.append({
                "path": path, "error": reason, "backup": "",
                "backup_error": backup_error,
            })
            log.warning("会话文件损坏且备份失败：%s | %s | 备份失败：%s",
                        path, reason, backup_error)
            return
        self.save_warnings.append({
            "path": path, "error": reason, "backup": backup,
        })
        log.warning("会话文件损坏/不可读，已备份为 %s | %s", backup, reason)

    def get_path(self, conversation_id: str) -> str:
        """返回某会话对应的文件路径。"""
        return os.path.join(self.directory, f"{conversation_id}.json")

    def _title_from_messages(self, messages: List[Dict[str, Any]], fallback: str = "") -> str:
        """从第一条 user 消息截取前 30 字符作为标题。"""
        for m in messages:
            if m.get("role") == "user":
                content = m.get("content", "") or ""
                text = content.strip().replace("\n", " ")
                if text:
                    return text[:30]
        return fallback or "未命名对话"

    def save(self, conversation_id: str, messages: List[Dict],
             title: str = "") -> str:
        """
        保存对话到 JSON 文件，返回文件路径。

        若文件已存在则更新 updated_at 与消息内容（upsert）。
        """
        path = self.get_path(conversation_id)
        now = time.time()

        # 保留已有的 created_at（若存在）
        created_at = now
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    old = json.load(f)
                if isinstance(old, dict):
                    created_at = old.get("created_at", now)
            except FileNotFoundError:
                pass  # 竞态：判存在后刚被删，等价于首次保存
            except (OSError, ValueError) as exc:
                # 真坏：不吞。原件先备份，创建时间重置这件事有据可查。
                self._quarantine_corrupt(path, exc)

        if not title:
            title = self._title_from_messages(messages)

        data = {
            "conversation_id": conversation_id,
            "title": title,
            "created_at": created_at,
            "updated_at": now,
            "message_count": len(messages),
            "messages": messages,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def load(self, conversation_id: str) -> List[Dict]:
        """加载对话消息列表；不存在时抛出 FileNotFoundError。"""
        path = self.get_path(conversation_id)
        if not os.path.exists(path):
            raise FileNotFoundError(f"对话不存在: {conversation_id}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("messages", [])

    def load_meta(self, conversation_id: str) -> Optional[ConversationMeta]:
        """加载单条会话元信息。

        文件不存在返回 ``None``（本来就允许为空）；文件存在但读不出来
        返回 ``None`` 的同时把原因记进 ``load_errors``——两者不再等价。
        """
        path = self.get_path(conversation_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None  # 竞态：判存在后刚被删，与「本来就没有」同义
        except (OSError, ValueError) as exc:
            self._record_load_error(path, exc)
            return None
        if not isinstance(data, dict):
            self._record_load_error(
                path, TypeError(f"顶层不是 JSON 对象，而是 {type(data).__name__}"))
            return None
        return ConversationMeta(
            conversation_id=data.get("conversation_id", conversation_id),
            title=data.get("title", ""),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            message_count=data.get("message_count", 0),
            file_path=path,
        )

    def list_conversations(self) -> List[ConversationMeta]:
        """列出所有已保存的会话，按 updated_at 倒序（最新在前）。

        读不出来的会话不会静默消失：原因记在 ``load_errors``。
        """
        self.load_errors.clear()
        result: List[ConversationMeta] = []
        if not os.path.isdir(self.directory):
            return result
        for name in os.listdir(self.directory):
            if not name.endswith(".json"):
                continue
            cid = name[:-len(".json")]
            meta = self.load_meta(cid)
            if meta is not None:
                result.append(meta)
        result.sort(key=lambda m: m.updated_at, reverse=True)
        return result

    def delete(self, conversation_id: str) -> bool:
        """删除对话文件，返回是否删除成功。"""
        path = self.get_path(conversation_id)
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        return False
