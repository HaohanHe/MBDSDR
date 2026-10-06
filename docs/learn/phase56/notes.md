# Phase56 弱信号 ASM 容忍 + 软路径联合精恢复

HEAD=64f7e7a 接手。把 Phase55 定位的 ASM 严格匹配瓶颈往下推。

## 块1：ASM hamming 容忍
- `ccsds_ssdv._scan_asm_offsets(..., asm_tol)` 改 popcount(XOR) 容忍；硬 framer `AsmFramer(max_hamming=asm_tol)`（原本已支持）。`ccsds_iq_to_result(..., asm_tol=0)`。
- 弱信号 sd 梯度（10 reps 累计 MCU）：

| sd | 硬 tol0 | 硬 tol2 | 软 tol2 |
|---|---|---|---|
| 0.8 | 36 | 36 | **288** |
| 1.0 | 0 | 0 | 97 |
| 1.1 | 0 | 72 | 97 |
| 1.3 | 0 | 0 | 72 |

新失效阈值：**软+tol2 到 sd=1.3 仍恢复 MCU**（硬在 sd≥1.0 全废）。
- **假同步实测**（纯噪声 200 trial）：tol=0/1/2/3 全部 **0 假帧**。
  理论单窗假同步 t=2≈1.2e-7、t=3≈1.3e-6；实测 0（窗口数有限，统计内一致）。
  → 选 tol=2：弱信号大收益、假同步实测 0，安全。

## 块2：软路径联合精恢复（afc+pll 串在 demod 前）
- CFO 150Hz 叠加 sd=0.8：硬=0 软=0 **软+afc+pll=108**（联合才解 CFO+弱信号）。
- sd=1.0+CFO150Hz / 扫频0→300Hz+sd1.0：联合也 0（诚实 FAIL，PLL 带宽-噪声权衡）。

## 块3：onboard/工具
- onboard CLI：`--ssdv-asm-tolerance N`（0严格/2弱信号）；联合=组合已有 `--ssdv-soft --ssdv-afc --ssdv-pll`。
- 工具 `ssdv_ccsds_decode` 新增参数 `soft` / `asm_tol`（注册计数不变=1，参数扩展）。

## 未解决
- sd≥1.0+CFO/陡扫频联合仍失效（PLL 带宽-噪声固有）；真机过境 IQ 未验证。
