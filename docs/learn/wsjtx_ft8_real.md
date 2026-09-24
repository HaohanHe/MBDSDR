# WSJT-X FT8/FST4 真实源码参数校准笔记

> 日期：2026-09-24  作者：BI4MIB
> 目标：把 mbdsdr_ai/ 里"凭印象写的"FT8/FST4 参数换成 WSJT-X 真实源码参数，
> 并做端到端往返验证。本笔记记录从 `.f90`/`.cpp` 源码里抠出来的真实常量。

## 1. 物理层常量（来源: WSJT-X lib/ft8/ft8_params.f90）

| 常量 | 值 | 来源 |
|------|-----|------|
| 信息位 KK | 91 (77 消息 + 14 CRC) | ft8_params.f90:2 |
| 数据符号 ND | 58 | ft8_params.f90:3 |
| 同步符号 NS | 21 (3×Costas7) | ft8_params.f90:4 |
| 总符号 NN | 79 | ft8_params.f90:5 |
| 每符号采样 NSPS | 1920 @12000S/s = 160ms | ft8_params.f90:6 |
| LDPC | (174,91)，码率 0.523 | ft8_params.f90:1 |
| 音调间隔 | 6.25 Hz | FT8 标准 |
| 音调数 | 8 (8FSK) | genft8.f90:15 |

## 2. Costas7 同步序列（来源: lib/ft8/genft8.f90:14）

```fortran
data icos7/3,1,4,0,6,5,2/   ! Costas 7x7 tone pattern
```

帧结构 **S7 D29 S7 D29 S7**（genft8.f90:32）：
- 同步块1：符号 0-6
- 数据块1：符号 7-35（29 个）
- 同步块2：符号 36-42
- 数据块2：符号 43-71（29 个）
- 同步块3：符号 72-78

三个同步块都用同一个 `icos7` 序列。

## 3. Gray 映射（来源: genft8.f90:15）

```fortran
data graymap/0,1,3,2,5,6,4,7/
```
3 bit 索引 → 音调：`[0,1,3,2,5,6,4,7]`。每 3 个码字比特映射成一个 8FSK 音调，
58×3=174 = LDPC 码字长。

## 4. CRC14（来源: lib/ft8/get_crc14.f90:11-12）

```fortran
! polynomial for 14-bit CRC 0x6757
data p/1,1,0,0,1,1,1,0,1,0,1,0,1,1,1/
```
- 完整 15 位多项式 = `0x6757`；截短 14 位 = `0x2757`（对应 lib/crc14.cpp:10 `POLY 0x2757`）。
- 77 bit 消息 + 5 bit 零填充凑 96 bit（12 字节），后接 14 bit CRC 字段，模-2 长除。
- 校验：把收到的 91 位（77 消息+14 CRC）放回 96 bit 块求余，余数为 0 即通过。

## 5. LDPC (174,91)（来源: lib/ft8/bpdecode174_91.f90, ldpc_174_91_c_parity.f90）

- N=174, K=91, M=83 个校验方程。
- 每个变量节点连 3 个校验（Mn(3,N)）；每个校验连 6-7 个变量（Nm(7,M), nrw）。
- 最大迭代 30 次（decode174_91.f90:27 `maxiterations=30`）。
- 生成矩阵 g(83)：每行 23 个 hex 字符 = 91 bit（前 22 个各 4 bit，最后 1 个 3 bit）。
- 编码：`pchecks(i) = Σ_j message(j)·gen(i,j) mod 2`，码字 = message(91) + pchecks(83)。
- 解码：本项目用归一化 min-sum BP（0.75 因子），与 WSJT-X 精确 tanh BP 等价但更鲁棒。

## 6. 77 位消息打包（来源: lib/77bit/packjt77.f90）

Type-1 标准消息位布局（packjt77.f90:1304）：
```
2(b28.28,b1), b1, b15.15, b3.3
= n28a(28) ipa(1) n28b(28) ipb(1) ir(1) igrid4(15) i3(3) = 77 bit
```

### 呼号打包 pack28（packjt77.f90:703-832）
- 特殊 token：DE=0, QRZ=1, CQ=2。
- 标准呼号归一到 6 位：区号数字在第 2 位 → 前补空格；在第 3 位 → 取前 6 位。
- 字符集：a1=` 0-9 A-Z`(37), a2=`0-9 A-Z`(36), a3=`0-9`(10), a4=` A-Z`(27)。
- `n28 = 36·10·27³·i1 + 10·27³·i2 + 27³·i3 + 27²·i4 + 27·i5 + i6 + NTOKENS(2063592) + MAX22(4194304)`。

### 网格/报告编码（packjt77.f90:1290-1297）
- 网格4：`igrid4 = (grid[0]-A)·1800 + (grid[1]-A)·100 + (grid[2]-0)·10 + (grid[3]-0)`，范围 0..32399。
- 报告：`igrid4 = 32400 + irpt`；irpt 经 `(-50..-31)+101` 再 `+35` 偏移。
- 特殊：32401=无报告，32402=RRR，32403=RR73，32404=73。

## 7. FST4（来源: lib/fst4/fst4_params.f90, genfst4.f90）

| 常量 | 值 | 来源 |
|------|-----|------|
| KK | 77 消息位 (+24 CRC = 101) | fst4_params.f90:4 |
| ND | 120 数据符号 | fst4_params.f90:5 |
| NS | 40 同步符号 (5×8) | fst4_params.f90:6 |
| NN | 160 | fst4_params.f90:7 |
| LDPC | (240,101) | fst4_params.f90 |
| 音调数 | 4 (4FSK) | genfst4.f90:92-97 |

帧结构 **s8 d30 s8 d30 s8 d30 s8 d30 s8**（genfst4.f90:11-12）。
同步字（genfst4.f90:27-28）：
- isyncword1 = [0,1,3,2,1,0,2,3]
- isyncword2 = [2,3,1,0,3,2,0,1]

加扰向量 rvec(77)（genfst4.f90:29-31）：编码时 `msgbits ^= rvec`，解码后再 `^= rvec` 还原。
CRC24 多项式 0x100065b（get_crc24.f90:12）。

## 8. 往返验证结果（tests/ft8_real_roundtrip.py）

```
[1] 参考向量 CQ BI4MIB OM74 → 79 音调，3 个 Costas7 块正确，无噪解码文本一致  PASS
[2] 加噪往返（sigma=0.35 高斯噪声）：199/200 = 100% 成功率 > 50%            PASS
[3] 100 个随机合法呼号编码→解码还原：100/100 = 100%                         PASS
[4] 消息格式：CQ/呼号+网格/报告/73/RRR/RR73 全部文本一致                    PASS
[5] FST4 往返：CQ/73/网格 全部解码一致                                      PASS
合计：22 passed, 0 failed
```

## 9. 关键教训

1. **CRC 多项式注释有坑**：get_crc14.f90 注释写 0x6757（完整 15 位），crc14.cpp 写
   0x2757（截短 14 位）——两者是同一个多项式，不是两个。
2. **R+报告的网格误判**：`R-07` 的首字符 R 在 A-R 网格字母范围内，必须用严格的
   "两字母+两数字"格式判断网格，不能只看首字母。
3. **标准呼号长度**：区号在第 2 位的呼号只能是 5 字符（如 K1ABC），6 字符会被
   pack28 截断；6 字符呼号区号必须在第 3 位（如 BI4MIB）。
4. **LDPC 生成矩阵直接从 .f90 hex 串解析**，不需要运行 Fortran；H 矩阵从 parity.f90
   的 Mn/Nm/nrw 解析。
