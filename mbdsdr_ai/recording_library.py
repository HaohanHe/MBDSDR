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
