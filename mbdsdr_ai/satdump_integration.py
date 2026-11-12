# SPDX-License-Identifier: MIT
"""
MBDSDR AI - SatDump 外部工具集成层
=====================================

若本机安装了 SatDump 命令行工具，本模块通过子进程调用它，
把气象卫星解码能力作为可选后端暴露为 MCP 工具。本模块只做进程封装，
不包含 SatDump 的任何源代码；SatDump 仅作外部可执行技术参考。

参考：https://www.satdump.org/
"""

import os
import subprocess
import json
from typing import Dict, List, Optional, Tuple
from pathlib import Path

try:  # 可选依赖：仅用于读取 product.cbor 的通道/投影元数据
    import cbor2 as _cbor2
except ImportError:  # pragma: no cover - 取决于运行环境
    _cbor2 = None


# ========================================================================
# SatDump 路径检测
# ========================================================================

SATDUMP_PATHS = [
    "/usr/bin/satdump",
    "/usr/local/bin/satdump",
    "/opt/satdump/bin/satdump",
    "satdump",  # PATH中
]


def find_satdump() -> Optional[str]:
    """查找 SatDump 可执行文件。找不到/不可执行一律返回 None（诚实空态），
    不允许把 PermissionError / 无效可执行体（WinError 193）等抛给调用方。"""
    for path in SATDUMP_PATHS:
        try:
            result = subprocess.run(
                [path, "--help"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0 or b"satdump" in result.stderr.lower():
                return path
        except (OSError, subprocess.TimeoutExpired):
            # OSError 覆盖 FileNotFoundError / PermissionError / 无效可执行体
            continue
    return None


def check_satdump_installed() -> Dict:
    """检查 SatDump 是否安装。"""
    path = find_satdump()
    if path:
        return {
            "installed": True,
            "path": path,
            "message": f"SatDump 已安装: {path}",
        }
    else:
        return {
            "installed": False,
            "path": None,
            "message": "SatDump 未安装。安装方法: https://www.satdump.org/",
        }


# ========================================================================
# SatDump 命令封装
# ========================================================================

def satdump_live(satellite: str, frequency: float, output_dir: str,
                 samplerate: int = 2048000, gain: float = 30.0) -> Dict:
    """
    SatDump 实时接收模式。

    参数:
        satellite: 卫星名称（如 gk2a_lrit / fy4a_lrit / noaa19）
        frequency: 中心频率（Hz）
        output_dir: 输出目录
        samplerate: 采样率（Hz）
        gain: 增益（dB）
    """
    satdump_path = find_satdump()
    if not satdump_path:
        return {"success": False, "error": "SatDump 未安装"}

    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        satdump_path, "live",
        satellite,
        output_dir,
        "--source", "rtlsdr",
        "--samplerate", str(samplerate),
        "--frequency", str(int(frequency)),
        "--gain", str(gain),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,  # 30秒后超时（实际接收需要更长时间）
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-500:],  # 最后500字符
            "stderr": result.stderr[-500:],
            "command": ' '.join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "satdump 启动超时（30s 未完成），进程已被 kill",
            "command": ' '.join(cmd),
        }


def satdump_process(input_file: str, satellite: str, output_dir: str) -> Dict:
    """
    SatDump 离线处理模式。

    参数:
        input_file: 输入IQ文件
        satellite: 卫星名称
        output_dir: 输出目录
    """
    satdump_path = find_satdump()
    if not satdump_path:
        return {"success": False, "error": "SatDump 未安装"}

    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        satdump_path, "processing",
        input_file,
        satellite,
        output_dir,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5分钟超时
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-1000:],
            "stderr": result.stderr[-1000:],
            "command": ' '.join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "处理超时"}


# ========================================================================
# 支持的卫星列表（SatDump数据库）
# ========================================================================

SATDUMP_SATELLITES = {
    # 气象卫星 - 同步轨道
    "gk2a_lrit": {"name": "GK-2A LRIT", "freq": 1692.14e6},
    "fy4a_lrit": {"name": "FY-4A LRIT", "freq": 1697e6},
    "fy4a_hrit": {"name": "FY-4A HRIT", "freq": 1681e6},
    "goes16_lrit": {"name": "GOES-16 LRIT", "freq": 1692e6},
    "goes17_lrit": {"name": "GOES-17 LRIT", "freq": 1692e6},
    "himawari8_lrit": {"name": "Himawari-8 LRIT", "freq": 1686e6},

    # 气象卫星 - 极轨
    "noaa15_apt": {"name": "NOAA-15 APT", "freq": 137.62e6},
    "noaa18_apt": {"name": "NOAA-18 APT", "freq": 137.91e6},
    "noaa19_apt": {"name": "NOAA-19 APT", "freq": 137.1e6},
    "meteor_m2_hrpt": {"name": "Meteor-M2 HRPT", "freq": 1700e6},
    "fy3_hrpt": {"name": "FY-3 HRPT", "freq": 1704.5e6},

    # 其他
    "iss_sstv": {"name": "ISS SSTV", "freq": 145.8e6},
}


def list_satdump_satellites() -> List[Dict]:
    """列出 SatDump 支持的卫星。"""
    result = []
    for key, info in SATDUMP_SATELLITES.items():
        result.append({
            "key": key,
            "name": info["name"],
            "frequency_mhz": info["freq"] / 1e6,
        })
    return result


# ========================================================================
# 图像合成工具
# ========================================================================

# 与历史 glob 口径一致：只认 png / jpg，不扩面
_IMAGE_SUFFIXES = (".png", ".jpg")

#: 允许随 selection 透出的 product.cbor 图像级键
_IMAGE_METADATA_KEYS = (
    "abs_index", "bit_depth", "wavenumber", "polarization",
    "bandwidth", "calibration_type",
)


def _read_dataset_manifest(
        input_dir: Path) -> Tuple[Optional[Tuple[Path, Dict]], str]:
    """定位并解析 SatDump 的 dataset.json。

    SatDump 的 DataSet.load() 读的就是这个文件（satellite / timestamp /
    products 相对路径列表）。返回 ((清单目录, 清单内容), "") 或
    (None, 原因)；原因取值 no_manifest / bad_manifest，供调用方如实说明
    退回 glob 的理由（旧版产物没有清单）。
    """
    root_manifest = input_dir / "dataset.json"
    if root_manifest.is_file():
        candidates: List[Path] = [root_manifest]
    else:
        try:
            candidates = sorted(
                p for p in input_dir.glob("**/dataset.json") if p.is_file()
            )
        except OSError:
            candidates = []
        if not candidates:
            return None, "no_manifest"

    for manifest_path in candidates:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("products"), list):
            return (manifest_path.parent, data), ""
    return None, "bad_manifest"


def _load_product_metadata(product_dir: Path) -> Optional[Dict]:
    """读取 product.cbor 元数据；cbor2 未装 / 文件缺失 / 解析失败 → None。

    这是纯增强项：任何失败都退回文件名规则，不允许影响选图或抛错，
    也不把 cbor2 变成硬依赖。要启用按清单取图，装 extras 即可：
    ``pip install "mbdsdr-ai[satdump]"``（pyproject [project.optional-dependencies].satdump）。
    """
    if _cbor2 is None:
        return None
    try:
        data = _cbor2.loads((product_dir / "product.cbor").read_bytes())
    except Exception:  # noqa: BLE001 - 可选增强，坏文件一律忽略
        return None
    return data if isinstance(data, dict) else None


def _metadata_image_entries(
        product_dir: Path, metadata: Optional[Dict]) -> List[Tuple[Path, Dict]]:
    """按 product.cbor 的 images 顺序返回磁盘上确实存在的图像条目。"""
    entries: List[Tuple[Path, Dict]] = []
    if not isinstance(metadata, dict):
        return entries
    images = metadata.get("images")
    if not isinstance(images, list):
        return entries

    base = product_dir.resolve()
    for item in images:
        if not isinstance(item, dict):
            continue
        filename = item.get("file")
        if not isinstance(filename, str) or not filename:
            continue
        path = (product_dir / filename).resolve()
        try:
            path.relative_to(base)
        except ValueError:
            continue
        try:
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
                entries.append((path, item))
        except OSError:
            continue
    return entries


def _files_by_suffix(product_dir: Path) -> Dict[str, List[Path]]:
    """按扩展名分组并排序（png 先于 jpg，同类文件名升序）。"""
    grouped: Dict[str, List[Path]] = {}
    try:
        entries = list(product_dir.iterdir())
    except OSError:
        return grouped
    for p in entries:
        suffix = p.suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            try:
                if p.is_file():
                    grouped.setdefault(suffix, []).append(p)
            except OSError:
                continue
    for group in grouped.values():
        group.sort(key=lambda q: q.name)
    return grouped


def _pick_image_in_product(
        product_dir: Path, metadata: Optional[Dict] = None
) -> Tuple[Optional[Path], Optional[Dict], int]:
    """在产品目录内选图，返回 (图像路径, 图像元数据, 可选图像总数)。

    有 product.cbor 时遵循 SatDump 的 images 顺序（images[0] 优先），元数据
    与所选文件一一对应；没有 CBOR 时用「png 先于 jpg、同类文件名升序」的
    确定性规则——行为与历史实现一致。
    """
    meta_entries = _metadata_image_entries(product_dir, metadata)
    if meta_entries:
        return meta_entries[0][0], meta_entries[0][1], len(meta_entries)

    grouped = _files_by_suffix(product_dir)
    total = sum(len(group) for group in grouped.values())
    for suffix in _IMAGE_SUFFIXES:
        group = grouped.get(suffix, [])
        if group:
            return group[0], None, total
    return None, None, 0


def _select_image_by_manifest(
        input_dir: Path) -> Tuple[Optional[Tuple[Path, Dict]], str]:
    """按 dataset.json 的 products 清单选图（与 SatDump 遍历 products_list 同构）。

    清单顺序即 SatDump 的写盘顺序，第一个含图产品的第一张图即选中项；
    产品内若装了 cbor2 则再按 product.cbor 的 images 顺序精确取图，并把
    仪器/产品类型/通道/波段/投影等元数据一并带出。清单不可用时不猜，
    返回 (None, 原因) 让调用方走 glob 兜底。
    """
    found, reason = _read_dataset_manifest(input_dir)
    if found is None:
        return None, reason
    manifest_dir, data = found

    base_dir = manifest_dir.resolve()
    for entry in data.get("products") or []:
        if not isinstance(entry, str) or not entry:
            continue
        product_dir = (manifest_dir / entry).resolve()
        try:
            # 清单项必须落在数据集目录内，越界视为坏清单项
            product_dir.relative_to(base_dir)
        except ValueError:
            continue
        if not product_dir.is_dir():
            continue

        metadata = _load_product_metadata(product_dir)
        chosen, image_meta, image_count = _pick_image_in_product(
            product_dir, metadata)
        if chosen is None:
            continue

        selection: Dict = {
            "source": "dataset.json",
            "manifest": str(manifest_dir / "dataset.json"),
            "product": entry,
            "satellite": data.get("satellite"),
            "timestamp": data.get("timestamp"),
            "image_count": image_count,
            "image_index": 1,
            # 序号来自 images 列表位置还是文件名排序，输出文案必须区分
            "picked_by": "product_cbor" if isinstance(image_meta, dict) else "filename",
        }
        if isinstance(metadata, dict):
            for key in ("instrument", "type"):
                value = metadata.get(key)
                if isinstance(value, str) and value:
                    selection[key] = value
            if "product_timestamp" in metadata:
                selection["product_timestamp"] = metadata.get("product_timestamp")
        if isinstance(image_meta, dict):
            channel = image_meta.get("name")
            if isinstance(channel, str) and channel:
                selection["channel"] = channel
            for key in _IMAGE_METADATA_KEYS:
                if key in image_meta:
                    selection[key] = image_meta.get(key)
        return (chosen, selection), ""
    return None, "no_images"


def compose_cloud_image(input_dir: str, output_file: str,
                        enhance: bool = True) -> Dict:
    """
    从 SatDump 输出目录合成云图。

    选图规则：优先读 SatDump 的 dataset.json 清单，按 products 顺序取第一个
    含图产品的第一张图（可复现）；无清单 / 清单坏 / 清单无图时退回历史
    glob 行为，兼容旧版产物。

    参数:
        input_dir: SatDump输出目录
        output_file: 输出PNG文件
        enhance: 是否增强（直方图均衡）
    """
    from PIL import Image, ImageEnhance
    import numpy as np

    # 查找输出图像
    output_path = Path(input_dir)

    selected, fallback_reason = _select_image_by_manifest(output_path)
    if selected is not None:
        image_path, selection = selected
    else:
        # 旧版 SatDump 产物没有 dataset.json：沿用历史 glob 逻辑（不改语义）
        images = list(output_path.glob("**/*.png")) + list(output_path.glob("**/*.jpg"))
        if not images:
            return {"success": False, "error": "未找到输出图像"}
        image_path = images[0]
        selection = {
            "source": "glob",
            "fallback_reason": fallback_reason,
            "image_count": len(images),
            "image_index": 1,
            "picked_by": "filename",
        }

    # 取选中的图像
    img = Image.open(image_path)

    # 图像增强
    if enhance:
        # 直方图均衡
        img_array = np.array(img)
        if len(img_array.shape) == 2:
            # 灰度图
            img_array = ((img_array - img_array.min()) /
                         (img_array.max() - img_array.min() + 1e-6) * 255)
            img = Image.fromarray(img_array.astype(np.uint8))
        else:
            # 彩色图
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.5)

    # 保存
    img.save(output_file)

    return {
        "success": True,
        "input_image": str(image_path),
        "output_image": output_file,
        "size": img.size,
        "selection": selection,
    }
