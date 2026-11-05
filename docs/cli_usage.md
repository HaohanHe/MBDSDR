# MBDSDR CLI 用法

命令行入口：`python -m mbdsdr_ai`（包名 `mbdsdr_ai`；`mbdsdr` 是友好命令名）。

所有命令转发给 `SDRController`，无设备时打印明确错误并以非零退出码结束。

## 常用示例

```bash
# 枚举设备（无硬件时列出 debug 调试源）
python -m mbdsdr_ai --list-devices

# 枚举音频输出
python -m mbdsdr_ai --list-audio

# 用调试信号源连接（无需硬件即可测试整条链路）
python -m mbdsdr_ai --connect debug

# 连接真实设备（device_id 来自 --list-devices 的 driver:serial）
python -m mbdsdr_ai --connect "rtlsdr:00000001"
```

## 接收与解调

```bash
# 调谐到 98.5 MHz，WFM 立体声，增益 20 dB，采样率 2.400 MS/s
python -m mbdsdr_ai --connect debug \
    --freq 98500000 --demod WFM --gain 20 --sample-rate 2400000

# 设置带宽 / 静噪
python -m mbdsdr_ai --bandwidth 200000 --squelch -60
```

## 音频

```bash
# 选择音频输出设备（索引来自 --list-audio），启动播放
python -m mbdsdr_ai --connect debug --audio-out 3 --start-audio
```

## 数据抓取

```bash
# 打印一帧功率谱（256 点）的均值/峰值
python -m mbdsdr_ai --connect debug --freq 98500000 --spectrum 256

# 步进扫频 88–108 MHz，步进 100 kHz，打印活动段
python -m mbdsdr_ai --connect debug --scan 88000000 10800000 100000

# 启动 ADS-B 解码器，监听 5 秒打印报文
python -m mbdsdr_ai --connect debug --freq 109000000 --decode adsb

# 录制 IQ（SigMF）10 秒
python -m mbdsdr_ai --connect debug --freq 98500000 --record /tmp/rec
```

## 服务

```bash
# 启动 GQRX 风格远程控制（rigctld 协议，端口 7356）
python -m mbdsdr_ai --connect debug --remote-port 7356

# 启动 Web 服务器（端口 8000）
python -m mbdsdr_ai --connect debug --web-port 8000
```

## 状态

```bash
# 打印完整系统状态（JSON）
python -m mbdsdr_ai --connect debug --status
```

## 配置文件

```bash
# 指定书签/状态持久化目录
python -m mbdsdr_ai --config /path/to/configdir --list-bookmarks
```

## 发射确认 token（当前操作面缺口）

目前 CLI **没有签发 `confirm_token` 的子命令**：`python -m mbdsdr_ai` 只能执行动作并携带 token，不能生成 token。

- **谁能签发**：只有本机、进程内走“外部确认通道”的调用方——桌面真人确认面（`desktop/main_window.py` 调 `tx_guard.issue_confirmation`）、测试注入、受信任本地脚本。模型 / MCP 客户端 / 远端调用只能持有 opaque token，**不能签发**（否则模型自报即放行）。
- **签发条件**：为**一次具体动作**签发；token 绑定 `tool_name + 参数摘要`（`arm_manual/confirm_token/tx_token/approve_session` 等门参数不计入摘要），换工具/换参数重放即 `gate_action_mismatch`；TTL 必须为正；默认一次性消费，消费/过期/撤销后分别返回 `gate_token_used / gate_expired / gate_revoked`。
- **有效期**：默认 **120 秒**（`tx_guard.DEFAULT_TTL_S`），签发时可显式传 `ttl_s`。
- **记录落在哪**：**落盘审计** `D:\mbdsdr\logs\tx_audit.jsonl`（JSONL，单文件 10 MB × 保留 5 个文件轮转；路径可用 `MBDSDR_TX_AUDIT_PATH` 覆盖）。每次签发/放行/拒绝/过期落一条：本地+UTC 时间戳、工具名、已剔除门参数的参数摘要、token **前 8 位**（不落全串）、TTL、结果（`armed`/`allowed`/`refused`/`expired`）、原因码、来源（`desktop`/`test`/`script`）。审计写失败按 **fail-closed** 处理：不签发、不放行，失败原因见 `tx_guard.tx_audit_status()` 与 `get_tx_gate().snapshot()["audit"]`。
- **缺口的实际影响**：无头/CLI 用户今天无法自己签发 token，发射必须由桌面确认面或受信任脚本代签。补齐形态应是受信任的 CLI 子命令（绑定动作摘要、TTL、写审计日志），且该签发能力不得暴露到模型工具面。

## 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 用户错误（如非法解调模式），stderr 打印 message + 建议 |
| 2 | 未连接设备就执行数据动作（已提示 `--connect`）|
| 3 | 频谱无数据（后端无样本）|
| 4 | 录制打开失败 |

## 典型一行流（无头服务器）

```bash
python -m mbdsdr_ai --connect debug --freq 98500000 --demod WFM \
    --gain 20 --start-audio --web-port 8000 --remote-port 7356
```
