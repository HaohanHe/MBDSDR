# Phase53：二阶 PLL 精载波恢复

> 攻 Phase52 诚实遗留的陡扫频缺口：分段 AFC 粗校正 + 二阶 decision-directed PLL 精跟踪。

## PLL 机制（`ssdv_phy.py pll_bpsk`）
- NCO 相位累加器 θ → 旋转输入 → BPSK 硬判决 d=sign(Re) → 鉴相误差 e=Im(rot)·d；
- 二阶环路滤波器：积分器 integ += ki·e；NCO 频率增量 = kp·e + integ；
- **type-2 环路对频率斜升（多普勒斜率）稳态误差为零**——这正是 AFC（type-1，
  窗量化残 ~7Hz）攻不动陡扫频的根因。
- 具名门限：`PLL_KP=0.05`、`PLL_KI=0.002`、`PLL_WARMUP=200`。

## 实测对比（seed 53，AFC 粗校基线）

| 扫频 | AFC-only | +PLL |
|---|---|---|
| 0→200Hz sd=0.5 | asm=1 rs=[[3,12]] mcu=0/0 | asm=1 rs=[[0,0]] **36/36** |
| 0→500Hz sd=0.5 | **asm=0** mcu=0/0 | asm=1 rs=[[0,0]] **36/36** |
| 0→1000Hz sd=0.5 | — | asm=1 **36/36** |
| const 100Hz sd=0.5（该种子 AFC-only 0/0） | 0/0 | **36/36** |

## 诚实 FAIL 区间
- **sd=1.0 × 0→1000Hz**：PLL 失锁，ASM=0。理论：PLL 带宽为抑噪必须做窄；
  带宽窄→跟踪频斜的速度/范围受限；低 SNR 下鉴相噪声压过信号→失锁。
  这是**带宽-噪声-跟踪范围三角权衡**，不是 bug。

## 红线
干净室 MIT；活动参数零硬编码；失效 0 帧不伪造；pytest 不回归。
