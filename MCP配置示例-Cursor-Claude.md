# MBDSDR ai-sdr Mini MCP 配置示例

将 ai-sdr Mini 的 14 个硬件工具接入任意 MCP 客户端（Cursor / Claude Desktop / VS Code Copilot / 通用 agent）。

**原理**：`mbdsdr_mcp_client.py --mcp` 作为 stdio MCP server 运行，通过 WebSocket 桥接 ai-sdr Mini 硬件。MCP 客户端通过 stdin/stdout 与桥接器通信，桥接器转发到硬件。

---

## 前置条件

1. ai-sdr Mini 已上电，WiFi AP 模式启动（SSID: `MBDSDR-Mini`，密码: `mbdsdr123`）
2. 电脑已连接 WiFi `MBDSDR-Mini`
3. 电脑已安装 Python 3 和 `websocket-client`：
   ```bash
   pip install websocket-client
   ```
4. 确认能 ping 通 `192.168.4.1`

---

## 1. Cursor IDE 配置

Cursor 支持项目级 MCP 配置。在项目根目录创建 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "mbdsdr-mini": {
      "command": "python3",
      "args": [
        "/path/to/mbdsdr_mcp_client.py",
        "--mcp",
        "--host",
        "192.168.4.1"
      ],
      "env": {}
    }
  }
}
```

**使用方法**：
1. 在 Cursor 中打开项目，Cursor 会自动加载 MCP 配置
2. 在 AI 对话中输入："列出可用的 SDR 工具" → Cursor 会调用 `tools/list`
3. 输入："调谐到 FM 98.5 MHz" → Cursor 会调用 `tune_fm` 工具
4. 输入："获取当前 SDR 状态" → 调用 `get_status`
5. 输入："在 87.5-108 MHz 扫频找信号最强的电台" → Cursor 会规划多次 `tune_fm` + `get_status` 调用

**注意**：将 `/path/to/` 替换为 `mbdsdr_mcp_client.py` 的实际绝对路径。Windows 用户将 `python3` 改为 `python`。

---

## 2. Claude Desktop 配置

Claude Desktop 的 MCP 配置文件位置：
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

添加以下配置：

```json
{
  "mcpServers": {
    "mbdsdr-mini": {
      "command": "python3",
      "args": [
        "/path/to/mbdsdr_mcp_client.py",
        "--mcp",
        "--host",
        "192.168.4.1",
        "--verbose"
      ]
    }
  }
}
```

**使用方法**：
1. 重启 Claude Desktop
2. 在对话中输入："你能调用哪些 SDR 工具？"
3. Claude 会自动发现并调用 ai-sdr Mini 的 14 个工具
4. 可以用自然语言驱动："帮我接收 14.070 MHz 的短波信号"、"录 30 秒当前电台"、"获取 GPS 定位"

---

## 3. 命令行直接测试（无需 MCP 客户端）

在配置 MCP 之前，先用命令行验证硬件连接：

```bash
# 列出全部工具
python3 mbdsdr_mcp_client.py --host 192.168.4.1 list_tools

# 调谐 FM 98.5 MHz
python3 mbdsdr_mcp_client.py --host 192.168.4.1 tune_fm --freq 98.5

# 获取状态
python3 mbdsdr_mcp_client.py --host 192.168.4.1 get_status

# 获取 GPS
python3 mbdsdr_mcp_client.py --host 192.168.4.1 get_gps

# 获取 9 轴姿态
python3 mbdsdr_mcp_client.py --host 192.168.4.1 get_imu

# FM 扫频找最强电台
python3 mbdsdr_mcp_client.py --host 192.168.4.1 find_strongest_fm

# 详细调试模式
python3 mbdsdr_mcp_client.py -v --host 192.168.4.1 get_status
```

---

## 4. 无硬件时用模拟服务器测试

如果 ai-sdr Mini 硬件还没回来，可以用模拟服务器测试 MCP 全链路：

```bash
# 终端 1: 启动模拟服务器 (监听 ws://localhost:81)
python3 mbdsdr_sim_server.py --port 81

# 终端 2: 用 MCP 客户端连接模拟服务器
python3 mbdsdr_mcp_client.py --host localhost --port 81 list_tools
python3 mbdsdr_mcp_client.py --host localhost --port 81 tune_fm --freq 98.5
python3 mbdsdr_mcp_client.py --host localhost --port 81 get_status
```

模拟服务器返回合理的模拟数据（RSSI、GPS、IMU 等），用于验证 MCP 客户端逻辑、工具调用流程、MCP 客户端（Cursor/Claude）集成。

---

## 5. 14 个工具一览

| 工具名 | 功能 | 参数 |
|---|---|---|
| `list_tools` | 列出全部工具（MCP 工具发现） | 无 |
| `tune_fm` | 调谐 FM 广播 | `freq_mhz` (64-108) |
| `tune_am` | 调谐 AM/MW 广播 | `freq_khz` (531-1710) |
| `set_volume` | 设置音量 | `volume` (0-63) |
| `get_status` | 获取 SDR 状态 | 无（mode/freq/rssi/snr） |
| `get_gps` | 获取 GPS/北斗定位 | 无（fix/lat/lon/alt/sats/hdop） |
| `get_imu` | 获取 9 轴姿态 | 无（acc/gyr/mag/temp） |
| `start_record` | 开始 I2S 基带录音 | 无 |
| `stop_record` | 停止录音 | 无（返回采样数） |
| `get_version` | 获取固件/硬件信息 | 无 |
| `check_update` | 检查固件更新 | 无 |
| `trigger_ota` | 触发 OTA 升级 | 无 |
| `web_ota_url` | 获取 OTA 页面 URL | 无 |
| `reboot` | 重启 ESP32 | 无 |

---

## 6. 故障排查

| 问题 | 原因 | 解决 |
|---|---|---|
| `连接失败` | WiFi 未连接 / 硬件未上电 | 连接 WiFi `MBDSDR-Mini`，确认硬件上电 |
| `调用超时` | 硬件响应慢 / 网络延迟 | 增加 `--timeout` 参数（默认 10 秒） |
| `Method not found` | 工具名拼写错误 | 用 `list_tools` 确认正确工具名 |
| Cursor 不显示工具 | MCP 配置路径错误 | 确认 `mbdsdr_mcp_client.py` 路径是绝对路径 |
| Claude 不显示工具 | 配置文件位置错误 | 确认配置文件在正确目录，重启 Claude |
| `缺少 websocket-client` | 依赖未安装 | `pip install websocket-client` |

---

*MBDSDR Project - AI定义无线电 - 全开源 GPL-3.0 - 呼号 BI4MIB*
*文档版本: v0.1 (2026-09-16)*
