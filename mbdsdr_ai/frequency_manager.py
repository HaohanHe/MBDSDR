# SPDX-License-Identifier: MIT
"""
频率管理器 / 书签库（frequency manager）
=======================================

对标 SDR++ 的 Frequency Manager：内置一份公开、通用的标准频率库
（调频广播、航空、海事、业余频段与 FT8 频点、气象卫星 APT、ISS、
ADS-B、公众对讲、ISM、授时、导航卫星），并支持用户自定义书签，
持久化到 ~/.mbdsdr/bookmarks.json，与仓库分离、不进版本库。

AI 可经工具：列出/搜索书签、按名称或频率跳转（同时设置解调模式与带宽）、
新增自定义书签；扫频结果也可用 nearest() 与已知台站对照标注。

频率均为公开标准频点；地方台/中继/具体卫星下行因地因时而异，
只给频段或国际通用点，不臆造具体台名。
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, asdict, field
from typing import List, Optional

from .bookmark_manager import Bookmark as StoreBookmark
from .bookmark_manager import BookmarkManager

logger = logging.getLogger(__name__)


@dataclass
class Bookmark:
    name: str
    freq_hz: float = 0.0          # 单点频率（区间时为 0）
    mode: str = ""                # WFM/NFM/AM/USB/LSB/CW/DIG/RAW
    category: str = ""
    bandwidth_hz: float = 0.0
    start_hz: float = 0.0         # 频段起点（区间书签）
    end_hz: float = 0.0           # 频段终点
    note: str = ""
    builtin: bool = True

    @property
    def is_range(self) -> bool:
        return self.end_hz > self.start_hz > 0

    @property
    def center_hz(self) -> float:
        if self.is_range:
            return (self.start_hz + self.end_hz) / 2.0
        return self.freq_hz

    def to_dict(self) -> dict:
        return asdict(self)


def _m(mhz: float) -> float:
    return float(mhz) * 1e6


def _num(value) -> Optional[float]:
    """把书签字段转成有限浮点；缺失/非法返回 None（绝不默认成 0）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _default_bookmarks() -> List[Bookmark]:
    B = Bookmark
    lib: List[Bookmark] = []

    # ---- 调频广播（WFM，200k 信道）----
    lib += [
        B("调频广播（中国/国际 FM 段）", mode="WFM", category="广播",
          bandwidth_hz=200e3, start_hz=_m(87.5), end_hz=_m(108.0),
          note="各地电台频率不同，扫频 87.5-108MHz 找台；50µs 去加重"),
        B("校园/日本窄 FM 段", mode="WFM", category="广播",
          bandwidth_hz=200e3, start_hz=_m(76.0), end_hz=_m(87.5),
          note="部分校园广播与日本 FM 段，视棒与地区而定"),
    ]

    # ---- 航空（AM）----
    lib += [
        B("民用航空频段（塔台/进近/ATIS）", mode="AM", category="航空",
          bandwidth_hz=10e3, start_hz=_m(118.0), end_hz=_m(136.0),
          note="AM 调制；扫频找活动频点，ATIS 为连续情报广播"),
        B("航空应急频率 121.500", _m(121.5), "AM", "航空", 10e3,
          note="国际航空遇险/应急守听频率，只守听勿发射"),
        B("航空应急备用 123.100", _m(123.1), "AM", "航空", 10e3,
          note="通用航空空对空/辅助"),
        B("军用航空 UHF 段", mode="AM", category="航空",
          bandwidth_hz=25e3, start_hz=_m(225.0), end_hz=_m(400.0),
          note="部分国家军航 UHF，AM"),
    ]

    # ---- 海事 VHF（NFM）----
    lib += [
        B("海事 VHF 频段", mode="NFM", category="海事",
          bandwidth_hz=12.5e3, start_hz=_m(156.025), end_hz=_m(162.025),
          note="船舶/港口，信道间隔 25/12.5k"),
        B("海事 CH16 呼叫与遇险 156.800", _m(156.8), "NFM", "海事", 12.5e3,
          note="国际海事遇险安全与呼叫频率，守听勿占"),
        B("海事 CH70 DSC 156.525", _m(156.525), "DIG", "海事", 12.5e3,
          note="数字选择性呼叫，数字信号"),
    ]

    # ---- 业余卫星 / 空间 ----
    lib += [
        B("ISS 国际空间站下行 145.800", _m(145.8), "NFM", "空间",
          12.5e3, note="语音/SSTV 下行常用频点，NFM"),
        B("ISS APRS 分组 145.825", _m(145.825), "DIG", "空间", 12.5e3,
          note="1200bps AFSK 分组（1200/2200Hz）"),
        B("业余卫星 VHF 下行段", mode="NFM", category="空间",
          bandwidth_hz=15e3, start_hz=_m(145.8), end_hz=_m(146.0),
          note="多数 LEO 业余卫星 VHF 下行集中区，具体看卫星频率表"),
        B("业余卫星 UHF 下行段", mode="NFM", category="空间",
          bandwidth_hz=15e3, start_hz=_m(435.0), end_hz=_m(438.0),
          note="LEO 业余卫星 UHF 下行常见区"),
    ]

    # ---- 气象卫星 APT（宽带 FM）----
    for f, tag in [(_m(137.100), "NOAA APT 137.100"),
                   (_m(137.620), "NOAA APT 137.620"),
                   (_m(137.9125), "NOAA APT 137.9125")]:
        lib.append(B(tag, f, "WFM", "气象卫星", 34e3,
                     note="APT 自动图像传送，宽带 FM，±17k 频偏，需过境跟踪"))

    # ---- 业余无线电地面 ----
    lib += [
        B("中国业余 VHF 段（2 米）", mode="NFM", category="业余",
          bandwidth_hz=12.5e3, start_hz=_m(144.0), end_hz=_m(145.8),
          note="VHF 业余段；145.800 为卫星保护频点"),
        B("中国业余 UHF 段（70 厘米）", mode="NFM", category="业余",
          bandwidth_hz=12.5e3, start_hz=_m(430.0), end_hz=_m(440.0),
          note="UHF 业余段，含中继上下行"),
        B("业余呼叫频点 145.000", _m(145.0), "NFM", "业余", 12.5e3,
          note="2 米段常见呼叫频点（地区惯例不同）"),
        B("业余呼叫频点 438.500", _m(438.5), "NFM", "业余", 12.5e3,
          note="70cm 段常见守听/中继区（地区惯例不同）"),
    ]

    # ---- FT8 数字模式频点（USB dial，WSJT-X 标准；HF 需上变频/direct sampling）----
    ft8 = [
        ("FT8 160m 1.840", 1.840), ("FT8 80m 3.573", 3.573),
        ("FT8 40m 7.074", 7.074), ("FT8 30m 10.136", 10.136),
        ("FT8 20m 14.074", 14.074), ("FT8 17m 18.100", 18.100),
        ("FT8 15m 21.074", 21.074), ("FT8 12m 24.915", 24.915),
        ("FT8 10m 28.074", 28.074), ("FT8 6m 50.313", 50.313),
        ("FT8 2m 144.174", 144.174),
    ]
    for name, f in ft8:
        lib.append(B(name, _m(f), "USB", "FT8", 3e3,
                     note="WSJT-X 标准 dial 频率；HF 频点需上变频或 direct sampling"))

    # ---- ADS-B / 航管 ----
    lib += [
        B("ADS-B 1090ES 1090.000", _m(1090.0), "RAW", "航管", 2e6,
          note="1090MHz Mode S 扩展电文，宽带脉冲，需专用 ADS-B 解码"),
        B("二次雷达询问 1030.000", _m(1030.0), "RAW", "航管", 2e6,
          note="地面询问，机载在 1090 应答"),
    ]

    # ---- 公众对讲 / ISM ----
    lib += [
        B("409 公众对讲机段", mode="NFM", category="对讲/ISM",
          bandwidth_hz=12.5e3, start_hz=_m(409.7500), end_hz=_m(409.9875),
          note="中国免执照公众对讲，20 信道，12.5k 间隔，409.7500 起"),
        B("433 ISM 段", mode="NFM", category="对讲/ISM",
          bandwidth_hz=12.5e3, start_hz=_m(433.05), end_hz=_m(434.79),
          note="遥控/遥测/数传 ISM，含 OOK/FSK 设备"),
        B("VHF 专业对讲段", mode="NFM", category="对讲/ISM",
          bandwidth_hz=12.5e3, start_hz=_m(137.0), end_hz=_m(174.0),
          note="VHF 专业/业务对讲机常见范围"),
        B("UHF 专业对讲段", mode="NFM", category="对讲/ISM",
          bandwidth_hz=12.5e3, start_hz=_m(403.0), end_hz=_m(470.0),
          note="UHF 专业/业务对讲机常见范围"),
    ]

    # ---- 授时（HF，需 HF 前端）----
    for f, tag in [(2.5, "BPM 国家授时 2.5MHz"), (5.0, "BPM 国家授时 5MHz"),
                   (10.0, "BPM 国家授时 10MHz"), (15.0, "BPM 国家授时 15MHz")]:
        lib.append(B(tag, _m(f), "AM", "授时", 1e3,
                     note="中国国家授时中心 BPM 标准频率/时间，HF 需上变频"))

    # ---- 导航卫星（接收信号，非解调语音）----
    lib += [
        B("GPS L1 1575.420", _m(1575.42), "RAW", "导航卫星", 2e6,
          note="GPS L1 C/A，扩频信号，需 GNSS 接收前端，普通 SDR 仅见噪声底抬升"),
        B("北斗 B1I 1561.098", _m(1561.098), "RAW", "导航卫星", 4e6,
          note="北斗二号 B1I；B1C 在 1575.42 与 GPS L1 同频"),
        B("GLONASS L1 ~1602", _m(1602.0), "RAW", "导航卫星", 8e6,
          note="GLONASS L1 FDMA，1602+n*0.5625"),
    ]

    return lib


class FrequencyManager:
    def __init__(self, user_path: Optional[str] = None):
        if user_path is None:
            user_path = os.path.join(os.path.expanduser("~"), ".mbdsdr",
                                     "bookmarks.json")
        self.user_path = user_path
        self.bookmarks: List[Bookmark] = _default_bookmarks()
        #: 上次读取用户书签的失败原因（""=无错）。损坏时由 BookmarkManager
        #: 给出「已备份为 X / 备份失败拒绝写」的人话，而不是静默成空列表。
        self.load_error = ""
        #: 损坏文件的备份路径（None = 未损坏或备份失败）。
        self.corrupt_backup: Optional[str] = None
        self._reload_from_store()

    # ------------------------------------------------------- 共用书签存储
    # `bookmarks.json` 由书签面板（BookmarkManager）和本模块共用。读写一律
    # 走 BookmarkManager，绝不在这里另写一份 json.dump：两个真值源正是
    # 「书签面板刚修好备份、这边一保存又把用户书签写成只剩内置项」的根。
    def _open_store(self) -> BookmarkManager:
        """每次操作重新打开：读盘拿到书签面板刚写的最新状态，不用缓存。"""
        return BookmarkManager(
            config_dir=os.path.dirname(os.path.abspath(self.user_path)) or ".",
            bookmarks_path=self.user_path)

    def _reload_from_store(self) -> None:
        """从共享存储重建用户书签；损坏/备份状态原样透传给 UI。"""
        store = self._open_store()
        self.corrupt_backup = store.corrupt_backup
        self.load_error = store.last_error or ""
        self.bookmarks = _default_bookmarks()
        skipped = 0
        for b in store.all():
            freq = _num(b.frequency_hz)
            start = _num(b.start_hz) or 0.0
            end = _num(b.end_hz) or 0.0
            if freq is not None and freq <= 0:
                # 0 Hz / 负频率不是可调谐书签：历史上会被当成「上次频率」
                # 回填并触发三条硬件 WARNING，这里一律按无效丢弃。
                freq = None
            if freq is None and not end > start > 0:
                skipped += 1
                continue
            self.bookmarks.append(Bookmark(
                name=b.name,
                freq_hz=freq if freq is not None else 0.0,
                mode=b.modulation,
                category=b.group or "自定义",
                bandwidth_hz=float(b.bandwidth_hz or 0.0),
                start_hz=start, end_hz=end,
                note=b.note, builtin=False))
        if skipped:
            note = f"跳过 {skipped} 条缺少有效频率/字段非法的书签"
            self.load_error = (f"{self.load_error}；{note}"
                               if self.load_error else note)
            logger.warning("%s（%s）", note, self.user_path)

    @staticmethod
    def _to_store(b: Bookmark) -> StoreBookmark:
        return StoreBookmark(
            frequency_hz=int(round(b.freq_hz)),
            name=b.name, modulation=b.mode or "",
            bandwidth_hz=int(round(b.bandwidth_hz)),
            group=b.category, note=b.note,
            start_hz=float(b.start_hz), end_hz=float(b.end_hz))


    def categories(self) -> List[str]:
        seen = []
        for b in self.bookmarks:
            if b.category not in seen:
                seen.append(b.category)
        return seen

    def list(self, category: Optional[str] = None,
             query: Optional[str] = None) -> List[Bookmark]:
        out = self.bookmarks
        if category:
            out = [b for b in out if b.category == category]
        if query:
            q = query.lower()
            out = [b for b in out
                   if q in b.name.lower() or q in b.note.lower()
                   or q in b.mode.lower() or q in b.category.lower()]
        return out

    def nearest(self, freq_hz: float, within_hz: Optional[float] = None
                ) -> Optional[Bookmark]:
        """返回中心频率最接近的书签；within_hz 给定时限制最大距离。"""
        cand = [b for b in self.bookmarks if b.center_hz > 0]
        if not cand:
            return None
        b = min(cand, key=lambda x: abs(x.center_hz - freq_hz))
        if within_hz is not None and abs(b.center_hz - freq_hz) > within_hz:
            return None
        return b

    def find(self, name_or_freq: str) -> Optional[Bookmark]:
        """按名称（包含匹配，优先精确）或 MHz/Hz 数字解析书签。"""
        s = str(name_or_freq).strip()
        # 数字：MHz（含小数点）或整数 Hz
        try:
            v = float(s)
            hz = v * 1e6 if ("." in s or v < 100000) else v
            exact = [b for b in self.bookmarks if abs(b.center_hz - hz) < 1]
            if exact:
                return exact[0]
            return Bookmark(name=f"自定义 {v:g} MHz", freq_hz=hz,
                            category="自定义", builtin=False)
        except ValueError:
            pass
        exact = [b for b in self.bookmarks if b.name == s]
        if exact:
            return exact[0]
        q = s.lower()
        partial = [b for b in self.bookmarks if q in b.name.lower()]
        return partial[0] if partial else None

    def add(self, name: str, freq_hz: float, mode: str = "",
            category: str = "自定义", bandwidth_hz: float = 0.0,
            note: str = "") -> Bookmark:
        bm = Bookmark(name=name, freq_hz=float(freq_hz), mode=mode.upper(),
                      category=category, bandwidth_hz=float(bandwidth_hz),
                      note=note, builtin=False)
        store = self._open_store()
        store.add(self._to_store(bm))
        self._reload_from_store()
        return bm

    def remove(self, name: str) -> bool:
        store = self._open_store()
        hit = next((x for x in store.all() if x.name == name), None)
        if hit is None:
            return False
        ok = store.remove(hit.frequency_hz, hit.name)
        self._reload_from_store()
        return ok

    def get_bookmarks_in_range(self, low_hz: float, high_hz: float
                               ) -> List[Bookmark]:
        """返回频率范围 [low_hz, high_hz] 内可见的书签。

        对标 openwebrx/owrx/bookmarks.py 按可视频谱范围查询书签的做法：
        点频书签以 freq_hz 为其频点，区间书签以 [start_hz, end_hz] 为覆盖段；
        只要书签频点/频段与 [low_hz, high_hz] 有交集即返回。
        """
        out: List[Bookmark] = []
        for b in self.bookmarks:
            if b.is_range:
                lo, hi = b.start_hz, b.end_hz
            else:
                lo = hi = b.freq_hz
            if lo <= high_hz and hi >= low_hz:   # 区间相交
                out.append(b)
        return out


# ---- 数字模式默认参数表（依据 FT8/FT4 等公开协议标准；wsjtx 仅作技术参考）----
MODE_PARAMS = {
    "FT8": {"tr_period_s": 15.0, "nsps": 1920, "ftol_hz": 50,
            "tone_spacing_hz": 6.25, "sample_rate": 12000},
    "FT4": {"tr_period_s": 7.5, "nsps": 576, "ftol_hz": 50,
            "tone_spacing_hz": 1 / 0.048, "sample_rate": 12000},
    "FST4": {"tr_period_s": 15.0, "nsps": 6912, "ftol_hz": 50,
             "tone_spacing_hz": 6.25, "sample_rate": 12000},
    "WSPR": {"tr_period_s": 120.0, "nsps": 16384, "ftol_hz": 100,
             "tone_spacing_hz": 1.4648, "sample_rate": 12000},
}


def get_mode_params(mode: str) -> dict:
    """返回指定数字模式的默认帧/采样参数；未知模式返回空 dict。"""
    return MODE_PARAMS.get(str(mode).upper(), {})
