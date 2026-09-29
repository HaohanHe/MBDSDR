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
import os
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple


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
        self.load()

    # ------------------------------------------------------------------
    # 持久化（原子写：临时文件 + os.replace，对应 fm:337-347 的 release(true)）
    # ------------------------------------------------------------------
    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for g, meta in data.get("groups", {}).items():
            self.groups[g] = dict(meta)
        self.bookmarks = {g: [] for g in self.groups}
        for bm in data.get("bookmarks", []):
            try:
                b = BookmarkV2.from_dict(bm)
            except KeyError:
                continue
            if b.group not in self.bookmarks:
                self.bookmarks[b.group] = []
            self.bookmarks[b.group].append(b)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {
            "version": 2,
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
