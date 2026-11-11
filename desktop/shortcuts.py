# SPDX-License-Identifier: MIT
"""
桌面端快捷键登记表（唯一事实源）
================================

main_window 只从 ``bind(id)`` 取键序列，不在各构建函数里散落字符串；
``tests/test_shortcuts_guard.py`` 反向核对窗口里实际绑定的 QAction /
QShortcut 与本表是否一致——新增按钮忘了登记会直接红。

约定：
  * ``key=None`` 表示该动作**故意不配快捷键**，必须在 ``note`` 里写原因；
  * ``scope`` 区分生效范围：window=主窗恒生效，planetarium=仅星象仪形态，
    spectrum=仅频谱控件自身获焦时生效；
  * id 命名 ``域.动作``，域与菜单/顶栏分区一致。

键位归属规则：**焦点在哪，局部优先**。
-------------------------------------
同一键序列允许在不同 scope 各登记一条（例如 ``A`` 同时是
``view.all_sky``（window）和 ``spectrum.mode.am``（spectrum））：

  * 频谱控件自身获焦 → A 归频谱的 AM 解调，主窗「全天模式」不抢键；
  * 频谱没有焦点（焦点在天空视图/主窗其他区域）→ A 归主窗，进入全天模式。

实现靠 Qt 的上下文优先级，不靠 keyPressEvent 拦截：
频谱侧的局部键装成 ``QShortcut`` + ``Qt.WidgetShortcut``（只在父控件自身
获焦时匹配，是 Qt 里最具体的上下文）；主窗侧的全天模式仍是
``WidgetWithChildrenShortcut``，并在槽内跳过「焦点属于频谱局部键作用域」
的情形兜底。这样输入框里按 A 只会输入字母，频谱里按 A 只切 AM。
"""

from typing import Dict, List, NamedTuple, Optional, Tuple


class Shortcut(NamedTuple):
    id: str
    key: Optional[str]
    label: str
    scope: str = "window"
    note: str = ""


SHORTCUTS: Tuple[Shortcut, ...] = (
    # ---- 文件菜单 ----
    Shortcut("file.connect", "Ctrl+C", "连接设备...", note="焦点在文本框时放行复制"),
    Shortcut("file.disconnect", "Ctrl+D", "断开连接"),
    Shortcut("file.quit", "Ctrl+Q", "退出"),
    # ---- 视图菜单 ----
    Shortcut("view.waterfall", "Ctrl+Shift+W", "显示瀑布图"),
    Shortcut("view.fullscreen", "F11", "全屏"),
    Shortcut("view.reset_position", None, "重置窗口位置",
             note="窗口跑到屏幕外时的救援入口，低频操作，只从「视图」菜单点击"),
    Shortcut("view.all_sky", "A", "全天模式（160°）",
             note="焦点在频谱时该键归频谱的 A=AM（局部优先）；频谱无焦点时"
                  "才进入全天模式。规则见本文件顶部「焦点在哪，局部优先」"),
    Shortcut("view.planetarium", "Ctrl+Shift+P", "星象仪模式"),
    Shortcut("view.planetarium_exit", "Esc", "星象仪形态退出", scope="planetarium",
             note="只在该形态启用；先关 AI 回复卡再退出形态"),
    Shortcut("planetarium.search", "Ctrl+F", "找天体（星象仪）",
             scope="planetarium",
             note="只在该形态启用：把天空搜索框抬到画面底部并聚焦；"
                  "IDE 形态不劫持 Ctrl+F，留给文本/列表自身的查找语义"),
    # ---- 工具菜单 ----
    Shortcut("tools.sweep", "Ctrl+Shift+F", "FM 扫频找台"),
    Shortcut("tools.record", "Ctrl+R", "开始/停止录音"),
    Shortcut("tools.ntrip", "Ctrl+Shift+N", "NTRIP 设置"),
    Shortcut("tools.gnss_connect", "Ctrl+Shift+G", "连接 GNSS..."),
    Shortcut("tools.gnss_disconnect", None, "断开 GNSS",
             note="与连接成对，重复配键会与连接抢同一次按键"),
    Shortcut("tools.sat_start", None, "开始跟踪选中卫星", note="需先选卫星，低频操作"),
    Shortcut("tools.sat_stop", None, "停止卫星跟踪", note="与开始跟踪成对"),
    Shortcut("tools.sat_tle", None, "更新卫星 TLE (Celestrak)",
             note="联网操作，避免误触"),
    Shortcut("tools.about", "F1", "关于 MBDSDR"),
    # ---- 帮助菜单 ----
    # 可发现性入口：内容由本表与注册表实时生成，不另抄一份键位/能力清单。
    # 不配键是刻意的——F1 已绑「关于」，避免抢占系统帮助键的既有语义。
    Shortcut("help.quick_start", None, "三步上手",
             note="帮助菜单常驻入口；低频操作，不占系统帮助键 F1"),
    Shortcut("help.catalog", None, "能力目录",
             note="帮助菜单常驻入口；从注册表实时生成，低频操作"),
    Shortcut("help.shortcuts", None, "快捷键一览",
             note="帮助菜单常驻入口；内容由本表生成，低频操作"),
    Shortcut("help.command_palette", "Ctrl+K", "命令面板",
             note="一个搜索框定位菜单命令/面板页签/能力/快捷键；想不起入口在哪时用"),
    # ---- 面板菜单 ----
    Shortcut("panel.capability_matrix", None, "能力矩阵（◧ 事实源）",
             note="能力只读清单，低频入口，不占快捷键"),
    Shortcut("panel.reset_layout", None, "恢复出厂布局", note="破坏性操作，要求点击确认"),
    # ---- 顶栏 ----
    Shortcut("layout.save", "Ctrl+Shift+S", "保存"),
    Shortcut("layout.delete", None, "删除", note="破坏性操作，内置布局本就置灰"),
    Shortcut("vfo.new", "Ctrl+Alt+V", "+ VFO", note="避开输入框里的 Ctrl+Shift+V 粘贴"),
    Shortcut("vfo.cycle", "Ctrl+Tab", "切换主听"),
    Shortcut("vfo.mute", "Ctrl+M", "静音"),
    Shortcut("vfo.replay", "Ctrl+Shift+R", "回放"),
    # ---- 频谱视图（焦点内局部键，scope=spectrum）----
    # 只在频谱控件自身获焦时生效（QShortcut + Qt.WidgetShortcut）。这些键
    # 不进主窗 QAction，避免焦点在输入框/其他面板时误触。同一键位可与
    # window 条目重复（见本文件顶部「焦点在哪，局部优先」）。
    Shortcut("spectrum.mode.am", "A", "AM 解调", scope="spectrum",
             note="频谱获焦时 A=AM；焦点不在频谱时 A 归「全天模式」"),
    Shortcut("spectrum.mode.nfm", "N", "NFM 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.mode.fm", "F", "FM 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.mode.wfm", "W", "WFM 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.mode.cw", "C", "CW 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.sideband.lsb", "S", "LSB 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.sideband.usb", "Shift+S", "USB 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.tune.left", "Left", "频率 −1 格（Shift ×10 / Alt ×0.1）",
             scope="spectrum", note="频谱获焦时生效；按网格步进向下调谐"),
    Shortcut("spectrum.tune.right", "Right", "频率 +1 格（Shift ×10 / Alt ×0.1）",
             scope="spectrum", note="频谱获焦时生效；按网格步进向上调谐"),
    Shortcut("spectrum.tune.up", "Up", "频率细调 +（1/10 格）", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.tune.down", "Down", "频率细调 −（1/10 格）",
             scope="spectrum", note="频谱获焦时生效"),
    Shortcut("spectrum.filter.narrow", ",", "滤波收窄一档", scope="spectrum",
             note="频谱获焦时生效；按当前模式的带宽预设表步进"),
    Shortcut("spectrum.filter.wide", ".", "滤波放宽一档", scope="spectrum",
             note="频谱获焦时生效；按当前模式的带宽预设表步进"),
)

#: id -> Shortcut（构建期按 id 取键）
BY_ID = {s.id: s for s in SHORTCUTS}

# ---------------------------------------------------------------------------
# 用户覆写层（「设置 → 快捷键」可改键）
# ---------------------------------------------------------------------------
# 默认键位写在上面的表里，是**唯一事实源**；用户改过的键只叠一层覆写存在
# QSettings（`shortcuts/<id>`）。「恢复默认」= 删掉覆写键，不改代码、不留
# 第二份默认表——否则两份默认值迟早不一致。
_OVERRIDES: Optional[Dict[str, str]] = None


def _store():
    """QSettings 句柄：与 FloatingPanel / 布局记忆同一个 node。"""
    from PySide6.QtCore import QSettings
    return QSettings("MBDSDR", "Desktop")


def load_overrides(force: bool = False) -> Dict[str, str]:
    """读用户覆盖表（id -> 键序列）。损坏/未知 id 一律忽略，不抛。"""
    global _OVERRIDES
    if _OVERRIDES is not None and not force:
        return _OVERRIDES
    out: Dict[str, str] = {}
    try:
        st = _store()
        st.beginGroup("shortcuts")
        for key in st.childKeys():
            if key not in BY_ID:
                continue          # 旧版本遗留 id：静默丢弃，别让它复活
            val = str(st.value(key) or "").strip()
            if val:
                out[key] = val
        st.endGroup()
    except Exception as e:  # noqa: BLE001 - 读配置失败按「没有覆写」降级，但要留证
        import logging
        logging.getLogger(__name__).warning(
            "读取快捷键覆写失败（按默认键位继续）: %s: %s", type(e).__name__, e)
    _OVERRIDES = out
    return out


def default_key(shortcut_id: str) -> Optional[str]:
    """出厂键位（用于「恢复默认」按钮与差异显示）。"""
    return BY_ID[shortcut_id].key


def bind(shortcut_id: str) -> Optional[str]:
    """按登记 id 取**当前生效**键序列（覆写优先，其次出厂值）。

    未登记的 id 直接抛 KeyError（构建期就暴露）。
    """
    entry = BY_ID[shortcut_id]
    overrides = load_overrides()
    if shortcut_id in overrides:
        return overrides[shortcut_id]
    return entry.key


def is_overridden(shortcut_id: str) -> bool:
    return shortcut_id in load_overrides()


def set_override(shortcut_id: str, key: Optional[str]) -> None:
    """写入一条覆写；``key`` 为空 = 取消绑定（用户主动解绑）。"""
    if shortcut_id not in BY_ID:
        raise KeyError(shortcut_id)
    key = (key or "").strip()
    st = _store()
    st.beginGroup("shortcuts")
    if key:
        st.setValue(shortcut_id, key)
    else:
        st.remove(shortcut_id)
    st.endGroup()
    st.sync()
    load_overrides(force=True)


def clear_override(shortcut_id: str) -> None:
    """恢复某条出厂键位。"""
    set_override(shortcut_id, None)
    # set_override 写空串等价于删除；这里再显式清一次缓存，语义更直白
    load_overrides(force=True)


def clear_all_overrides() -> None:
    st = _store()
    st.beginGroup("shortcuts")
    st.remove("")           # 清空该 group 下全部键
    st.endGroup()
    st.sync()
    load_overrides(force=True)


def conflicts(overrides: Optional[Dict[str, str]] = None) -> List[Tuple[str, str, str]]:
    """同一 scope 内的键位冲突：返回 [(scope, 键序列, id 甲, id 乙)...] 的简化表。

    只查同 scope —— 跨 scope 同键是**设计允许**的（见文件顶部「焦点在哪，
    局部优先」：A 归全天模式，频谱获焦时归 AM）。
    没配键（None/空）的动作不参与，避免「多个解绑」被算成冲突。
    """
    ov = load_overrides() if overrides is None else overrides
    seen: Dict[Tuple[str, str], str] = {}
    out: List[Tuple[str, str, str]] = []
    for s in SHORTCUTS:
        key = (ov.get(s.id, s.key) or "").strip()
        if not key:
            continue
        norm = key.upper().replace(" ", "")
        slot = (s.scope, norm)
        if slot in seen:
            out.append((s.scope, key, seen[slot], s.id))
        else:
            seen[slot] = s.id
    return out


