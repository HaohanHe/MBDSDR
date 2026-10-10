# NanoVNA 接入 MBDSDR 落地路径（landing-path）

> 本文件是 P5 仪器仪表主线「MBDSDR × NanoVNA」的**分阶段路线图**。
> 本轮（第①轮）只交付：协议深研笔记 + Python 干净室客户端 + pytest。
> 三通道工具与桌面 UI **本轮不落码**，仅在此登记方案。全仓 MIT 口径。

---

## 1. 目标定位

把 NanoVNA（H/H4）作为 MBDSDR 的**外置仪器仪表**接入，与 SDR 接收链联动：
仪器做"阻抗 / 网络参数"的精确测量，SDR 做"频谱 / 信号监听"，两者在同一台主机、
同一套工作流里互补，而不是各自为政。

## 2. 分阶段路线

### 第①轮（本轮已做）——协议 + Python 干净室客户端
- [x] 协议深研：`docs/learn/nanovna/protocol-study.md`（命令集 / 型号差异 / VID-PID / cal 语义 / 公式链，逐条 file:line）
- [x] 干净室客户端：`mbdsdr_ai/nanovna_client.py`（MIT，pyserial 传输，自写不抄上游）
- [x] pytest：`mbdsdr_ai/tests/test_nanovna_client.py`（replay 自造 fixture，确定性）
- [x] 诚实空态：无设备 → 明确报错/空态，不伪造数据；提供 replay 回放模式跑测试

### 第②轮（下一轮）——三通道工具接入
计划在 SDR 工具层暴露两个 MCP/工具入口（签名草案，未落码）：
- `set_vna_sweep(start_hz, stop_hz, points=101, bandwidth=1000)` → 下发 `sweep` / `bandwidth`
- `get_vna_data()` → 读 `frequencies` + `data 0` + `data 1`，返回 S11/S21 复数数组 + cal 状态 + 派生量（VSWR/RL/增益 dB）

### 第③轮——桌面仪器 tab
在桌面端加一个「仪器仪表 / VNA」tab：Smith 圆图 + S11 驻波曲线 + S21 传输曲线 +
cal 状态灯；接入方式与现有 SDR 面板并列，不侵入 `cpp/*` 冻结面。

## 3. 与 SDR 的联动场景（价值锚点）

| 场景 | NanoVNA 测什么 | MBDSDR/SDR 做什么 | 联动收益 |
|---|---|---|---|
| 天线 SWR 指导发射 | S11→VSWR/谐振点 | SDR 定在发射频率监听天线口反射 | 发射前量化天线匹配，避免驻波烧功放 |
| 滤波器扫描 | S21→通带/插损/带外抑制 | SDR 验证实际接收带外杂散 | 把网络分析仪的频选特性与实际接收谱对齐 |
| 频率校准交叉验证 | NanoVNA 频点表作频率参考 | SDR 读同一点对比频偏 | 用仪表级频率源给 SDR 本振做交叉校验 |

## 4. 非目标与红线（本轮）

- ❌ 不做三通道/UI（只落本方案）
- ❌ 不实现 V2 / S-A-A-2 二进制协议（仅落档）
- ❌ 不抄 nanovna-saver 代码或测试数据；fixture 全部自造
- ❌ 不 git add/commit/push；不碰 `cpp/*`、`ui_diag_freeze.cpp`、`cpp/scratch/*`
- ✅ 纯 Python + docs，保持现有双构型基线（D101/R101 140/140）有效

## 5. 真机待办（需用户插设备回传）

本轮无硬件在环，客户端在真机上的首次握手（`help` 能力探测 / `version` / `info` 首行机型串）
尚未实证。待用户插上 NanoVNA-H/H4 回传以下最小会话，再据实修正时序与机型识别：
```
help<CR>        → 期望回 "Commands: ... ch>"
version<CR>     → 期望回版本单行
info<CR>        → 期望首行为板名（"NanoVNA-H" / "NanoVNA-H 4"）
sweep 1000000 30000000 101<CR>
frequencies<CR> → 期望 101 行 Hz
data 0<CR>      → 期望 101 行 "re im"
cal<CR>         → 期望一行空格分隔状态（可空）
```
