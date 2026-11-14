# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端面板注册表
========================

集中声明所有可用面板的元数据（id / 名称 / 图标 / 默认停靠区 / 弹性尺寸约束 /
是否可折叠 / **构造工厂**）。注册表本身不缓存控件实例：面板由 MainWindow 在
构建中央区与 Dock 时调 ``create(id)`` 取得，再通过 ``attach_widget(id, widget)``
挂回来（注册表不拥有生命周期）。

这样做的好处：
  * 新增面板只改这一处声明（元数据 + 工厂），DockLayoutManager 按 id 布局，
    MainWindow 不再逐个 ``ControlPanel()`` 硬编码构造。
  * 尺寸用"比例 + 最小/最大弹性约束"，不写死像素（与 tokens.RATIO 对齐）。
  * 可被测试离线查询（不需要 QApplication 就能 list()/get()；只有 create()
    才真正 import Qt 模块）。

红线：注册表只描述"有哪些面板、默认放哪、多大多小弹性、怎么构造"，不造任何
业务数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

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
    # 一句话能力说明（帮助面板「能力目录」直接取此字段，避免另抄一份清单）
    summary: str = ""
    # 零参构造工厂：返回该面板的 Qt 控件；可选面板不可用时返回 None。
    # 放在最后一个字段，既有按位置传参的调用方（含测试）不受影响。
    factory: Optional[Callable[[], object]] = None


# ---------------------------------------------------------------------------
# 面板工厂（声明式构造）
#
# 用惰性 import 而不是模块顶部 import：``panel_registry`` 要被纯逻辑测试在
# 没有 QApplication 的环境里 import（list()/get() 不碰 Qt），只有真正
# ``create()`` 时才把控件模块拉进来。工厂只负责「造对象」，信号接线仍在
# MainWindow（接线是宿主职责，不是面板元数据）。
# ---------------------------------------------------------------------------
def _cls_factory(module_name: str, class_name: str) -> Callable[[], object]:
    """``module.class()`` 形式的零参工厂（惰性 import）。"""
    def _factory():
        mod = __import__(module_name)
        cls = getattr(mod, class_name)
        return cls()
    return _factory


def _optional_factory(module_name: str,
                      class_name: str) -> Callable[[], object]:
    """可选面板：模块/类缺失时返回 None（宿主按「无此面板」降级）。"""
    def _factory():
        try:
            mod = __import__(module_name)
        except Exception:  # noqa: BLE001 - 缺依赖不得拖垮启动
            return None
        cls = getattr(mod, class_name, None)
        if cls is None:
            return None
        try:
            return cls()
        except Exception:  # noqa: BLE001 - 控件构造失败等同「无此面板」
            return None
    return _factory


def _spectrum_factory():
    """频谱控件：与 MainWindow 原调用完全一致的参数（prefer_opengl=False）。"""
    from spectrum_widget import create_spectrum_widget
    return create_spectrum_widget(prefer_opengl=False)


def _sky_factory():
    """星象仪控件：路B工厂 ``create_sky_widget()`` 优先，回退 RFSkyView。"""
    try:
        from rf_sky_view import create_sky_widget as _factory
        widget = _factory()
        if widget is not None:
            return widget
    except Exception:  # noqa: BLE001 - 工厂缺失/异常时回退，与旧行为一致
        pass
    from rf_sky_view import RFSkyView
    return RFSkyView()


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
                      ratio_hint=t.RATIO["splitter_center"],
                      summary="实时频谱与瀑布；拖动谱面改频，滚轮缩放，单键切解调模式",
                      factory=_spectrum_factory),
            PanelSpec("rf_sky",     "星象仪模式", "◎", AREA_CENTER,
                      collapsible=False, min_w=center_min,
                      ratio_hint=t.RATIO["splitter_center"],
                      summary="全天球视图：恒星/行星/深空/卫星；点天体看信息，点卫星调谐收听",
                      factory=_sky_factory),
            PanelSpec("weather",    "气象云图",   "☁", AREA_CENTER,
                      collapsible=False, min_w=center_min,
                      summary="气象卫星云图：GOES/GK-2A/Meteor/NOAA/FY 用录制文件离线解码出图"
                              "（不实时边收边解）",
                      factory=_cls_factory("weather_panel", "WeatherPanel")),
            PanelSpec("module",      "模块/信号流","▤", AREA_CENTER,
                      collapsible=False, min_w=center_min,
                      summary="SDR++ 式信号流：源设备 → 解调 → 解码 → 输出，逐段调参数",
                      factory=_cls_factory("module_panel", "ModulePanel")),
            PanelSpec("sat_track",   "卫星跟踪",   "◍", AREA_CENTER,
                      collapsible=False, min_w=center_min,
                      summary="选卫星后闭环跟踪：500ms 更新方位/仰角/多普勒并自动调谐",
                      factory=_cls_factory("sat_track_panel", "SatTrackPanel")),
            PanelSpec("new_spacetime","新时空",    "◷", AREA_CENTER,
                      collapsible=False, min_w=center_min,
                      summary="调制自动识别 + 卫星过境时间线与授时 + 频率-轨道关系图",
                      factory=_cls_factory("new_spacetime_panel",
                                           "NewSpacetimePanel")),
            PanelSpec("adsb",       "ADS-B 航路", "✈", AREA_CENTER,
                      collapsible=False, min_w=center_min,
                      summary="ADS-B 飞机位置图与航班列表；只画真实解算出的经纬度",
                      factory=_cls_factory("adsb_map_panel", "AdsbMapPanel")),
            # 右侧停靠面板（可折叠成窄条 / 可悬浮）
            PanelSpec("control",    "控制",       "⚙", AREA_RIGHT,
                      collapsible=True, min_w=side_min,
                      ratio_hint=t.RATIO["focus_side"],
                      summary="接收参数：频率/解调/带宽/增益/静噪/采样率与音量",
                      factory=_cls_factory("control_panel", "ControlPanel")),
            PanelSpec("status",      "状态",       "▦", AREA_RIGHT,
                      collapsible=True, min_w=side_min,
                      summary="设备/信号/GNSS/接收参数实时读数；无数据显「—」",
                      factory=_cls_factory("status_panel", "StatusPanel")),
            PanelSpec("ai",          "AI 助手",    "✦", AREA_RIGHT,
                      collapsible=True, min_w=side_min,
                      summary="用自然语言描述需求，AI 调用工具完成调谐/解码/录制等操作",
                      factory=_cls_factory("ai_panel", "AIPanel")),
            # 以下两个是右栏页签（MainWindow._attach_digital_panels 创建），
            # 此前只在代码里存在、不在注册表，能力目录与「面板」菜单都看不到。
            PanelSpec("aprs",        "APRS 数据包", "✉", AREA_RIGHT,
                      collapsible=True, min_w=side_min,
                      summary="解出的 AX.25/APRS 报文列表；需 NFM 解调且中心频率对准 "
                              "144.390/144.800 MHz 等 APRS 频点",
                      factory=_optional_factory("aprs_panel", "AprsPanel")),
            PanelSpec("sat_image",   "卫星云图",   "▨", AREA_RIGHT,
                      collapsible=True, min_w=side_min,
                      summary="NOAA APT / Meteor LRPT 云图与最近产物；离线 IQ 出图，"
                              "不是实时边收边解",
                      factory=_optional_factory("satellite_image_panel",
                                                "SatelliteImagePanel")),
            # 右栏固定页签（MainWindow._add_right_tab 挂载）：此前由主窗口
            # 逐个 new，现在统一走注册表工厂。closable=False —— 它们没有关闭
            # 按钮，也不进「面板」菜单（右栏导航已能到达），避免同文案动作
            # 在两处菜单里重复。
            PanelSpec("scanner",     "扫频",       "⌕", AREA_RIGHT,
                      collapsible=True, closable=False, min_w=side_min,
                      summary="在指定频段内自动扫频，找出活动频点；点活动段可直接调谐过去",
                      factory=_optional_factory("scanner_panel", "ScannerPanel")),
            PanelSpec("modulation",  "调制识别",   "◔", AREA_RIGHT,
                      collapsible=True, closable=False, min_w=side_min,
                      summary="对当前信号做调制识别并给出候选解调方式，可一键切换",
                      factory=_optional_factory("modulation_panel",
                                                "ModulationPanel")),
            PanelSpec("bookmark",    "书签",       "★", AREA_RIGHT,
                      collapsible=True, closable=False, min_w=side_min,
                      summary="频率书签增删改与快速调谐；随配置目录持久化",
                      factory=_optional_factory("bookmark_panel", "BookmarkPanel")),
            PanelSpec("anr",         "ANR",        "≋", AREA_RIGHT,
                      collapsible=True, closable=False, min_w=side_min,
                      summary="自适应降噪：谱减/门限等模式与降噪强度，可学习底噪",
                      factory=_optional_factory("anr_panel", "AnrPanel")),
            PanelSpec("constellation", "星座图",   "✧", AREA_RIGHT,
                      collapsible=True, closable=False, min_w=side_min,
                      summary="IQ 星座图与幅度/EVM 统计；用来判断解调质量",
                      factory=_optional_factory("constellation_panel",
                                                "ConstellationPanel")),
            PanelSpec("device",      "设备",       "▣", AREA_RIGHT,
                      collapsible=True, closable=False, min_w=side_min,
                      summary="SDR 设备枚举、连接/断开与音频输出选择；感知热插拔",
                      factory=_optional_factory("device_panel", "DevicePanel")),
            PanelSpec("gain",        "增益",       "▥", AREA_RIGHT,
                      collapsible=True, closable=False, min_w=side_min,
                      summary="分段增益与 AGC 开关；滑杆值实时同步后端，失败可见",
                      factory=_optional_factory("gain_panel", "GainPanel")),
            PanelSpec("adsb_list",   "ADS-B 列表", "☷", AREA_RIGHT,
                      collapsible=True, closable=False, min_w=side_min,
                      summary="ADS-B 报文文本列表（呼号/高度/速度/位置）；"
                              "与航路图共用同一份解码数据",
                      factory=_optional_factory("adsb_panel", "AdsbPanel")),

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

    def create(self, panel_id: str) -> Optional[object]:
        """按声明的工厂构造一个**新**控件（不缓存、不挂载）。

        返回 None 的两种情况：id 未注册；或该面板声明为可选且依赖缺失。
        工厂自身抛异常一律向上冒——那是接线/依赖错误，不该被静默吞掉。
        """
        spec = self._specs.get(panel_id)
        if spec is None or spec.factory is None:
            return None
        return spec.factory()

    def factoryless(self) -> List[str]:
        """没有声明工厂的面板 id（用于测试守住「构造已声明式」）。"""
        return [s.id for s in self._specs.values() if s.factory is None]

    def list(self, area: Optional[str] = None) -> List[PanelSpec]:
        """列出全部面板；可选按停靠区过滤。"""
        if area is None:
            return list(self._specs.values())
        return [s for s in self._specs.values() if s.area == area]

    def attach_widget(self, panel_id: str, widget: object) -> None:
        """MainWindow 创建控件后挂进来（注册表不拥有控件生命周期）。"""
        if panel_id in self._specs:
            self._widgets[panel_id] = widget

    def detach_widget(self, panel_id: str, widget: object) -> None:
        """attach_widget 的对称摘除：仅当当前挂载正是该控件时才摘。

        条件摘除防止旧窗口关闭时误摘新窗口已挂载的同 id 控件
        （单例 _widgets 是强引用表，不摘则旧窗整棵子树不可回收）。
        先判在不在表里：get 对缺失键返回 None，若只比 is 则
        detach(未挂载 id, None) 会去 del 不存在的键（KeyError）。
        """
        if panel_id in self._widgets and self._widgets[panel_id] is widget:
            del self._widgets[panel_id]

    def widget(self, panel_id: str) -> Optional[object]:
        """取已挂载的控件（未挂载返回 None）。"""
        return self._widgets.get(panel_id)

    def ids(self) -> List[str]:
        return list(self._specs.keys())


def registry() -> PanelRegistry:
    """返回全局 PanelRegistry 单例。"""
    return PanelRegistry()
