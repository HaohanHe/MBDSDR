# SPDX-License-Identifier: MIT
"""
远程控制 v2：补齐 GQRX remote_control.cpp 完整命令集
=====================================================

对照上游 gqrx/remote_control.cpp（见 docs/learn/gnuradio_gqrx.md）：
  - 端口 7356 / 仅 127.0.0.1             <-> remote_control.cpp:31-32
  - 按行读、空格拆分                      <-> remote_control.cpp:201-280
  - 应答 RPRT 0 / RPRT 1                <-> remote_control.cpp:619/622/661
  - q/Q 关闭连接                         <-> remote_control.cpp:263-270

与 :mod:`mbdsdr_ai.remote_control`（v1，仅 f/m/g/q 且命令大小写不敏感）的关系：
- v1 已上线、被既有测试覆盖，本文件**不改 v1**，而是子类化它并**覆写
  process_line 为大小写敏感分发**，补齐 GQRX 完整命令表。
- v1 的 TCP server / WebSocket / 锁 / 安全降级胶水全部复用（继承而来）。

命令表（大小写敏感；对应 GQRX remote_control.cpp 命令分发）：

  小写 = 查询，大写 = 绝对设置（GQRX 约定）：
    f            查询频率(Hz)        f <hz>    设频率（向后兼容 v1）
    F <hz>       绝对设频率
    m            查询模式            m <mode>  设模式（兼容 v1）
    M <mode>     绝对设模式
    g            查询增益(dB)        g <db>    设增益（兼容 v1）
    G <db>       绝对设增益
    l            查询静噪(dB)        l <db>    设静噪
    L <db>       绝对设静噪

  开关 / 动作 / 查询：
    s            切换 DSP 启动/停止（toggle）
    u            切换录制 启动/停止（toggle）
    A            查询 AGC 状态       A <0|1>   设 AGC 开关
    v            查询版本字符串
    d            查询设备列表
    q / Q        关闭连接

无后端（backend=None）安全降级：
  - 查询命令返回**默认值**（不崩、不抛）；
  - 设置/动作命令返回 ``RPRT 1``。
后端方法抛异常一律吞掉（继承 v1._call_backend）。
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from .remote_control import (
    CLOSE_CONNECTION, RPRT_ERR, RPRT_OK, RemoteControl,
)

logger = logging.getLogger(__name__)

VERSION_STRING = "MBDSDR RemoteControl v2.0 (GQRX-compatible)"


class RemoteControlV2(RemoteControl):
    """GQRX 完整命令集（大小写敏感）。"""

    def __init__(self, backend: Any = None,
                 host: str = "127.0.0.1", port: int = 7356,
                 default_freq: float = 100_000_000.0,
                 default_mode: str = "FM",
                 default_gain: float = 0.0,
                 default_squelch: float = -150.0,
                 default_agc: bool = True):
        super().__init__(backend=backend, host=host, port=port)
        # 无后端时查询命令返回的默认值（明确标注为默认，不冒充实时读数）
        self.default_freq = float(default_freq)
        self.default_mode = str(default_mode)
        self.default_gain = float(default_gain)
        self.default_squelch = float(default_squelch)
        self.default_agc = bool(default_agc)
        self.version_string = VERSION_STRING

    # ------------------------------------------------------------ query glue
    def _query(self, method: str, default: Any) -> Any:
        """查询后端方法；无后端/无此方法/抛异常 → 返回 default（不崩）。"""
        v = self._call_backend(method)
        return default if v is None else v

    # ------------------------------------------------------------ core dispatch
    def process_line(self, line: str) -> str:
        """大小写敏感的单行命令分发（覆写 v1 的 lower-case 版）。"""
        parts = line.strip().split()
        if not parts:
            return RPRT_ERR
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else None

        with self._lock:
            # ---- 频率 ----
            if cmd == "f":
                return self._get_set("get_freq", "set_freq", arg,
                                     self.default_freq, cast=int)
            if cmd == "F":
                if arg is None:
                    return RPRT_ERR
                return self._set_abs("set_freq", arg, cast=int)
            # ---- 模式 ----
            if cmd == "m":
                return self._get_set("get_mode", "set_mode", arg,
                                     self.default_mode, cast=str)
            if cmd == "M":
                if arg is None:
                    return RPRT_ERR
                return self._set_abs_str("set_mode", arg)
            # ---- 增益 ----
            if cmd == "g":
                return self._get_set("get_gain", "set_gain", arg,
                                     self.default_gain, cast=float)
            if cmd == "G":
                if arg is None:
                    return RPRT_ERR
                return self._set_abs("set_gain", arg, cast=float)
            # ---- 静噪 ----
            if cmd == "l":
                return self._get_set("get_squelch", "set_squelch", arg,
                                     self.default_squelch, cast=float)
            if cmd == "L":
                if arg is None:
                    return RPRT_ERR
                return self._set_abs("set_squelch", arg, cast=float)
            # ---- 开关/动作 ----
            if cmd == "s":
                return self._toggle_dsp()
            if cmd == "u":
                return self._toggle_record()
            if cmd == "A":
                return self._agc(arg)
            # ---- 查询 ----
            if cmd == "v":
                v = self._query("get_version", self.version_string)
                return f"{v}\n"
            if cmd == "d":
                return self._device_list()
            if cmd in ("q", "Q"):
                return CLOSE_CONNECTION
            # 未知命令 -> RPRT 1（remote_control.cpp:271-276）
            return RPRT_ERR

    # ------------------------------------------------------------ helpers
    def _get_set(self, get_method: str, set_method: str,
                 arg: Optional[str], default: Any, cast) -> str:
        """无参=查询(后端缺失返回 default)；有参=设置(后端缺失 RPRT 1)。"""
        if arg is None:
            v = self._query(get_method, default)
            try:
                return f"{cast(v)}\n"
            except (ValueError, TypeError):
                return f"{v}\n"
        # set
        try:
            val = cast(float(arg)) if cast is not str else arg.upper()
        except (ValueError, TypeError):
            return RPRT_ERR
        ok = self._call_backend(set_method, val)
        return RPRT_OK if ok else RPRT_ERR

    def _set_abs(self, set_method: str, arg: str, cast) -> str:
        """绝对设置（大写命令）。无后端/失败 → RPRT 1。"""
        try:
            val = cast(float(arg))
        except (ValueError, TypeError):
            return RPRT_ERR
        ok = self._call_backend(set_method, val)
        return RPRT_OK if ok else RPRT_ERR

    def _set_abs_str(self, set_method: str, arg: str) -> str:
        ok = self._call_backend(set_method, arg.upper())
        return RPRT_OK if ok else RPRT_ERR

    # ------------------------------------------------------------ s: DSP toggle
    def _toggle_dsp(self) -> str:
        running = self._query("get_dsp_running", None)
        if running is None:
            # 后端不暴露状态 → 无法安全 toggle，RPRT 1
            return RPRT_ERR
        new_state = not bool(running)
        ok = self._call_backend("set_dsp_running", new_state)
        return RPRT_OK if ok else RPRT_ERR

    # ------------------------------------------------------------ u: record toggle
    def _toggle_record(self) -> str:
        recording = self._query("is_recording", None)
        if recording is None:
            return RPRT_ERR
        if bool(recording):
            ok = self._call_backend("stop_recording")
        else:
            ok = self._call_backend("start_recording")
        return RPRT_OK if ok else RPRT_ERR

    # ------------------------------------------------------------ A: AGC
    def _agc(self, arg: Optional[str]) -> str:
        if arg is None:
            v = self._query("get_agc", self.default_agc)
            return f"{1 if v else 0}\n"
        try:
            on = bool(int(float(arg)))
        except (ValueError, TypeError):
            return RPRT_ERR
        ok = self._call_backend("set_agc", on)
        return RPRT_OK if ok else RPRT_ERR

    # ------------------------------------------------------------ d: device list
    def _device_list(self) -> str:
        devs = self._query("get_device_list", None)
        if devs is None:
            # 无后端 → 返回空列表标记（一行 JSON），不崩
            return "[]\n"
        if isinstance(devs, (list, tuple)):
            items: List[str] = []
            for d in devs:
                if isinstance(d, dict):
                    items.append(str(d.get("name", d.get("id", d))))
                else:
                    items.append(str(d))
            return ", ".join(items) + "\n"
        return f"{devs}\n"
