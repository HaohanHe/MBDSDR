# SPDX-License-Identifier: MIT
"""``mbdsdr_ai.location`` — 地点检索子包。

只有一件事：把「地名 → 经纬度」做成可替换的 provider，界面不绑定任何
具体数据源（当前 OSM，将来可换高德）。详见 ``places.py``。
"""
from .places import (  # noqa: F401
    MIN_INTERVAL_S,
    OfflineJsonProvider,
    OsmNominatimProvider,
    Place,
    PlaceProvider,
    USER_AGENT,
    haversine_km,
    make_provider,
)

__all__ = [
    "MIN_INTERVAL_S", "OfflineJsonProvider", "OsmNominatimProvider", "Place",
    "PlaceProvider", "USER_AGENT", "haversine_km", "make_provider",
]
