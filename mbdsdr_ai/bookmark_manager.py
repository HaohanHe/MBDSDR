# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 书签管理器
============================

设计要点（书签模型/分组/标签、频率区间二分查询、CSV 导入导出、标签去重；
GQRX 仅作技术参考，本仓未包含其源代码）：

持久化到 ``~/.mbdsdr/bookmarks.json``。兼容导入 GQRX CSV：
``Frequency,Name,Modulation,Bandwidth,Color``（逗号分隔，5 列），
同时兼容 GQRX 原生分号格式（``Freq;Name;Mod;BW;Tags``）。

红线：**不预存任何地区电台**——库初始为空，全部由用户导入/添加。

我们的增强：
  * :meth:`BookmarkManager.suggest_mode`：根据频率/带宽/功率包络启发式建议
    模式与带宽（不依赖任何预存电台库）。
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import bisect
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .corrupt_evidence import write_reason

logger = logging.getLogger(__name__)


DEFAULT_COLOR = "#888888"
UNTAGGED = "Untagged"


@dataclass(order=False)
class Bookmark:
    """一条书签记录。

    Attributes:
        frequency_hz: 中心频率（Hz，int）。
        name:         名称。
        modulation:   模式，如 "FM" / "AM" / "USB" / "CW"。
        bandwidth_hz: 信道带宽（Hz）。
        color:        显示颜色（#RRGGBB）。
        tags:         标签列表（分组/过滤用）。
        group:        分组名（树形分组的一个层级）。
    """

    frequency_hz: int
    name: str = ""
    modulation: str = "FM"
    bandwidth_hz: int = 12500
    color: str = DEFAULT_COLOR
    tags: List[str] = field(default_factory=list)
    group: str = ""
    # 以下三项来自 FrequencyManager 的旧条目：同一份 bookmarks.json 由两个
    # 组件共用，缺了它们 FrequencyManager 一保存就会把区间/备注丢掉。
    start_hz: float = 0.0
    end_hz: float = 0.0
    note: str = ""

    def __lt__(self, other: "Bookmark") -> bool:  # 按频率排序
        return self.frequency_hz < other.frequency_hz


def _from_legacy_entry(d: dict) -> "Bookmark":
    """把 FrequencyManager 的旧条目（freq_hz/mode/category）转成 Bookmark。"""
    def _num(key: str, default: float = 0.0) -> int:
        try:
            return int(float(d.get(key, default) or default))
        except (TypeError, ValueError):
            return int(default)

    return Bookmark(
        frequency_hz=_num("frequency_hz", _num("freq_hz")),
        name=str(d.get("name", "")),
        modulation=str(d.get("modulation") or d.get("mode") or "FM"),
        bandwidth_hz=_num("bandwidth_hz", 12500) or 12500,
        group=str(d.get("group") or d.get("category") or ""),
        start_hz=float(_num("start_hz") or 0.0),
        end_hz=float(_num("end_hz") or 0.0),
        note=str(d.get("note", "")),
    )


def backup_corrupt_bookmarks(path: Path) -> Optional[str]:
    """把损坏的书签文件改名成 ``*.corrupt-时间戳``，返回备份路径。

    只改名不删除：原件字节必须原样留在备份里，后续写新文件才不会吞掉证据。
    改名失败返回 None，调用方据此拒绝写盘。

    这是损坏留证的**公共接缝**：测试模拟「备份失败」时替换本函数返回
    ``None``，不再改模块私有名。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, backup)
    except OSError as exc:
        logger.error("书签损坏文件备份失败：%s（%s）", path, exc)
        return None
    return str(backup)


class BookmarkManager:
    """书签管理器：增删改查、分组树、标签、搜索、CSV 导入导出、JSON 持久化。"""

    def __init__(self, config_dir: Optional[os.PathLike] = None,
                 bookmarks_path: Optional[os.PathLike] = None):
        if config_dir is None:
            config_dir = Path.home() / ".mbdsdr"
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.bookmarks_path = (Path(bookmarks_path) if bookmarks_path
                               else self.config_dir / "bookmarks.json")
        self.bookmarks_path.parent.mkdir(parents=True, exist_ok=True)
        self._bookmarks: List[Bookmark] = []
        self._tag_colors: Dict[str, str] = {UNTAGGED: DEFAULT_COLOR}
        #: 上次读取失败的人话说明（None = 正常）；调用方可据此提示用户。
        self.last_error: Optional[str] = None
        #: 损坏文件的备份路径（None = 未损坏或备份失败）。
        self.corrupt_backup: Optional[str] = None
        #: 损坏且备份失败时拒绝写盘：此时写新文件会覆盖唯一证据。
        self._save_blocked = False
        self.load()

    # ------------------------------------------------------------------ CRUD
    def add(self, bm: Bookmark) -> None:
        """追加书签并按频率有序插入。"""
        bisect.insort(self._bookmarks, bm)
        for t in bm.tags:
            self.find_or_add_tag(t)
        self.save()

    def remove(self, frequency_hz: int, name: str = "") -> bool:
        """按频率（+可选名称）删除。返回是否删除成功。"""
        for i, b in enumerate(self._bookmarks):
            if b.frequency_hz == frequency_hz and (not name or b.name == name):
                del self._bookmarks[i]
                self.save()
                return True
        return False

    def update(self, frequency_hz: int, **kwargs) -> bool:
        """更新指定频率书签的字段。"""
        for b in self._bookmarks:
            if b.frequency_hz == frequency_hz:
                for k, v in kwargs.items():
                    if hasattr(b, k):
                        setattr(b, k, v)
                if "tags" in kwargs:
                    for t in kwargs["tags"]:
                        self.find_or_add_tag(t)
                self._bookmarks.sort()
                self.save()
                return True
        return False

    def all(self) -> List[Bookmark]:
        return list(self._bookmarks)

    # ------------------------------------------------------------ range/search
    def in_range(self, low_hz: int, high_hz: int) -> List[Bookmark]:
        """返回 [low, high] 内的书签（二分查找）。"""
        lo = bisect.bisect_left(self._bookmarks, Bookmark(frequency_hz=low_hz))
        hi = bisect.bisect_right(self._bookmarks, Bookmark(frequency_hz=high_hz))
        return list(self._bookmarks[lo:hi])

    def search(self, query: str) -> List[Bookmark]:
        """按名称/标签/模式/分组不区分大小写过滤。"""
        if not query:
            return self.all()
        q = query.strip().lower()
        out = []
        for b in self._bookmarks:
            hay = " ".join(
                [b.name, b.modulation, b.group, " ".join(b.tags)]
            ).lower()
            if q in hay:
                out.append(b)
        return out

    # ------------------------------------------------------------------ tags
    def find_or_add_tag(self, tag: str) -> str:
        """返回 tag 名（不存在则新建）。"""
        tag = (tag or "").strip() or UNTAGGED
        if tag not in self._tag_colors:
            self._tag_colors[tag] = DEFAULT_COLOR
        return tag

    def set_tag_color(self, tag: str, color: str) -> None:
        self._tag_colors[self.find_or_add_tag(tag)] = color

    def groups(self) -> Dict[str, List[Bookmark]]:
        """按 group 字段聚合成分组树（空 group 归入 ''）。"""
        out: Dict[str, List[Bookmark]] = {}
        for b in self._bookmarks:
            out.setdefault(b.group or "", []).append(b)
        return out

    # ------------------------------------------------------------ persistence
    def load(self) -> bool:
        if not self.bookmarks_path.exists():
            self._bookmarks = []
            self.last_error = None
            self.corrupt_backup = None
            self._save_blocked = False
            return False
        try:
            data = json.loads(self.bookmarks_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self._mark_corrupt(f"解析失败（{exc.__class__.__name__}：{exc}）")
            return False
        if isinstance(data, list):
            # 兼容 FrequencyManager 写入的旧格式（裸列表，字段名不同）。
            # 两个组件共用 ~/.mbdsdr/bookmarks.json，读到哪种都得能起床。
            self._tag_colors = {UNTAGGED: DEFAULT_COLOR}
            self._bookmarks = [_from_legacy_entry(d) for d in data
                               if isinstance(d, dict)]
            self._bookmarks.sort()
            self.last_error = None
            self.corrupt_backup = None
            self._save_blocked = False
            return True
        if not isinstance(data, dict):
            self._mark_corrupt(f"顶层不是对象（{type(data).__name__}）")
            return False
        self._tag_colors = data.get("tag_colors", {UNTAGGED: DEFAULT_COLOR})
        self._bookmarks = [Bookmark(**b) for b in data.get("bookmarks", [])]
        self._bookmarks.sort()
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False
        return True

    def _mark_corrupt(self, reason: str) -> None:
        """损坏文件：备份原件、留人话错误；备份失败则禁止后续写盘。"""
        self._bookmarks = []
        self._tag_colors = {UNTAGGED: DEFAULT_COLOR}
        self.corrupt_backup = backup_corrupt_bookmarks(self.bookmarks_path)
        self._save_blocked = self.corrupt_backup is None
        message = (
            f"书签文件读取失败（{reason}）：{self.bookmarks_path}；"
            f"原文件已备份为 {self.corrupt_backup or '（备份失败）'}，"
            "本次按空书签列表启动"
        )
        # 备份已成功 → 才写理由；理由写失败只记日志，备份与启动都不受影响。
        if self.corrupt_backup:
            write_reason(self.corrupt_backup, message, logger)
        self.last_error = message
        logger.warning("书签文件不可用（%s）：%s；备份=%s", self.bookmarks_path,
                       reason, self.corrupt_backup or "（备份失败）")

    def save(self) -> bool:
        """写回书签；损坏且未能备份时拒绝写入，避免覆盖唯一证据。"""
        if self._save_blocked:
            self.last_error = (
                f"书签未保存：{self.bookmarks_path} 已损坏且备份失败；"
                "为避免覆盖唯一证据，本次不写入。"
                "请检查目录权限或手动移走该文件后重试")
            logger.error("书签保存被拒绝（损坏文件未能备份）：%s",
                         self.bookmarks_path)
            return False
        data = {
            "tag_colors": self._tag_colors,
            "bookmarks": [asdict(b) for b in self._bookmarks],
        }
        self.bookmarks_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True

    # ------------------------------------------------------------ CSV import/export
    def import_csv_text(self, text: str) -> int:
        """导入 GQRX CSV 文本，返回导入条数。

        支持两种列分隔：
          * 逗号（任务指定格式）：``Frequency,Name,Modulation,Bandwidth,Color``
          * 分号（GQRX 原生）：``Frequency;Name;Modulation;Bandwidth;Tags``
        跳过 ``#`` 注释行与空行；列数不对的行忽略。
        """
        imported = 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # 自动判定分隔符
            delim = ";" if line.count(";") >= line.count(",") else ","
            parts = [p.strip() for p in line.split(delim)]
            if len(parts) < 4:
                continue
            try:
                freq = int(float(parts[0]))
            except ValueError:
                continue
            name = parts[1] if len(parts) > 1 else ""
            mod = parts[2] if len(parts) > 2 else "FM"
            try:
                bw = int(float(parts[3]))
            except ValueError:
                bw = 12500
            color = parts[4] if len(parts) > 4 and parts[4] else DEFAULT_COLOR
            # 第 5 列在分号格式下是 tags（逗号分隔），在逗号格式下是 color
            tags: List[str] = []
            if delim == ";" and len(parts) > 4 and parts[4]:
                tags = [t.strip() for t in parts[4].split(",") if t.strip()]
            bm = Bookmark(
                frequency_hz=freq, name=name, modulation=mod,
                bandwidth_hz=bw, color=color if delim == "," else DEFAULT_COLOR,
                tags=tags,
            )
            # 去重：同频率同名不重复加
            if not any(
                x.frequency_hz == freq and x.name == name for x in self._bookmarks
            ):
                self.add(bm)
                imported += 1
        return imported

    def export_csv_text(self) -> str:
        """导出为任务指定的逗号 CSV：Frequency,Name,Modulation,Bandwidth,Color。"""
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Frequency", "Name", "Modulation", "Bandwidth", "Color"])
        for b in self._bookmarks:
            w.writerow([b.frequency_hz, b.name, b.modulation, b.bandwidth_hz, b.color])
        return buf.getvalue()

    # ------------------------------------------------------------ AI suggestion
    def suggest_mode(self, frequency_hz: int, bandwidth_hz: int,
                     power_profile: Optional[str] = None) -> Dict[str, object]:
        """根据频率/带宽/功率包络启发式建议模式与带宽（我们的增强）。

        不查任何预存电台库，纯规则：
          * BW <= 50  Hz  -> CW
          * BW <= 3 kHz   -> 语音边带（HF 用 USB，MF 以下用 AM）
          * BW <= 20 kHz  -> NFM / NBFM
          * BW <= 200 kHz -> WFM
          * 否则 -> "RAW"
        power_profile ∈ {"weak","normal","strong"} 用于提示增益。
        """
        bw = bandwidth_hz
        if bw <= 50:
            mode = "CW"
        elif bw <= 3000:
            mode = "USB" if frequency_hz >= 10_000_000 else "AM"
        elif bw <= 20_000:
            mode = "NFM"
        elif bw <= 200_000:
            mode = "WFM"
        else:
            mode = "RAW"
        gain_hint = {"weak": +6.0, "normal": 0.0, "strong": -3.0}.get(
            power_profile or "normal", 0.0
        )
        return {"suggested_mode": mode, "suggested_bandwidth_hz": bw,
                "gain_delta_db": gain_hint}
