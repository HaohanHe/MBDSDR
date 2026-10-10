# NanoVNA-H/H4 真机最小会话（到手即测）

本机为 CLI/offscreen 开发环境；真机到手后照此自检并回传。全程真实串口，无设备即诚实报错，不伪造。

## 1. 接线与权限
- 天线/被测件接 **PORT 1（S11 口）**；测 S21（滤波器/电缆损耗）时信号源 PORT1→DUT→PORT2。
- USB-C 接电脑，设备应为 STM32 CDC 虚拟串口：**VID:PID = `0x0483:0x5740`**。
- 权限：用户加入 `dialout` 组（`sudo usermod -aG dialout $USER`，重登生效），或写 udev 规则。
- 确认枚举：`python3 -c "from mbdsdr_ai.nanovna_client import find_nanovna_ports as f; print(f())"`
  应输出形如 `[('/dev/ttyACM0', 1155, 22336)]`。空列表 = 未枚举到（查 USB/线/权限）。

## 2. 一键 dump（推荐）
```
python3 -m mbdsdr_ai.nanovna_dump --port /dev/ttyACM0 \
    --start 430000000 --stop 440000000 --points 101 --out sweep.json
```
- 不带 `--port` 会自动探测；探测不到会**诚实报错并提示权限**。
- 输出 JSON：`model/version/sn/cal/sweep/frequencies_hz/S11/S21/vswr/return_loss_db`。

## 3. 自检命令序列（等价于上面脚本内部步骤）
1. 连接握手：`help` → `version` → `info`（首行板名 NanoVNA-H/H4）。
2. 校准状态：`cal` —— 应见 `open/short/load`（单端口）或含 `thru`（两端口）已置位；
   未校准先在设备面板做 OSL。
3. `sweep 430000000 440000000 101`
4. `frequencies` → 101 个整数 Hz。
5. `data 0` → 101 行 "re im" 复数对（S11）。
6. `data 1` → 101 行（S21）。

## 4. 常见失败排查
| 现象 | 排查 |
|---|---|
| 列表为空 | USB 线/口；`lsusb` 看 0483:5740；dialout 组权限；串口被占用 |
| 连得上但 data 行数≠点数 | 扫频未完成或点数不符；重发 sweep 后再读 |
| cal 状态空/未置位 | 先 OSL 校准；校准后再 dump |
| 端口 busy | 关闭其它占用串口的程序（含本 GUI） |

## 5. 最短回传格式
回传以下两项即可比对合成/真机差异：
1. 连接日志（`version`/`info`/`cal` 三行原始输出）。
2. 一组 `sweep.json`（上面 dump 的产物，可删冗余只留 frequencies_hz + S11 + vswr）。

> 合成 replay fixture 仅用于 CI 确定性测试，绝不代表真实测量值。

## 附：CDCSS badge 真机验证步骤（offscreen 无法注入合成 DCS 流）

offscreen 测试环境无法把真实 DCS 位流喂进解调链，故 badge "DCS 023" 读数无 CI 快照证据，
需真机验证：
1. 发射端/对端发出带 DCS 码（如 023）的数字亚音。
2. 接收端解调音频喂入后，CDCSS latch（连续 3 字）→ badge 应从"未检测到"变绿色 **DCS 023**。
3. 换码或停发 → 5 字内 badge 回退"未检测到"。
DSP 端已用合成位流钉扎（test_squelch_gate 13，含 bit-flip 纠错）；本步仅为 UI 呈现真机确认。
