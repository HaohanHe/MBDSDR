# MBDSDR MCP 工具列表

`mbdsdr_ai/core/mcp_server.py` 把 `SDRController` 的能力暴露成标准 MCP 工具
（JSON-RPC 2.0 over stdio）。共 **53** 个工具，与 controller 方法一一对应。

## 协议

```
→ {"jsonrpc":"2.0","id":1,"method":"initialize"}
← {"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"mbdsdr-core","version":"0.1.0"},...}}

→ {"jsonrpc":"2.0","id":2,"method":"tools/list"}
← {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":...,"description":...,"inputSchema":...},...]}}

→ {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"set_frequency","arguments":{"hz":98500000}}}
← {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"ok\": true}"}]}}
```

无后端 / 未连接时，数据工具返回：
```json
{"data": null, "error": "not_connected", "suggestion": "请先 connect() 一个设备或调试信号源。"}
```

## 运行

```bash
python -m mbdsdr_ai.core.mcp_server        # stdio MCP 服务器
```

作为库：
```python
from mbdsdr_ai.core.mcp_server import MCPToolSet
ts = MCPToolSet()                 # 可传入共享 SDRController
ts.call_tool("connect", {"device_id": "debug"})
ts.call_tool("read_spectrum", {"nfft": 256})
```

## 工具清单

### 设备
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `list_devices` | — | 枚举 SDR 设备（含 debug 调试源）|
| `connect` | `device_id` | 连接设备；`"debug"` 用调试信号源 |
| `disconnect` | — | 断开设备 |
| `is_connected` | — | 是否已连接 |

### 接收参数
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `set_frequency` | `hz` | 调谐中心频率 |
| `get_frequency` | — | 读频率 |
| `set_demod` | `mode` | AM/FM/WFM/NFM/USB/LSB/CW |
| `get_demod` | — | 读模式 |
| `set_gain` | `db` | 总增益 |
| `set_sample_rate` | `sr` | 采样率 |
| `set_bandwidth` | `hz` | 信道带宽 |
| `set_squelch` | `db` | 静噪门限 |

### 音频
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `list_audio_outputs` | — | 枚举音频输出 |
| `set_audio_output` | `index` | 选输出设备 |
| `start_audio` | — | 启动播放 |
| `stop_audio` | — | 停止播放 |
| `set_volume` | `db` | 音量 |
| `set_mute` | `muted` | 静音 |

### DSP
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `read_spectrum` | `nfft` | 一帧功率谱（dB 数组）|
| `read_iq` | `n` | n 个复样本 |
| `read_audio` | `n` | n 个解调音频样本 |
| `start_analyze` | — | 自动调制识别 |

### 扫描
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `start_scan` | `start_hz, stop_hz, step_hz` | 启动扫频，返回 handle |
| `stop_scan` | `handle` | 停止扫频 |
| `get_scan_results` | `handle` | 活动段列表 |

### 解码
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `start_decoder` | `decoder_type` (adsb/aprs/noaa_apt/meteor) | 启动解码器 |
| `stop_decoder` | `handle` | 停止解码器 |
| `get_decoder_messages` | `handle` | 取走累积报文 |

### 卫星
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `list_satellites` | — | 内置卫星列表 |
| `get_satellite_pass` | `norad` | 最近一次过境预报 |
| `tune_satellite` | `norad` | 按多普勒调谐 |
| `set_ground_station` | `lat, lon` | 设置地面站（alt_m 可选）|

### 书签
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `add_bookmark` | `freq_hz, name` (mode/group 可选) | 添加书签 |
| `list_bookmarks` | — (group 可选) | 列书签 |
| `delete_bookmark` | `id` | 按频率 id 删除 |
| `find_nearest_bookmark` | `freq_hz` | 找最近书签 |

### 录制
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `start_recording` | `path` | 开始 IQ 录制（SigMF）|
| `stop_recording` | — | 停止录制 |
| `get_recording_status` | — | 录制状态 |

### 服务
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `start_remote_control` | `port` | GQRX rigctl 远程控制 |
| `stop_remote_control` | — | 停止远程控制 |
| `start_web_server` | `port` | Web 服务器 |
| `stop_web_server` | — | 停止 Web |
| `get_service_status` | — | 服务状态 |

### ANR / 降噪
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `set_anr_enabled` | `enabled` | 开关降噪 |
| `set_anr_strength` | `level` (0–10) | 降噪强度 |
| `learn_noise_floor` | — | 学习噪声底 |

### 多 VFO
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `add_vfo` | `freq_hz` (mode/bw_hz 可选) | 新建 VFO |
| `remove_vfo` | `vfo_id` | 删除 VFO |
| `set_primary_vfo` | `vfo_id` | 设为主听 |
| `list_vfos` | — | 列 VFO |

### 系统
| 工具 | 必填参数 | 说明 |
|---|---|---|
| `get_status` | — | 完整系统状态 |
| `shutdown` | — | 优雅关闭 |
