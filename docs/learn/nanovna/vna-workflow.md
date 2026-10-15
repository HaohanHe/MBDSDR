# VNA 分析工作流（Python 面全链路）

> 本文是**可运行文档**：所有代码段用合成数据演示完整链路，无需硬件。
> 有真机时把"合成"段换成 `nanovna_client` 采标即可，其余环节不变。
> 各环节的单测在 `mbdsdr_ai/tests/`，端到端冒烟在 `test_vna_e2e.py`。

## 链路总览

```
采标（三件标准 + DUT 过同一误差盒）
  -> vna_solve_error_box   求误差盒（线性闭式解，1e-16 精确）
  -> vna_apply_calibration 校正被测件 Γ
  -> vna_analyze_sweep     一键分析报告
  -> export_vna_report     log-mag + Smith 双面板 PNG
  -> vna_calio 落盘        误差盒/定义件跨会话复用
```

## 1. 合成演示（无硬件可跑）

```python
import numpy as np
from mbdsdr_ai.vna_cal import ErrorBox

F = np.linspace(1e6, 1001e6, 401)
box = ErrorBox(e00=np.full(len(F), 0.06+0.02j),   # 一台"有误差项的 VNA"
               t=np.full(len(F), 0.97-0.02j),
               e11=np.full(len(F), 0.12-0.04j))

def simulate(g):            # 任意真实 Γ 过误差盒 -> 实测 Γm
    return box.e00 + box.t*g/(1 - box.e11*g)

# DUT：2m 之后阻抗阶跃到 75Ω 的电缆（RG-58, vf=0.66）
c = 299792458.0; vf = 0.66; rho = (75-50)/(75+50)
dut = rho * np.exp(-2j * (2*np.pi*F/(vf*c)) * 2.0)

meas = {"open": simulate(np.ones(len(F))),
        "short": simulate(-np.ones(len(F))),
        "load": simulate(np.zeros(len(F))),
        "dut": simulate(dut)}
```

## 2. 工具面（MCP）或纯 Python 任选

MCP 工具（Kilo / Claude 等客户端里直接调）：

```python
# solve -> apply -> analyze 三个只读工具；报告/落盘是写工具（arm_manual 门）
solved = call("vna_solve_error_box", freqs_hz=F.tolist(),
              meas_open=[[v.real, v.imag] for v in meas["open"]],
              meas_short=[[v.real, v.imag] for v in meas["short"]],
              meas_load=[[v.real, v.imag] for v in meas["load"]])
corrected = call("vna_apply_calibration", s11=[[v.real, v.imag] for v in meas["dut"]],
                 e00=solved["e00"], t=solved["t"], e11=solved["e11"])
rep = call("vna_analyze_sweep", freqs_hz=F.tolist(), s11=corrected, cable="RG-58")
# rep["tdr"]["length_m"] ≈ 2.0
```

纯 Python 等价物：`mbdsdr_ai.vna_cal.solve_error_box / apply_calibration`、
`mbdsdr_ai.vna_pipeline.analyze_one_port`（参数同构）。

## 3. 报告与复用

```python
# PNG 报告（写工具，需 arm_manual）：左 log-mag，右 Smith（r/x 网格）
call("export_vna_report", freqs_hz=F.tolist(), s11=corrected,
     path="report.png", arm_manual=True)

# 误差盒落盘 -> 下次会话直接加载复用
from mbdsdr_ai.vna_calio import save_error_box, load_error_box
from mbdsdr_ai.vna_cal import ErrorBox
import numpy as np
box2 = ErrorBox(e00=np.array([complex(*p) for p in solved["e00"]]),
                t=np.array([complex(*p) for p in solved["t"]]),
                e11=np.array([complex(*p) for p in solved["e11"]]))
save_error_box("cal_box.json", F, box2)
f2, box3 = load_error_box("cal_box.json")     # 坏文件显式拒绝
```

## 4. 各环节口径速查

| 环节 | 模块/工具 | 口径要点 |
|---|---|---|
| 误差盒求解 | `vna_cal.solve_error_box` | Γm=e00+TΓ/(1−e11Γ) 对三未知**线性**，3×3 闭式；非理想 OSL 多项式（f 代 GHz）+ 偏移双程相移 |
| 校正 | `vna_cal.apply_calibration` | Γ=(Γm−e00)/(T+e11(Γm−e00)) |
| 谐振 | `vna_pipeline` 内 | 阻抗域：Z=Z0(1+Γ)/(1−Γ)，esr·√2 交点定 −3dB 带宽，Q=fr/BW |
| TDR | `tdr_length_py` + `tdr_modes` | bandpass 双曲插值精修；LP-Step 默认矩形窗（Kaiser 杀 DC 是踩过的坑） |
| LC 匹配 | `vna_match.lc_match` | Pozar 闭式解，端到端接回负载验证 |
| 电缆损耗 | `vna_dsp.coax_loss_s11` | 去趋势 + FFT 定位振铃窄谱线 + 陷波零相位；检不出 None 不硬滤 |
| 扫间平均 | `vna_avg.SweepAverager` | 256 环形缓冲，|Z|²+相位 / IQ 双模式，2 的幂长度 |
| 协议 | `nanovna_v2_proto` / `janvna2_proto` | V2 命令帧 + seed 0x46 校验和；JanVNA2 magic 0x5A + zlib CRC32 |

## 5. 诚实边界（如实登记，不藏）

- **校准不内嵌**：分析管道不做"是否已校准"假设，喂原始数据就按原始分析。
- 偏移段只建模相位、**不建模偏移段损耗**。
- TDR 端点复制处群延迟精度低一档；矩形窗 LP-Step 有 ~1/span 上升时间。
- 谐振判定要求 −3dB 交点不碰频率轴端（碰端 = 无可判谐振，返回 None）。
- S21 增强响应采用上游同款近似式（严格式分子含 (1−e11²)，注释标明）。
- 所有"检不出/无定义"路径返回 None 或显式报错，**绝不静默补零**。
