#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""证据包公共件：隔离 profile、产物目录、空帧守卫、统一结果结构。

设计红线
--------
* **导入顺序是硬约束**：任何 group 脚本都必须先 ``isolate_profile()``，
  再 ``import PySide6`` / ``import main_window``。Qt 在 QApplication 构造时
  就按环境变量定位配置目录，晚一步产物就会写进真实用户的 ``~/.mbdsdr``
  ——那是污染用户数据，不是"跑了个测试"。
* **本模块不导入 Qt**。run_all 要在没有 PySide6 的机器上也能把
  ``architecture`` 这组跑完，并如实把其余组报成 unavailable，而不是崩。
* **空帧必须失败**。``widget.grab()`` 在窗口没真正建起来时返回一张纯色图，
  看着像截图成功。这类假证据比没有证据更坏，所以 save_frame 会算亮度
  标准差，太扁就抛 :class:`FlatFrame`。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

#: tools/evidence/common.py → 仓库根
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

#: 各组默认产物目录（run_all 会传 --out 覆盖）
DEFAULT_OUT: Path = REPO_ROOT / "evidence-out"

#: 结果协议版本；字段增删都要动它（契约测试会钉住）
RESULT_SCHEMA = 1


def repo_root() -> Path:
    return REPO_ROOT


def add_repo_paths() -> None:
    """把仓库根与 desktop/ 放进 sys.path（desktop 模块是平铺导入）。"""
    for p in (str(REPO_ROOT), str(REPO_ROOT / "desktop")):
        if p not in sys.path:
            sys.path.insert(0, p)


# --------------------------------------------------------------------------
# 环境隔离
# --------------------------------------------------------------------------

def isolate_profile(prefix: str = "mbdsdr_evidence_") -> Path:
    """把 HOME/APPDATA 指到一个全新临时目录，返回该目录。

    必须在导入 Qt / main_window **之前**调用。不这么做的话，每组脚本都会
    读写真实 ``~/.mbdsdr/gui_config.json``：用户存过的位置、形态、窗口几何
    会被悄悄改掉，而这在截图里看不出来。
    """
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    for sub in ("AppData/Roaming", "AppData/Local"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(tmp)
    os.environ["USERPROFILE"] = str(tmp)
    os.environ["APPDATA"] = str(tmp / "AppData" / "Roaming")
    os.environ["LOCALAPPDATA"] = str(tmp / "AppData" / "Local")
    # 离线：不碰真机、不联网授时、不调模型 API。想联网的组自己关。
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    return tmp


def ensure_qt_platform() -> None:
    """无显示器的 Linux 上退到 offscreen；Windows/macOS 不动。"""
    if sys.platform.startswith("linux"):
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --------------------------------------------------------------------------
# Qt 小工具（惰性导入）
# --------------------------------------------------------------------------

def pump(app, n: int = 30, dt: float = 0.02) -> None:
    """转 n 次事件循环。Qt 的绘制/布局是异步的，grab 前必须转够。"""
    for _ in range(n):
        app.processEvents()
        time.sleep(dt)


class FlatFrame(RuntimeError):
    """截图几乎纯色 —— 说明窗口没建起来或 grab 抓了个空，不许当证据。"""


def save_frame(widget, path: Path, *, min_std: float = 0.8,
               allow_flat: bool = False) -> Dict[str, Any]:
    """抓一帧并存盘，返回 {name,path,w,h,bytes,mean,std}。

    亮度标准差低于 ``min_std`` 视为空帧。这不是洁癖：一整屏星空和
    "窗口没 show 出来"在文件大小上可能一样，只有方差能分开。
    """
    from PySide6.QtGui import QImage

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix = widget.grab()
    if pix.isNull():
        raise FlatFrame(f"grab() 返回空 pixmap: {path.name}")
    if not pix.save(str(path)):
        raise FlatFrame(f"pixmap 存盘失败: {path}")

    img = QImage(str(path))
    if img.isNull():
        raise FlatFrame(f"存盘后无法回读: {path}")
    small = img.scaled(64, 64)
    vals: List[float] = []
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            vals.append(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    if std < min_std and not allow_flat:
        raise FlatFrame(
            f"{path.name} 亮度标准差 {std:.2f} < {min_std}：疑似空帧/窗口未建起来")
    return {
        "name": path.stem,
        "path": str(path),
        "w": int(img.width()),
        "h": int(img.height()),
        "bytes": path.stat().st_size,
        "mean": round(mean, 2),
        "std": round(std, 2),
    }


def visible_texts(widget) -> List[str]:
    """可见且有文字内容的控件文本（用来衡量首屏信息负荷）。"""
    from PySide6.QtWidgets import QLabel, QPushButton, QToolButton

    out: List[str] = []
    for cls in (QLabel, QPushButton, QToolButton):
        for c in widget.findChildren(cls):
            try:
                if not c.isVisible():
                    continue
                text = str(c.text() or "").strip()
            except (AttributeError, RuntimeError):
                continue
            if text:
                out.append(text.replace("\n", " / ")[:70])
    return out


# --------------------------------------------------------------------------
# 结果结构
# --------------------------------------------------------------------------

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def host_info() -> Dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def json_safe(obj: Any) -> Any:
    """把 numpy 标量/ndarray 等转成可 json 的原生类型。"""
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (str, bool)) or obj is None:
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        # nan/inf 不是合法 JSON，改成字符串，别让 json.dump 写坏文件
        if obj != obj or obj in (float("inf"), float("-inf")):
            return str(obj)
        return float(obj)
    if hasattr(obj, "item"):
        try:
            return json_safe(obj.item())
        except (TypeError, ValueError):
            pass
    if hasattr(obj, "tolist"):
        try:
            return json_safe(obj.tolist())
        except (TypeError, ValueError):
            pass
    return str(obj)


def new_result(gid: str, title: str, *, status: str = "pass") -> Dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "id": gid,
        "title": title,
        "status": status,          # pass | degraded | skipped | fail
        "facts": {},
        "frames": [],
        "notes": [],               # 跳过了什么、为什么
        "not_verified": [],        # 这一组里"没验到"的具体链路
        "started_at": now_iso(),
        "duration_s": 0.0,
    }


def write_result(path: Optional[Path], result: Dict[str, Any]) -> Dict[str, Any]:
    result = json_safe(result)
    if path is not None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    return result


def emit(res: Dict[str, Any], frames_dir: Path) -> None:
    """人读输出：把 facts / notes / not_verified 摊平打印。"""
    print(f"[{res['id']}] {res['title']}  ->  {res['status'].upper()}")
    for k, v in res.get("facts", {}).items():
        print(f"    {k} = {v}")
    for fr in res.get("frames", []):
        print(f"    frame {fr['name']}: {fr['w']}x{fr['h']} "
              f"{fr['bytes']}B std={fr['std']} -> {fr['path']}")
    for n in res.get("notes", []):
        print(f"    note: {n}")
    for n in res.get("not_verified", []):
        print(f"    not_verified: {n}")
    print(f"    dir: {frames_dir}")


def parse_common_args(argv: Optional[List[str]] = None) -> "argparse.Namespace":
    """每组共用的 --out / --json-out。"""
    import argparse

    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help=f"产物目录（默认 {DEFAULT_OUT}）")
    p.add_argument("--json-out", default=None,
                   help="把本组结果写成 JSON（run_all 用）")
    return p.parse_args(argv)


def finish(res: Dict[str, Any], out_dir: Path, json_out: Optional[str],
           started: float) -> int:
    """统一收尾：写 JSON、打印、给退出码。"""
    res["duration_s"] = round(time.time() - started, 1)
    write_result(Path(json_out) if json_out else None, res)
    emit(res, out_dir)
    return 0 if res["status"] in ("pass", "degraded") else 1


def which_all(names: Iterable[str]) -> Dict[str, Optional[str]]:
    return {n: shutil.which(n) for n in names}
