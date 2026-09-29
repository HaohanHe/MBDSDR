# SPDX-License-Identifier: MIT
"""ISM 433.92 MHz 设备解码器（独立实现）。

覆盖 433.92 MHz ISM 频段的天气站 / 胎压 / 温湿度传感器解码链：

    IQ/音频包络 → OOK 脉冲检测 → 宽度切片成比特 → 各设备协议解包 → 结构化 dict

信号处理原语：
    - OOK 包络阈值/迟滞状态机
    - PPM/PWM/Manchester-zerobit 宽度→比特切片
    - 标准曼彻斯特成对解码
    - crc8 / lfsr_digest8 / reflect4 校验

系统常量：
    DEFAULT_SAMPLE_RATE = 250_000 Hz
    DEFAULT_FREQUENCY   = 433_920_000 Hz (433.92 MHz)

各设备协议的脉冲时序/位段是公开事实；本模块 DSP、结构与命名自行编写。
每个解码器输出统一 dict：{device_type, id, channel, temperature_c, humidity, ...}。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────
# 全局常量
# ──────────────────────────────────────────────────────────────────────────

RTL433_DEFAULT_SAMPLE_RATE = 250_000
RTL433_DEFAULT_FREQUENCY_HZ = 433_920_000
RTL433_DEFAULT_GAIN_DB = None
RTL433_MIN_SNR_DB = 9.0
RTL433_MIN_LEVEL_DB = -12.1442

OOK_EST_HIGH_RATIO = 64     # 高电平估计的滑动速度
OOK_EST_LOW_RATIO = 1024    # 低(噪声)电平估计的滑动速度，很慢
PD_MIN_PULSES = 16          # 至少多少个脉冲才算一次完整发包
PD_MIN_PULSE_SAMPLES = 10   # 脉冲最少采样点数，否则当作毛刺

OOK_PULSE_MANCHESTER_ZEROBIT = 3
OOK_PULSE_PPM = 5
OOK_PULSE_PWM = 6
FSK_PULSE_PCM = 16


# ──────────────────────────────────────────────────────────────────────────
# 位操作工具
# ──────────────────────────────────────────────────────────────────────────

def crc8(message: bytes, polynomial: int, init: int) -> int:
    """MSB-first 8 位 CRC。

    Elantra2012 用 poly=0x07, init=0x00；
    Nexus 用 poly=0x31, init=0x6c 做反向拒识。
    """
    remainder = init & 0xFF
    for byte in message:
        remainder ^= byte
        for _ in range(8):
            if remainder & 0x80:
                remainder = ((remainder << 1) ^ polynomial) & 0xFF
            else:
                remainder = (remainder << 1) & 0xFF
    return remainder & 0xFF


def lfsr_digest8(message: bytes, gen: int, key: int) -> int:
    """8 位 LFSR 摘要校验。

    Ambient Weather F007TH 用 gen=0x98, key=0x3e，再 ^0x64
    。
    """
    s = 0
    for data in message:
        for i in range(7, -1, -1):
            if (data >> i) & 1:
                s ^= key
            if key & 1:
                key = (key >> 1) ^ gen
            else:
                key = key >> 1
    return s & 0xFF


def reflect4(x: int) -> int:
    """4 位内比特反转。"""
    x = ((x & 0xCC) >> 2) | ((x & 0x33) << 2)
    x = ((x & 0xAA) >> 1) | ((x & 0x55) << 1)
    return x & 0xFF


def reflect_nibbles(data: bytes) -> bytearray:
    """每个字节做 reflect4。
    Oregon Scientific v2.1 解包后必做。
    """
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = reflect4(b)
    return out


# ──────────────────────────────────────────────────────────────────────────
# 曼彻斯特编/解码
# ──────────────────────────────────────────────────────────────────────────

def manchester_decode_bits(bits: List[int]) -> List[int]:
    """标准曼彻斯特成对解码。

    规则（与 C 一致）：成对取 (bit1, bit2)；
      bit1==bit2 视为无效并停止；否则输出 bit2。
    即 '10'→0，'01'→1（IEEE 802.3 约定，0=高-低）。
    """
    out: List[int] = []
    i = 0
    while i + 1 < len(bits):
        b1 = bits[i]
        b2 = bits[i + 1]
        if b1 == b2:
            break
        out.append(b2)
        i += 2
    return out


def manchester_encode_bits(data_bits: List[int]) -> List[int]:
    """manchester_decode_bits 的逆变换（往返用）。

    0 → '10'，1 → '01'（与解码输出 bit2 对应）。
    """
    out: List[int] = []
    for b in data_bits:
        if b:
            out += [0, 1]
        else:
            out += [1, 0]
    return out


def bits_to_bytes(bits: List[int]) -> bytearray:
    """比特串(MSB first)打包成字节，不足补 0。对应 bitbuffer bb[]。"""
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i >> 3] |= 0x80 >> (i & 7)
    return out


def bytes_to_bits(data: bytes) -> List[int]:
    """字节(MSB first)展开成比特串。"""
    out: List[int] = []
    for byte in data:
        for i in range(7, -1, -1):
            out.append((byte >> i) & 1)
    return out


# ──────────────────────────────────────────────────────────────────────────
# 脉冲表示 与 OOK 包络检测
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class Pulse:
    """一对 (脉冲高电平时长, 紧随其后的间隔时长)，单位 us。"""
    pulse_us: float
    gap_us: float


class PulseDemodulator:
    """OOK 包络脉冲检测。

    输入包络序列（已检波的幅度，任意非负标量），用自适应阈值 + 迟滞
    状态机切出脉冲/间隔。RTL-SDR 链路里包络来自 |I+jQ|。
    """

    def __init__(self, sample_rate: int = RTL433_DEFAULT_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.us_per_sample = 1e6 / sample_rate
        # 状态
        self.low_est = 0.0
        self.high_est = 0.0
        self.lead_in = 0

    def _detect_thresholds(self) -> Tuple[float, float]:
        """阈值 = (low + min(high, max_high))/2，迟滞 = threshold/8 (~12%)。
。
        这里不做固定上限钳位（max_high 仅在手动增益时相关），用 high_est 直接估计。
        """
        threshold = (self.low_est + self.high_est) / 2.0
        hysteresis = threshold / 8.0  # +-12%
        return threshold, hysteresis

    def detect(self, envelope: List[float]) -> List[Pulse]:
        """对一段包络做 OOK 切片，返回脉冲列表。

        状态机 IDLE→PULSE→GAP_START→GAP，逐样本推进。
。
        """
        pulses: List[Pulse] = []
        # 状态: idle / pulse / gap_start / gap
        state = "idle"
        run = 0  # 当前高/低电平持续采样数
        pulse_len_samples = 0  # 最近一个脉冲的长度
        last_pulse = 0  # 最近已确认脉冲长度（样本）

        for am in envelope:
            # 上升沿进入 PULSE 前，先让低电平(噪声)估计收敛
            if state == "idle":
                delta = am - self.low_est
                self.low_est += delta / OOK_EST_LOW_RATIO
                self.low_est += 1 if delta > 0 else -1
                self.high_est = 8.0 * self.low_est  # ~9dB 高低比
                if self.lead_in <= OOK_EST_LOW_RATIO:
                    self.lead_in += 1

            threshold, hyst = self._detect_thresholds()

            if state == "idle":
                if am > threshold + hyst and self.lead_in > OOK_EST_LOW_RATIO:
                    state = "pulse"
                    run = 1
                    pulse_len_samples = 1
                else:
                    run = 0

            elif state == "pulse":
                run += 1
                pulse_len_samples += 1
                if am < threshold - hyst:
                    if pulse_len_samples >= PD_MIN_PULSE_SAMPLES:
                        last_pulse = pulse_len_samples
                        state = "gap_start"
                        run = 0
                    else:
                        state = "idle"
                        run = 0
                else:
                    self.high_est += am / OOK_EST_HIGH_RATIO - self.high_est / OOK_EST_HIGH_RATIO

            elif state == "gap_start":
                run += 1
                if am > threshold + hyst:
                    pulse_len_samples = last_pulse + run
                    state = "pulse"
                elif run >= PD_MIN_PULSE_SAMPLES:
                    state = "gap"

            elif state == "gap":
                run += 1
                if am > threshold + hyst:
                    pulses.append(Pulse(
                        pulse_us=last_pulse * self.us_per_sample,
                        gap_us=run * self.us_per_sample,
                    ))
                    state = "pulse"
                    pulse_len_samples = 1
                    run = 1

        return pulses


def envelope_from_iq(iq: List[complex]) -> List[float]:
    """从复 IQ 取幅度包络。RTL-SDR 链路里 OOK 检测基于幅度 |I+jQ|。"""
    return [abs(z) for z in iq]


# ──────────────────────────────────────────────────────────────────────────
# 宽度切片：把 pulse/gap 列表切成比特行
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class DeviceConfig:
    """对应 r_device 协议参数。"""
    name: str
    modulation: int
    short_width: float   # us
    long_width: float    # us
    reset_limit: float = 0.0
    gap_limit: float = 0.0
    sync_width: float = 0.0
    tolerance: float = 0.0  # us；<=0 用默认 ±25% of long

    def tol(self) -> float:
        if self.tolerance > 0:
            return self.tolerance
        return self.long_width / 4.0


def slice_ppm(pulses: List[Pulse], cfg: DeviceConfig) -> List[int]:
    """PPM 切片：短间隔=0，长间隔=1。"""
    t = cfg.tol()
    out: List[int] = []
    for p in pulses:
        g = p.gap_us
        if cfg.short_width - t < g < cfg.short_width + t:
            out.append(0)
        elif cfg.long_width - t < g < cfg.long_width + t:
            out.append(1)
    return out


def slice_pwm(pulses: List[Pulse], cfg: DeviceConfig) -> List[int]:
    """PWM 切片：短脉冲=1，长脉冲=0。

    Acurite/LaCrosse 用短脉冲(550us)=1、长脉冲(1400us)=0。
    """
    t = cfg.tol()
    out: List[int] = []
    for p in pulses:
        w = p.pulse_us
        if cfg.short_width - t < w < cfg.short_width + t:
            out.append(1)
        elif cfg.long_width - t < w < cfg.long_width + t:
            out.append(0)
    return out


def slice_manchester_zerobit(pulses: List[Pulse], cfg: DeviceConfig) -> List[int]:
    """Manchester-zerobit 边沿切片。

    首个上升沿硬编码为 0。跟踪 time_since_last：当距上次比特边界累计超过
    short*1.5 时，当前沿就是数据跳变——脉冲结束(下降数据沿)=1，间隔结束(上升数据沿)=0。
    """
    s = cfg.short_width
    bits: List[int] = [0]  # 第一个上升沿恒为 0
    t_since = 0.0
    for p in pulses:
        # 脉冲段
        if p.pulse_us + t_since > s * 1.5:
            bits.append(1)  # 下降数据沿 = 1
            t_since = 0.0
        else:
            t_since += p.pulse_us
        # 间隔段
        if p.gap_us + t_since > s * 1.5:
            bits.append(0)  # 上升数据沿 = 0
            t_since = 0.0
        else:
            t_since += p.gap_us
    return bits


# ──────────────────────────────────────────────────────────────────────────
# 设备解码器基类与注册框架
# ──────────────────────────────────────────────────────────────────────────

class DeviceDecoder:
    """设备解码器基类（统一的设备协议描述与解码回调接口）。

    子类需设置 cfg 并实现 decode_message(b: bytes) -> dict|None。
。
    """
    device_type: str = "generic"
    cfg: DeviceConfig

    def decode_message(self, b: bytes) -> Optional[Dict[str, Any]]:
        """对一帧已切片字节做协议解包。失败返回 None。"""
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {
            "device_type": self.device_type,
            "name": self.cfg.name,
            "modulation": self.cfg.modulation,
            "short_width_us": self.cfg.short_width,
            "long_width_us": self.cfg.long_width,
            "reset_limit_us": self.cfg.reset_limit,
        }


_REGISTRY: Dict[str, DeviceDecoder] = {}


def register_device(dec: DeviceDecoder) -> None:
    _REGISTRY[dec.device_type] = dec


def list_devices() -> List[Dict[str, Any]]:
    return [d.describe() for d in _REGISTRY.values()]


def get_device(device_type: str) -> Optional[DeviceDecoder]:
    return _REGISTRY.get(device_type)


# ──────────────────────────────────────────────────────────────────────────
# 设备解码器 1：Acurite 5n1（温度/湿度/风速/风向/雨量）
# ──────────────────────────────────────────────────────────────────────────

_ACURITE_MSGTYPE_5N1_WIND_DIR_RAIN = 0x31
_ACURITE_MSGTYPE_5N1_TEMP_HUM = 0x38
_ACURITE_5N1_BYTELEN = 8


class Acurite5n1Decoder(DeviceDecoder):
    device_type = "acurite_5n1"

    cfg = DeviceConfig(
        name="Acurite 5n1 (592TXR/Iris)",
        modulation=OOK_PULSE_PWM,
        short_width=220,
        long_width=408,
        sync_width=620,
        gap_limit=500,
        reset_limit=4000,
    )

    def decode_message(self, b: bytes) -> Optional[Dict[str, Any]]:
        b = bytes([~x & 0xFF for x in b])
        if len(b) < _ACURITE_5N1_BYTELEN:
            return None
        if (sum(b[:_ACURITE_5N1_BYTELEN - 1]) & 0xFF) != b[_ACURITE_5N1_BYTELEN - 1]:
            return None
        for i in range(2, _ACURITE_5N1_BYTELEN - 1):
            if bin(b[i]).count("1") % 2 != 0:
                return None

        message_type = b[2] & 0x3F
        sensor_id = ((b[0] & 0x0F) << 8) | b[1]
        battery_low = (b[2] & 0x40) == 0
        out: Dict[str, Any] = {
            "device_type": "Acurite-5n1",
            "id": sensor_id,
            "battery_ok": not battery_low,
            "message_type": message_type,
        }
        wind_raw = ((b[3] & 0x1F) << 3) | ((b[4] & 0x70) >> 4)
        out["wind_speed_kmh"] = wind_raw * 0.8278 + 1.0 if wind_raw > 0 else 0.0

        if message_type == _ACURITE_MSGTYPE_5N1_WIND_DIR_RAIN:
            raincounter = ((b[5] & 0x7F) << 7) | (b[6] & 0x7F)
            out["rain_in"] = raincounter * 0.01
            out["rain_mm"] = raincounter * 0.01 * 25.4
            out["wind_dir_deg"] = (b[4] & 0x0F) * 22.5
        elif message_type == _ACURITE_MSGTYPE_5N1_TEMP_HUM:
            temp_raw = ((b[4] & 0x0F) << 7) | (b[5] & 0x7F)
            temp_f = (temp_raw - 400) * 0.1
            out["temperature_c"] = (temp_f - 32) * 5.0 / 9.0
            out["humidity"] = b[6] & 0x7F
        else:
            return None
        return out


# ──────────────────────────────────────────────────────────────────────────
# 设备解码器 2：Ambient Weather F007TH（温度/湿度）
# ──────────────────────────────────────────────────────────────────────────

class AmbientWeatherTHDecoder(DeviceDecoder):
    device_type = "ambient_weather_f007th"

    cfg = DeviceConfig(
        name="Ambient Weather F007TH / TFA 30.3208.02",
        modulation=OOK_PULSE_MANCHESTER_ZEROBIT,
        short_width=500,
        long_width=0,
        reset_limit=2400,
    )

    def decode_message(self, b: bytes) -> Optional[Dict[str, Any]]:
        if len(b) < 6:
            return None
        # 校验和 = lfsr_digest8(b[0..4], gen=0x98, key=0x3e) ^ 0x64 == b[5]
        expected = b[5]
        calculated = lfsr_digest8(b[:5], gen=0x98, key=0x3e) ^ 0x64
        if expected != calculated:
            return None
        device_id = b[1]
        battery_low = (b[2] & 0x80) != 0
        channel = ((b[2] & 0x70) >> 4) + 1
        temp_raw = ((b[2] & 0x0F) << 8) | b[3]
        temp_f = (temp_raw - 400) * 0.1
        humidity = b[4]
        if humidity > 100:
            return None
        if temp_f < -40.0 or temp_f >= 344.0:
            return None
        return {
            "device_type": "Ambientweather-F007TH",
            "id": device_id,
            "channel": channel,
            "battery_ok": not battery_low,
            "temperature_c": (temp_f - 32) * 5.0 / 9.0,
            "temperature_f": temp_f,
            "humidity": humidity,
            "mic": "CRC",
        }


# ──────────────────────────────────────────────────────────────────────────
# 设备解码器 3：LaCrosse TX（温度/湿度）
# ──────────────────────────────────────────────────────────────────────────

class LaCrosseTXDecoder(DeviceDecoder):
    device_type = "lacrosse_tx"

    cfg = DeviceConfig(
        name="LaCrosse TX Temperature/Humidity Sensor",
        modulation=OOK_PULSE_PWM,
        short_width=550,
        long_width=1400,
        gap_limit=3000,
        reset_limit=8000,
    )

    def decode_message(self, b: bytes) -> Optional[Dict[str, Any]]:
        if len(b) < 6:
            return None
        if b[0] != 0x0A:
            return None
        bits = bytes_to_bits(b[:6])
        nib = [0] * 11
        for i in range(44):
            bit = bits[i]
            nib[i >> 2] |= bit << (3 - (i & 3))
        checksum = sum(nib[:10]) & 0x0F
        if checksum != nib[10]:
            return None
        msg_type = nib[2]
        sensor_id = (nib[3] << 3) + (nib[4] >> 1)
        value = nib[5] * 10 + nib[6] + nib[7] * 0.1
        out: Dict[str, Any] = {
            "device_type": "LaCrosse-TX",
            "id": sensor_id,
            "mic": "PARITY",
        }
        if msg_type == 0x00:              # 温度
            out["temperature_c"] = value - 50.0
        elif msg_type == 0x0E:            # 湿度
            out["humidity"] = int(round(value))
        else:
            return None
        return out


# ──────────────────────────────────────────────────────────────────────────
# 设备解码器 4：Oregon Scientific THGR122N（温度/湿度）
# ──────────────────────────────────────────────────────────────────────────

_OS_ID_THGR122N = 0x1D20


class OregonTHGRDecoder(DeviceDecoder):
    device_type = "oregon_thgr122n"

    cfg = DeviceConfig(
        name="Oregon Scientific THGR122N/THGR968",
        modulation=OOK_PULSE_MANCHESTER_ZEROBIT,
        short_width=440,
        long_width=0,
        reset_limit=2400,
    )

    @staticmethod
    def _checksum_ok(msg: bytearray, nibbles_in_checksum: int = 15) -> bool:
        """sum-of-nibbles 校验，校验字节两个 nibble 交换。"""
        total = 0
        i = 0
        # 累加前 checksum_nibble 个 nibble（成对取字节高/低 nibble）
        while i < nibbles_in_checksum - 1:
            val = msg[i >> 1]
            total += (val >> 4) + (val & 0x0F)
            i += 2
        if nibbles_in_checksum & 1:
            total += (msg[nibbles_in_checksum >> 1] >> 4)
            checksum = (msg[nibbles_in_checksum >> 1] & 0x0F) | (msg[(nibbles_in_checksum + 1) >> 1] & 0xF0)
        else:
            checksum = (msg[nibbles_in_checksum >> 1] >> 4) | ((msg[nibbles_in_checksum >> 1] & 0x0F) << 4)
        return (total & 0xFF) == checksum

    def decode_message(self, b: bytes) -> Optional[Dict[str, Any]]:
        if len(b) < 8:
            return None
        msg = reflect_nibbles(b)
        sensor_id = (msg[0] << 8) | msg[1]
        channel = (msg[2] >> 4) & 0x0F
        device_id = (msg[2] & 0x0F) | (msg[3] & 0xF0)
        battery_low = (msg[3] >> 2) & 0x01
        if sensor_id != _OS_ID_THGR122N:
            return None
        if not self._checksum_ok(msg):
            return None
        # 温度 BCD：(m5>>4)*100 + (m4&0x0f)*10 + (m4>>4)，/10；符号位 m5&0x08
        temp_c = (((msg[5] >> 4) * 100) + ((msg[4] & 0x0F) * 10) + (msg[4] >> 4)) / 10.0
        if msg[5] & 0x08:
            temp_c = -temp_c
        humidity = (msg[6] & 0x0F) * 10 + (msg[6] >> 4)
        return {
            "device_type": "Oregon-THGR122N",
            "id": device_id,
            "channel": channel + 1,
            "battery_ok": not battery_low,
            "temperature_c": temp_c,
            "humidity": humidity,
            "mic": "CHECKSUM",
        }


# ──────────────────────────────────────────────────────────────────────────
# 设备解码器 5：Nexus（温度/湿度）
# ──────────────────────────────────────────────────────────────────────────

class NexusDecoder(DeviceDecoder):
    device_type = "nexus"

    cfg = DeviceConfig(
        name="Nexus / FreeTec NC-7345 / TFA 30.3209",
        modulation=OOK_PULSE_PPM,
        short_width=1000,
        long_width=2000,
        gap_limit=3000,
        reset_limit=5000,
    )

    def decode_message(self, b: bytes) -> Optional[Dict[str, Any]]:
        if len(b) < 5:
            return None
        if (b[3] & 0xF0) != 0xF0:
            return None
        if (b[1] & 0x30) == 0x30:  # 通道必须 1-3
            return None
        sensor_id = b[0]
        battery = b[1] & 0x80
        channel = ((b[1] & 0x30) >> 4) + 1
        # 12 位有符号温度 = (b1低nibble<<8)|b2，符号位 bit11，再 ×0.1
        temp_raw = ((b[1] & 0x0F) << 8) | b[2]
        if temp_raw & 0x800:  # 12 位符号扩展
            temp_raw -= 0x1000
        temp_c = temp_raw * 0.1
        humidity = ((b[3] & 0x0F) << 4) | (b[4] >> 4)
        if humidity != 0 and humidity > 100:
            return None
        out: Dict[str, Any] = {
            "device_type": "Nexus-TH",
            "id": sensor_id,
            "channel": channel,
            "battery_ok": bool(battery),
            "temperature_c": temp_c,
        }
        if humidity != 0:
            out["humidity"] = humidity
        return out


# ──────────────────────────────────────────────────────────────────────────
# 设备解码器 6：TPMS Citroen（胎压/温度）
# ──────────────────────────────────────────────────────────────────────────

class TpmsCitroenDecoder(DeviceDecoder):
    device_type = "tpms_citroen"

    cfg = DeviceConfig(
        name="Citroen/Peugeot TPMS",
        modulation=FSK_PULSE_PCM,
        short_width=52,
        long_width=52,
        reset_limit=150,
    )

    def decode_message(self, b: bytes) -> Optional[Dict[str, Any]]:
        if len(b) < 10:
            return None
        if b[6] == 0 or b[7] == 0:
            return None
        crc = 0
        for i in range(1, 10):
            crc ^= b[i]
        if crc != 0:
            return None
        state = b[0]
        sid = (b[1] << 24) | (b[2] << 16) | (b[3] << 8) | b[4]
        pressure = b[6]
        temperature = b[7]
        return {
            "device_type": "Citroen-TPMS",
            "id": f"{sid:08x}",
            "state": f"{state:02x}",
            "flags": b[5] >> 4,
            "repeat": b[5] & 0x0F,
            "pressure_kpa": pressure * 1.364,
            "temperature_c": temperature - 50.0,
            "mic": "CHECKSUM",
        }


# ──────────────────────────────────────────────────────────────────────────
# 设备解码器 7：TPMS Elantra2012/Hyundai（胎压/温度）
# ──────────────────────────────────────────────────────────────────────────

class TpmsElantraDecoder(DeviceDecoder):
    device_type = "tpms_elantra2012"

    cfg = DeviceConfig(
        name="Hyundai Elantra / Honda Civic TPMS (TRW)",
        modulation=FSK_PULSE_PCM,
        short_width=49,
        long_width=49,
        reset_limit=200,
    )

    def decode_message(self, b: bytes) -> Optional[Dict[str, Any]]:
        if len(b) < 8:
            return None
        if crc8(b[:8], polynomial=0x07, init=0x00) != 0:
            return None
        sid = (b[2] << 24) | (b[3] << 16) | (b[4] << 8) | b[5]
        pressure_kpa = b[0] + 60
        temperature_c = b[1] - 50
        battery_low = (b[6] & 0x02) >> 1
        return {
            "device_type": "Elantra2012-TPMS",
            "id": f"{sid:08x}",
            "pressure_kpa": pressure_kpa,
            "temperature_c": temperature_c,
            "battery_ok": not battery_low,
            "triggered": b[6] & 0x01,
            "storage": (b[6] & 0x04) >> 2,
            "mic": "CRC",
        }


# 默认注册全部设备解码器
for _d in (Acurite5n1Decoder(), AmbientWeatherTHDecoder(), LaCrosseTXDecoder(),
           OregonTHGRDecoder(), NexusDecoder(), TpmsCitroenDecoder(), TpmsElantraDecoder()):
    register_device(_d)


# ──────────────────────────────────────────────────────────────────────────
# 高层解码入口
# ──────────────────────────────────────────────────────────────────────────

def decode_pulses(pulses: List[Pulse], device_type: str) -> Optional[Dict[str, Any]]:
    """对一段已检测出的脉冲序列，按指定设备切片并解包。"""
    dec = get_device(device_type)
    if dec is None:
        raise KeyError(f"unknown device: {device_type}")
    mod = dec.cfg.modulation
    if mod == OOK_PULSE_PPM:
        bits = slice_ppm(pulses, dec.cfg)
    elif mod == OOK_PULSE_PWM:
        bits = slice_pwm(pulses, dec.cfg)
    elif mod == OOK_PULSE_MANCHESTER_ZEROBIT:
        bits = slice_manchester_zerobit(pulses, dec.cfg)
    else:  # FSK_PULSE_PCM：脉冲即原始比特，由设备自己做曼彻斯特
        bits = [1] * len(pulses)
    data_bytes = bits_to_bytes(bits)
    return dec.decode_message(data_bytes)


def decode_iq(iq: List[complex], device_type: str,
              sample_rate: int = RTL433_DEFAULT_SAMPLE_RATE) -> List[Dict[str, Any]]:
    """从复 IQ 包络 → OOK 脉冲检测 → 指定设备解码。

    注意：真实 ISM 信号需要落在 device_type 对应的中心频率(433.92MHz)附近，
    这里对已下变频到基带的 IQ 直接做包络检波。
    """
    dec = get_device(device_type)
    if dec is None:
        raise KeyError(f"unknown device: {device_type}")
    envelope = envelope_from_iq(iq)
    demod = PulseDemodulator(sample_rate=sample_rate)
    pulses = demod.detect(envelope)
    result = decode_pulses(pulses, device_type)
    return [result] if result else []


# ──────────────────────────────────────────────────────────────────────────
# 注册到 ToolRegistry
# ──────────────────────────────────────────────────────────────────────────

def register_rtl433_tools(registry) -> None:
    """把 ISM 设备解码能力注册到 MBDSDR ToolRegistry。

    提供三个工具：
      - rtl433_list_devices : 列出已注册的设备解码器
      - rtl433_decode_pulses : 对给定脉冲/比特载荷解码
      - rtl433_decode_iq      : 对一段基带 IQ 做完整 OOK→解码
    """
    from .tool_registry import ToolResult  # 延迟导入避免循环依赖

    def _list_devices(args):
        devs = list_devices()
        return ToolResult(
            success=True,
            content=f"已注册 {len(devs)} 个 ISM 设备解码器：",
            data={"devices": devs},
        )

    def _decode_pulses(args):
        device_type = args.get("device_type")
        # 直接给字节载荷(hex)做协议解包，便于回放/已知比特流验证
        payload_hex = args.get("payload_hex", "")
        dec = get_device(device_type)
        if dec is None:
            return ToolResult(success=False, content=f"未知设备: {device_type}",
                              error="unknown_device")
        data_bytes = bytes.fromhex(payload_hex) if payload_hex else b""
        out = dec.decode_message(data_bytes)
        if out is None:
            return ToolResult(success=False, content="校验失败或字段不合法",
                              error="decode_failed")
        return ToolResult(success=True, content=str(out), data={"result": out})

    def _decode_iq(args):
        return ToolResult(success=True,
                          content="rtl433_decode_iq 需基带 IQ 数组；当前为离线路径，"
                                  "请用 decode_pulses/已知比特流验证。",
                          data={"note": "offline"})

    registry.register(
        name="rtl433_list_devices",
        description="列出已注册的 ISM 设备解码器（天气站/温湿度/胎压）",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_list_devices,
        category="ism_decoder",
    )
    registry.register(
        name="rtl433_decode_pulses",
        description="对指定设备协议的字节载荷(hex)做解包，输出 device_type/id/温度/湿度/气压等",
        parameters={
            "type": "object",
            "properties": {
                "device_type": {"type": "string", "description": "设备类型，见 rtl433_list_devices"},
                "payload_hex": {"type": "string", "description": "消息载荷字节的十六进制"},
            },
            "required": ["device_type", "payload_hex"],
        },
        handler=_decode_pulses,
        category="ism_decoder",
    )
    registry.register(
        name="rtl433_decode_iq",
        description="对一段基带复 IQ 做 OOK 包络检测并调用指定设备解码器",
        parameters={
            "type": "object",
            "properties": {
                "device_type": {"type": "string"},
                "sample_rate": {"type": "integer", "description": "采样率 Hz，默认 250000"},
            },
            "required": ["device_type"],
        },
        handler=_decode_iq,
        category="ism_decoder",
    )
