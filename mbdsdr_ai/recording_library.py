# SPDX-License-Identifier: MIT
"""
录音库索引（索引 ↔ 磁盘一致性）
================================

长期使用才会暴露的问题：录音文件会被人在资源管理器里删掉/改名/搬走，
索引会指向不存在的文件，磁盘上也会冒出索引不知道的录音。本模块把
「索引」当作**可核对、可修复、可留痕**的记录，而不是真相本身。

设计约束（红线）：
- **索引不删用户数据**：修复只改索引/写日志，任何动作都不 ``unlink`` 录音文件。
  被外部删除的文件在索引里标记 ``state="missing"`` 并保留原条目，便于事后追查。
- **每个动作留痕**：修复动作同时写入 ``library.repairs.jsonl``（JSONL 逐行追加），
  记录时间、动作、条目 id、路径与依据。
- **不猜元数据**：采样率/中心频率/时间戳/时长/设备型号拿不到就记 ``None``，
  并在 ``unknown_fields`` 里显式标注；恢复时也绝不编造数值（见 ``recover_metadata``）。
- **原子落盘**：库文件 ``library.json`` 用临时文件 + ``os.replace`` 写入，读取方
  永远看到完整旧版或完整新版，不会读到半截 JSON。

与既有录制模块的关系：
- ``recorder.inspect_recording`` 判定单个 IQ 文件完整性（ok/incomplete/truncated/...）。
- ``recorder.recover_recording_meta`` 为中断录制补写 ``complete=false`` 的诚实 meta。
- ``recording_meta`` 提供 SigMF 侧车读写与展示字段拍平。
本模块只做「库」这一层，不重复实现录制/修复算法。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
import wave as _wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .recorder import inspect_recording, recover_recording_meta
from .recording_meta import metadata_for_display, read_sigmf_meta, sigmf_meta_path

logger = logging.getLogger(__name__)

#: 库文件格式版本。写盘永远写当前版本；读盘遇到更高版本拒绝加载（不猜字段）。
LIBRARY_FORMAT_VERSION = 1
#: 索引文件名（相对库根目录）。
LIBRARY_FILENAME = "library.json"
#: 修复日志文件名（JSONL，逐行追加）。
JOURNAL_FILENAME = "library.repairs.jsonl"
#: 文件锁名（跨进程互斥用；不存业务数据）。
LOCK_FILENAME = ".library.lock"
#: 回收站目录名（保留策略只把文件搬进这里，绝不直接删除）。
TRASH_DIRNAME = "_trash"

#: 扫描磁盘时认可的录音数据文件后缀。
_IQ_SUFFIXES = (".sigmf-data",)
_AUDIO_SUFFIXES = (".wav",)
_SCAN_SUFFIXES = _IQ_SUFFIXES + _AUDIO_SUFFIXES

#: 元数据字段（拿不到即 None，并列入 unknown_fields）。
_META_FIELDS = (
    "sample_rate_hz",
    "center_freq_hz",
    "start_time",
    "duration_s",
    "device",
)


class LibraryBusyError(RuntimeError):
    """库文件被另一方占用，超时仍未拿到锁。"""

    code = "library_busy"


@dataclass
class LibraryResult:
    """库操作结果：成功/失败与可区分的原因码。"""

    ok: bool
    code: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecordingEntry:
    """一条录音索引记录。

    ``path`` 以 ``{"kind": "relative"|"absolute", "value": ...}`` 存储：
    库内文件用相对路径（库整体搬走后仍可解析），跨盘符无法求相对路径时
    退化为绝对路径（跨盘移动后由 ``relocate``/``reconcile`` 修正）。
    """

    entry_id: str
    path: Dict[str, str]
    kind: str = "iq"
    size_bytes: int = 0
    mtime: float = 0.0
    fingerprint: str = ""
    sample_rate_hz: Optional[float] = None
    center_freq_hz: Optional[float] = None
    start_time: Optional[str] = None
    duration_s: Optional[float] = None
    device: Optional[str] = None
    num_samples: Optional[int] = None
    complete: Optional[bool] = None
    integrity: str = "unknown"
    #: ok / incomplete / truncated / corrupt / missing / duplicate / trashed
    state: str = "ok"
    unknown_fields: List[str] = field(default_factory=list)
    revision: int = 1
    updated_at: str = ""
    trash_path: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "path": dict(self.path),
            "kind": self.kind,
            "size_bytes": int(self.size_bytes),
            "mtime": float(self.mtime),
            "fingerprint": self.fingerprint,
            "sample_rate_hz": self.sample_rate_hz,
            "center_freq_hz": self.center_freq_hz,
            "start_time": self.start_time,
            "duration_s": self.duration_s,
            "device": self.device,
            "num_samples": self.num_samples,
            "complete": self.complete,
            "integrity": self.integrity,
            "state": self.state,
            "unknown_fields": list(self.unknown_fields),
            "revision": int(self.revision),
            "updated_at": self.updated_at,
            "trash_path": self.trash_path,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RecordingEntry":
        raw_path = d.get("path")
        if isinstance(raw_path, dict):
            path = {
                "kind": str(raw_path.get("kind") or "relative"),
                "value": str(raw_path.get("value") or ""),
            }
        elif raw_path:
            value = str(raw_path)
            path = {
                "kind": "absolute" if os.path.isabs(value) else "relative",
                "value": value,
            }
        else:
            path = {"kind": "relative", "value": ""}

        def _opt_float(key: str) -> Optional[float]:
            v = d.get(key)
            return float(v) if isinstance(v, (int, float)) else None

        def _opt_int(key: str) -> Optional[int]:
            v = d.get(key)
            return int(v) if isinstance(v, (int, float)) else None

        complete = d.get("complete")
        return cls(
            entry_id=str(d.get("entry_id") or d.get("id") or uuid.uuid4().hex),
            path=path,
            kind=str(d.get("kind") or "iq"),
            size_bytes=int(d.get("size_bytes") or 0),
            mtime=float(d.get("mtime") or 0.0),
            fingerprint=str(d.get("fingerprint") or ""),
            sample_rate_hz=_opt_float("sample_rate_hz"),
            center_freq_hz=_opt_float("center_freq_hz"),
            start_time=(str(d["start_time"])
                        if d.get("start_time") not in (None, "") else None),
            duration_s=_opt_float("duration_s"),
            device=str(d["device"]) if d.get("device") not in (None, "") else None,
            num_samples=_opt_int("num_samples"),
            complete=(bool(complete) if complete is not None else None),
            integrity=str(d.get("integrity") or "unknown"),
            state=str(d.get("state") or "ok"),
            unknown_fields=[str(x) for x in (d.get("unknown_fields") or [])],
            revision=int(d.get("revision") or 1),
            updated_at=str(d.get("updated_at") or ""),
            trash_path=(str(d["trash_path"])
                        if d.get("trash_path") not in (None, "") else None),
            notes=str(d.get("notes") or ""),
        )


@dataclass
class RetentionPolicy:
    """容量/保留策略（全部可选，未设置即不启用该项规则）。"""

    max_total_bytes: Optional[int] = None
    max_age_days: Optional[float] = None
    max_entries: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_total_bytes": self.max_total_bytes,
            "max_age_days": self.max_age_days,
            "max_entries": self.max_entries,
        }


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    """解析 ISO8601 时间；无法解析返回 None（不猜时间）。"""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ── 跨进程文件锁 ─────────────────────────────────────────────────────────
_PROCESS_LOCKS: Dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _process_lock(path: str) -> threading.RLock:
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(path, threading.RLock())


def _try_file_lock(fp) -> None:
    if os.name == "nt":
        import msvcrt
        fp.seek(0)
        msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - Linux 分支
        import fcntl
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fp) -> None:
    if os.name == "nt":
        import msvcrt
        try:
            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:  # pragma: no cover - Linux 分支
        import fcntl
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


class _FileLock:
    """进程内 RLock + 跨进程文件锁；超时抛出 :class:`LibraryBusyError`。"""

    def __init__(self, path: str, timeout_s: float) -> None:
        self.path = path
        self.timeout_s = float(timeout_s)
        self._proc: Optional[threading.RLock] = None
        self._fp = None

    def __enter__(self) -> "_FileLock":
        self._proc = _process_lock(self.path)
        deadline = time.monotonic() + self.timeout_s
        if not self._proc.acquire(timeout=self.timeout_s):
            self._proc = None
            raise LibraryBusyError(f"库被同进程另一线程占用（等待 {self.timeout_s}s 超时）")
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        try:
            self._fp = open(self.path, "a+b")
        except OSError as exc:
            self._proc.release()
            self._proc = None
            raise LibraryBusyError(f"无法打开库锁文件: {exc}") from exc
        last_exc: Optional[OSError] = None
        while True:
            try:
                _try_file_lock(self._fp)
                return self
            except OSError as exc:
                last_exc = exc
                if time.monotonic() >= deadline:
                    self._fp.close()
                    self._fp = None
                    self._proc.release()
                    self._proc = None
                    raise LibraryBusyError(
                        f"库文件被另一方占用（等待 {self.timeout_s}s 超时）"
                    ) from last_exc
                time.sleep(0.02)

    def __exit__(self, *exc_info) -> None:
        if self._fp is not None:
            _unlock_file(self._fp)
            self._fp.close()
            self._fp = None
        if self._proc is not None:
            self._proc.release()
            self._proc = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fingerprint(abs_path: str) -> str:
    """快速指纹：文件大小 + 首尾各 64KiB 的 SHA1。

    用于识别「外部改名/搬走」：同一份录音改名后大小与首尾字节不变。
    读取失败返回空串（此时不参与改名匹配，避免把不同文件误配）。
    """
    try:
        size = os.path.getsize(abs_path)
        h = hashlib.sha1()
        chunk = 65536
        with open(abs_path, "rb") as f:
            h.update(f.read(chunk))
            if size > chunk:
                f.seek(max(0, size - chunk))
                h.update(f.read(chunk))
        return f"{size}:{h.hexdigest()}"
    except OSError:
        return ""


class RecordingLibrary:
    """一个目录下的录音索引。

    典型用法::

        lib = RecordingLibrary("/data/recordings")
        lib.add_recording("/data/recordings/foo.sigmf-data")
        report = lib.reconcile()            # 只报告差异（dry-run）
        fixed = lib.reconcile(apply=True)   # 应用修复并写留痕日志
    """

    def __init__(self, root: str, *, filename: str = LIBRARY_FILENAME,
                 lock_timeout_s: float = 5.0) -> None:
        self.root = os.path.abspath(root)
        self.filename = filename
        self.lock_timeout_s = float(lock_timeout_s)

    # ── 路径 ─────────────────────────────────────────────────────────────
    @property
    def library_path(self) -> str:
        return os.path.join(self.root, self.filename)

    @property
    def journal_path(self) -> str:
        return os.path.join(self.root, JOURNAL_FILENAME)

    @property
    def lock_path(self) -> str:
        return os.path.join(self.root, LOCK_FILENAME)

    @property
    def trash_root(self) -> str:
        return os.path.join(self.root, TRASH_DIRNAME)

    def _lock(self) -> _FileLock:
        return _FileLock(self.lock_path, self.lock_timeout_s)

    # ── 存储路径 ─────────────────────────────────────────────────────────
    def _store_path(self, abs_path: str) -> Dict[str, str]:
        abs_path = os.path.normpath(os.path.abspath(abs_path))
        try:
            rel = os.path.relpath(abs_path, self.root)
        except ValueError:
            # Windows 跨盘符无法求相对路径：存绝对路径，移动后由 reconcile 修正
            return {"kind": "absolute", "value": abs_path}
        if os.path.isabs(rel) or rel == ".." or rel.startswith(".." + os.sep):
            return {"kind": "absolute", "value": abs_path}
        return {"kind": "relative", "value": rel.replace(os.sep, "/")}

    def _resolve_path(self, stored: Dict[str, str]) -> str:
        kind = stored.get("kind", "relative")
        value = str(stored.get("value") or "")
        if kind == "absolute":
            return os.path.normpath(value)
        return os.path.normpath(os.path.join(self.root, value))

    # ── 文档读写 ─────────────────────────────────────────────────────────
    def _empty_doc(self) -> Dict[str, Any]:
        return {
            "format_version": LIBRARY_FORMAT_VERSION,
            "root": self.root,
            "revision": 0,
            "updated_at": _now_iso(),
            "entries": [],
        }

    def _read_unlocked(self) -> Tuple[Optional[Dict[str, Any]], str, str]:
        """读取库文件。返回 (doc, code, message)；doc 为 None 表示不可用。"""
        path = self.library_path
        if not os.path.exists(path):
            return self._empty_doc(), "not_found", "库文件尚未创建（空库）"
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return None, "corrupt_library", f"库文件无法解析: {exc}"
        if not isinstance(doc, dict):
            return None, "corrupt_library", "库文件结构不是 JSON 对象"
        version = doc.get("format_version", doc.get("version", 0))
        try:
            version = int(version or 0)
        except (TypeError, ValueError):
            return None, "corrupt_library", f"库文件格式版本非法: {version!r}"
        if version > LIBRARY_FORMAT_VERSION:
            return None, "unsupported_version", (
                f"库文件格式版本 {version} 高于本程序支持版本 "
                f"{LIBRARY_FORMAT_VERSION}；拒绝加载以免误读/丢字段"
            )
        if version < LIBRARY_FORMAT_VERSION:
            # 旧版本（含无版本字段的 v0）向后兼容：补默认值，不丢条目
            doc = dict(doc)
            doc["format_version"] = LIBRARY_FORMAT_VERSION
            doc.setdefault("entries", [])
            doc["migrated_from"] = version
        if not isinstance(doc.get("entries"), list):
            return None, "corrupt_library", "库文件 entries 字段不是数组"
        doc.setdefault("revision", 0)
        doc.setdefault("updated_at", "")
        doc["root"] = self.root
        return doc, "ok", ""

    def _write_doc_unlocked(self, doc: Dict[str, Any]) -> None:
        doc["format_version"] = LIBRARY_FORMAT_VERSION
        # 迁移标记只用于本次加载提示，不写回文件（否则每次读都像「刚迁移」）
        doc.pop("migrated_from", None)
        doc["root"] = self.root
        doc["revision"] = int(doc.get("revision", 0)) + 1
        doc["updated_at"] = _now_iso()
        os.makedirs(self.root, exist_ok=True)
        tmp = f"{self.library_path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            for attempt in range(6):
                try:
                    os.replace(tmp, self.library_path)
                    return
                except PermissionError:
                    # Windows 上读端正开着文件时替换会短暂失败，重试而非放弃
                    if attempt >= 5:
                        raise
                    time.sleep(0.03)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _append_journal_unlocked(self, action: str, entry_id: str = "",
                                 detail: Optional[Dict[str, Any]] = None) -> None:
        """追加一条修复留痕。写日志失败直接抛错：不允许「改了却没记录」。"""
        os.makedirs(self.root, exist_ok=True)
        record = {
            "at": _now_iso(),
            "action": action,
            "entry_id": entry_id,
            "detail": detail or {},
        }
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    # ── 单文件事实采集 ──────────────────────────────────────────────────
    def _collect_file_facts(self, abs_path: str,
                            metadata: Optional[Dict[str, Any]] = None,
                            ) -> Dict[str, Any]:
        """采集一个文件的元数据/完整性事实；拿不到的字段保持 None。"""
        facts: Dict[str, Any] = {
            "kind": "audio" if abs_path.lower().endswith(_AUDIO_SUFFIXES)
            else "iq",
            "size_bytes": 0,
            "mtime": 0.0,
            "fingerprint": "",
            "sample_rate_hz": None,
            "center_freq_hz": None,
            "start_time": None,
            "duration_s": None,
            "device": None,
            "num_samples": None,
            "complete": None,
            "integrity": "missing",
            "state": "missing",
        }
        if not os.path.exists(abs_path):
            return facts
        try:
            st = os.stat(abs_path)
            facts["size_bytes"] = int(st.st_size)
            facts["mtime"] = float(st.st_mtime)
        except OSError:
            facts["integrity"] = "io_error"
            facts["state"] = "corrupt"
            return facts
        facts["fingerprint"] = _fingerprint(abs_path)

        if facts["kind"] == "iq":
            info = inspect_recording(abs_path)
            integrity = str(info.get("integrity") or "unknown")
            facts["integrity"] = integrity
            facts["complete"] = info.get("complete_flag")
            facts["num_samples"] = int(info.get("samples_on_disk") or 0)
            facts["state"] = {
                "ok": "ok",
                "incomplete": "incomplete",
                "truncated": "truncated",
                "missing-meta": "incomplete",
                "meta-corrupt": "corrupt",
                "corrupt": "corrupt",
                "empty": "corrupt",
                "io_error": "corrupt",
                "missing": "missing",
            }.get(integrity, "corrupt")

            meta = read_sigmf_meta(abs_path)
            disp = metadata_for_display(meta)
            sample_rate = _numeric(disp.get("sample_rate_hz"))
            if sample_rate is not None and sample_rate <= 0:
                sample_rate = None
            center_freq = _numeric(disp.get("center_freq_hz"))
            facts["sample_rate_hz"] = sample_rate
            facts["center_freq_hz"] = center_freq
            start = disp.get("start_time")
            facts["start_time"] = str(start) if start else None
            device = disp.get("hardware")
            if not device and isinstance(meta, dict):
                device = (meta.get("global", {}) or {}).get("mbdsdr:device")
            facts["device"] = str(device) if device else None
            declared = _numeric(disp.get("num_samples"))
            if declared is not None:
                facts["num_samples"] = int(declared)
            # 时长以磁盘实际样本为准（截断/中断时也如实反映已有数据的时长），
            # 采样率未知则时长未知，绝不编造。
            if sample_rate and facts["size_bytes"] % 8 == 0:
                facts["duration_s"] = (facts["size_bytes"] // 8) / sample_rate
        else:
            facts["integrity"] = "ok" if facts["size_bytes"] > 0 else "empty"
            facts["state"] = "ok" if facts["size_bytes"] > 0 else "corrupt"
            try:
                with _wave.open(abs_path, "rb") as wf:
                    rate = int(wf.getframerate() or 0)
                    frames = int(wf.getnframes() or 0)
                if rate > 0:
                    facts["sample_rate_hz"] = float(rate)
                    facts["num_samples"] = frames
                    facts["duration_s"] = frames / float(rate)
            except (OSError, _wave.Error):
                facts["integrity"] = "corrupt"
                facts["state"] = "corrupt"

        if metadata:
            for key in _META_FIELDS + ("num_samples", "complete"):
                if key in metadata and metadata[key] is not None:
                    facts[key] = metadata[key]
            # 显式提供了采样率、且磁盘样本数已知时，时长可由两者如实换算
            sr = _numeric(facts.get("sample_rate_hz"))
            n = facts.get("num_samples")
            if (facts.get("duration_s") is None and sr and sr > 0
                    and isinstance(n, int) and n >= 0):
                facts["duration_s"] = n / sr
        facts["unknown_fields"] = [k for k in _META_FIELDS
                                   if facts.get(k) is None]
        return facts

    def _entry_from_file(self, abs_path: str, *,
                         entry_id: Optional[str] = None,
                         metadata: Optional[Dict[str, Any]] = None
                         ) -> RecordingEntry:
        facts = self._collect_file_facts(abs_path, metadata)
        return RecordingEntry(
            entry_id=entry_id or uuid.uuid4().hex,
            path=self._store_path(abs_path),
            kind=facts["kind"],
            size_bytes=facts["size_bytes"],
            mtime=facts["mtime"],
            fingerprint=facts["fingerprint"],
            sample_rate_hz=facts["sample_rate_hz"],
            center_freq_hz=facts["center_freq_hz"],
            start_time=facts["start_time"],
            duration_s=facts["duration_s"],
            device=facts["device"],
            num_samples=facts["num_samples"],
            complete=facts["complete"],
            integrity=facts["integrity"],
            state=facts["state"],
            unknown_fields=facts["unknown_fields"],
            revision=1,
            updated_at=_now_iso(),
        )

    def _apply_facts(self, entry: RecordingEntry, facts: Dict[str, Any],
                     *, bump: bool = True) -> Dict[str, Any]:
        """把文件事实写回条目，返回实际变化的字段（供报告/留痕）。"""
        changes: Dict[str, Any] = {}
        for key in ("kind", "size_bytes", "mtime", "fingerprint", "sample_rate_hz",
                    "center_freq_hz", "start_time", "duration_s", "device",
                    "num_samples", "complete", "integrity", "state",
                    "unknown_fields"):
            old = getattr(entry, key)
            new = facts.get(key)
            if old != new:
                setattr(entry, key, new)
                changes[key] = {"from": old, "to": new}
        if changes and bump:
            entry.revision += 1
            entry.updated_at = _now_iso()
        return changes

    # ── 公开读接口 ───────────────────────────────────────────────────────
    def load(self) -> LibraryResult:
        """读取库快照（拿锁，互斥于写入）。"""
        try:
            with self._lock():
                doc, code, message = self._read_unlocked()
        except LibraryBusyError as exc:
            return LibraryResult(False, exc.code, str(exc))
        if doc is None:
            return LibraryResult(False, code, message)
        entries = [e.to_dict() for e in self._entries_of(doc)]
        return LibraryResult(
            True, code, message or "库已加载",
            data={
                "library": doc,
                "entries": entries,
                "format_version": doc.get("format_version"),
                "revision": doc.get("revision", 0),
                "migrated_from": doc.get("migrated_from"),
            },
        )

    @staticmethod
    def _entries_of(doc: Dict[str, Any]) -> List[RecordingEntry]:
        out: List[RecordingEntry] = []
        for raw in doc.get("entries") or []:
            if isinstance(raw, dict):
                out.append(RecordingEntry.from_dict(raw))
        return out

    def list_entries(self, *, include_trashed: bool = False,
                     include_missing: bool = True) -> LibraryResult:
        """列出条目（默认包含 missing，便于用户看到「文件丢了」）。"""
        loaded = self.load()
        if not loaded.ok:
            return loaded
        entries = []
        for raw in loaded.data["entries"]:
            state = raw.get("state")
            if state == "trashed" and not include_trashed:
                continue
            if state == "missing" and not include_missing:
                continue
            entries.append(raw)
        return LibraryResult(True, "ok", f"共 {len(entries)} 条", {"entries": entries})

    def journal(self, *, limit: int = 100) -> LibraryResult:
        """读取修复留痕（最近 limit 条，按写入顺序）。"""
        if not os.path.exists(self.journal_path):
            return LibraryResult(True, "empty", "暂无修复记录", {"records": []})
        records: List[Dict[str, Any]] = []
        malformed = 0
        try:
            with open(self.journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        malformed += 1
        except OSError as exc:
            return LibraryResult(False, "io_error", f"读取修复日志失败: {exc}")
        if limit > 0:
            records = records[-limit:]
        return LibraryResult(True, "ok", f"修复记录 {len(records)} 条",
                             {"records": records, "malformed_lines": malformed})

    # ── 登记录音 ─────────────────────────────────────────────────────────
    def add_recording(self, data_path: str, *,
                      metadata: Optional[Dict[str, Any]] = None,
                      entry_id: Optional[str] = None) -> LibraryResult:
        """把磁盘上一个录音登记进索引（不复制、不修改录音文件）。"""
        abs_path = os.path.abspath(data_path)
        if not os.path.exists(abs_path):
            return LibraryResult(False, "file_missing",
                                 f"文件不存在，无法登记: {abs_path}")
        try:
            with self._lock():
                doc, code, message = self._read_unlocked()
                if doc is None:
                    return LibraryResult(False, code, message)
                entries = self._entries_of(doc)
                target_key = _norm_key(abs_path)
                for e in entries:
                    if e.state in ("trashed", "duplicate"):
                        continue
                    if _norm_key(self._resolve_path(e.path)) == target_key:
                        return LibraryResult(
                            True, "already_present",
                            f"该文件已在索引中（条目 {e.entry_id}）",
                            {"entry_id": e.entry_id, "path": abs_path},
                        )
                entry = self._entry_from_file(abs_path, entry_id=entry_id,
                                              metadata=metadata)
                entries.append(entry)
                doc["entries"] = [e.to_dict() for e in entries]
                doc["last_add"] = {"at": _now_iso(), "entry_id": entry.entry_id}
                self._write_doc_unlocked(doc)
                return LibraryResult(
                    True, "ok", f"已登记 {entry.entry_id}",
                    {"entry_id": entry.entry_id, "entry": entry.to_dict()},
                )
        except LibraryBusyError as exc:
            return LibraryResult(False, exc.code, str(exc))

    # ── 并发写入：乐观并发控制 ──────────────────────────────────────────
    _UPDATABLE_FIELDS = (
        "kind", "size_bytes", "mtime", "fingerprint", "sample_rate_hz",
        "center_freq_hz", "start_time", "duration_s", "device", "num_samples",
        "complete", "integrity", "state", "unknown_fields", "notes",
    )

    def update_entry(self, entry_id: str, *,
                     expected_revision: Optional[int] = None,
                     **fields: Any) -> LibraryResult:
        """更新一条索引记录。

        并发语义：
        - 传 ``expected_revision``：乐观并发控制，版本不匹配立刻返回
          ``revision_conflict``（带当前条目内容），绝不覆盖别人的修改。
        - 不传：显式「最后写入者胜出」，结果码与 ``mode`` 如实标注。
        - 整个读-改-写过程持库锁，两个写入者不会互相踩踏或丢记录。
        """
        unknown = set(fields) - set(self._UPDATABLE_FIELDS)
        if unknown:
            return LibraryResult(False, "bad_field",
                                 f"不允许更新的字段: {sorted(unknown)}")
        if expected_revision is not None:
            try:
                expected = int(expected_revision)
            except (TypeError, ValueError):
                return LibraryResult(False, "bad_param",
                                     f"expected_revision 非法: {expected_revision!r}")
        else:
            expected = None
        try:
            with self._lock():
                doc, code, message = self._read_unlocked()
                if doc is None:
                    return LibraryResult(False, code, message)
                entries = self._entries_of(doc)
                entry = next((e for e in entries if e.entry_id == entry_id), None)
                if entry is None:
                    return LibraryResult(False, "entry_not_found",
                                         f"索引中没有条目 {entry_id}")
                if expected is not None and expected != entry.revision:
                    return LibraryResult(
                        False, "revision_conflict",
                        (f"条目已被另一方修改（当前 revision={entry.revision}，"
                         f"本次期望 {expected}），未覆盖"),
                        {"entry_id": entry_id,
                         "current_revision": entry.revision,
                         "expected_revision": expected,
                         "current": entry.to_dict()},
                    )
                changed: Dict[str, Any] = {}
                for key, value in fields.items():
                    old = getattr(entry, key)
                    if old != value:
                        setattr(entry, key, value)
                        changed[key] = {"from": old, "to": value}
                if not changed:
                    return LibraryResult(
                        True, "no_change", "字段与当前值一致，无需更新",
                        {"entry_id": entry_id, "revision": entry.revision,
                         "mode": "optimistic" if expected is not None
                         else "last_writer_wins"},
                    )
                previous = entry.to_dict()
                entry.revision += 1
                entry.updated_at = _now_iso()
                doc["entries"] = [e.to_dict() for e in entries]
                self._write_doc_unlocked(doc)
                return LibraryResult(
                    True, "ok", f"已更新条目（revision {entry.revision}）",
                    {"entry_id": entry_id, "revision": entry.revision,
                     "changed": changed, "previous": previous,
                     "mode": "optimistic" if expected is not None
                     else "last_writer_wins"},
                )
        except LibraryBusyError as exc:
            return LibraryResult(False, exc.code, str(exc))

    # ── 索引 ↔ 磁盘核对 ─────────────────────────────────────────────────
    def _scan_disk_unlocked(self) -> Dict[str, str]:
        """扫描库根目录下的录音数据文件（跳过回收站；不跟随符号链接）。"""
        found: Dict[str, str] = {}
        if not os.path.isdir(self.root):
            return found
        trash = os.path.normcase(os.path.abspath(self.trash_root))
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = [d for d in dirnames
                           if os.path.normcase(os.path.abspath(
                               os.path.join(dirpath, d))) != trash]
            for name in filenames:
                if not name.lower().endswith(_SCAN_SUFFIXES):
                    continue
                abs_path = os.path.abspath(os.path.join(dirpath, name))
                found[_norm_key(abs_path)] = abs_path
        return found

    def _plan_reconcile_unlocked(
        self, doc: Dict[str, Any], *, scan: bool = True
    ) -> Tuple[List[Dict[str, Any]], List[RecordingEntry]]:
        entries = self._entries_of(doc)
        actions: List[Dict[str, Any]] = []

        # 1) 同一条目路径被多条记录引用：保留第一条，其余标 duplicate（不删除）
        seen: Dict[str, RecordingEntry] = {}
        active: List[RecordingEntry] = []
        for e in entries:
            if e.state in ("trashed", "duplicate"):
                continue
            key = _norm_key(self._resolve_path(e.path))
            if key in seen:
                first = seen[key]
                e.state = "duplicate"
                e.notes = f"与条目 {first.entry_id} 指向同一文件"
                e.revision += 1
                e.updated_at = _now_iso()
                actions.append({
                    "action": "mark_duplicate",
                    "entry_id": e.entry_id,
                    "path": self._resolve_path(e.path),
                    "reason": "索引内多条记录指向同一文件",
                    "detail": {"kept_entry_id": first.entry_id},
                })
                continue
            seen[key] = e
            active.append(e)

        disk = self._scan_disk_unlocked() if scan else {}
        tracked_keys = set(seen.keys())
        untracked = {k: p for k, p in disk.items() if k not in tracked_keys}

        # 2) 索引里有、磁盘上也在：刷新事实；曾标 missing 的恢复
        missing: List[RecordingEntry] = []
        for e in active:
            abs_path = self._resolve_path(e.path)
            if os.path.exists(abs_path):
                facts = self._collect_file_facts(abs_path)
                changes = self._apply_facts(e, facts)
                if e.state == "missing" and not changes.get("state"):
                    e.state = facts["state"]
                    changes["state"] = {"from": "missing", "to": facts["state"]}
                if changes:
                    actions.append({
                        "action": "refresh_entry",
                        "entry_id": e.entry_id,
                        "path": abs_path,
                        "reason": "磁盘文件事实与索引不一致（大小/修改时间/完整性/元数据）",
                        "detail": changes,
                    })
            else:
                missing.append(e)

        # 3) 索引缺失文件：先按指纹识别「改名/移动」，否则标 missing（保留条目）
        if scan:
            for e in missing:
                matched_key = ""
                matched_path = ""
                if e.fingerprint:
                    for key, p in untracked.items():
                        facts = self._collect_file_facts(p)
                        if (facts["fingerprint"] == e.fingerprint
                                and facts["size_bytes"] == e.size_bytes):
                            matched_key, matched_path = key, p
                            break
                if matched_path:
                    old_path = self._resolve_path(e.path)
                    e.path = self._store_path(matched_path)
                    facts = self._collect_file_facts(matched_path)
                    changes = self._apply_facts(e, facts)
                    actions.append({
                        "action": "adopt_moved",
                        "entry_id": e.entry_id,
                        "path": matched_path,
                        "reason": "索引条目原路径不存在，但磁盘上发现同指纹文件（改名/移动）",
                        "detail": {"from": old_path, "to": matched_path,
                                   "match": "size+fingerprint", "changes": changes},
                    })
                    untracked.pop(matched_key, None)
                else:
                    if e.state != "missing":
                        e.state = "missing"
                        e.revision += 1
                        e.updated_at = _now_iso()
                        actions.append({
                            "action": "mark_missing",
                            "entry_id": e.entry_id,
                            "path": self._resolve_path(e.path),
                            "reason": "索引指向的文件不存在（外部删除/移动）；保留条目不删数据",
                            "detail": {"bytes_still_referenced": e.size_bytes},
                        })
        else:
            for e in missing:
                if e.state != "missing":
                    e.state = "missing"
                    e.revision += 1
                    e.updated_at = _now_iso()
                    actions.append({
                        "action": "mark_missing",
                        "entry_id": e.entry_id,
                        "path": self._resolve_path(e.path),
                        "reason": "索引指向的文件不存在（未扫描磁盘查找改名）",
                    })

        # 4) 磁盘有、索引无：登记为新条目（同样不移动/不删除文件）
        for key, p in untracked.items():
            new_entry = self._entry_from_file(p)
            entries.append(new_entry)
            actions.append({
                "action": "adopt_new",
                "entry_id": new_entry.entry_id,
                "path": p,
                "reason": "磁盘上有录音文件但索引无记录（外部拷入/索引丢失）",
                "detail": {"size_bytes": new_entry.size_bytes,
                           "integrity": new_entry.integrity,
                           "state": new_entry.state},
            })

        return actions, entries

    def reconcile(self, *, apply: bool = False, scan: bool = True
                  ) -> LibraryResult:
        """双向核对索引与磁盘。

        ``apply=False``（默认）只报告将做什么；``apply=True`` 才改索引并写留痕。
        无论哪种模式都不会删除录音文件。
        """
        try:
            with self._lock():
                doc, code, message = self._read_unlocked()
                if doc is None:
                    return LibraryResult(False, code, message)
                actions, entries = self._plan_reconcile_unlocked(doc, scan=scan)
                summary: Dict[str, int] = {}
                for a in actions:
                    summary[a["action"]] = summary.get(a["action"], 0) + 1
                if not apply:
                    return LibraryResult(
                        True, "dry_run" if actions else "clean",
                        (f"发现 {len(actions)} 项差异（未修改，apply=False）"
                         if actions else "索引与磁盘一致"),
                        {"actions": actions, "summary": summary,
                         "applied": False, "entries": len(entries)},
                    )
                if not actions:
                    return LibraryResult(
                        True, "clean", "索引与磁盘一致",
                        {"actions": [], "summary": {}, "applied": True,
                         "entries": len(entries)},
                    )
                # 先写留痕，再落盘索引：宁可日志多一条，也不允许改了却没记录
                for a in actions:
                    self._append_journal_unlocked(
                        a["action"], entry_id=a.get("entry_id", ""),
                        detail={"path": a.get("path", ""),
                                "reason": a.get("reason", ""),
                                "detail": a.get("detail", {})},
                    )
                doc["entries"] = [e.to_dict() for e in entries]
                doc["last_reconcile"] = {"at": _now_iso(),
                                         "actions": len(actions),
                                         "summary": summary}
                self._write_doc_unlocked(doc)
                return LibraryResult(
                    True, "applied", f"已应用 {len(actions)} 项修复（已留痕）",
                    {"actions": actions, "summary": summary, "applied": True,
                     "entries": len(entries), "journal": self.journal_path},
                )
        except LibraryBusyError as exc:
            return LibraryResult(False, exc.code, str(exc))
        except OSError as exc:
            logger.error("录音库核对失败: %s", exc)
            return LibraryResult(False, "io_error", f"核对/落盘失败: {exc}")

    # ── 容量与保留策略 ───────────────────────────────────────────────────
    def _age_of(self, entry: RecordingEntry,
                now_dt: datetime) -> Tuple[Optional[float], str]:
        """条目年龄（天）与依据。拿不到时间就返回 (None, "unknown")。"""
        start = _parse_iso_dt(entry.start_time)
        basis = "start_time"
        if start is None and entry.mtime > 0:
            # 录制开始时间缺失时才退到文件 mtime，并如实标注依据
            start = datetime.fromtimestamp(entry.mtime, tz=timezone.utc)
            basis = "file_mtime"
        if start is None:
            return None, "unknown"
        return max(0.0, (now_dt - start).total_seconds() / 86400.0), basis

    def _build_retention_plan(self, entries: List[RecordingEntry],
                              policy: RetentionPolicy,
                              now_dt: datetime) -> Dict[str, Any]:
        active = [e for e in entries
                  if e.state not in ("trashed", "duplicate", "missing")]
        ages: Dict[str, Tuple[float, str]] = {}
        unavailable: List[Dict[str, Any]] = []
        for e in active:
            age, basis = self._age_of(e, now_dt)
            if age is None:
                unavailable.append({
                    "entry_id": e.entry_id,
                    "path": self._resolve_path(e.path),
                    "reason": "age_unknown",
                })
            else:
                ages[e.entry_id] = (age, basis)
        for e in entries:
            if e.state == "missing":
                unavailable.append({
                    "entry_id": e.entry_id,
                    "path": self._resolve_path(e.path),
                    "reason": "file_missing",
                    "size_bytes": int(e.size_bytes),
                })

        candidates: Dict[str, Dict[str, Any]] = {}

        def add_candidate(entry: RecordingEntry, reason: str) -> None:
            item = candidates.get(entry.entry_id)
            if item is None:
                age, basis = ages.get(entry.entry_id, (None, "unknown"))
                item = {
                    "entry_id": entry.entry_id,
                    "path": self._resolve_path(entry.path),
                    "size_bytes": int(entry.size_bytes),
                    "age_days": (round(age, 3) if age is not None else None),
                    "age_basis": basis,
                    "reasons": [],
                }
                candidates[entry.entry_id] = item
            if reason not in item["reasons"]:
                item["reasons"].append(reason)

        if policy.max_age_days is not None:
            for e in active:
                a = ages.get(e.entry_id)
                if a and a[0] > float(policy.max_age_days):
                    add_candidate(
                        e,
                        f"超过保留期限 {policy.max_age_days:g} 天"
                        f"（实际 {a[0]:.2f} 天，依据 {a[1]}）",
                    )

        total_bytes = sum(int(e.size_bytes) for e in active)
        ordered = sorted(
            active,
            key=lambda e: (e.entry_id not in ages,
                           -(ages[e.entry_id][0] if e.entry_id in ages else 0.0)),
        )
        if (policy.max_total_bytes is not None
                and total_bytes > int(policy.max_total_bytes)):
            remaining = total_bytes
            for e in ordered:
                if remaining <= int(policy.max_total_bytes):
                    break
                if int(e.size_bytes) <= 0:
                    continue
                add_candidate(
                    e,
                    f"活动库容量 {total_bytes} 字节超过上限 "
                    f"{int(policy.max_total_bytes)} 字节，按最旧优先移出",
                )
                remaining -= int(e.size_bytes)
        if (policy.max_entries is not None
                and len(active) > int(policy.max_entries)):
            count = len(active)
            for e in ordered:
                if count <= int(policy.max_entries):
                    break
                add_candidate(
                    e,
                    f"条目数 {len(active)} 超过上限 {int(policy.max_entries)}，"
                    f"按最旧优先移出",
                )
                count -= 1

        ordered_candidates = sorted(
            candidates.values(),
            key=lambda c: (c["age_days"] is None, -(c["age_days"] or 0.0)),
        )
        would_free = sum(int(c["size_bytes"]) for c in ordered_candidates)
        return {
            "policy": policy.to_dict(),
            "now": now_dt.isoformat(),
            "candidates": ordered_candidates,
            "unavailable": unavailable,
            "active_entries": len(active),
            "active_bytes": total_bytes,
            "kept_entries": len(active) - len(ordered_candidates),
            "kept_bytes": total_bytes - would_free,
            "would_free_bytes": would_free,
            "action": "move_to_trash",
            "notes": ("清理动作只把文件移入库内回收站 _trash/，可手动恢复；"
                      "磁盘空间在清空回收站后才真正释放。"),
        }

    @staticmethod
    def _validate_policy(policy: RetentionPolicy) -> Optional[LibraryResult]:
        for attr in ("max_total_bytes", "max_age_days", "max_entries"):
            value = getattr(policy, attr, None)
            if value is None:
                continue
            try:
                if float(value) < 0:
                    return LibraryResult(
                        False, "bad_param",
                        f"保留策略 {attr} 不能为负数: {value!r}")
            except (TypeError, ValueError):
                return LibraryResult(
                    False, "bad_param", f"保留策略 {attr} 非法: {value!r}")
        return None

    def plan_retention(self, policy: RetentionPolicy, *,
                       now: Optional[datetime] = None) -> LibraryResult:
        """预览会移出哪些录音（dry-run；不改索引、不动文件）。"""
        bad = self._validate_policy(policy)
        if bad is not None:
            return bad
        now_dt = now or datetime.now(timezone.utc)
        loaded = self.load()
        if not loaded.ok:
            return loaded
        entries = [RecordingEntry.from_dict(d) for d in loaded.data["entries"]]
        plan = self._build_retention_plan(entries, policy, now_dt)
        return LibraryResult(
            True, "dry_run" if plan["candidates"] else "no_action",
            (f"将移出 {len(plan['candidates'])} 条（约 "
             f"{plan['would_free_bytes']} 字节，未执行）"
             if plan["candidates"] else "没有需要清理的录音"),
            plan,
        )

    def _unique_trash_dest(self, dirpath: str, name: str,
                           entry_id: str) -> str:
        dest = os.path.join(dirpath, name)
        if not os.path.exists(dest):
            return dest
        stem, ext = os.path.splitext(name)
        for i in range(1, 1000):
            suffix = f"__{entry_id[:8]}" + (f"_{i}" if i > 1 else "")
            candidate = os.path.join(dirpath, f"{stem}{suffix}{ext}")
            if not os.path.exists(candidate):
                return candidate
        raise OSError(f"回收站目标文件名冲突无法解决: {name}")

    def apply_retention(self, policy: RetentionPolicy, *,
                        confirm: bool = False,
                        plan: Optional[Dict[str, Any]] = None,
                        now: Optional[datetime] = None,
                        trash_subdir: Optional[str] = None) -> LibraryResult:
        """执行清理：必须 ``confirm=True``；计划过期则拒绝；文件只进回收站。"""
        bad = self._validate_policy(policy)
        if bad is not None:
            return bad
        now_dt = now or datetime.now(timezone.utc)
        if confirm is not True:
            preview = self.plan_retention(policy, now=now_dt)
            if not preview.ok:
                return preview
            return LibraryResult(
                False, "confirmation_required",
                "清理会把文件移入回收站，必须 confirm=True 显式确认后才执行",
                {"plan": preview.data},
            )
        try:
            with self._lock():
                doc, code, message = self._read_unlocked()
                if doc is None:
                    return LibraryResult(False, code, message)
                entries = self._entries_of(doc)
                fresh = self._build_retention_plan(entries, policy, now_dt)
                if plan is not None:
                    prev_ids = sorted(str(c.get("entry_id"))
                                      for c in (plan.get("candidates") or []))
                    fresh_ids = sorted(c["entry_id"]
                                       for c in fresh["candidates"])
                    if prev_ids != fresh_ids:
                        return LibraryResult(
                            False, "plan_stale",
                            "清理计划已过期（库内容已变化），请重新 dry-run 后再确认",
                            {"plan": fresh},
                        )
                if not fresh["candidates"]:
                    return LibraryResult(True, "no_action",
                                         "没有需要清理的录音",
                                         {"plan": fresh})
                stamp = now_dt.strftime("%Y%m%dT%H%M%SZ")
                trash_dir = os.path.join(self.trash_root,
                                         trash_subdir or stamp)
                os.makedirs(trash_dir, exist_ok=True)
                by_id = {e.entry_id: e for e in entries}
                moved: List[Dict[str, Any]] = []
                failed: List[Dict[str, Any]] = []
                rollback: List[Tuple[str, str]] = []

                for c in fresh["candidates"]:
                    entry = by_id.get(c["entry_id"])
                    if entry is None:
                        continue
                    orig = self._resolve_path(entry.path)
                    if not os.path.exists(orig):
                        failed.append({"entry_id": entry.entry_id,
                                       "path": orig, "reason": "file_missing"})
                        continue
                    dest = self._unique_trash_dest(
                        trash_dir, os.path.basename(orig), entry.entry_id)
                    try:
                        shutil.move(orig, dest)
                    except OSError as exc:
                        failed.append({"entry_id": entry.entry_id,
                                       "path": orig, "reason": str(exc)})
                        continue
                    rollback.append((dest, orig))
                    if entry.kind == "iq":
                        meta_orig = sigmf_meta_path(orig)
                        meta_dest = os.path.splitext(dest)[0] + ".sigmf-meta"
                        if os.path.exists(meta_orig):
                            try:
                                shutil.move(meta_orig, meta_dest)
                                rollback.append((meta_dest, meta_orig))
                            except OSError:
                                # 数据已移走；meta 留着也不影响（不删用户数据）
                                pass
                    entry.state = "trashed"
                    entry.trash_path = dest
                    entry.revision += 1
                    entry.updated_at = _now_iso()
                    moved.append({"entry_id": entry.entry_id,
                                  "from": orig, "to": dest,
                                  "size_bytes": int(c["size_bytes"]),
                                  "reasons": list(c["reasons"])})

                if moved:
                    doc["entries"] = [e.to_dict() for e in entries]
                    doc["last_retention"] = {
                        "at": _now_iso(), "moved": len(moved),
                        "failed": len(failed), "trash_dir": trash_dir,
                    }
                    try:
                        self._write_doc_unlocked(doc)
                    except OSError as exc:
                        # 索引没落盘成功：把文件搬回原位，避免「文件在回收站、索引不知道」
                        for dest, orig in reversed(rollback):
                            try:
                                shutil.move(dest, orig)
                            except OSError:
                                pass
                        return LibraryResult(False, "io_error",
                                             f"索引落盘失败，已回滚移动: {exc}")
                    for item in moved:
                        self._append_journal_unlocked(
                            "retention_move", entry_id=item["entry_id"],
                            detail={"from": item["from"], "to": item["to"],
                                    "size_bytes": item["size_bytes"],
                                    "reasons": item["reasons"],
                                    "trash": True},
                        )
                for item in failed:
                    self._append_journal_unlocked(
                        "retention_failed", entry_id=item["entry_id"],
                        detail={"path": item["path"],
                                "reason": item["reason"]},
                    )
                ok = not failed
                return LibraryResult(
                    ok, "applied" if ok else "partial_failure",
                    (f"已移入回收站 {len(moved)} 条"
                     + (f"；{len(failed)} 条失败" if failed else "")
                     + "（可从 _trash/ 恢复，未删除）"),
                    {"moved": moved, "failed": failed, "trash_dir": trash_dir,
                     "plan": fresh, "journal": self.journal_path,
                     "notes": fresh["notes"]},
                )
        except LibraryBusyError as exc:
            return LibraryResult(False, exc.code, str(exc))
        except OSError as exc:
            logger.error("保留策略执行失败: %s", exc)
            return LibraryResult(False, "io_error", f"清理执行失败: {exc}")

    # ── 迁移、导出与格式版本 ─────────────────────────────────────────────
    @staticmethod
    def _is_within(child: str, parent: str) -> bool:
        """child 是否位于 parent 之下（跨盘符返回 False，不抛异常）。"""
        try:
            cp = os.path.commonpath([os.path.abspath(child),
                                     os.path.abspath(parent)])
        except ValueError:
            return False
        return os.path.normcase(cp) == os.path.normcase(os.path.abspath(parent))

    def relocate(self, new_root: str, *, verify: bool = True) -> LibraryResult:
        """库目录被整体搬走后，在新位置重新锚定索引。

        条目优先使用相对路径；跨盘符无法相对化的条目保留绝对路径。
        只改路径/根目录，不移动、不复制、不删除任何录音文件。
        """
        new_root = os.path.abspath(new_root)
        if os.path.normcase(new_root) == os.path.normcase(self.root):
            return LibraryResult(True, "no_change", "库根目录未变化",
                                 {"root": new_root})
        if not os.path.isdir(new_root):
            return LibraryResult(False, "dest_missing",
                                 f"新库根目录不存在: {new_root}")
        new_lib = RecordingLibrary(new_root, filename=self.filename,
                                  lock_timeout_s=self.lock_timeout_s)
        try:
            with new_lib._lock():
                doc, code, message = new_lib._read_unlocked()
                if doc is None:
                    return LibraryResult(False, code, message)
                if code == "not_found":
                    return LibraryResult(False, "library_not_found",
                                         f"新目录下没有 {self.filename}，"
                                         f"拒绝创建空库覆盖原索引")
                entries = new_lib._entries_of(doc)
                rebased = 0
                for e in entries:
                    if e.path.get("kind") == "absolute":
                        value = os.path.normpath(str(e.path.get("value") or ""))
                        if self._is_within(value, self.root):
                            rel = os.path.relpath(value, self.root)
                            e.path = new_lib._store_path(
                                os.path.join(new_root, rel))
                            rebased += 1
                    if e.trash_path and self._is_within(e.trash_path, self.root):
                        rel = os.path.relpath(e.trash_path, self.root)
                        e.trash_path = os.path.normpath(
                            os.path.join(new_root, rel))
                missing: List[Dict[str, Any]] = []
                if verify:
                    for e in entries:
                        if e.state in ("duplicate",):
                            continue
                        abs_path = new_lib._resolve_path(e.path)
                        if not os.path.exists(abs_path):
                            missing.append({"entry_id": e.entry_id,
                                            "path": abs_path,
                                            "state": e.state})
                doc["entries"] = [e.to_dict() for e in entries]
                doc["last_relocate"] = {
                    "at": _now_iso(), "from_root": self.root,
                    "to_root": new_root, "rebased": rebased,
                    "missing": len(missing),
                }
                new_lib._write_doc_unlocked(doc)
                new_lib._append_journal_unlocked(
                    "relocate",
                    detail={"from_root": self.root, "to_root": new_root,
                            "rebased": rebased, "missing": len(missing)},
                )
                return LibraryResult(
                    True, "relocated",
                    f"已在 {new_root} 重新锚定 {len(entries)} 条（{len(missing)} 条文件缺失）",
                    {"old_root": self.root, "new_root": new_root,
                     "entries": len(entries), "rebased": rebased,
                     "missing": missing, "journal": new_lib.journal_path},
                )
        except LibraryBusyError as exc:
            return LibraryResult(False, exc.code, str(exc))
        except OSError as exc:
            return LibraryResult(False, "io_error", f"重新锚定失败: {exc}")

    def export_to(self, dest_root: str, *,
                  entry_ids: Optional[Iterable[str]] = None,
                  overwrite: bool = False) -> LibraryResult:
        """把录音与索引复制到新目录（源库原样保留）。

        复制后在新目录写一份自洽的 ``library.json``，条目全部使用相对路径，
        因此跨盘符搬迁后仍然可用；源文件一个字节都不删。
        """
        dest_root = os.path.abspath(dest_root)
        if os.path.normcase(dest_root) == os.path.normcase(self.root):
            return LibraryResult(False, "bad_param", "导出目标不能是源库根目录")
        loaded = self.load()
        if not loaded.ok:
            return loaded
        entries = [RecordingEntry.from_dict(d) for d in loaded.data["entries"]]
        wanted = None
        if entry_ids is not None:
            wanted = {str(x) for x in entry_ids}
        selected = [e for e in entries
                    if e.state not in ("trashed", "duplicate")
                    and (wanted is None or e.entry_id in wanted)]
        if wanted is not None:
            found = {e.entry_id for e in selected}
            missing_ids = sorted(wanted - found)
        else:
            missing_ids = []
        if not overwrite and os.path.exists(
                os.path.join(dest_root, self.filename)):
            return LibraryResult(
                False, "dest_not_empty",
                f"目标目录已有 {self.filename}；如需覆盖请显式 overwrite=True",
                {"dest_root": dest_root},
            )
        os.makedirs(dest_root, exist_ok=True)
        exported: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        new_entries: List[Dict[str, Any]] = []
        for e in selected:
            src = self._resolve_path(e.path)
            if not os.path.exists(src):
                skipped.append({"entry_id": e.entry_id, "path": src,
                                "reason": "file_missing"})
                continue
            if self._is_within(src, self.root):
                rel = os.path.relpath(src, self.root)
            else:
                # 库外文件（跨盘符绝对路径）放到 _external/，导出后仍是库内相对路径
                rel = os.path.join("_external", e.entry_id[:8],
                                   os.path.basename(src))
            dst = os.path.join(dest_root, rel)
            meta_src = (sigmf_meta_path(src) if e.kind == "iq" else "")
            meta_dst = (os.path.splitext(dst)[0] + ".sigmf-meta"
                        if meta_src else "")
            if (not overwrite and os.path.exists(dst)) or \
                    (meta_dst and not overwrite and os.path.exists(meta_dst)):
                skipped.append({"entry_id": e.entry_id, "path": dst,
                                "reason": "dest_exists"})
                continue
            try:
                os.makedirs(os.path.dirname(dst) or dest_root, exist_ok=True)
                shutil.copy2(src, dst)
                if os.path.getsize(dst) != os.path.getsize(src):
                    raise OSError("复制后大小不一致")
                if meta_src and os.path.exists(meta_src):
                    shutil.copy2(meta_src, meta_dst)
                if meta_src and os.path.exists(meta_src) and \
                        not os.path.exists(meta_dst):
                    raise OSError("SigMF meta 复制失败")
            except OSError as exc:
                failed.append({"entry_id": e.entry_id, "path": src,
                               "reason": str(exc)})
                # 只清理本次自身复制出的半成品，不触碰源文件
                for p in (dst, meta_dst):
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                continue
            item = e.to_dict()
            item["path"] = {"kind": "relative",
                            "value": rel.replace(os.sep, "/")}
            item["trash_path"] = None
            item["updated_at"] = _now_iso()
            new_entries.append(item)
            exported.append({"entry_id": e.entry_id, "from": src, "to": dst,
                             "size_bytes": int(e.size_bytes)})

        dest_lib = RecordingLibrary(dest_root, filename=self.filename,
                                   lock_timeout_s=self.lock_timeout_s)
        doc = dest_lib._empty_doc()
        doc["entries"] = new_entries
        doc["last_export"] = {"at": _now_iso(), "from_root": self.root,
                              "exported": len(exported),
                              "skipped": len(skipped),
                              "failed": len(failed)}
        try:
            with dest_lib._lock():
                dest_lib._write_doc_unlocked(doc)
                dest_lib._append_journal_unlocked(
                    "export", detail={"from_root": self.root,
                                      "exported": len(exported),
                                      "skipped": len(skipped),
                                      "failed": len(failed)},
                )
        except LibraryBusyError as exc:
            return LibraryResult(False, exc.code, str(exc))
        except OSError as exc:
            return LibraryResult(False, "io_error",
                                 f"导出索引落盘失败: {exc}")
        ok = not failed
        return LibraryResult(
            ok, "exported" if ok else "partial_failure",
            (f"已导出 {len(exported)} 条到 {dest_root}"
             + (f"；跳过 {len(skipped)} 条" if skipped else "")
             + (f"；失败 {len(failed)} 条" if failed else "")),
            {"dest_root": dest_root, "exported": exported, "skipped": skipped,
             "failed": failed, "unknown_entry_ids": missing_ids,
             "library": dest_lib.library_path},
        )

    def upgrade_format(self) -> LibraryResult:
        """把旧版本库文件原地升级为当前格式（保留全部条目）。"""
        try:
            with self._lock():
                doc, code, message = self._read_unlocked()
                if doc is None:
                    return LibraryResult(False, code, message)
                migrated_from = doc.get("migrated_from")
                if migrated_from is None:
                    return LibraryResult(True, "up_to_date",
                                         f"库格式已是 v{LIBRARY_FORMAT_VERSION}",
                                         {"format_version": LIBRARY_FORMAT_VERSION})
                entries = self._entries_of(doc)
                doc["entries"] = [e.to_dict() for e in entries]
                self._write_doc_unlocked(doc)
                self._append_journal_unlocked(
                    "upgrade_format",
                    detail={"from_version": migrated_from,
                            "to_version": LIBRARY_FORMAT_VERSION},
                )
                return LibraryResult(
                    True, "upgraded",
                    f"库格式 v{migrated_from} → v{LIBRARY_FORMAT_VERSION}",
                    {"from_version": migrated_from,
                     "to_version": LIBRARY_FORMAT_VERSION,
                     "entries": len(entries)},
                )
        except LibraryBusyError as exc:
            return LibraryResult(False, exc.code, str(exc))
        except OSError as exc:
            return LibraryResult(False, "io_error", f"格式升级失败: {exc}")

    # ── 元数据恢复（中断写入 / 断电式损坏）─────────────────────────────
    def recover_metadata(self, entry_id: str, *,
                         sample_rate_hz: Optional[float] = None,
                         center_freq_hz: Optional[float] = None,
                         device: Optional[str] = None) -> LibraryResult:
        """为 meta 缺失/损坏的 IQ 录制补写诚实元数据。

        数值来源优先级：显式参数 > 索引里已有的旧值 > 未知。
        未知字段写进 ``mbdsdr:unknown_fields`` 且不编造数值。
        """
        try:
            with self._lock():
                doc, code, message = self._read_unlocked()
                if doc is None:
                    return LibraryResult(False, code, message)
                entries = self._entries_of(doc)
                entry = next((e for e in entries if e.entry_id == entry_id), None)
                if entry is None:
                    return LibraryResult(False, "entry_not_found",
                                         f"索引中没有条目 {entry_id}")
                abs_path = self._resolve_path(entry.path)
                if entry.kind != "iq" or not abs_path.lower().endswith(_IQ_SUFFIXES):
                    return LibraryResult(False, "unsupported_kind",
                                         "只有 SigMF IQ 录制支持元数据恢复")
                if not os.path.exists(abs_path):
                    return LibraryResult(False, "file_missing",
                                         f"录音文件不存在: {abs_path}")
                info = inspect_recording(abs_path)
                integrity = str(info.get("integrity") or "unknown")
                if integrity in ("corrupt", "empty", "io_error"):
                    return LibraryResult(
                        False, "unrecoverable",
                        f"数据本身已损坏（完整性={integrity}），拒绝编造元数据",
                        {"integrity": integrity, "data_path": abs_path},
                    )
                if integrity not in ("missing-meta", "meta-corrupt"):
                    return LibraryResult(
                        True, "not_needed",
                        f"元数据无需恢复（当前完整性={integrity}）",
                        {"integrity": integrity},
                    )

                sources: Dict[str, str] = {}
                unknown_fields: List[str] = []
                sr = _numeric(sample_rate_hz)
                if sr is None:
                    sr = entry.sample_rate_hz
                    if sr is not None:
                        sources["sample_rate_hz"] = "index"
                elif sr > 0:
                    sources["sample_rate_hz"] = "explicit"
                if sr is None or sr <= 0:
                    sr = None
                    unknown_fields.append("sample_rate_hz")
                cf = _numeric(center_freq_hz)
                if cf is None:
                    cf = entry.center_freq_hz
                    if cf is not None:
                        sources["center_freq_hz"] = "index"
                else:
                    sources["center_freq_hz"] = "explicit"
                if cf is None:
                    unknown_fields.append("center_freq_hz")
                dev = device or entry.device
                if device:
                    sources["device"] = "explicit"
                elif entry.device:
                    sources["device"] = "index"
                if not dev:
                    unknown_fields.append("device")
                start = entry.start_time
                if start:
                    sources["start_time"] = "index"
                else:
                    unknown_fields.append("start_time")

                meta_path = recover_recording_meta(
                    abs_path, sample_rate_hz=float(sr or 0.0),
                    center_freq_hz=float(cf or 0.0),
                )
                if not meta_path:
                    return LibraryResult(
                        False, "unrecoverable",
                        "数据字节不完整（非 8 字节整数倍或为空），拒绝编造元数据",
                        {"integrity": integrity, "data_path": abs_path},
                    )

                # 在已写出的诚实 meta 上补齐设备/时间戳/未知字段标注
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    return LibraryResult(False, "io_error",
                                         f"恢复后元数据读取失败: {exc}")
                g = meta.setdefault("global", {})
                g["mbdsdr:device"] = str(dev or "")
                g["mbdsdr:recovered_fields"] = sorted(sources.keys())
                g["mbdsdr:unknown_fields"] = sorted(set(unknown_fields))
                g["mbdsdr:complete"] = False
                caps = meta.get("captures") or [{}]
                if start:
                    caps[0]["core:datetime"] = start
                else:
                    caps[0]["core:datetime"] = ""
                meta["captures"] = caps
                self._write_json_atomic(meta_path, meta)

                facts = self._collect_file_facts(abs_path)
                changes = self._apply_facts(entry, facts)
                # complete=false 的恢复结果按事实标注为 incomplete
                if facts["integrity"] == "incomplete" and entry.state != "incomplete":
                    entry.state = "incomplete"
                    changes["state"] = {"from": entry.state, "to": "incomplete"}
                doc["entries"] = [e.to_dict() for e in entries]
                self._append_journal_unlocked(
                    "recover_metadata", entry_id=entry.entry_id,
                    detail={"meta_path": meta_path,
                            "integrity_before": integrity,
                            "sources": sources,
                            "unknown_fields": sorted(set(unknown_fields))},
                )
                self._write_doc_unlocked(doc)
                return LibraryResult(
                    True, "recovered",
                    f"已补写诚实元数据（{len(unknown_fields)} 个字段未知）",
                    {"entry_id": entry.entry_id, "meta_path": meta_path,
                     "sources": sources,
                     "unknown_fields": sorted(set(unknown_fields)),
                     "changes": changes},
                )
        except LibraryBusyError as exc:
            return LibraryResult(False, exc.code, str(exc))
        except OSError as exc:
            logger.error("元数据恢复失败: %s", exc)
            return LibraryResult(False, "io_error", f"元数据恢复失败: {exc}")

    @staticmethod
    def _write_json_atomic(path: str, obj: Dict[str, Any]) -> None:
        tmp = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
