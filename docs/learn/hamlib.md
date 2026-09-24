# Hamlib 源码学习笔记

> 本地参考克隆：`repos/hamlib/`（`git clone --depth 1 https://github.com/Hamlib/Hamlib.git`）
> 本文件用于沉淀阅读 Hamlib 关键源码后的协议要点，作为 `mbdsdr_ai/radio_control.py` 重写的依据。

## 1. 整体架构

Hamlib 是 C 写的电台控制抽象库。对外 API（`rig_set_freq` 等）在 `src/rig.c`，
真正和电台对话的后端按厂商分目录放在 `rigs/`：

| 厂商 | 目录 | 主文件 |
|---|---|---|
| ICOM | `rigs/icom/` | `icom.c`、`frame.c`、`icom_defs.h` |
| Yaesu（二进制老协议） | `rigs/yaesu/` | `yaesu.c`、`frg100.c` |
| Yaesu（NewCAT ASCII） | `rigs/yaesu/` | `newcat.c`（FT-818/891/991A/FTX-1…） |
| Kenwood | `rigs/kenwood/` | `kenwood.c` |
| 通用工具 | `src/misc.c` | `to_bcd` / `from_bcd` |

调用链：`rig_set_freq(rig, vfo, freq)`（src/rig.c:2153）→ 各后端 `*.c` 里的 `*_set_freq()` →
`*_transaction()` → `write_block()` 写串口。

## 2. ICOM CI-V 协议

### 2.1 帧格式
来源：`rigs/icom/frame.c:52 make_cmd_frame()`

```
FE FE  <rig_addr>  <ctrl_addr>  <cmd>  [subcmd]  [data...]  FD
```

- `FE FE` 前导（PR，icom_defs.h:28）
- `rig_addr` 电台 CI-V 地址（如 IC-7300 = 0x94，见 `rigs/icom/ic7300.c:450`）
- `ctrl_addr` 控制器地址，默认 `0xE0`（icom_defs.h:29 CTRLID）
- `cmd` 命令字（见下表）
- `subcmd` 可选，没有就传 -1（frame.c:68）
- `data` 负载
- `FD` 帧结束（FI，icom_defs.h:31）

应答：成功 `ACK=0xFB`（icom_defs.h:32），失败 `NAK=0xFA`（:33）。

### 2.2 常用命令字
来源：`rigs/icom/icom_defs.h:66-105`

| 命令 | 值 | 用途 |
|---|---|---|
| C_SND_FREQ | 0x00 | 收发模式下发频率（无 ACK） |
| C_SND_MODE | 0x01 | 收发模式下发模式 |
| C_RD_FREQ | 0x03 | **读显示频率** |
| C_RD_MODE | 0x04 | 读显示模式 |
| C_SET_FREQ | 0x05 | **写频率**（icom.c:1535） |
| C_SET_MODE | 0x06 | 写模式（icom.c:2227） |
| C_CTL_PTT | 0x1C | PTT 控制（icom.c:5369） |
| C_RD_TRXID | 0x19 | 读电台 ID |

模式子命令（icom_defs.h:115-122）：LSB=0x00, USB=0x01, AM=0x02, CW=0x03, RTTY=0x04, FM=0x05, CWR=0x07。
PTT 子命令：S_PTT=0x00（icom_defs.h:369），data=[1=ON, 0=OFF]（icom.c:5367）。

### 2.3 频率 BCD 编码
来源：`src/misc.c:146 to_bcd()`，`src/misc.c:193 from_bcd()`

**小端 BCD**，低位字节在前。misc.c:182 注释举例：
`1234567890 Hz → 字节序 90 78 56 34 12`。

icom.c:1515：默认 5 字节（=10 位 BCD 数字），`civ_731_mode` 老电台才用 4 字节。

举例：`14074000 Hz` → BCD 字节 `00 40 07 14 00`。
完整写频率帧（IC-7300，rig_addr=0x94）：
```
FE FE 94 E0 05 00 40 07 14 00 FD
```

### 2.4 典型 CI-V 地址
来源：各 `rigs/icom/ic*.c` 中的 `.civ_addr` 字段

| 型号 | CI-V 地址 |
|---|---|
| IC-705 | 0xA4 |
| IC-7300 / IC-7300MK2 | 0x94 |
| IC-7610 | 0x76 |
| IC-7600 | 0x7A |
| IC-9700 | 0x88 |

注意：用户可在电台菜单改 CI-V 地址，代码里要允许 `civ_addr` 覆盖。

## 3. Yaesu NewCAT 协议（FT-818/891/991A/FTDX 系列）

来源：`rigs/yaesu/newcat.c`

ASCII 命令，每条以 `;` 结尾。

- **写频率**：`FA%09.0f;` — newcat.c:1647
  例：`14074000 Hz` → `FAA014074000;`（9 位零填充）
- **读频率**：`FA;` → 电台回 `FA014074000;`
- **写模式**：`MD<n>;`（0=LSB,1=USB,2=CW,3=FM,4=AM,5=RTTY-L,6=RTTY-U,7=CWR,8=DIG-U,9=DIG-L）
- **PTT**：`TX1;` / `TX0;`

老 Yaesu 二进制协议（FT-1000D 等）在 `rigs/yaesu/yaesu.c:143`，用 5 字节二进制帧
`{0x00,0x00,0x00,0x00,0xFA}` 查询 ID，本工程暂不实现。

## 4. Kenwood ASCII 协议（TS-590/TS-480/TM-D710）

来源：`rigs/kenwood/kenwood.c:2042 kenwood_set_freq()`

```c
SNPRINTF(freqbuf, sizeof(freqbuf), "F%c%011"PRIll, vfo_letter, (int64_t)freq);
```

- **写频率**：`F<vfo><11位零填充Hz>;`
  例：`14074000 Hz` → `FA00014074000;`
- **读频率**：`FA;`
- **模式**：`MD<n>;`（0=LSB,1=USB,2=CWL,3=CWR,4=FM,5=AM,6=RTTY,7=PSK）
- **PTT**：也可走 RTS 线（KENWOOD 经典手咪口）

## 5. 与本工程 `radio_control.py` 的对应

`mbdsdr_ai/radio_control.py` 中：

| 函数 | 对应 Hamlib 位置 |
|---|---|
| `_to_bcd_le()` | src/misc.c:146 `to_bcd` |
| `_from_bcd_le()` | src/misc.c:193 `from_bcd` |
| `_civ_frame()` | rigs/icom/frame.c:52 `make_cmd_frame` |
| `build_icom_set_freq_frame()` | rigs/icom/icom.c:1467 `icom_set_freq` + :1523 `to_bcd` + :1535 `cmd=C_SET_FREQ` |
| `build_yaesu_set_freq_frame()` | rigs/yaesu/newcat.c:1647 `"FA%09.0f;"` |
| `build_kenwood_set_freq_frame()` | rigs/kenwood/kenwood.c:2042 `"F%c%011d"` |
| `set_ptt()` ICOM 分支 | rigs/icom/icom.c:5367-5369 `C_CTL_PTT, S_PTT, [1/0]` |

## 6. 红线

- 串口打不开 → `connect()` 必须返回 `False`，**绝不**因为"有 pySerial"或"模拟一下"就返回 True。
- 未连接时 `set_frequency / set_mode / set_ptt` 一律返回 `False`，不得更新内部缓存假装成功。
- 所有协议常量在代码注释里标 `来源: <file>:<line>`。

## 7. 待扩展

- ICOM 模式滤波器带宽（`C_SET_MODE` 第二字节）：当前固定 0（普通）。
- `set_gain` / `set_att`：ICOM `C_CTL_LVL (0x14)` + 子命令 AF/RF/SQL，需要按电台型号表补。
- Yaesu/Kenwood 真正的 `get_mode`（当前只回缓存值）。
- Split 模式、VFO A/B 切换、记忆信道读写。
