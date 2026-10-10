# 仪器普及化·实证叙事素材（用户原话 → 真实工具链 → 真实输出）

双实证：SDR 模式接收（FT8/CTCSS/LRPT）与 VNA 仪器测量（驻波/谐振/TDR/L·C/滤波器）。
下表为「对话可达路径」骨架；具体数值以真机/真实回放为准，占位处待真机回传后补，不伪造。

## 对照表

| 用户原话任务 | 真实工具链 | 真实输出（待真机/实测填） |
|---|---|---|
| 438.5MHz 驻波多少？ | get_vna_status → set_vna_sweep → get_vna_data → VSWR/RL 纯函数 | VSWR=… / RL=… dB |
| 这个晶体谐振频率/Q/ESR？ | status → 窄扫 → analyze_vna_resonance | fr=… / Q=… / ESR=… Ω |
| 这段电缆多长/哪里断了？ | status → vna_tdr_cable(vf) | 长度…m / 故障…m |
| 这个电感多大？ | get_vna_data → reactanceToHenries | … nH @ … MHz |
| 这个滤波器什么特性？ | status → analyze_vna_filter | 形态… / -3dB… Hz / 插损… dB |
| 调到 2 米波段 | band_preset 查表 → onSetCenterFreq+NFM+带宽 | 中心 146MHz / NFM / 12.5k |
| 我想听 409.75MHz 手台 | onSetCenterFreq + NFM + 源/CTCSS 提示 | 已调谐 + 启动源引导 |
| FT8 解码 / CTCSS 亚音 / LRPT 云图 | SDR 解码链（既有） | 既有输出 |

## 设计要点（论文骨架）
- **一句话意图 → 真实工具编排**：分类纯函数 `classifyVnaIntent` / localCommand 分支，
  全部走已落地三通道工具，未连接/未校准/点数不足各自给真实引导，不内置假数据。
- **诚实空态即卖点**：未插 PORT1、USB 未枚举、cal 未置位时，回复是可执行排查步骤而非假曲线。
- **replay 仅测试夹具**：CI 用自造协议文本钉扎数值（RLC 理论 fr±0.5MHz），真机路径不内置假值。

## 待真机补
- 上表数值列待 NanoVNA-H/H4 回传 sweep.json 后填入（对照合成/真机差异）。
