# R2 深度审查：FT8 完整链路

审查范围：`mbdsdr_ai/ft8_decode.py`、`ft8_lite.py`、`ft8_ldpc.py`、`ft8_callsign.py`、`ft8_unpack.py`。
对照基准：`repos/wsjtx/lib/ft8/*.f90`、`repos/wsjtx/lib/77bit/packjt77.f90`。

## 0. 标准事实卡（以 WSJT-X 源码为准）

| 项 | WSJT-X 出处 | 标准值 |
|---|---|---|
| 采样率 | `ft8_params.f90` | 12000 S/s |
| 每符号采样数 | `ft8_params.f90` NSPS=1920 | **1920** |
| 符号时长 | 1920/12000 | **160 ms**（不是 256 ms） |
| 音调间隔 | 1/0.16 | **6.25 Hz** |
| 帧符号数 | NS(21)+ND(58) | **79** |
| 帧时长 | 79×160ms | 12.64 s（周期 15 s） |
| 数据符号 | ND=58 | 58×3 bit = 174 |
| LDPC | `encode174_91.f90` | **(N=174, K=91, M=83)** |
| graymap | `genft8.f90` | `[0,1,3,2,5,6,4,7]` |
| Costas7 | `genft8.f90` icos7 | `[3,1,4,0,6,5,2]` |
| 同步位置(0-based) | genft8 S7 D29 S7 D29 S7 | 0-6, 36-42, 72-78 |
| CRC14 多项式 | `get_crc14.f90` | 0x6757 |
| CRC 移位位数 | `encode174_91.f90`/`chkcrc14a.f90` | **k=19**（77 bit 消息按 12 字节=96 bit 处理，含 5 bit 零填充） |

---

## 1. [真bug] 符号时长 256ms，错误从 digital_modes.py 渗透

**`ft8_lite.py:21`** `SYMBOL_MS = 256`
- 标准为 **160 ms**（NSPS=1920 @ 12000Hz）。
- 后果：`ft8_lite.py:81` 与 `:134` 用 `sps = sample_rate * SYMBOL_MS/1000` 切分 Goertzel 段。@12000Hz 得 sps=3072，而真实符号为 1920。切分窗口比真实符号长 60%，边界逐符号漂移 928 样本，**对真实空中信号完全失步，必然解调失败**。
- 这正是上个 agent 在 `digital_modes.py:27` 发现的 256ms 错误，已渗透进 FT8 链。
- `ft8_lite.py:22` `PERIOD_S=15` 正确，但与 256ms×79=20.2s 自相矛盾。

## 2. [真bug] 对 (174,91) 错误施加了 colorder 置换

**`ft8_decode.py:52-56`** `reorder_to_ldpc()` 调用 `ft8_ldpc.get_colorder()` 对 LLR 做反置换。

- 关键事实：标准 FT8 的 `encode174_91.f90:54-55` 直接
  ```
  codeword(1:K)=message        ! 前 91 bit = 消息+CRC
  codeword(K+1:N)=pchecks      ! 后 83 bit = 校验
  ```
  **没有任何 colorder 置换**。`bpdecode174_91.f90:31` 直接 `llr(Nm(i,j))` 解码，H 矩阵与空中 codeword 同序。
- `colorder` 置换只属于**旧的 (174,87) 码** `encode174.f90:47`，以及实验性 FT8var 模式 `lib/ft8var/osd174var.f90:62`。grep 全仓确认 `ldpc_174_91_c_colorder.f90` 在标准 FT8 编解码路径中**从未被 include**。
- 该 colorder 非恒等（`ldpc_174_91_c_colorder.f90:2-6` 前 80 项被重排），所以这是一次真实的错排，不是 no-op。
- **自洽陷阱**：`ft8_decode.py:122` 自测用"全零符号"，全零向量在任意置换下不变，BP 仍解出全零、CRC 仍通过 → 实验室绿。喂真实信号时 174 个 LLR 与 H 矩阵 `Nm/Mn` 索引错位 → 解码乱码。这是典型的"往返同代码、真机红"。
- 修复方向：删除 `reorder_to_ldpc`，`llr_ldpc` 直接等于 `llr_cw`。

## 3. [真bug] CRC14 校验移位位数 k=14，与 WSJT-X 的 k=19 不兼容

**`ft8_decode.py:94-118`** `crc14()` / `check_crc14()`。

- 多项式 `_CRC_P=[1,1,0,0,1,1,1,0,1,0,1,0,1,1,1]` = 0x6757，**正确**（对照 `get_crc14.f90:12`）。逐位移位算法本身也对。
- 但 ft8 把 CRC 当作"77 bit 消息后接 14 bit"的标准 CRC（k=14）：`crc14` 构造 `mc = msg(77)+[0]*14`，循环 `range(0, len-14)`=77 次。
- WSJT-X 实际用 **k=19**：`encode174_91.f90:38-43` 把 77 bit 消息写入 12 字节（96 bit，尾部 19 bit 补零）后算 CRC；`chkcrc14a.f90:16-18` 校验时把 CRC 字段与尾字节清零后重算；`decode174_91.f90:76-78` 的 m96 布局为 `msg(1:77) + zero(78:82) + CRC(83:96)`。即 CRC 多项式除法在消息后多滑了 **5 个零 bit**。
- 后果：ft8 自测 `ft8_decode.py:134-147` 用 k=14 自算自校，必然通过；但真实 WSJT-X 信号的 CRC 是按 k=19 生成的，`check_crc14` 余数不为零 → **真机全部 CRC 失败**，`ft8_lite.py:163` 的 `decoded` 永远 False。
- 修复方向：按 96 bit 块处理（消息 77 bit + 5 个零 bit + 接收 CRC 14 bit）做余数判零，或按 `chkcrc14a` 重算比较。

## 4. [真bug] Type 0.0 自由文本：字符集与编码方式都错

**`ft8_unpack.py:15`** `_TEXT40=" 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ/"`（40 字符）+ `:38-44` 定长 6bit×11。

- WSJT-X `unpacktext77`（`packjt77.f90:1552-1573`）：
  - 字符集为 **42**：`' 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ+-./?'`（多出 `+ - . , ?`，不只是 `/`）。
  - 编码是把 71 bit 当**大整数反复除 42** 解出 **13 个字符**，不是 6bit 定长切 11 个。
- 后果：真实 Type 0.0 自由文本解出的字符序列与 WSJT-X 不一致。（Type 0.0 占比不高，但属标准偏差。）

## 5. [空壳] 没有 Costas 时间/频率同步，仅靠 8 相位暴力猜

**`ft8_decode.py:15`** 只用了同步位置 `SYNC_POS`，**从未校验 Costas 序列 `[3,1,4,0,6,5,2]`**。
**`ft8_lite.py:86-100`、`:139-155`** 解调：从采样 0 开始切 79 段（假设音频起点正好对齐 FT8 周期边界），再枚举 peak 对应第 0..7 音调，选判决裕度最大者。

- WSJT-X 靠 `sync8.f90`/`sync8d.f90` 做 Costas 相关来同时估计时间偏移（≤160ms 内粗捕）和频偏。ft8 链：
  - 无 Costas 相关 → 时间不对齐时（真机最常见）直接错位；
  - 频率搜索仅覆盖相对中心 ±3.5×6.25=±21.9Hz，且依赖"第一段就是符号 0"；
  - 与第 1 条（256ms 切分）叠加，真机基本不可能解出。
- Costas 序列常量 `icos7` 在整个 ft8_*.py 中缺失，应补相关检测。

## 6. [建议] LDPC 用 min-sum(0.75) 而非 WSJT-X 的精确 tanh BP

**`ft8_ldpc.py:93`** `r[(c,v)] = prod_sign*min_abs*0.75`。
- WSJT-X `bpdecode174_91.f90:100-111` 用精确 `tanh(-toc/2)` 乘积 + `platanh`（和积 BP）。
- min-sum×0.75 是合理近似，能纠正自测中的 6 bit 翻转（`ft8_ldpc.py:113-121`），但纠错门限略松于和积。非阻断，列为建议。
- H 矩阵解析正确：Mn(3,N) 按列每 bit 3 个校验、Nm(7,M)+nrw 按列每校验实际变量数，`ft8_ldpc.py:56-63` 的 0-based 换算与 `ldpc_174_91_c_parity.f90` 列主序一致；M=83/N=174/K=91 与标准一致。

## 7. 已核对正确的项

- **帧结构/位置**：`ft8_decode.py:15` SYNC_POS={0-6,36-42,72-78}=21 个同步，DATA_POS=58，与 `genft8.f90` S7 D29 S7 D29 S7 一致。
- **graymap**：`ft8_decode.py:20` `[0,1,3,2,5,6,4,7]` 与 `genft8.f90:15` 一致；反 gray 后按 4/2/1 (MSB→LSB) 算 max-log LLR，与 `genft8.f90:41` `indx=cw(i)*4+cw(i+1)*2+cw(i+2)` 的位序一致。
- **28 位呼号解压** `ft8_callsign.py`：NTOKENS=2063592、MAX22=4194304、DE/QRZ/CQ/CQ_nnn/CQ_aaaa 边界(≤1002,≤532443)、标准呼号 36·10·27³ 权重，全部与 `packjt77.f90:836-917` unpack28 逐项一致；字符表 _C1/_C2/_C3/_C4 一致。hash 段返回 `<hash:n>` 是已知占位（需 WSJT-X 呼号表）。
- **网格编码** `ft8_unpack.py:25-35` `_to_grid4`：18×18×10×10 权重与 `packjt77.f90:1644` to_grid4 一致；MAXGRID4=32400 正确。
- **77 位位布局** `ft8_unpack.py:53-66`：n3=bit71:74、i3=bit74:77、n28a=0:28、ipa=28、n28b=29:57、ipb=57、ir=58、igrid4=59:73，与 `packjt77.f90:365` `read(c77(72:77),'(2b3)')` 和 `:536` format `2(b28,b1),b1,b15,b3` 一致。
- **报告/ SNR 分支** `ft8_unpack.py:78-93`：irpt 1=纯呼叫、2=RRR、3=RR73、4=73、≥5 时 `isnr=irpt-35; >50 再 -101`，与 `packjt77.f90:569-580` 一致（仅 "R" 空格排版略不同，内容等价）。

## 8. 小问题

- **`ft8_lite.py:10`** docstring 写 "LDPC(K=79,N=174)"，K 应为 **91**（79 是符号数）。
- **`ft8_lite.py:23`** BANDWIDTH_HZ=79：8FSK 实际跨度 7×6.25=43.75Hz，79Hz 偏大，仅影响扫峰窗口，非阻断。
- **`ft8_callsign.py:51`** 标准呼号用 `.strip()`（去首尾空格），WSJT-X 用 `adjustl`（仅左对齐）；对 6 位补空格呼号结果等价，无实际偏差。
- **`ft8_decode.py:56`** 访问私有 `ft8_ldpc._N`，风格问题。

---

## 结论（真机红根因排序）

1. **#1 符号时长 256ms→160ms**：切分窗口错，物理上无法对齐真实符号。
2. **#2 多余 colorder 置换**：LLR 与 H 矩阵索引错位（全零自测掩盖）。
3. **#3 CRC k=14→k=19**：即使解出 bit，CRC 永不通过，`decoded` 恒 False。
4. **#5 无 Costas 同步**：时间/频偏未估计，真机失步。
5. **#4 自由文本 base-42/40**：仅影响 Type 0.0。

#1/#2/#3 都是"本项目自编码/自测用同一份错误约定，闭环通过，但与 WSJT-X 空中标准不一致"的典型实验室绿、真机红。呼号解压与网格编码数学本身正确，问题集中在物理层时序、LDPC 位序与 CRC 移位宽度。
