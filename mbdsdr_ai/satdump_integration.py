"""
MBDSDR AI - SatDump 集成层
============================

通过子进程调用 SatDump 命令行工具，
将气象卫星解码能力暴露为 MCP 工具。

参考：https://www.satdump.org/
"""

import os
import subprocess
import json
from typing import Dict, List, Optional, Tuple
from pathlib import Path


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
    """查找 SatDump 可执行文件。"""
    for path in SATDUMP_PATHS:
        try:
            result = subprocess.run(
                [path, "--help"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0 or b"satdump" in result.stderr.lower():
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
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
        str(frequency),
        output_dir,
        "-s", str(samplerate),
        "-g", str(gain),
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
            "success": True,
            "message": "接收已启动（后台运行中）",
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

def compose_cloud_image(input_dir: str, output_file: str,
                        enhance: bool = True) -> Dict:
    """
    从 SatDump 输出目录合成云图。

    参数:
        input_dir: SatDump输出目录
        output_file: 输出PNG文件
        enhance: 是否增强（直方图均衡）
    """
    from PIL import Image, ImageEnhance
    import numpy as np

    # 查找输出图像
    output_path = Path(input_dir)
    images = list(output_path.glob("**/*.png")) + list(output_path.glob("**/*.jpg"))

    if not images:
        return {"success": False, "error": "未找到输出图像"}

    # 取第一个图像
    img = Image.open(images[0])

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
        "input_image": str(images[0]),
        "output_image": output_file,
        "size": img.size,
    }
