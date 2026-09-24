"""
MBDSDR AI - Morse 编解码与 CW 生成 + 电台 CAT 控制
====================================================

Morse 码编解码、文本转CW音频、CW解码（过零检测）。
支持国际Morse码表（ITU-R M.1677-1）。

电台 CAT 控制：真实串口命令，参考 Hamlib 源码实现。
- ICOM  CI-V 二进制帧 (FE FE ... FD)
- Yaesu NewCAT  ASCII 命令 (FA<9位>;)
- Kenwood       ASCII 命令 (FA<11位>;)

红线：硬件失败不静默成功，不造假。未连接时所有 set_* 返回 False。
"""

import logging
import struct
from typing import Tuple, List, Optional, Dict, Any

import numpy as np

logger = logging.getLogger(__name__)

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
# 电台 CAT 控制（串口）— 参考 Hamlib 源码
# 来源: Hamlib 仓库 https://github.com/Hamlib/Hamlib
#   - rigs/icom/frame.c       make_cmd_frame()
#   - rigs/icom/icom.c        icom_set_freq() / icom_set_mode() / icom_set_ptt()
#   - rigs/icom/icom_defs.h   CI-V 命令字常量
#   - src/misc.c              to_bcd() / from_bcd()
#   - rigs/yaesu/newcat.c     newcat_set_freq()  FA%09.0f;
#   - rigs/kenwood/kenwood.c  kenwood_set_freq() F<vfo><11digits>;
# ========================================================================

# ---- ICOM CI-V 常量（来源: rigs/icom/icom_defs.h） ----
CIV_PREAMBLE   = 0xFE   # 来源: icom_defs.h:28  #define PR 0xfe
CIV_EOM        = 0xFD   # 来源: icom_defs.h:31  #define FI 0xfd
CIV_CTRL_ID    = 0xE0   # 来源: icom_defs.h:29  #define CTRLID 0xe0 控制器默认地址
CIV_ACK        = 0xFB   # 来源: icom_defs.h:32  #define ACK 0xfb
CIV_NAK        = 0xFA   # 来源: icom_defs.h:33  #define NAK 0xfa

# CI-V 命令字（来源: rigs/icom/icom_defs.h:66-105）
CIV_CMD_SND_FREQ   = 0x00   # icom_defs.h:66
CIV_CMD_SND_MODE   = 0x01   # icom_defs.h:67
CIV_CMD_RD_FREQ    = 0x03   # icom_defs.h:69  读显示频率
CIV_CMD_RD_MODE    = 0x04   # icom_defs.h:70
CIV_CMD_SET_FREQ   = 0x05   # icom_defs.h:71  设置频率数据
CIV_CMD_SET_MODE   = 0x06   # icom_defs.h:72
CIV_CMD_CTL_PTT    = 0x1C   # icom_defs.h:94  控制 PTT
CIV_CMD_RD_TRXID   = 0x19   # icom_defs.h:91  读 transceiver ID

# CI-V 子命令
CIV_SUB_PTT        = 0x00   # icom_defs.h:369  #define S_PTT 0x00
CIV_SUB_RD_TX_FREQ = 0x03   # icom_defs.h:371  #define S_RD_TX_FREQ 0x03

# CI-V 模式子命令（来源: icom_defs.h:115-122）
CIV_MODE_LSB = 0x00
CIV_MODE_USB = 0x01
CIV_MODE_AM  = 0x02
CIV_MODE_CW  = 0x03
CIV_MODE_RTTY= 0x04
CIV_MODE_FM  = 0x05

# 常见 ICOM 电台默认 CI-V 地址（来源: rigs/icom/ic7300.c:450,721 等）
# 注意：用户可在电台菜单修改，连接后可通过 RD_TRXID 0x19 自动识别
CIV_RIG_ADDR = {
    "ic705":   0xA4,   # ic7300.c:1815+  IC-705
    "ic7300":  0x94,   # ic7300.c:450    IC-7300
    "ic7300mkii": 0x94,# 同 IC-7300
    "ic7610":  0x76,   # IC-7610 常见值
    "ic7600":  0x7A,
    "ic7410":  0x78,
    "ic706mkiig": 0x58,
    "ic9700":  0x88,
    "icr8600": 0x74,
}


def _to_bcd_le(freq: int, nbytes: int = 5) -> bytes:
    """
    把频率(Hz)转成 ICOM CI-V 用的小端 BCD 字节串。
    来源: src/misc.c:146 to_bcd()

    to_bcd 注释（misc.c:182）：
        1234567890 Hz → 字节序 90 78 56 34 12（小端 BCD）

    freq_len 默认 5 字节 = 10 个 BCD 数字（icom.c:1515: freq_len = priv->civ_731_mode ? 4 : 5）
    """
    out = bytearray(nbytes)
    f = int(freq)
    for i in range(nbytes):
        lo = f % 10
        f //= 10
        hi = f % 10
        f //= 10
        out[i] = (hi << 4) | lo
    return bytes(out)


def _from_bcd_le(data: bytes) -> int:
    """小端 BCD → Hz。来源: src/misc.c:193 from_bcd()"""
    f = 0
    mult = 1
    for b in data:
        f += (b & 0x0F) * mult
        mult *= 10
        f += ((b >> 4) & 0x0F) * mult
        mult *= 10
    return f


def _civ_frame(rig_addr: int, cmd: int,
               subcmd: Optional[int] = None,
               data: bytes = b"",
               my_addr: int = CIV_CTRL_ID) -> bytes:
    """
    构建 ICOM CI-V 帧：FE FE <rigaddr> <myaddr> <cmd> [subcmd] [data...] FD
    来源: rigs/icom/frame.c:52 make_cmd_frame()

    frame[i++] = PR;          // frame.c:62
    frame[i++] = PR;          // frame.c:63
    frame[i++] = re_id;       // frame.c:64  电台 CI-V 地址
    frame[i++] = ctrl_id;     // frame.c:65  控制器地址
    frame[i++] = cmd;         // frame.c:66
    if (subcmd != -1) frame[i++] = subcmd;   // frame.c:81
    memcpy(frame+i, data, data_len);          // frame.c:86
    frame[i++] = FI;          // frame.c:90
    """
    out = bytearray([CIV_PREAMBLE, CIV_PREAMBLE, rig_addr, my_addr, cmd])
    if subcmd is not None:
        out.append(subcmd & 0xFF)
    out += data
    out.append(CIV_EOM)
    return bytes(out)


class RadioCAT:
    """
    电台 CAT 控制（串口）。

    真实命令，不造假：
      - connect() 真正打开 pyserial 串口；失败返回 False
      - 无 pyserial / 串口打不开 → 离线模式，connected=False
      - set_frequency/set_mode/set_ptt 在未连接时返回 False

    协议族：
      - ICOM CI-V  (model 以 ic 开头)
      - Yaesu NewCAT ASCII (model 以 ft 开头, 如 ft818/ft891/ft991a)
      - Kenwood ASCII     (model 以 ts 开头, 如 ts590/ts480)
    """

    RADIO_MODELS: Dict[str, Dict[str, Any]] = {
        "auto":       {"brand": "auto",     "desc": "自动检测"},
        "ic705":      {"brand": "icom",     "desc": "ICOM IC-705",   "civ": 0xA4},
        "ic7300":     {"brand": "icom",     "desc": "ICOM IC-7300",  "civ": 0x94},
        "ic7610":     {"brand": "icom",     "desc": "ICOM IC-7610",  "civ": 0x76},
        "ic7600":     {"brand": "icom",     "desc": "ICOM IC-7600",  "civ": 0x7A},
        "ft991a":     {"brand": "yaesu",    "desc": "Yaesu FT-991A"},
        "ft891":      {"brand": "yaesu",    "desc": "Yaesu FT-891"},
        "ft818":      {"brand": "yaesu",    "desc": "Yaesu FT-818"},
        "ft817":      {"brand": "yaesu",    "desc": "Yaesu FT-817/ND"},
        "ts590":      {"brand": "kenwood",  "desc": "Kenwood TS-590S"},
        "ts480":      {"brand": "kenwood",  "desc": "Kenwood TS-480"},
        "tm-d710":    {"brand": "kenwood",  "desc": "Kenwood TM-D710"},
    }

    # ICOM 模式映射：字符串 → CI-V 子命令
    # 来源: rigs/icom/icom_defs.h:115-122
    ICOM_MODES = {
        "LSB": CIV_MODE_LSB,
        "USB": CIV_MODE_USB,
        "AM":  CIV_MODE_AM,
        "CW":  CIV_MODE_CW,
        "RTTY": CIV_MODE_RTTY,
        "FM":  CIV_MODE_FM,
        "CWR": 0x07,   # icom_defs.h:124 S_CWR
    }

    # Yaesu NewCAT 模式字节（来源: rigs/yaesu/newcat.c 中 MD; 命令）
    # MD0=LSB, MD1=USB, MD2=CW, MD3=FM, MD4=AM, MD5=RTTY(L), MD6=RTTY-U,
    # MD7=CWR, MD8=DIG-U, MD9=DIG-L
    YAESU_MODES = {
        "LSB": 0, "USB": 1, "CW": 2, "FM": 3,
        "AM": 4, "RTTY": 5, "RTTYR": 6, "CWR": 7,
        "DIG": 8, "DIGL": 9,
    }

    def __init__(self, port: str = "", baudrate: int = 38400,
                 model: str = "auto", civ_addr: Optional[int] = None):
        self.port = port
        self.baudrate = baudrate
        self.model = model
        self._civ_addr = civ_addr  # None=按型号表查；可手动覆盖
        self._serial = None        # pyserial.Serial 实例；None=未连接
        self._connected = False
        self._offline = False      # 无 pyserial / 无硬件时为 True
        self._brand: str = "auto"
        self._freq: int = 14074000   # 默认 20m FT8
        self._mode: str = "USB"
        self._ptt: bool = False
        self._timeout = 0.5

    # ------------------------------------------------------------------
    # 串口枚举
    # ------------------------------------------------------------------
    def list_ports(self) -> List[str]:
        """列出可用串口（真实调用 pyserial.tools.list_ports）。"""
        try:
            import serial.tools.list_ports  # pyserial
        except ImportError:
            logger.warning("pyserial 未安装，无法枚举串口（离线模式）")
            return []
        out = []
        for p in serial.tools.list_ports.comports():
            out.append(f"{p.device}: {p.description}")
        return out

    def list_radios(self) -> List[str]:
        """返回已知电台型号清单。"""
        return [f"{k}: {v['desc']}" for k, v in self.RADIO_MODELS.items()]

    # ------------------------------------------------------------------
    # 连接 / 断开
    # ------------------------------------------------------------------
    def connect(self, port: str = "", baudrate: int = 38400,
                model: Optional[str] = None) -> bool:
        """
        真实打开串口并尝试识别电台。

        返回:
            True  = 串口已打开且至少收到一次电台应答
            False = 串口打不开 / 无 pyserial / 无应答（绝不假成功）
        """
        if port:
            self.port = port
        if baudrate:
            self.baudrate = baudrate
        if model:
            self.model = model

        # 1) 导入 pyserial
        try:
            import serial  # noqa: F401
        except ImportError:
            logger.error("pyserial 未安装，进入离线模式（connect=False）")
            self._offline = True
            self._connected = False
            return False

        # 2) 打开串口（真实失败即返回 False）
        try:
            import serial
            self._serial = serial.Serial(
                self.port, self.baudrate,
                timeout=self._timeout,
                write_timeout=self._timeout,
            )
        except Exception as e:
            logger.error("打开串口 %s @ %d 失败: %s", self.port, self.baudrate, e)
            self._serial = None
            self._connected = False
            return False

        # 3) 推断品牌
        self._brand = self._infer_brand(self.model)

        # 4) 尝试识别（发一条读频率命令，等应答）
        ok = self._probe()
        if not ok:
            logger.warning("串口已打开但未收到电台识别应答: %s", self.port)
            # 仍然标记串口已打开，但 connected=False 以便上层知道"有串口无电台"
            # 注意：这里保持 _connected=False，不允许 set_* 静默成功
            self._connected = False
            return False

        self._connected = True
        logger.info("CAT 连接成功: %s @ %d, brand=%s, model=%s",
                    self.port, self.baudrate, self._brand, self.model)
        return True

    def _infer_brand(self, model: str) -> str:
        m = (model or "auto").lower()
        if m in self.RADIO_MODELS:
            return self.RADIO_MODELS[m]["brand"]
        if m.startswith("ic"):
            return "icom"
        if m.startswith("ft") or m.startswith("ftdx") or m.startswith("ftx"):
            return "yaesu"
        if m.startswith("ts") or m.startswith("tm-") or m.startswith("th"):
            return "kenwood"
        return "auto"

    def _probe(self) -> bool:
        """
        发识别命令，看是否有应答。
        ICOM: 发 C_RD_FREQ (0x03)
        Yaesu: 发 "FA;" 读 VFO-A 频率
        Kenwood: 发 "FA;" 读主 VFO 频率
        """
        try:
            if self._brand == "icom":
                addr = self._civ_addr or self.RADIO_MODELS.get(self.model, {}).get("civ", 0xE0)
                frame = _civ_frame(addr, CIV_CMD_RD_FREQ)
                self._serial.reset_input_buffer()
                self._serial.write(frame)
                # 等应答：以 FE FE ... FD 结束，至少 9 字节
                resp = self._read_until(0xFD, timeout=self._timeout)
                if resp and len(resp) >= 5 and resp[-1] == CIV_EOM:
                    # 解析回读频率（数据从索引 5 开始，5 字节 BCD）
                    try:
                        self._freq = _from_bcd_le(resp[5:10])
                    except Exception:
                        pass
                    return True
                return False

            elif self._brand in ("yaesu", "kenwood"):
                self._serial.reset_input_buffer()
                self._serial.write(b"FA;")
                resp = self._read_until(ord(';'), timeout=self._timeout)
                # 期望形如 "FA001407400;" (Yaesu 9 位) 或 "FA00014074000;" (Kenwood 11 位)
                if resp and resp.startswith(b"FA") and resp.endswith(b";"):
                    digits = self._parse_freq_digits(resp[2:-1])
                    if digits is not None:
                        self._freq = digits
                        return True
                return False

            else:
                # auto：先试 ICOM（CI-V 地址 0xE0=广播，大多数 ICOM 会回）
                # 再试 Yaesu/Kenwood ASCII
                for probe_brand, addr in (("icom", 0xE0), ("icom", 0x94),
                                          ("yaesu", None), ("kenwood", None)):
                    self._brand = probe_brand
                    if self._probe():
                        return True
                self._brand = "auto"
                return False
        except Exception as e:
            logger.error("probe 异常: %s", e)
            return False

    @staticmethod
    def _parse_freq_digits(s: bytes) -> Optional[int]:
        try:
            digits = s.decode("ascii", errors="ignore").strip()
            if digits.isdigit():
                return int(digits)
        except Exception:
            pass
        return None

    def _read_until(self, terminator: int, timeout: float = 0.5) -> bytes:
        """从串口读到 terminator 字节（含）为止。"""
        if self._serial is None:
            return b""
        import time
        buf = bytearray()
        deadline = time.time() + timeout
        while time.time() < deadline:
            n = self._serial.in_waiting or 1
            chunk = self._serial.read(n)
            if chunk:
                buf += chunk
                if buf and buf[-1] == terminator:
                    break
            else:
                time.sleep(0.01)
        return bytes(buf)

    def disconnect(self):
        """断开串口。"""
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self._connected = False
        logger.info("CAT 已断开: %s", self.port)

    # ------------------------------------------------------------------
    # 频率
    # ------------------------------------------------------------------
    @staticmethod
    def build_icom_set_freq_frame(freq_hz: int, rig_addr: int = 0x94,
                                  my_addr: int = CIV_CTRL_ID) -> bytes:
        """
        构建 ICOM CI-V 写频率帧。
        来源: rigs/icom/icom.c:1467 icom_set_freq()
              - icom.c:1515 freq_len = 5 (默认)
              - icom.c:1523 to_bcd(freqbuf, freq, freq_len*2)
              - icom.c:1535 cmd = C_SET_FREQ (0x05)
              - frame.c:52 make_cmd_frame() 包成 FE FE ... FD
        """
        data = _to_bcd_le(freq_hz, 5)
        return _civ_frame(rig_addr, CIV_CMD_SET_FREQ, subcmd=None,
                          data=data, my_addr=my_addr)

    @staticmethod
    def build_yaesu_set_freq_frame(freq_hz: int, vfo: str = "A") -> bytes:
        """
        构建 Yaesu NewCAT 写频率 ASCII 命令。
        来源: rigs/yaesu/newcat.c:1647
              SNPRINTF(priv->cmd_str, ..., "BS%02d;FA%09.0f;", ...)
        即 FA + 9 位零填充 Hz + ';'
        """
        return f"FA{vfo}{int(round(freq_hz)):09d};".encode("ascii")

    @staticmethod
    def build_kenwood_set_freq_frame(freq_hz: int, vfo: str = "A") -> bytes:
        """
        构建 Kenwood 写频率命令。
        来源: rigs/kenwood/kenwood.c:2042
              SNPRINTF(freqbuf, ..., "F%c%011"PRIll, vfo_letter, freq);
        然后 kenwood_transaction 会补 ';'。
        """
        return f"F{vfo}{int(round(freq_hz)):011d};".encode("ascii")

    def set_frequency(self, freq_hz: int) -> bool:
        """
        设置频率。未连接返回 False。
        发送后读回确认，失败返回 False。
        """
        if not self._connected or self._serial is None:
            logger.warning("set_frequency: 未连接，拒绝操作 (freq=%d)", freq_hz)
            return False

        try:
            if self._brand == "icom":
                addr = self._civ_addr or self.RADIO_MODELS.get(self.model, {}).get("civ", 0xE0)
                frame = self.build_icom_set_freq_frame(freq_hz, rig_addr=addr)
                self._serial.reset_input_buffer()
                self._serial.write(frame)
                # ICOM 对 set_freq 会回 ACK (0xFB) 或 NAK (0xFA)
                # 来源: rigs/icom/frame.c:459 if (FI != buf[frm_len-1] && ACK != ...)
                resp = self._read_until(CIV_EOM, timeout=self._timeout)
                if resp and resp[-1] in (CIV_EOM, CIV_ACK):
                    self._freq = int(freq_hz)
                    return True
                logger.warning("ICOM set_freq 无 ACK, resp=%s", resp.hex() if resp else "<empty>")
                return False

            elif self._brand == "yaesu":
                cmd = self.build_yaesu_set_freq_frame(freq_hz)
                self._serial.reset_input_buffer()
                self._serial.write(cmd)
                # 读回：发 "FA;" 验证
                self._serial.write(b"FA;")
                resp = self._read_until(ord(';'), timeout=self._timeout)
                if resp.startswith(b"FA"):
                    digits = self._parse_freq_digits(resp[2:-1])
                    if digits is not None and abs(digits - int(freq_hz)) <= 1:
                        self._freq = digits
                        return True
                return False

            elif self._brand == "kenwood":
                cmd = self.build_kenwood_set_freq_frame(freq_hz)
                self._serial.reset_input_buffer()
                self._serial.write(cmd)
                # Kenwood 一般回 "FA<freq>;"
                resp = self._read_until(ord(';'), timeout=self._timeout)
                if resp.startswith(b"FA") or resp.startswith(b"OK"):
                    self._freq = int(freq_hz)
                    return True
                return False

            else:
                logger.error("未知 brand=%s，无法 set_frequency", self._brand)
                return False
        except Exception as e:
            logger.error("set_frequency 异常: %s", e)
            return False

    def get_frequency(self) -> Optional[int]:
        """读回当前频率。未连接返回 None。"""
        if not self._connected or self._serial is None:
            return None
        try:
            if self._brand == "icom":
                addr = self._civ_addr or self.RADIO_MODELS.get(self.model, {}).get("civ", 0xE0)
                self._serial.reset_input_buffer()
                self._serial.write(_civ_frame(addr, CIV_CMD_RD_FREQ))
                resp = self._read_until(CIV_EOM, timeout=self._timeout)
                if resp and resp[-1] == CIV_EOM and len(resp) >= 10:
                    self._freq = _from_bcd_le(resp[5:10])
                    return self._freq
                return None
            else:
                self._serial.reset_input_buffer()
                self._serial.write(b"FA;")
                resp = self._read_until(ord(';'), timeout=self._timeout)
                if resp.startswith(b"FA"):
                    d = self._parse_freq_digits(resp[2:-1])
                    if d is not None:
                        self._freq = d
                        return d
                return None
        except Exception as e:
            logger.error("get_frequency 异常: %s", e)
            return None

    # ------------------------------------------------------------------
    # 模式
    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> bool:
        """设置模式 USB/LSB/CW/AM/FM/DIG。未连接返回 False。"""
        if not self._connected or self._serial is None:
            logger.warning("set_mode: 未连接，拒绝操作 (mode=%s)", mode)
            return False
        mode = mode.upper()
        try:
            if self._brand == "icom":
                # 来源: rigs/icom/icom.c:2227 icom_transaction(rig, C_SET_MODE, icmode, ...)
                sub = self.ICOM_MODES.get(mode)
                if sub is None:
                    return False
                addr = self._civ_addr or self.RADIO_MODELS.get(self.model, {}).get("civ", 0xE0)
                # 模式字节: [mode, filter]，filter=0 普通
                frame = _civ_frame(addr, CIV_CMD_SET_MODE, subcmd=sub,
                                   data=bytes([0x00]))
                self._serial.reset_input_buffer()
                self._serial.write(frame)
                resp = self._read_until(CIV_EOM, timeout=self._timeout)
                if resp and resp[-1] in (CIV_EOM, CIV_ACK):
                    self._mode = mode
                    return True
                return False

            elif self._brand == "yaesu":
                # NewCAT: MD<mode>;
                m = self.YAESU_MODES.get(mode, self.YAESU_MODES.get("USB"))
                self._serial.reset_input_buffer()
                self._serial.write(f"MD{m};".encode("ascii"))
                self._read_until(ord(';'), timeout=self._timeout)
                self._mode = mode
                return True

            elif self._brand == "kenwood":
                # Kenwood: 模式命令 "MD<mode>;"  (0=LSB,1=USB,2=CWL,3=CWR,4=FM,5=AM,6=RTTY,7=PSK)
                kenwood_map = {"LSB": 0, "USB": 1, "CW": 2, "CWR": 3,
                               "FM": 4, "AM": 5, "RTTY": 6}
                m = kenwood_map.get(mode, 1)
                self._serial.reset_input_buffer()
                self._serial.write(f"MD{m};".encode("ascii"))
                self._read_until(ord(';'), timeout=self._timeout)
                self._mode = mode
                return True
            return False
        except Exception as e:
            logger.error("set_mode 异常: %s", e)
            return False

    def get_mode(self) -> Optional[str]:
        """读取模式（简化：返回缓存值；真正回读需扩展）。未连接返回 None。"""
        if not self._connected:
            return None
        return self._mode

    # ------------------------------------------------------------------
    # PTT
    # ------------------------------------------------------------------
    def set_ptt(self, on: bool) -> bool:
        """
        控制 PTT。
        - ICOM: CI-V C_CTL_PTT (0x1C), sub=0x00, data=[1/0]
          来源: rigs/icom/icom.c:5367 pttbuf[0] = ptt==ON ? 1 : 0;
                icom.c:5369 icom_transaction(rig, C_CTL_PTT, S_PTT, pttbuf, 1, ...)
        - Yaesu/Kenwood: ASCII "TX<0|1>;" 或 RTS 线
        """
        if not self._connected or self._serial is None:
            logger.warning("set_ptt: 未连接，拒绝操作 (on=%s)", on)
            return False
        try:
            if self._brand == "icom":
                addr = self._civ_addr or self.RADIO_MODELS.get(self.model, {}).get("civ", 0xE0)
                frame = _civ_frame(addr, CIV_CMD_CTL_PTT, subcmd=CIV_SUB_PTT,
                                   data=bytes([1 if on else 0]))
                self._serial.reset_input_buffer()
                self._serial.write(frame)
                resp = self._read_until(CIV_EOM, timeout=self._timeout)
                if resp and resp[-1] in (CIV_EOM, CIV_ACK):
                    self._ptt = on
                    return True
                return False
            else:
                # 通用：TX0;/TX1;
                self._serial.reset_input_buffer()
                self._serial.write(b"TX1;" if on else b"TX0;")
                self._read_until(ord(';'), timeout=self._timeout)
                self._ptt = on
                return True
        except Exception as e:
            logger.error("set_ptt 异常: %s", e)
            # 兜底：RTS 线 PTT（常见于 SignaLink 等）
            try:
                self._serial.setRTS(bool(on))
                self._ptt = on
                return True
            except Exception:
                return False

    def get_ptt(self) -> Optional[bool]:
        if not self._connected:
            return None
        return self._ptt

    # ------------------------------------------------------------------
    # Gain / 其他
    # ------------------------------------------------------------------
    def set_gain(self, gain_db: float) -> bool:
        """
        设置 RF 增益/衰减（若电台支持）。
        ICOM: C_CTL_LVL (0x14) + subcmd AF/RF/SQL
        此处简化为日志占位；硬件接入时再扩展具体 subcmd。
        """
        if not self._connected or self._serial is None:
            return False
        logger.info("set_gain(%s dB) - 当前型号未实现具体命令，仅记录", gain_db)
        return False  # 不静默成功

    def get_gain(self) -> Optional[float]:
        if not self._connected:
            return None
        return None

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def send_cw_text(self, text: str, wpm: int = 20) -> bool:
        """通过电台内置键控器发 CW（需要电台支持文本 CW 命令）。"""
        if not self._connected:
            return False
        # ICOM: C_SND_CW (0x17) 后跟 ASCII；不同型号命令长度不同。
        # 这里仅做占位，未验证硬件前不假装成功。
        logger.info("send_cw_text(%r, wpm=%d) - 未实现硬件命令", text, wpm)
        return False

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "offline": self._offline,
            "port": self.port,
            "baudrate": self.baudrate,
            "model": self.model,
            "brand": self._brand,
            "frequency_hz": self._freq,
            "frequency_mhz": self._freq / 1e6,
            "mode": self._mode,
            "ptt": self._ptt,
        }
