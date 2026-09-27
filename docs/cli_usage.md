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
# 调谐到 98.5 MHz，WFM 立体声，增益 20 dB，采样率 2.4 MSps
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
