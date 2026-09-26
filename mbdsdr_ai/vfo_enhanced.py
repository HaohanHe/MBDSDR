"""
VFO 音频路由增强（VFO Mute Mutex / Primary Listen Priority）
===============================================================

对照 SDR++ ``core/src/signal_path/vfo_manager.h`` +
``decoder_modules/radio/src/radio_module.cpp``：每个 radio module 持有自己的 VFO，
但同一时刻只有一个 VFO 的解调音频送声卡（主听），其他 VFO 虽然在解调但音频
静音。SDR++ 通过 ``audio_sink`` 只接当前 active module 的输出来实现。

本模块**不修改**现有 ``vfo_manager.py``，而是在外层包一个
:class:`VfoAudioRouter`：
- 维护"当前主听 VFO"（primary）。
- 切主听时，把旧 primary 静音、新 primary 取消静音（互斥）。
- 每个 VFO 可独立设置静音标志（用户手动 mute 不影响 primary 互斥）。
- :meth:`audible_vfo_id` 返回真正应该送声卡的 VFO id（primary 且未被手动静音）。

纯数据/状态层，不接音频硬件。
"""
from __future__ import annotations

from typing import Optional

from .vfo_manager import VfoManager, VfoState


class VfoAudioRouter:
    """在已有 :class:`VfoManager` 之上加一层"主听互斥"。

    Parameters
    ----------
    mgr : VfoManager
        已有的 VFO 管理器（本类不拥有它，只读/改它的 muted 字段）。
    """

    def __init__(self, mgr: VfoManager) -> None:
        self._mgr = mgr
        self._primary_id: Optional[str] = None
        # 用户手动静音的 VFO 集合（与 primary 互斥独立）
        self._manual_muted: set = set()

    # ------------------------------------------------------------------
    @property
    def primary_id(self) -> Optional[str]:
        """当前主听 VFO id。"""
        return self._primary_id

    def set_primary(self, vfo_id: str) -> Optional[VfoState]:
        """把 vfo_id 设为主听；互斥地把其他所有 VFO 静音。

        返回被设为 primary 的 VfoState；id 不存在返回 None。
        """
        v = self._mgr.get(vfo_id)
        if v is None:
            return None

        # 静音其他所有 VFO（除了新 primary）
        for other in self._mgr.list_all():
            if other.vfo_id != vfo_id:
                other.muted = True
        # 新 primary 取消静音（除非用户手动 mute 过它）
        v.muted = vfo_id in self._manual_muted
        self._primary_id = vfo_id
        return v

    # ------------------------------------------------------------------
    def toggle_manual_mute(self, vfo_id: str) -> bool:
        """切换某 VFO 的手动静音；返回切换后是否被静音。

        手动静音与 primary 互斥叠加：即使某 VFO 是 primary，若被手动 mute，
        audible_vfo_id 也不会选它。
        """
        v = self._mgr.get(vfo_id)
        if v is None:
            return False
        if vfo_id in self._manual_muted:
            self._manual_muted.discard(vfo_id)
            # 取消手动静音：primary 恢复可听；非 primary 仍保持互斥静音
            v.muted = (vfo_id != self._primary_id)
        else:
            self._manual_muted.add(vfo_id)
            v.muted = True
        return vfo_id in self._manual_muted

    # ------------------------------------------------------------------
    @property
    def audible_vfo_id(self) -> Optional[str]:
        """当前真正应送声卡的 VFO id。

        规则：
        1. 取 primary；
        2. 若 primary 不存在或被手动静音 → 没有可听 VFO（返回 None）。
        """
        if self._primary_id is None:
            return None
        if self._primary_id in self._manual_muted:
            return None
        v = self._mgr.get(self._primary_id)
        if v is None or v.muted:
            return None
        return self._primary_id

    # ------------------------------------------------------------------
    def advance_primary(self) -> Optional[VfoState]:
        """按频率顺序切到下一个 VFO 作为主听（循环）。"""
        ordered = self._mgr.ordered_by_freq()
        if not ordered:
            return None
        if self._primary_id is None:
            nxt = ordered[0]
        else:
            cur = self._mgr.get(self._primary_id)
            try:
                idx = ordered.index(cur) if cur in ordered else -1
            except ValueError:
                idx = -1
            nxt = ordered[(idx + 1) % len(ordered)] if idx >= 0 else ordered[0]
        return self.set_primary(nxt.vfo_id)
