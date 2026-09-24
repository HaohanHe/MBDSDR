"""
MBDSDR AI - OpenAPI 集成层
=============================

将公开API查询能力封装为MCP工具，
让AI能调用外部API获取卫星/气象/GNSS数据。

支持的API类别：
- 卫星数据（NOAA/GK-2A/ISS）
- 气象数据
- GNSS/RTK
- ADS-B航空
- AIS船舶
"""

import math

import requests
from typing import Dict, List, Optional


# ========================================================================
# 卫星API
# ========================================================================

def get_iss_position() -> Dict:
    """获取ISS（国际空间站）当前位置。"""
    try:
        resp = requests.get("http://api.open-notify.org/iss-now.json", timeout=5)
        data = resp.json()
        if data.get("message") == "success":
            pos = data["iss_position"]
            return {
                "success": True,
                "latitude": float(pos["latitude"]),
                "longitude": float(pos["longitude"]),
                "timestamp": data["timestamp"],
                "message": "ISS位置获取成功",
            }
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": False, "error": "获取失败"}


def get_people_in_space() -> Dict:
    """获取当前太空人数。"""
    try:
        resp = requests.get("http://api.open-notify.org/astros.json", timeout=5)
        data = resp.json()
        return {
            "success": True,
            "number": data.get("number", 0),
            "people": [p["name"] for p in data.get("people", [])],
            "message": f"当前太空有{data.get('number', 0)}人",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ========================================================================
# 气象API
# ========================================================================

def get_weather(lat: float, lon: float) -> Dict:
    """获取指定位置天气（Open-Meteo免费API）。"""
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,wind_speed_10m,relative_humidity_2m,weather_code"
        resp = requests.get(url, timeout=5)
        data = resp.json()

        current = data.get("current", {})
        return {
            "success": True,
            "latitude": lat,
            "longitude": lon,
            "temperature_c": current.get("temperature_2m"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "weather_code": current.get("weather_code"),
            "message": f"温度{current.get('temperature_2m', '?')}°C，风速{current.get('wind_speed_10m', '?')}km/h",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ========================================================================
# ADS-B API（飞机位置）
# ========================================================================

def get_aircraft_nearby(lat: float, lon: float, radius_km: float = 50) -> Dict:
    """获取附近飞机位置（OpenSky Network免费API）。"""
    try:
        # 计算边界框
        dlat = radius_km / 111.0
        # 经度 1 度 ≈ 111 * cos(lat) km，低纬地区 cos 接近 1
        lat_rad = math.radians(lat)
        dlon = radius_km / (111.0 * max(abs(math.cos(lat_rad)), 0.01))

        url = f"https://opensky-network.org/api/states/all?lamin={lat-dlat}&lamax={lat+dlat}&lomin={lon-dlon}&lomax={lon+dlon}"
        resp = requests.get(url, timeout=10)
        data = resp.json()

        states = data.get("states", [])
        aircraft = []
        for s in states[:10]:  # 最多10架
            aircraft.append({
                "callsign": s[1].strip() if s[1] else "UNKNOWN",
                "origin_country": s[2],
                "longitude": s[5],
                "latitude": s[6],
                "altitude_m": s[7],
                "velocity_ms": s[9],
                "heading": s[10],
            })

        return {
            "success": True,
            "count": len(states),
            "aircraft": aircraft,
            "radius_km": radius_km,
            "message": f"半径{radius_km}km内有{len(states)}架飞机",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ========================================================================
# 卫星过境预测API
# ========================================================================

def get_satellite_passes(sat_id: int, lat: float, lon: float, days: int = 7) -> Dict:
    """预测卫星过境（N2YO免费API需要key，这里用简化版）。"""
    return {
        "success": True,
        "message": "卫星过境预测请使用内置工具 new_spacetime",
        "note": "内置sgp4+skyfield计算更精确",
        "satellite_id": sat_id,
        "latitude": lat,
        "longitude": lon,
        "days": days,
    }


# ========================================================================
# API目录
# ========================================================================

AVAILABLE_APIS = {
    "iss_position": {
        "name": "ISS位置",
        "endpoint": "get_iss_position",
        "description": "获取国际空间站实时位置",
        "requires_key": False,
    },
    "people_in_space": {
        "name": "太空人数",
        "endpoint": "get_people_in_space",
        "description": "获取当前太空人数和姓名",
        "requires_key": False,
    },
    "weather": {
        "name": "天气预报",
        "endpoint": "get_weather",
        "description": "获取指定位置天气",
        "requires_key": False,
    },
    "aircraft_nearby": {
        "name": "附近飞机",
        "endpoint": "get_aircraft_nearby",
        "description": "获取附近飞机位置（ADS-B）",
        "requires_key": False,
    },
}


def list_available_apis() -> List[Dict]:
    """列出所有可用的外部API。"""
    result = []
    for key, info in AVAILABLE_APIS.items():
        result.append({
            "key": key,
            "name": info["name"],
            "description": info["description"],
            "requires_key": info["requires_key"],
        })
    return result
