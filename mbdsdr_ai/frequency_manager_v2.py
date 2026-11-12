# SPDX-License-Identifier: MIT
"""
MBDSDR 频率管理器 v2（frequency_manager_v2）
=============================================

频率管理器 v2：按「分组 + 书签」组织常用频率库，但**不替换**现有
frequency_manager.py（那个文件由其他 agent 拥有，本文件只新建）。
SDR++ 频率管理器仅作技术参考，本仓未包含其源代码。

数据结构设计：
  - FrequencyBookmark{frequency,bandwidth,mode,selected}
      -> BookmarkV2{name,freq_hz,mode,bandwidth_hz,notes,tags,group}
  - 分组下的书签 JSON
      -> groups: {group_name: {color, icon, bookmarks:[...]}}
  - 整表写回落盘
      -> 原子写：写临时文件 + os.replace
  - JSON 导入书签
      -> import_json + import_csv（CSV 为本仓新增）

MBDSDR 增强：
  * nearest(freq_hz, max_distance) —— 最近邻频率查找。
  * 分组带 color/icon。
  * classify_signal(features) —— AI 钩子：把未知信号自动归到某个分组。
  * 字段更全：notes/tags。

红线：
  * **不预存任何地区电台**——groups 只建空壳（航空/海事/业余/广播/卫星），
    不带任何具体频点。用户自己加。
"""
from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

from .corrupt_evidence import REASON_SUFFIX, write_reason

logger = logging.getLogger(__name__)

#: v2 文件格式的版本号；写入与读取必须一致。
FILE_VERSION = 2


def _same_content_backup(path: str) -> Optional[str]:
    """同目录下是否已有一份与当前文件字节相同的 ``*.corrupt-*`` 备份。"""
    directory = os.path.dirname(os.path.abspath(path))
    prefix = os.path.basename(path) + ".corrupt-"
    try:
        names = sorted(
            (n for n in os.listdir(directory)
             if n.startswith(prefix) and not n.endswith(REASON_SUFFIX)),
            reverse=True)
        if not names:
            return None
        candidate = os.path.join(directory, names[0])
        if os.path.getsize(candidate) != os.path.getsize(path):
            return None
        with open(candidate, "rb") as a, open(path, "rb") as b:
            return candidate if a.read() == b.read() else None
    except OSError:
        return None


def _backup_corrupt(path: str) -> Optional[str]:
    """把损坏书签文件改名为 ``*.corrupt-<时间戳>``，返回备份路径（失败 None）。

    同一份内容只保留一份副本；改名后原路径不再被读取，也不会被整表写回覆盖。
    """
    dst = _same_content_backup(path)
    if dst is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = f"{path}.corrupt-{stamp}"
        n = 0
        while os.path.exists(dst):
            n += 1
            dst = f"{path}.corrupt-{stamp}-{n}"
    try:
        os.replace(path, dst)
    except OSError:
        return None
    return dst


class UnsupportedBookmarkVersion(ValueError):
    """书签文件 version 与当前 v2 不兼容：拒绝读取，且不覆盖原文件。"""


# ----------------------------------------------------------------------
# 分组定义（只建空壳，不带频点——红线）
# ----------------------------------------------------------------------
DEFAULT_GROUPS: Dict[str, Dict[str, str]] = {
    "aviation":  {"label": "航空", "color": "#FFB300", "icon": "plane"},
    "marine":    {"label": "海事", "color": "#039BE5", "icon": "anchor"},
    "ham":       {"label": "业余", "color": "#43A047", "icon": "antenna"},
    "broadcast": {"label": "广播", "color": "#8E24AA", "icon": "radio"},
    "satellite": {"label": "卫星", "color": "#E53935", "icon": "satellite"},
}

# 解调模式（对齐 fm:40-49 NFM/WFM/AM/DSB/USB/CW/LSB/RAW）
KNOWN_MODES = ("NFM", "WFM", "AM", "DSB", "USB", "CW", "LSB", "RAW", "DIG")


@dataclass
class BookmarkV2:
    name: str
    freq_hz: float
    mode: str = "NFM"
    bandwidth_hz: float = 0.0
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    group: str = "ham"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "BookmarkV2":
        return BookmarkV2(
            name=d["name"],
            freq_hz=float(d["freq_hz"]),
            mode=d.get("mode", "NFM"),
            bandwidth_hz=float(d.get("bandwidth_hz", 0.0)),
            notes=d.get("notes", ""),
            tags=list(d.get("tags", [])),
            group=d.get("group", "ham"),
        )


class FrequencyManagerV2:
    """书签管理器：分组 CRUD + 书签 CRUD + JSON 原子持久化 + nearest + CSV。"""

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.path.join(os.path.expanduser("~"), ".mbdsdr",
                                "bookmarks_v2.json")
        self.path = path
        # group_key -> {label, color, icon}
        self.groups: Dict[str, Dict[str, str]] = json.loads(json.dumps(DEFAULT_GROUPS))
        # group_key -> list[BookmarkV2]
        self.bookmarks: Dict[str, List[BookmarkV2]] = {g: [] for g in self.groups}
        #: 读不了/损坏的证据；为空表示本次读取没有异常。
        self.last_error: Optional[str] = None
        #: 已落盘的损坏备份路径。
        self.corrupt_backups: List[str] = []
        #: 读失败且无法备份时置位：整表写回必须拒绝，避免拿空库盖掉原件。
        self._save_blocked = False
        self.load()

    # ------------------------------------------------------------------
    # 持久化（原子写：临时文件 + os.replace，对应 fm:337-347 的 release(true)）
    # ------------------------------------------------------------------
    def load(self) -> None:
        if not os.path.exists(self.path):
            return  # 首次运行：空库是正常状态，不是损坏
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return  # 竞态：判存在后刚被删，等价于首次运行
        except OSError as exc:
            # 读不了 ≠ 损坏：不备份、不改名，但本次绝不允许整表写回。
            self._save_blocked = True
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.error("书签文件读取失败（%s）：%s；保持空库，且本次拒绝写回，"
                         "避免覆盖可能仍可恢复的文件", type(exc).__name__, self.path)
            return
        except ValueError as exc:
            # 真损坏（JSON 解析失败/编码错误）：先备份 + 理由。
            self._quarantine(exc)
            return
        if not isinstance(data, dict):
            self._quarantine(
                TypeError(f"根节点不是对象，而是 {type(data).__name__}"))
            return

        version = data.get("version")
        if version is None:
            # 旧文件没有 version 字段：按 v1 语义读取，读出后补写 version=2，
            # 让下一次读取走显式校验而不是再次猜测。
            logger.info("书签文件缺少 version 字段，按 v1 语义读取：%s", self.path)
            skipped = self._apply_payload(data)
            if skipped:
                self._quarantine(ValueError(f"{skipped} 条书签/分组无法解析"))
                if self._save_blocked:
                    return
            try:
                self.save()
                logger.info("已补写 version=%s：%s", FILE_VERSION, self.path)
            except OSError as exc:
                logger.warning("补写 version=%s 失败（%s）：%s",
                               FILE_VERSION, exc.__class__.__name__, self.path)
            return

        if version != FILE_VERSION:
            # 明确的拒绝路径：不猜语义、不迁移、不改写文件。
            message = (f"书签文件 version={version!r} 不受支持（只接受 "
                       f"{FILE_VERSION}）：{self.path}；已拒绝读取，原文件保持不动")
            logger.error(message)
            # 拒绝读取之后任何 save() 都不得整表覆盖这个更新版本的文件。
            self._save_blocked = True
            self.last_error = message
            raise UnsupportedBookmarkVersion(message)

        skipped = self._apply_payload(data)
        if skipped:
            self._quarantine(ValueError(f"{skipped} 条书签/分组无法解析"))

    def _quarantine(self, exc: Exception) -> bool:
        """备份损坏原文并写一行理由；备份失败则置位拒写并返回 False。"""
        backup = _backup_corrupt(self.path)
        reason = f"{type(exc).__name__}: {exc}"
        if backup is None:
            self._save_blocked = True
            self.last_error = f"{reason}（无法备份：{self.path}）"
            logger.error("书签文件损坏且备份失败，本次拒绝写回：%s | %s",
                         self.path, reason)
            return False
        write_reason(backup, f"书签文件读取失败（{reason}）：{self.path}", logger)
        self.corrupt_backups.append(backup)
        self.last_error = reason
        logger.warning("书签文件损坏/不可解析，已备份为 %s | %s", backup, reason)
        return True

    def _apply_payload(self, data: dict) -> int:
        """把已通过版本校验（或 v1 迁移）的载荷装入内存，返回无法解析的条目数。"""
        skipped = 0
        groups = data.get("groups", {})
        if not isinstance(groups, dict):
            skipped += 1
            groups = {}
        for g, meta in groups.items():
            if isinstance(meta, dict):
                self.groups[g] = dict(meta)
            else:
                skipped += 1
        self.bookmarks = {g: [] for g in self.groups}
        raw_bookmarks = data.get("bookmarks", [])
        if not isinstance(raw_bookmarks, list):
            skipped += 1
            raw_bookmarks = []
        for bm in raw_bookmarks:
            try:
                b = BookmarkV2.from_dict(bm)
            except (KeyError, TypeError, ValueError):
                # 解析不了的条目不能悄悄丢掉：由 load() 备份原件并留理由。
                skipped += 1
                continue
            if b.group not in self.bookmarks:
                self.bookmarks[b.group] = []
            self.bookmarks[b.group].append(b)
        return skipped

    def _ensure_save_target_healthy(self) -> None:
        """写回前再确认目标文件可读；损坏则先备份，备份失败拒绝写回。"""
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except OSError as exc:
            self._save_blocked = True
            self.last_error = f"{type(exc).__name__}: {exc}"
            message = (f"书签文件读取失败（{type(exc).__name__}: {exc}）：{self.path}；"
                       "本次不写入，避免覆盖可能仍可恢复的文件")
            logger.error(message)
            raise OSError(message) from exc
        except ValueError as exc:
            if not self._quarantine(exc):
                message = (f"书签文件损坏且无法备份（{type(exc).__name__}: {exc}）："
                           f"{self.path}；本次不写入，避免覆盖唯一证据")
                logger.error(message)
                raise OSError(message) from exc
        else:
            if isinstance(data, dict):
                version = data.get("version")
                if version is not None and version != FILE_VERSION:
                    self._save_blocked = True
                    message = (f"书签文件 version={version!r} 与当前 "
                               f"{FILE_VERSION} 不兼容，拒绝整表覆盖：{self.path}")
                    self.last_error = message
                    logger.error(message)
                    raise OSError(message)

    def save(self) -> None:
        if self._save_blocked:
            message = (f"书签文件读取失败且未备份，拒绝整表写回：{self.path}"
                       f"（{self.last_error}）")
            logger.error(message)
            raise OSError(message)
        self._ensure_save_target_healthy()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {
            "version": FILE_VERSION,
            "groups": self.groups,
            "bookmarks": [b.to_dict() for g in self.bookmarks for b in self.bookmarks[g]],
        }
        # 原子写：同目录临时文件 -> replace
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # ------------------------------------------------------------------
    # 分组
    # ------------------------------------------------------------------
    def add_group(self, key: str, label: str, color: str = "#9E9E9E",
                  icon: str = "bookmark") -> None:
        if key in self.groups:
            raise ValueError(f"分组 '{key}' 已存在")
        self.groups[key] = {"label": label, "color": color, "icon": icon}
        self.bookmarks.setdefault(key, [])
        self.save()

    def list_groups(self) -> List[Dict[str, str]]:
        return [{"key": k, **v} for k, v in self.groups.items()]

    # ------------------------------------------------------------------
    # 书签 CRUD
    # ------------------------------------------------------------------
    def add(self, bm: BookmarkV2) -> BookmarkV2:
        if bm.group not in self.bookmarks:
            self.bookmarks[bm.group] = []
        # 同组同名去重
        for existing in self.bookmarks[bm.group]:
            if existing.name == bm.name:
                raise ValueError(f"分组 '{bm.group}' 下书签 '{bm.name}' 已存在")
        self.bookmarks[bm.group].append(bm)
        self.save()
        return bm

    def remove(self, name: str, group: Optional[str] = None) -> bool:
        groups = [group] if group else list(self.bookmarks.keys())
        for g in groups:
            lst = self.bookmarks.get(g, [])
            for i, b in enumerate(lst):
                if b.name == name:
                    del lst[i]
                    self.save()
                    return True
        return False

    def update(self, name: str, group: Optional[str] = None,
               **fields) -> Optional[BookmarkV2]:
        for g, lst in self.bookmarks.items():
            if group and g != group:
                continue
            for i, b in enumerate(lst):
                if b.name == name:
                    for k, v in fields.items():
                        if hasattr(b, k):
                            setattr(b, k, v)
                    self.save()
                    return b
        return None

    def list(self, group: Optional[str] = None) -> List[BookmarkV2]:
        if group:
            return list(self.bookmarks.get(group, []))
        return [b for lst in self.bookmarks.values() for b in lst]

    # ------------------------------------------------------------------
    # nearest 最近邻查找（本仓增强）
    # ------------------------------------------------------------------
    def nearest(self, freq_hz: float,
                max_distance: float = 0.0) -> Optional[Tuple[BookmarkV2, float]]:
        """返回 (最近书签, 绝对频率差 Hz)。max_distance<=0 表示不限距离。"""
        best: Optional[BookmarkV2] = None
        best_dist = float("inf")
        for b in self.list():
            d = abs(b.freq_hz - freq_hz)
            if d < best_dist:
                best_dist = d
                best = b
        if best is None:
            return None
        if max_distance > 0 and best_dist > max_distance:
            return None
        return best, best_dist

    # ------------------------------------------------------------------
    # CSV 导入导出（本仓在 JSON 之外新增 CSV）
    # ------------------------------------------------------------------
    def export_csv(self, path: str, group: Optional[str] = None) -> int:
        rows = self.list(group)
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["name", "freq_hz", "mode", "bandwidth_hz",
                        "group", "notes", "tags"])
            for b in rows:
                w.writerow([b.name, b.freq_hz, b.mode, b.bandwidth_hz,
                            b.group, b.notes, ";".join(b.tags)])
        return len(rows)

    def import_csv(self, path: str) -> int:
        n = 0
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    b = BookmarkV2(
                        name=row["name"],
                        freq_hz=float(row["freq_hz"]),
                        mode=row.get("mode", "NFM"),
                        bandwidth_hz=float(row.get("bandwidth_hz", 0) or 0),
                        group=row.get("group", "ham"),
                        notes=row.get("notes", ""),
                        tags=[t for t in (row.get("tags", "") or "").split(";") if t],
                    )
                except (KeyError, ValueError):
                    continue
                try:
                    self.add(b)
                    n += 1
                except ValueError:
                    # 重名跳过（对应 fm:772-775）
                    continue
        return n

    # ------------------------------------------------------------------
    # MBDSDR 增强：AI 自动分类未知信号到分组
    # ------------------------------------------------------------------
    def classify_signal(self, freq_hz: float,
                        bandwidth_hz: float = 0.0,
                        mode_hint: str = "") -> str:
        """按频率/带宽/模式粗略归类到分组 key。纯规则，可被 LLM 替换。

        不臆造电台，只判断"这类信号通常属于哪个分组"。
        """
        m = mode_hint.upper()
        if m in ("WFM",):
            return "broadcast"
        if m in ("DSC", "SHIP", "MARINE") or (156e6 <= freq_hz <= 163e6):
            return "marine"
        if 118e6 <= freq_hz <= 137e6:
            return "aviation"
        # 业余频段先判（144-148 MHz 与卫星 137-150 重叠，业余优先）
        if any(lo <= freq_hz <= hi for lo, hi in [
            (1.8e6, 4.0e6), (7.0e6, 7.3e6), (10.0e6, 10.2e6),
            (14.0e6, 14.35e6), (18.068e6, 18.168e6), (21.0e6, 21.45e6),
            (24.89e6, 24.99e6), (28.0e6, 29.7e6), (144e6, 148e6),
            (430e6, 440e6),
        ]):
            return "ham"
        if 137e6 <= freq_hz <= 150e6 or 400e6 <= freq_hz <= 406e6:
            return "satellite"
        if 87.5e6 <= freq_hz <= 108e6:
            return "broadcast"
        return "ham"  # 默认兜底
