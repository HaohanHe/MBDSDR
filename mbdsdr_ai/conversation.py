"""
MBDSDR AI 内核 - 多会话对话存储
================================
管理多个对话会话的持久化：保存、加载、列表、删除。
每个会话是一个 JSON 文件，存在 ~/.mbdsdr/conversations/ 下。
"""
import json
import os
import time
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional

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
                created_at = old.get("created_at", now)
            except Exception:
                pass

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
        """加载单条会话元信息。"""
        path = self.get_path(conversation_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ConversationMeta(
                conversation_id=data.get("conversation_id", conversation_id),
                title=data.get("title", ""),
                created_at=data.get("created_at", 0.0),
                updated_at=data.get("updated_at", 0.0),
                message_count=data.get("message_count", 0),
                file_path=path,
            )
        except Exception:
            return None

    def list_conversations(self) -> List[ConversationMeta]:
        """列出所有已保存的会话，按 updated_at 倒序（最新在前）。"""
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
