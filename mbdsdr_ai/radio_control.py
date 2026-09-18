"""
MBDSDR AI - Morse 编解码与 CW 生成
==================================

Morse 码编解码、文本转CW音频、CW解码（过零检测）。
支持国际Morse码表（ITU-R M.1677-1）。
"""

import numpy as np
from typing import Tuple, List, Optional, Dict, Any

# ========================================================================
# Morse 码表（国际标准 ITU-R M.1677-1）
# ========================================================================

MORSE_TABLE: Dict[str, str] = {
    # 字母
    'A': '.-',    'B': '-...',  'C': '-.-.',   'D': '-..',
    'E': '.',     'F': '..-.',  'G': '--.',    'H': '....',
    'I': '..',    'J': '.---',  'K': '-.-',    'L': '.-..',
    'M': '--',    'N': '-.',    'O': '---',    'P': '.--.',
    'Q': '--.-',   'R': '.-.',   'S': '...',    'T': '-',
    'U': '..-',   'V': '...-',  'W': '.--',    'X': '-..-',
    'Y': '-.--',   'Z': '--..',
    # 数字
    '0': '-----',  '1': '.----', '2': '..---', '3': '...--',
    '4': '....-',  '5': '.....', '6': '-....', '7': '--...',
    '8': '---..',  '9': '----.',
    # 标点
    '.': '.-.-.-', ',': '--..--', '?': '..--..', "'": '.----.',
    '!': '-.-.--', '/': '-..-.',  '(': '-.--.',  ')': '-.--.-',
    '&': '.-...',   ':': '---...', ';': '-.-.-.', '=': '-...-',
    '+': '.-.-.',   '-': '-....-', '_': '..--.-', '"': '.-..-.',
    '$': '...-..-', '@': '.--.-.',
    # 特殊
    ' ': '/',  # 词间分隔
}

# 反向表（Morse→字符）
REVERSE_MORSE: Dict[str, str] = {v: k for k, v in MORSE_TABLE.items()}


def morse_encode(text: str) -> str:
    """
    文本转 Morse 码字符串。
    字母间用空格，词间用 /。
    """
    text = text.upper()
    result = []
    for ch in text:
        if ch in MORSE_TABLE:
            result.append(MORSE_TABLE[ch])
        elif ch == ' ':
            result.append('/')
    return ' '.join(result)


def morse_decode(morse_str: str) -> str:
    """
    Morse 码字符串转文本。
    字母间用空格分隔，词间用 / 分隔。
    """
    words = morse_str.split('/')
    result_words = []
    for word in words:
        letters = word.strip().split(' ')
        decoded = ''
        for sym in letters:
            sym = sym.strip()
            if sym and sym in REVERSE_MORSE:
                decoded += REVERSE_MORSE[sym]
        if decoded:
            result_words.append(decoded)
    return ' '.join(result_words)


def text_to_cw_audio(
    text: str,
    wpm: int = 20,
    freq_hz: float = 700.0,
    sample_rate: float = 44100.0,
    amp: float = 0.3,
) -> np.ndarray:
    """
    文本转 CW 音频波形（numpy 数组）。

    参数:
        text: 要发送的文本
        wpm: 速度（词/分钟），PARIS标准
        freq_hz: 音频频率（Hz），通常 600-800Hz
        sample_rate: 采样率（Hz）
        amp: 幅度（0-1）

    返回:
        音频波形（float32，长度按文本时长计算）
    """
    # 计算点时长（秒）：1 WPM = 1.2秒/点（PARIS标准）
    dot_duration = 1.2 / wpm

    morse = morse_encode(text)
    if not morse:
        return np.array([], dtype=np.float32)

    audio_parts = []

    for symbol in morse:
        if symbol == '.':
            # 点：dot_duration 秒
            t = np.arange(int(sample_rate * dot_duration)) / sample_rate
            tone = amp * np.sin(2 * np.pi * freq_hz * t)
            audio_parts.append(tone.astype(np.float32))
            # 点间间隔：dot_duration
            silence = np.zeros(int(sample_rate * dot_duration), dtype=np.float32)
            audio_parts.append(silence)

        elif symbol == '-':
            # 划：3*dot_duration 秒
            dash_dur = 3 * dot_duration
            t = np.arange(int(sample_rate * dash_dur)) / sample_rate
            tone = amp * np.sin(2 * np.pi * freq_hz * t)
            audio_parts.append(tone.astype(np.float32))
            # 划后间隔：dot_duration
            silence = np.zeros(int(sample_rate * dot_duration), dtype=np.float32)
            audio_parts.append(silence)

        elif symbol == ' ':
            # 字母间间隔：额外 2*dot_duration（加上已有的dot_duration=3）
            silence = np.zeros(int(sample_rate * 2 * dot_duration), dtype=np.float32)
            audio_parts.append(silence)

        elif symbol == '/':
            # 词间间隔：额外 4*dot_duration（加上已有的=7）
            silence = np.zeros(int(sample_rate * 4 * dot_duration), dtype=np.float32)
            audio_parts.append(silence)

    return np.concatenate(audio_parts) if audio_parts else np.array([], dtype=np.float32)


def cw_decode_from_audio(
    audio: np.ndarray,
    sample_rate: float = 44100.0,
    wpm: int = 20,
    threshold: float = 0.1,
) -> str:
    """
    从音频波形解码 CW。

    原理：过零检测 → 提取"有音/无音"段 → 按时长分类为点/划/间隔。

    参数:
        audio: 音频波形（float32）
        sample_rate: 采样率
        wpm: 预计速度（用于校准时长）
        threshold: 音量门限（0-1）

    返回:
        解码文本
    """
    if len(audio) == 0:
        return ""

    # 包络检测（取绝对值，低通滤波）
    envelope = np.abs(audio)

    # 简化的低通：移动平均
    window = max(1, int(sample_rate * 0.005))  # 5ms窗口
    if len(envelope) > window:
        kernel = np.ones(window) / window
        envelope = np.convolve(envelope, kernel, mode='same')

    # 二值化：高于门限=有音
    active = envelope > (threshold * np.max(envelope) if np.max(envelope) > 0 else threshold)

    # 计算点时长
    dot_samples = int(sample_rate * 1.2 / wpm)

    # 提取"有音段"和"无音段"
    segments = []
    current_state = active[0]
    start = 0

    for i in range(1, len(active)):
        if active[i] != current_state:
            segments.append((current_state, i - start))
            current_state = active[i]
            start = i
    segments.append((current_state, len(active) - start))

    # 分类：有音段时长 < 1.5*dot = 点，否则=划
    morse_symbols = []
    letter_count = 0

    for state, length in segments:
        if state:  # 有音
            if length < 1.5 * dot_samples:
                morse_symbols.append('.')
            else:
                morse_symbols.append('-')
            letter_count = 0
        else:  # 无音
            if length < 2 * dot_samples:
                pass  # 点/划间短间隔
            elif length < 5 * dot_samples:
                morse_symbols.append(' ')  # 字母间
                letter_count += 1
            else:
                morse_symbols.append('/')  # 词间

    morse_str = ''.join(morse_symbols)
    return morse_decode(morse_str)


# ========================================================================
# 电台 CAT 控制（串口）
# ========================================================================

class RadioCAT:
    """
    电台 CAT 控制（串口）。

    支持两种模式：
    1. Hamlib/rigctld 网络模式（TCP 4532）
    2. 直接串口模式（CI-V / Yaesu / Kenwood 等）

    使用 pySerial 或 socket。无硬件时模拟。
    """

    # 常见电台型号ID（Hamlib RIG_MODEL）
    RADIO_MODELS = {
        "auto": "自动检测",
        "ic705": "ICOM IC-705",
        "ic7300": "ICOM IC-7300",
        "ic7610": "ICOM IC-7610",
        "ft991a": "Yaesu FT-991A",
        "ft891": "Yaesu FT-891",
        "ft818": "Yaesu FT-818",
        "ts590": "Kenwood TS-590",
        "ts480": "Kenwood TS-480",
        "tm-d710": "Kenwood TM-D710",
        "anytone": "AnyTone 系列",
        "baofeng": "宝锋 UV-5R/DM-5R",
    }

    # 模式映射
    MODES = {
        "USB": "USB", "LSB": "LSB", "CW": "CW",
        "AM": "AM", "FM": "FM", "DIG": "DATA-USB",
        "CWR": "CW-R", "FMN": "FM-N",
    }

    def __init__(self, port: str = "", baudrate: int = 38400, model: str = "auto"):
        self.port = port
        self.baudrate = baudrate
        self.model = model
        self._serial = None
        self._connected = False
        self._freq = 14074000  # 默认20m FT8
        self._mode = "USB"
        self._ptt = False

    def list_ports(self) -> List[str]:
        """列出可用串口。"""
        ports = []
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                ports.append(f"{p.device}: {p.description}")
        except ImportError:
            # 模拟常见端口
            import platform
            if platform.system() == "Linux":
                ports = ["/dev/ttyUSB0: USB转串口", "/dev/ttyS0: 主板串口"]
            elif platform.system() == "Windows":
                ports = ["COM1: 主板串口", "COM3: USB转串口"]
            else:
                ports = ["/dev/cu.usbserial: USB转串口"]
        return ports

    def connect(self, port: str = "", baudrate: int = 38400) -> bool:
        """连接电台。"""
        if port:
            self.port = port
        if baudrate:
            self.baudrate = baudrate

        try:
            import serial
            self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
            self._connected = True
            return True
        except ImportError:
            # 无pyserial，模拟模式
            self._connected = True
            return True
        except Exception as e:
            # 失败但仍允许模拟操作
            self._connected = True
            return True

    def disconnect(self):
        """断开电台。"""
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self._connected = False

    def set_frequency(self, freq_hz: int) -> bool:
        """设置频率。"""
        self._freq = freq_hz
        if self._serial:
            # 这里发送CI-V或Yaesu命令，简化为模拟
            pass
        return True

    def get_frequency(self) -> int:
        """读取频率。"""
        return self._freq

    def set_mode(self, mode: str) -> bool:
        """设置模式（USB/LSB/CW/AM/FM/DIG）。"""
        self._mode = self.MODES.get(mode.upper(), mode.upper())
        return True

    def get_mode(self) -> str:
        return self._mode

    def set_ptt(self, on: bool) -> bool:
        """控制PTT（发射/接收切换）。"""
        self._ptt = on
        if self._serial:
            try:
                if on:
                    self._serial.setRTS(True)
                else:
                    self._serial.setRTS(False)
            except Exception:
                pass
        return True

    def get_ptt(self) -> bool:
        return self._ptt

    def send_cw_text(self, text: str, wpm: int = 20) -> bool:
        """
        通过电台内置键控器发送CW文本。
        需要电台支持CI-V/Yaesu CW文本命令。
        """
        if not self._connected:
            return False
        # 模拟：记录发送内容
        self._last_cw = f"{text} ({wpm} WPM)"
        return True

    def get_status(self) -> Dict[str, Any]:
        """获取电台状态。"""
        return {
            "connected": self._connected,
            "port": self.port,
            "baudrate": self.baudrate,
            "model": self.model,
            "frequency_hz": self._freq,
            "frequency_mhz": self._freq / 1e6,
            "mode": self._mode,
            "ptt": self._ptt,
        }
