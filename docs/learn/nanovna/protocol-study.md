# NanoVNA 串口协议深研笔记（H / H4，文本命令集）

> 本笔记只做**互操作协议机制**的干净室学习：命令字、参数、响应格式、单位、
> 握手时序与 drain 节奏。所有结论逐条标注上游 `文件:行号`。
>
> **许可边界（严格遵守）**：三个学习源均为 GPL-3.0——
> - `repos/NanoVNA-edy555/`（ttrftech 原始固件，C/ChibiOS）
> - `repos/NanoVNA-H-hugen/`（Hugen 改进固件，H/H4 主流）
> - `repos/nanovna-saver/`（PC 客户端，Python，GPL-3.0-or-later）
>
> 串口文本命令是与硬件通信的**功能性接口**（与 AT/SCPI 命令同类，互操作场景），
> MBDSDR 以 MIT 干净室**自写**客户端合法；本笔记不复制任何上游代码，只落协议事实。
> 测试 fixture 为本项目按协议自造的响应文本，**不取自 nanovna-saver 仓库**。
>
> 本轮范围：只覆盖 H / H4 的 edy555 风格**文本协议**。V2 / S-A-A-2 二进制协议
> 仅落档（见 §6），本轮不实现。

---

## 1. 物理层与链路层

| 项 | 值 | 证据 |
|---|---|---|
| 物理接口 | USB CDC 虚拟串口（STMicro STM32 原生，非桥接芯片） | `nanovna-saver/.../Hardware.py:49` 表项 |
| 波特率 | 115200（固定，CDC 下实际忽略波特率） | `nanovna-saver/.../Serial.py:44` `self.baudrate = 115200` |
| 数据格式 | 8N1，无流控 | `Serial.py:46-47`（timeout 0.05，RLock 串行化） |
| 行结束（发往设备） | 命令以 `\r` 结尾 | `VNA.py:106` `self.serial.write(f"{command}\r".encode("ascii"))`；固件在 `\r` 处收行 `NanoVNA-edy555/main.c:2250` |
| 行结束（设备回包） | `\r\n` | 固件 `#define VNA_SHELL_NEWLINE_STR "\r\n"`（`edy555/main.c:42`，hugen `main.c:45` 同源） |
| 提示符 | `ch> ` | `edy555/main.c:44`、`hugen/main.c:45` `#define VNA_SHELL_PROMPT_STR "ch> "` |
| 未知命令回包 | `\<cmd>?\r\n` | `edy555/main.c:2311` `shell_printf("%s?" ..., shell_args[0])` |

**握手 / drain 节奏（关键时序，来自 nanovna-saver `VNA.exec_command`）：**
`VNA.py:102-124`
1. 先 `drain_serial()` 排空输入缓冲；
2. 写 `"<cmd>\r"`（ASCII）；
3. `sleep(wait)`，`wait = 0.05`s（`VNA.py:43` `WAIT=0.05`）；
4. 逐行 `readline()` 循环：空行视为未就绪 → 计入重试（`_max_retries` 随带宽/点数放大，`VNA.py:46-51`）；**回显行 == 命令本身则跳过**（`VNA.py:119`）；**遇到以 `ch>` 开头的提示符行即认为本轮结束并 break**（`VNA.py:121-123`）；其余行 `yield` 给上层解析。

**drain 实现：** `Serial.py:28-39`——把临时 timeout 设 0.05s，循环 `read(128)` 最多 512 次，读到空即恢复原 timeout 返回；读不满（堆积超过 ~64KB）才告警。

> 设计含义：客户端**不需要**逐命令硬等固定时长；正确姿势是"写命令 → 收行直到 `ch>` 提示符"。
> `data` / `frequencies` 会连续吐出 N 行（N = 当前 sweep 点数），逐行收齐再看提示符。

---

## 2. 完整命令集（本轮覆盖：H / H4 文本协议）

证据缩写：
- **E** = `repos/NanoVNA-edy555/main.c`（基线固件命令表 `E:2153-2208`）
- **H** = `repos/NanoVNA-H-hugen/main.c`（H/H4 命令表 `H:2911-3004`）
- **S** = `repos/nanovna-saver/src/NanoVNASaver/Hardware/VNA.py`（客户端发送/解析）

| 命令 | 用途 | 参数 | 响应格式（每行 `\r\n`） | 证据 |
|---|---|---|---|---|
| `help` | 能力探测 | 无 | `Commands: ver reset freq ...\r\n`（一行空格分隔命令名） | 固件 `E:2210-2222`；客户端据此探测 `capture`/`sn:`/`bandwidth` 特性 `S:126-140` |
| `version` | 固件版本 | 无 | 单行版本串，如 `1.0.174-...` | 固件 `E:2024-2029`（打印 `NANOVNA_VERSION`，`E:2022`）；客户端 `S:213-216` 取 `result[0]` |
| `info` | 硬件/构建横幅 | 无 | **多行**：首行板名（机型识别依据），后接版权/许可URL/`Version:`/构建时间等 | 固件 `E:2050-2057`、横幅数组 `E:92-104`；客户端逐行 join `S:200-203` |
| `sn` | 序列号 | 无 | 单行（**仅部分固件支持**；不支持时 `help` 里无 `sn:`，见 §4） | 客户端 `S:224-225`；特性门 `S:131-133` |
| `sweep` | 设置扫频 | 无参→读回；`<startHz> [stopHz] [points]` | 无参回 `start stop points\r\n` | H/H4 三点 `H:1603-1641`（第3参=点数 `H:1616/1636`）；基线 E **忽略第3参** `E:1098-1132`；客户端 `S:218-219` |
| `frequencies` | 读频点表 | 无 | 每行一个 **Hz 无符号整数**（0 频点跳过） | 固件 `E:1808-1817`（`%u\r\n`）；客户端 `S:164-165` `int(f.real)` |
| `data` | 读 S 参数 | `data [array]`，缺省=0 | 每行 **`re im` 两个 float**（归一化复 S 参数） | 固件 `E:682-701`、`H:738-755`（`"%f %f"` 逐点）；客户端 `S:205-211` `complex(*map(float, s.split()))` |
| `bandwidth` | 设置 IF 带宽 | `bandwidth {1000\|300\|100\|30\|10}`（kHz） | 非法参→usage；无回数据 | 固件 `E:1965-1980`（选项串 `E:1970`）；客户端读/写 `S:142-162` |
| `cal` | 校准状态/控制 | 无参→状态；`cal {load\|open\|short\|thru\|isoln\|done\|on\|off\|reset\|data\|in}` | 无参回**空格分隔**的已置位项列表 | 固件 `E:1458-1513`（状态项 `E:1460`）；客户端 `S:184-185` |
| `capture` | 屏幕截图 | 无 | **二进制像素流**（非文本），见 §5 | 固件 `E:727-745`（320×240 RGB565）；H4 可 BMP/RLE8 `H:757-787`；客户端按像素数精确读 `NanoVNA.py:60-67` |
| `pause` / `resume` | 停/续扫频 | 无 | 无 | 固件 `E:290-304`、命令表 `E:2182-2183` |

### 2.1 `data` 的数组下标语义（`E:690-695` / `H:745-748`）

| `data N` | 含义 |
|---|---|
| `data 0` | S11（归一化复数，参考面 = 校准后测量面） |
| `data 1` | S21 |
| `data 2..6` | 校准系数（load/open/short/thru/isoln 的误差项；一般客户端不读） |

> 注意：读出的 `re im` 是**复反射系数 / 复传输系数**本身（无量纲），不是阻抗。
> 工程量（VSWR、回波损耗、阻抗、增益 dB）由客户端按 §7 公式链换算。

---

## 3. 型号差异表（H vs H4 vs V2/S-A-A）

| 维度 | NanoVNA（edy555 基线） | NanoVNA-H（F072） | NanoVNA-H4（F303） | V2 / S-A-A-2 |
|---|---|---|---|---|
| MCU | STM32F072 | STM32F072 | STM32F303 | （另一代，二进制协议） |
| 点数上限 | 101 固定（`E nanovna.h:40`） | 101（`H nanovna.h:163`，`#else` 分支） | 401（`H nanovna.h:142`，`#if defined(NANOVNA_F303)` `H:123`） | — |
| 可选点数集 | {101} | {51,101} | {51,101,201,301,401}（`H nanovna.h:271-273`） | — |
| 点数下限 | 101 | 21（`H nanovna.h:166`） | 21 | — |
| `sweep` 第三参(点数) | 忽略 | 支持 | 支持 | — |
| 屏幕 | 320×240 | 320×240（`NanoVNA.py:34-35`） | 480×320（`NanoVNA_H4.py:24-25`） | — |
| 频段上限 | ~300 MHz | 1.5 GHz（`NanoVNA_H.py:32`） | 1.5 GHz（`NanoVNA_H4.py:32`） | — |
| 客户端合法点数 | (101,51,11) 基类 `S:56` | 继承基类 | (101,11,51,201,401) `NanoVNA_H4.py:26-31` | — |
| USB VID:PID | `0x0483:0x5740` | 同左 | 同左 | `0x04B4:0x0008`（`Hardware.py:48-52`） |
| 协议形态 | 文本 `\r` | 文本 `\r` | 文本 `\r`（新固件可 `scan` 优化，见下） | **二进制**（本轮不实现，仅落档） |

**机型识别**：`info` 首行板名字符串匹配——`"NanoVNA-H 4"→H4`、`"NanoVNA-H"→H`、`"NanoVNA"→NanoVNA`
（`Hardware.py:158-176`）。客户端不硬编码端口，靠 pyserial `list_ports.comports()` 枚举后按 VID:PID 过滤（`Hardware.py:103-126`）。

**H4 新固件可选的 `scan` 优化（本轮默认不走，落档）**：固件版本 ≥0.2.0 用 `scan start stop points`，
≥0.7.1 用 `scan ... 0b110` 一次取双信道（`NanoVNA.py:96-130`）。本轮一律用最稳的
`sweep start stop points` + 分别 `data 0` / `data 1`。

---

## 4. USB 识别（VID/PID）

`Hardware.py:48-52`：

| VID:PID | 名称 | 本轮是否接入 |
|---|---|---|
| `0x0483:0x5740` | NanoVNA（含 H / H4，ST CDC） | ✅ |
| `0x16C0:0x0483` | AVNA | ❌（非目标） |
| `0x04B4:0x0008` | S-A-A-2（V2） | ❌（二进制协议，落档不实现） |

> 客户端只把上述 VID:PID 当作**提示过滤**；真正连哪个端口由调用方显式传 `port=`，
> 不传则枚举并列出候选，绝不写死 `/dev/ttyACM0` 之类。

---

## 5. 校准（cal）状态语义

`cal` 无参回一行，空格分隔**当前已置位的校准项**（位掩码 `cal_status`），共 9 个符号
（`E:1460`）：

```
load  open  short  thru  isoln  Es  Er  Et  cal'ed
```

含义（工程通用定义，非代码搬运）：
- `load/open/short`：三类标准件已采集；`thru`：直通已采集；`isoln`：隔离（反向匹配）已采集；
- `Es/Er/Et`：12 项误差模型中已解出的方向性/反射跟踪/传输跟踪项；
- `cal'ed`：完整校准已 `done` 并**生效**（`cal on` 使能补偿）。

客户端据此判断：只有出现 `cal'ed` 才表示数据是校准后的；否则 `data` 仍是裸测量。
控制子命令（`cal done/on/off/reset` 等）本轮**不做**（桌面仪器 tab 阶段再议），客户端只读状态。

---

## 6. V2 / S-A-A-2 二进制协议（仅落档，不实现）

- USB VID:PID `0x04B4:0x0008`（`Hardware.py:51`）；Windows 下硬件信息串会伪装成
  `PORTS\VID_04B4&PID_0008\DEMO`，需 `_fix_v2_hwinfo` 纠正（`Hardware.py:82-86`）。
- 协议为二进制帧（非 `\r` 文本），客户端类 `NanoVNA_V2.py`。
- **本轮明确不实现**，仅在差异表登记；将来若接 V2 需单开干净室笔记。

---

## 7. 射频公式链（通用工程知识，客户端换算用）

设备上报的 `data 0/1` 是复 S 参数 `S = re + j·im`（无量纲，50 Ω 参考）。

### 7.1 S11 → 反射侧量

```
|Γ|      = |S11| = sqrt(re^2 + im^2)
回波损耗 RL = -20*log10(|Γ|)            [dB]，无反射时 → +∞
VSWR     = (1 + |Γ|) / (1 - |Γ|)        [驻波比]
阻抗 Z   = Z0 * (1 + S11) / (1 - S11)   [Ω，Z0=50]，复数
```

手工可验标准点：
- 理想匹配 Γ=0 → RL=+∞、VSWR=1.0、Z=50 Ω；
- 全反射 Γ=1（开路/短路）→ RL=0 dB、VSWR=∞；
- Γ=0.1∠0 → VSWR=(1.1)/(0.9)=1.222…。

### 7.2 S21 → 传输侧量

```
增益/损耗  = 20*log10(|S21|)            [dB]
相位       = atan2(im, re)               [rad → deg]
群延迟     = -d(phase)/dω                 [s]（相邻频点差分，本轮客户端可留接口不展开）
```

---

## 8. 上游证据索引（file:line 速查）

**固件（命令处理处）**
- 命令表：`NanoVNA-edy555/main.c:2153-2208`；`NanoVNA-H-hugen/main.c:2911-3004`
- 行结束 `\r` / 提示符 / 未知命令：`edy555/main.c:2250`、`:44`、`:2311`
- `version`：`edy555/main.c:2024`；`info` 横幅：`edy555/main.c:92-104,2050`
- `data`：`edy555/main.c:682-701`、`hugen/main.c:738-755`
- `frequencies`：`edy555/main.c:1808-1817`
- `sweep`：`edy555/main.c:1098-1132`（两点）、`hugen/main.c:1603-1641`（三点含点数）
- `bandwidth`：`edy555/main.c:1965-1980`
- `cal`：`edy555/main.c:1458-1513`
- `capture`：`edy555/main.c:727-745`、`hugen/main.c:757-787`
- 点数上限：`edy555/nanovna.h:40`；`hugen/nanovna.h:123,142,163,166,271-286`

**客户端（发送/解析处）**
- drain / 波特率：`nanovna-saver/.../Hardware/Serial.py:28-47`
- exec_command 时序：`VNA.py:102-124`
- 解析 data/freqs/version/cal/sn/info：`VNA.py:205-211,164-165,213-216,184-185,224-225,200-203`
- 特性探测：`VNA.py:126-140`
- USB VID/PID 与枚举：`Hardware/Hardware.py:48-52,103-126`
- 机型识别：`Hardware/Hardware.py:158-176`
- H/H4 差异：`Hardware/NanoVNA_H.py:32-35`、`NanoVNA_H4.py:24-32`、`NanoVNA.py:34-35,60-94`
