"""
MBDSDR 桌面端面板注册表
========================

集中声明所有可用面板的元数据（id / 名称 / 图标 / 默认停靠区 / 弹性尺寸约束 /
是否可折叠）。注册表本身**不实例化任何 Qt 控件**——面板由 MainWindow 在构建
中央区与 Dock 时创建，再通过 ``attach_widget(id, widget)`` 挂进来。

这样做的好处：
  * 新增面板只改这一处声明，DockLayoutManager 按 id 布局，MainWindow 不散落。
  * 尺寸用"比例 + 最小/最大弹性约束"，不写死像素（与 tokens.RATIO 对齐）。
  * 可被测试离线查询（不需要 QApplication 就能 list()/get()）。

红线：注册表只描述"有哪些面板、默认放哪、多大多小弹性"，不造任何业务数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tokens import tokens


# 停靠区语义（与 QDockWidgetArea 对应的逻辑分区）
AREA_CENTER = "center"   # 中央主表面（QMainWindow.centralWidget 的 Tab 页）
AREA_LEFT   = "left"
AREA_RIGHT  = "right"
AREA_BOTTOM = "bottom"
AREA_FLOAT  = "float"    # 悬浮叠加


@dataclass(frozen=True)
class PanelSpec:
    """单个面板的静态元数据。"""
    id: str
    name: str
    icon: str                 # 单字符图标（文字 glyph，避免彩色 emoji 依赖）
    area: str = AREA_CENTER   # 默认停靠区
    collapsible: bool = True  # 是否可折叠成 36px 图标窄条
    # 弹性尺寸约束（不给死值：min_w 兜底，ratio_hint 仅作初始建议）
    min_w: float = 280.0
    ratio_hint: float = 0.0   # 初始占可用宽度比例（0 = 由布局预设决定）
    closable: bool = True


class PanelRegistry:
    """面板元数据 + 运行时控件挂载的注册表（单例）。"""

    _instance: "PanelRegistry | None" = None

    def __new__(cls) -> "PanelRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._specs: Dict[str, PanelSpec] = {}
            cls._instance._widgets: Dict[str, object] = {}
            cls._instance._register_defaults()
        return cls._instance

    # ------------------------------------------------------------------
    # 默认面板清单（与 MainWindow 里实际创建的面板一一对应）
    # ------------------------------------------------------------------
    def _register_defaults(self) -> None:
        t = tokens()
        center_min = t.RATIO["card_min_w"]
        side_min   = t.SIZE["min_dock_w"]
        specs = [
            # 中央主表面（Tab 页，可全屏铺满）
            PanelSpec("spectrum",   "频谱",       "≡", AREA_CENTER,
                      collapsible=False, min_w=center_min,
                      ratio_hint=t.RATIO["splitter_center"]),
            PanelSpec("rf_sky",     "射频天空",   "◎", AREA_CENTER,
                      collapsible=False, min_w=center_min,
                      ratio_hint=t.RATIO["splitter_center"]),
            PanelSpec("weather",    "气象云图",   "☁", AREA_CENTER,
                      collapsible=False, min_w=center_min),
            PanelSpec("doppler",    "多普勒定轨", "⌖", AREA_CENTER,
                      collapsible=False, min_w=center_min),
            PanelSpec("module",      "模块/信号流","▤", AREA_CENTER,
                      collapsible=False, min_w=center_min),
            PanelSpec("sat_track",   "卫星跟踪",   "◍", AREA_CENTER,
                      collapsible=False, min_w=center_min),
            PanelSpec("new_spacetime","新时空",    "◷", AREA_CENTER,
                      collapsible=False, min_w=center_min),
            PanelSpec("adsb",       "ADS-B 航路", "✈", AREA_CENTER,
                      collapsible=False, min_w=center_min),
            # 右侧停靠面板（可折叠成窄条 / 可悬浮）
            PanelSpec("control",    "控制",       "⚙", AREA_RIGHT,
                      collapsible=True, min_w=side_min,
                      ratio_hint=t.RATIO["focus_side"]),
            PanelSpec("status",      "状态",       "▦", AREA_RIGHT,
                      collapsible=True, min_w=side_min),
            PanelSpec("ai",          "AI 助手",    "✦", AREA_RIGHT,
                      collapsible=True, min_w=side_min),
        ]
        for s in specs:
            self._specs[s.id] = s

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def register(self, spec: PanelSpec) -> None:
        """注册/覆盖一个面板元数据。"""
        self._specs[spec.id] = spec

    def get(self, panel_id: str) -> Optional[PanelSpec]:
        """按 id 取元数据，不存在返回 None。"""
        return self._specs.get(panel_id)

    def list(self, area: Optional[str] = None) -> List[PanelSpec]:
        """列出全部面板；可选按停靠区过滤。"""
        if area is None:
            return list(self._specs.values())
        return [s for s in self._specs.values() if s.area == area]

    def attach_widget(self, panel_id: str, widget: object) -> None:
        """MainWindow 创建控件后挂进来（注册表不拥有控件生命周期）。"""
        if panel_id in self._specs:
            self._widgets[panel_id] = widget

    def widget(self, panel_id: str) -> Optional[object]:
        """取已挂载的控件（未挂载返回 None）。"""
        return self._widgets.get(panel_id)

    def ids(self) -> List[str]:
        return list(self._specs.keys())


def registry() -> PanelRegistry:
    """返回全局 PanelRegistry 单例。"""
    return PanelRegistry()
