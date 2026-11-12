# SPDX-License-Identifier: MIT
"""站址单一事实源（single source of truth）。

## 为什么要这个模块

仓库里长期存在两套盘上站址，谁也不知道谁说了算：

* ``~/.mbdsdr/gui_config.json`` 的 ``observer_lat/lon``——桌面 UI 手填、
  示例天空、GNSS 落盘都走这里（``main_window._load/_save_gui_config``）；
* ``~/.mbdsdr/config.json`` 的 ``ground_station_lat/lon``——``mbdsdr_ai.
  station.station_from_config`` 读，agent / controller / MCP 侧消费。

GUI 手填只写前者，后者保持旧值，于是会出现「天空图按新位置算、agent 按旧
位置算」「面板里填了地点、过境列表纹丝不动」这类事故，而且两边都不报错。

本模块把关系钉死：

* **``gui_config.json`` 是唯一权威**——用户显式选的位置；
* **``config.json`` 是单向镜像**——只由 GUI 写，其他模块只读；
* 读侧容错：文件缺失/损坏/越界一律返回 ``None``，**绝不回退到任何城市
  坐标**（回退就是伪造位置）；
* 写侧原子 + 冲突备份：覆盖一个值不同的旧镜像之前先留 ``.bak`` 证据，
  避免"谁把 agent 的位置改了"变成无据可查。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from typing import Any, Dict, Optional

from mbdsdr_ai.config import ConfigStore

logger = logging.getLogger("mbdsdr.station_sync")

__all__ = [
    "GUI_LAT_KEY", "GUI_LON_KEY",
    "CFG_LAT_KEY", "CFG_LON_KEY", "CFG_ALT_KEY",
    "gui_config_path", "config_path",
    "read_gui_site", "gui_site_from", "read_config_site", "merge_sites",
    "write_config_site", "clear_config_site", "last_error",
]

#: 最近一次站址镜像写入的失败原因（None = 正常或未发生过）。
#: 备份失败时绝不能静默：否则用户以为有 ``.bak`` 可找，出事时却找不到。
_LAST_ERROR: Optional[str] = None


def last_error() -> Optional[str]:
    """返回最近一次站址镜像写入的失败原因（成功或未发生则 None）。"""
    return _LAST_ERROR


def _set_error(message: Optional[str]) -> None:
    global _LAST_ERROR
    _LAST_ERROR = message
    if message:
        logger.warning("%s", message)


GUI_LAT_KEY = "observer_lat"
GUI_LON_KEY = "observer_lon"
CFG_LAT_KEY = "ground_station_lat"
CFG_LON_KEY = "ground_station_lon"
CFG_ALT_KEY = "ground_station_alt_m"


def gui_config_path() -> str:
    """GUI 权威配置路径（与 ``main_window`` 里用的是同一个）。"""
    return os.path.expanduser("~/.mbdsdr/gui_config.json")


def config_path() -> str:
    """agent/controller 读的配置文件路径（本模块只镜像写这一侧）。"""
    return os.path.expanduser("~/.mbdsdr/config.json")


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Optional[Dict[str, Any]]:
    """读 JSON 对象；任何异常都返回 None（调用方是启动链，不能因坏文件崩）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _site_from(data: Optional[Dict[str, Any]], lat_key: str,
               lon_key: str) -> Optional[Dict[str, float]]:
    """从 dict 抽站址；缺键/非数值/越界一律 None。

    注意 ``0.0/0.0`` 是**合法坐标**（几内亚湾），不做任何"零值=未设置"
    的哨兵判断——那会把真实站址吃掉。
    """
    if not data:
        return None
    lat, lon = data.get(lat_key), data.get(lon_key)
    if lat is None or lon is None:
        return None
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return None
    return {"lat": lat_f, "lon": lon_f}


def gui_site_from(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """从已读好的 ``gui_config`` dict 抽站址（``read_gui_site`` 的无 IO 版本）。

    读入口只有 ``read_gui_site`` 与本函数（同一套键名/校验）。需要整个
    gui_config 的调用方（``MainWindow._load_gui_config``）把手上这份 dict
    交给这里，不要再自己 ``data.get("observer_lat")``——两处各解析一次，
    校验口径（范围、非数值）就会分叉。
    """
    return _site_from(data, GUI_LAT_KEY, GUI_LON_KEY)


def read_gui_site(path: Optional[str] = None) -> Optional[Dict[str, float]]:
    """用户权威站址；未配置返回 None。"""
    return gui_site_from(_read_json(path or gui_config_path()))


def read_config_site(path: Optional[str] = None) -> Optional[Dict[str, float]]:
    """镜像侧站址（agent/controller 读的那份）。"""
    return _site_from(_read_json(path or config_path()),
                      CFG_LAT_KEY, CFG_LON_KEY)


def merge_sites(gui: Optional[Dict[str, float]],
                cfg: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
    """合并策略：GUI 优先；GUI 没有才用镜像（供旧版本一次性迁移）。

    不在这里做"取平均""取较新"之类的聪明事——站址不是可以折中的量。
    """
    return gui or cfg


# --------------------------------------------------------------------------- #
# 写（只由 GUI 调）
# --------------------------------------------------------------------------- #
def write_config_site(lat: float, lon: float, alt_m: Optional[float] = 0.0,
                      path: Optional[str] = None) -> bool:
    """把站址镜像进 ``config.json``，保留文件里其它键。

    返回是否写成功；失败不抛（写盘失败不该让 UI 操作崩）。
    写盘走 :class:`ConfigStore`（唯一写入口）：锁内重读磁盘再改，避免与
    AI 面板 / 设置面板同时写时互相吃掉对方刚写的键。
    覆盖一个「值不同」的旧镜像前，先把原文件备份成 ``<path>.bak``。
    """
    target = path or config_path()
    try:
        new_lat, new_lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False

    def _mirror(data: Dict[str, Any]) -> Optional[bool]:
        old = _site_from(data, CFG_LAT_KEY, CFG_LON_KEY)
        conflict = old is not None and (
            abs(old["lat"] - new_lat) > 1e-9 or abs(old["lon"] - new_lon) > 1e-9)
        if conflict:
            backup = target + ".bak"
            try:
                shutil.copy2(target, backup)
            except OSError as exc:
                # 备份失败 ≠ 没有备份：说「留了 .bak」而实际没有，比不备份
                # 更坏——用户出事时会去找一个不存在的文件。此时继续写会覆盖
                # 旧站址这唯一证据，所以必须留痕并放弃本次写入。
                _set_error(
                    f"站址镜像备份失败（{type(exc).__name__}: {exc}）："
                    f"{target} → {backup}；本次未写入，避免覆盖唯一证据")
                return False
        data[CFG_LAT_KEY] = new_lat
        data[CFG_LON_KEY] = new_lon
        if alt_m is not None:
            try:
                data[CFG_ALT_KEY] = float(alt_m)
            except (TypeError, ValueError):
                pass
        return True

    try:
        ok = ConfigStore(target).update(_mirror)
    except OSError as exc:
        _set_error(
            f"站址镜像写入失败（{type(exc).__name__}: {exc}）：{target}")
        return False
    if ok:
        _set_error(None)
    return ok


def clear_config_site(path: Optional[str] = None) -> bool:
    """用户清空位置时，镜像也必须清——否则 agent 会继续按旧位置算。

    文件不存在、损坏或本来就没有这几个键时返回 True（已经是目标状态）。
    """
    target = path or config_path()
    data = _read_json(target)
    if not data:
        return True
    if not any(k in data for k in (CFG_LAT_KEY, CFG_LON_KEY, CFG_ALT_KEY)):
        return True

    def _drop(data: Dict[str, Any]) -> None:
        for key in (CFG_LAT_KEY, CFG_LON_KEY, CFG_ALT_KEY):
            data.pop(key, None)

    try:
        return ConfigStore(target).update(_drop)
    except OSError:
        return False
