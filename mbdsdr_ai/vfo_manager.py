"""
多 VFO 管理器（VFO Manager）
================================

纯 Python 数据层，不依赖 UI、Qt 或任何硬件后端，也不产生任何运行时信号。
行为对标 CubicSDR 的 DemodulatorMgr / DemodulatorInstance：

- 三态焦点指针：active_context（鼠标悬停的临时焦点）/ current（用户点击确认、
  正在听或配置的焦点）/ visual（送往视觉输出的焦点）。
- 粘滞默认值快照（last_*）：一旦 current 被确认，其带宽/模式/静噪/增益/静音/
  delta-lock 被记下，下次在新频率新建 VFO 时整体继承，避免重复设置。
- 区间重叠命中：按模式计算 VFO 实际占用的频率区间，查询某频率是否落在任一 VFO 内；
  单边带（USB/LSB）占用区间不对称。
- 新建 vs 移动裁决：按住 shift，或当前没有已确认 current 时，应新建 VFO；
  否则应移动现有 current。

引用的标杆源码位置（仅作行为对照，本文件不依赖 C++ 工程）：
- DemodulatorInstance.h        VFO 实例字段
- DemodulatorMgr.h:88-108      demods 列表与 activeContext/current/visual 三指针、last_* 快照
- DemodulatorMgr.cpp:62-168    list / ordered / next / prev / 删除时清空指针
- DemodulatorMgr.cpp:170-206   getDemodulatorsAt 区间重叠命中（含 USB/LSB 不对称）
- DemodulatorMgr.cpp:209-250   setActiveDemodulator(temporary) 悬停 vs 点击确认
- DemodulatorMgr.cpp:311-337   updateLastState 把 current 参数刷回 last_*
- WaterfallCanvas.cpp:675-676  isNew 裁决（shift 或无活跃 current 则新建）
- WaterfallCanvas.cpp:708-734  新建时从 last_* 继承全部参数
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple


# 单边带模式名（大小写不敏感）。其余模式一律按对称带宽处理。
_SSB_UPPER = "USB"
_SSB_LOWER = "LSB"


@dataclass
class VfoState:
    """单个 VFO（可变频率振荡器）的状态快照。

    纯数据对象为主：本类不主动创建线程/音频队列/硬件句柄。
    运行时可由上层 bind_dsp() 填入 dsp_vfo / output_stream（对照
    SDR++ vfo_manager.h:33 dspVFO + :29 output），老代码不填这两个
    字段完全不受影响。
    """

    vfo_id: str
    center_hz: float
    bw_hz: float
    mode: str  # "FM"/"AM"/"USB"/"LSB"/"CW"/"FT8"/...
    label: str = ""
    muted: bool = False
    squelch_level_db: float = -150.0
    squelch_enabled: bool = False
    gain_db: Optional[float] = None  # None 表示使用 AGC
    delta_lock: bool = False
    active: bool = True
    # 运行时 DSP 绑定（可选，默认 None = 纯配置态）
    dsp_vfo: Any = field(default=None, repr=False)        # 对照 vfo_manager.h:33
    output_stream: Any = field(default=None, repr=False)  # 对照 vfo_manager.h:29


def vfo_coverage(center_hz: float, bw_hz: float, mode: str) -> Tuple[float, float]:
    """计算 VFO 实际占用的频率区间 (low_hz, high_hz)。

    纯函数，不修改任何状态。

    - 对称模式（FM/AM/CW/FT8 等一切非 USB/LSB）：
        [center - bw/2, center + bw/2]
    - USB（上边带）：仅占用载频以上一侧
        [center, center + bw]
    - LSB（下边带）：仅占用载频以下一侧
        [center - bw, center]

    单边带不对称是本函数与对称模式的核心区别；查询命中时据此判断。
    """
    m = (mode or "").strip().upper()
    half = bw_hz / 2.0
    if m == _SSB_UPPER:
        return (center_hz, center_hz + bw_hz)
    if m == _SSB_LOWER:
        return (center_hz - bw_hz, center_hz)
    return (center_hz - half, center_hz + half)


class VfoManager:
    """管理多个 VFO 实例及其三态焦点与粘滞默认值。"""

    def __init__(self) -> None:
        # 全部 VFO，按加入顺序保存
        self._vfos: List[VfoState] = []
        self._by_id: dict = {}

        # 三态焦点指针（对标 activeContextModem / currentModem / activeVisualDemodulator）
        self.active_context: Optional[VfoState] = None  # 鼠标悬停（临时）
        self.current: Optional[VfoState] = None        # 点击确认、正在听/配置
        self.visual: Optional[VfoState] = None         # 送视觉输出

        # 粘滞默认值快照（对标 lastBandwidth / lastDemodType / ...）
        # _has_last 标记是否已有一次有效的 current 快照；首建 VFO 前不启用继承。
        self._has_last: bool = False
        self.last_bw_hz: float = 8000.0
        self.last_mode: str = "FM"
        self.last_squelch_db: float = -150.0
        self.last_squelch_enabled: bool = False
        self.last_gain_db: Optional[float] = None
        self.last_muted: bool = False
        self.last_delta_lock: bool = False

        self._counter: int = 0

    # ── 增删查 ──────────────────────────────────────────

    def add(self, center_hz: float, bw_hz: float, mode: str,
            inherit_last: bool = True) -> VfoState:
        """新建并注册一个 VFO。

        center_hz 始终取传入值。bw_hz / mode 在以下两种情况之外，也取传入值：
        当 inherit_last=True 且已有有效粘滞快照时，新 VFO 的带宽、模式以及全部
        运行参数（静噪/增益/静音/delta-lock）整体从 last_* 复制——对标 CubicSDR
        新建解调实例时继承上次参数的行为。传入的 bw_hz/mode 在这种情况下作为
        首次建 VFO（尚无快照）或 inherit_last=False 时的初始值。
        """
        self._counter += 1
        vfo_id = f"vfo{self._counter:03d}"

        use_sticky = inherit_last and self._has_last
        if use_sticky:
            vfo = VfoState(
                vfo_id=vfo_id,
                center_hz=float(center_hz),
                bw_hz=self.last_bw_hz,
                mode=self.last_mode,
                squelch_level_db=self.last_squelch_db,
                squelch_enabled=self.last_squelch_enabled,
                gain_db=self.last_gain_db,
                muted=self.last_muted,
                delta_lock=self.last_delta_lock,
            )
        else:
            vfo = VfoState(
                vfo_id=vfo_id,
                center_hz=float(center_hz),
                bw_hz=float(bw_hz),
                mode=mode,
            )

        self._vfos.append(vfo)
        self._by_id[vfo_id] = vfo
        return vfo

    def remove(self, vfo_id: str) -> None:
        """移除指定 VFO；若它正被任一焦点指针引用，清空相应指针。"""
        vfo = self._by_id.pop(vfo_id, None)
        if vfo is None:
            return
        self._vfos.remove(vfo)
        # 对标 DemodulatorMgr.cpp:151-159 删除时清空三指针
        if self.active_context is vfo:
            self.active_context = None
        if self.current is vfo:
            self.current = None
        if self.visual is vfo:
            self.visual = None

    def get(self, vfo_id: str) -> Optional[VfoState]:
        """按 id 取 VFO；不存在返回 None。"""
        return self._by_id.get(vfo_id)

    def list_all(self) -> List[VfoState]:
        """返回全部 VFO（加入顺序）。"""
        return list(self._vfos)

    def ordered_by_freq(self) -> List[VfoState]:
        """按中心频率升序返回 VFO；同频保持加入顺序（稳定排序）。"""
        return sorted(self._vfos, key=lambda v: v.center_hz)

    # ── DSP 运行时绑定（对照 SDR++ vfo_manager.cpp:30-49）──────────

    def bind_dsp(self, vfo_id: str, dsp_vfo: Any,
                 output_stream: Any = None) -> bool:
        """把一个已创建的 DSP VFO 对象和输出流绑定到配置态 VfoState。

        对照 vfo_manager.cpp:16 output = &dspVFO->out。老代码不调用本方法
        时 VfoState.dsp_vfo 保持 None，纯数据语义不变。
        """
        v = self._by_id.get(vfo_id)
        if v is None:
            return False
        v.dsp_vfo = dsp_vfo
        v.output_stream = output_stream
        return True

    def push_offset(self, vfo_id: str, offset_hz: float) -> bool:
        """把变频偏移实时推到已绑定的 DSP VFO（对照 vfo_manager.cpp:32
        dspVFO->setOffset(...)）。未绑定则静默返回 False。"""
        v = self._by_id.get(vfo_id)
        if v is None or v.dsp_vfo is None:
            return False
        setter = getattr(v.dsp_vfo, "set_offset", None)
        if callable(setter):
            setter(float(offset_hz))
            return True
        return False

    def push_bandwidth(self, vfo_id: str, bw_hz: float) -> bool:
        """把带宽实时推到已绑定 DSP VFO（对照 vfo_manager.cpp:48
        dspVFO->setBandwidth(...)）。"""
        v = self._by_id.get(vfo_id)
        if v is None or v.dsp_vfo is None:
            return False
        setter = getattr(v.dsp_vfo, "set_bandwidth", None)
        if callable(setter):
            setter(float(bw_hz))
            v.bw_hz = float(bw_hz)
            return True
        return False

    # ── 主听 / 次听（primary / secondary）──────────────────
    #
    # “主听”即正在解调并送声卡的 VFO，等价于 CubicSDR 的 current（用户点击确认、
    # 正在听或配置的焦点）。这里暴露一个稳定的字符串 id 视图，供 UI 层接线
    # （工具栏按钮 / 频谱点击 / Ctrl+Tab 循环）使用，避免 UI 直接持有 VfoState
    # 引用导致删除后悬空。

    @property
    def active_vfo_id(self) -> Optional[str]:
        """当前主听（current）VFO 的 id；无主听返回 None。"""
        return self.current.vfo_id if self.current is not None else None

    def set_primary(self, vfo_id: str) -> Optional[VfoState]:
        """把指定 VFO 设为主听（current），并刷新粘滞快照。

        找不到该 id 时返回 None 且不改动现有焦点。与 set_active_context(...,
        temporary=False) 等价，但按 id 调用，便于 UI 层信号槽接线。
        """
        v = self._by_id.get(vfo_id)
        if v is None:
            return None
        self.set_active_context(v, temporary=False)
        return v

    # ── 循环切换 current ──────────────────────────────────

    def _cycle(self, step: int) -> Optional[VfoState]:
        """在频率升序列表里按 step（+1 / -1）循环切换 current。"""
        ordered = self.ordered_by_freq()
        if not ordered or self.current is None:
            return None
        try:
            idx = ordered.index(self.current)
        except ValueError:
            return None
        nxt = ordered[(idx + step) % len(ordered)]
        # 切换焦点后同步三态指针，并把新焦点参数刷回粘滞快照
        self.current = nxt
        self.active_context = nxt
        self.visual = nxt
        self.update_last_state()
        return nxt

    def next(self) -> Optional[VfoState]:
        """切到频率更高的下一个 VFO；到末尾后绕回首项。无 current 返回 None。"""
        return self._cycle(+1)

    def prev(self) -> Optional[VfoState]:
        """切到频率更低的上一个 VFO；到首项后绕回末项。无 current 返回 None。"""
        return self._cycle(-1)

    # ── 三态焦点 ────────────────────────────────────────

    def set_active_context(self, vfo: Optional[VfoState],
                           temporary: bool = True) -> None:
        """设置焦点。

        temporary=True  —— 鼠标悬停：只改 active_context（以及视觉输出 visual），
                          不触碰 current，也不刷新粘滞快照。
        temporary=False —— 用户点击确认：把该 VFO 收编为 current，并调用
                          update_last_state() 把其参数记为粘滞默认值。
        """
        # 视觉输出跟随焦点；vfo 为空时回落到 current（对标 setActiveDemodulator 末尾）
        self.visual = vfo if vfo is not None else self.current

        if not temporary:
            self.current = vfo
            self.update_last_state()

        self.active_context = vfo

    def clear_active_context(self) -> None:
        """鼠标离开：清空悬停态 active_context，current 保持不变；视觉回落 current。"""
        self.active_context = None
        self.visual = self.current

    # ── 粘滞默认值 ────────────────────────────────────────

    def update_last_state(self) -> None:
        """把当前已确认 current 的参数刷回 last_* 粘滞快照。

        对标 DemodulatorMgr.cpp:311-337：current 无效（不存在或已停用）时清空 current，
        只有有效的 current 才会写入快照。
        """
        if self.current is not None and not self.current.active:
            self.current = None
        if self.current is None:
            return
        self.last_bw_hz = self.current.bw_hz
        self.last_mode = self.current.mode
        self.last_squelch_db = self.current.squelch_level_db
        self.last_squelch_enabled = self.current.squelch_enabled
        self.last_gain_db = self.current.gain_db
        self.last_muted = self.current.muted
        self.last_delta_lock = self.current.delta_lock
        self._has_last = True

    # ── 区间重叠命中 ──────────────────────────────────────

    def get_at(self, freq_hz: float,
               half_bw_hz: float = 0.0) -> List[VfoState]:
        """返回占用频率与查询窗口相交的全部 VFO。

        查询窗口为 [freq_hz - half_bw_hz, freq_hz + half_bw_hz]；
        每个 VFO 的占用区间由 vfo_coverage() 给出（USB/LSB 不对称）。
        half_bw_hz 为查询容差（对标 CubicSDR 的 halfBuffer）。
        """
        q_low = freq_hz - half_bw_hz
        q_high = freq_hz + half_bw_hz
        hits: List[VfoState] = []
        for vfo in self._vfos:
            v_low, v_high = vfo_coverage(vfo.center_hz, vfo.bw_hz, vfo.mode)
            # 两区间相交：vfo 区间不整体落在查询窗口左侧或右侧
            if v_low <= q_high and v_high >= q_low:
                hits.append(vfo)
        return hits

    # ── 新建 vs 移动裁决 ──────────────────────────────────

    def decide_create_or_move(self, shift_down: bool = False) -> bool:
        """裁决本次点击应新建 VFO 还是移动现有 current。

        返回 True  = 应新建（按住 shift，或当前没有已确认且活跃的 current）
        返回 False = 应移动现有 current
        对标 WaterfallCanvas.cpp:675-676 的 isNew 判定。
        """
        if shift_down:
            return True
        if self.current is None or not self.current.active:
            return True
        return False
