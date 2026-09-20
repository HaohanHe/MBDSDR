# 在 Chromebook（ChromeOS / Crostini）上运行 MBDSDR

适用：x86_64 Chromebook（如 Dell Latitude 5400 Chromebook / C640 一类 Intel 机型）。
ChromeOS 不直接支持 RTL-SDR，需要在 **Crostini（Linux 开发环境，Debian 容器）** 里运行，
并把 USB 设备透传进 Linux。整个过程不需要进入开发者模式、不影响系统安全。

ARM 架构 Chromebook 思路相同，但需确认有对应架构的 `librtlsdr`；本文命令以 x86_64 Debian 为准。

---

## 1. 开启 Linux 开发环境

设置 → 高级 → 开发者 → Linux 开发环境 → 开启。首次会下载并创建 Debian 容器，
完成后得到“终端”App。

打开终端，先更新并装好基础工具链：

```bash
sudo apt update
sudo apt install -y git python3 python3-pip rtl-sdr librtlsdr-dev
```

> `rtl-sdr` 提供 `rtl_test / rtl_sdr / rtl_fm / rtl_tcp`；
> `librtlsdr-dev` 提供 Python `pyrtlsdr` 依赖的系统库。

## 2. 把 RTL-SDR 棒透传给 Linux（关键步骤）

ChromeOS 出于安全默认不把 USB 共享给 Linux：

1. 插好 RTL2832U 棒。
2. 设置 → 高级 → 开发者 → Linux 开发环境 → **USB 设备 / Manage USB devices**。
3. 在列表里勾选 Realtek 半导体设备（VID `0bda`，PID `2838`）。
4. 设备随即出现在 Linux 容器的 `/dev/bus/usb` 下。

注意：

- 每次重新插拔、或重启后，可能需要重新勾选一次。
- 透传后在终端执行 `lsusb`，应能看到 `Realtek Semiconductor Corp. RTL2838 DVB-T`。

## 3. 验证棒能被打开

```bash
rtl_test -t
```

正常会打印：

- `Found 1 device(s): 0: Realtek, RTL2838UHIDIR ...`
- 调谐器型号（你的廉价棒常见 `FC0012` / `FC0013` / `R820T`）
- 一组增益值

看到这些就说明 USB 透传和驱动都正常。`PLL not locked` 之类的个别告警可忽略。

### 如果提示设备被占用 / Permission denied

Crostini 是隔离 VM，通常不需要像普通 Linux 那样 blacklist `dvb_usb_rtl28xxu`。
按下面顺序处理：

1. 拔掉棒，在 USB 设备列表里取消勾选，再重新勾选、重插。
2. 确认没有别的程序占用（关掉容器里其它 SDR 软件）。
3. 仍报权限问题，把当前用户加入对应组并重开终端：
   ```bash
   sudo usermod -aG plugdev,audio,video $USER
   ```
   然后重启 Crostini（设置里关闭 Linux 再打开）。
4. 实在透传不稳定，用第 6 节的 `rtl_tcp` 网络方式兜底。

## 4. 拉取项目并安装 Python 依赖

```bash
git clone https://github.com/HaohanHe/MBDSDR.git
cd MBDSDR
pip3 install -r requirements.txt   # 若没有该文件则手动装：
pip3 install pyrtlsdr numpy matplotlib
```

## 5. 一键自检

```bash
python3 scripts/rtl_selfcheck.py --save
```

默认在 98MHz 调频广播段采集并分析，结果存到 `experiments/rtl_selfcheck/`。
把终端输出和频谱图发回，用于校准 ppm 与选频。

常用参数：

```bash
python3 scripts/rtl_selfcheck.py --freq 101800000 --save   # 指定频点
python3 scripts/rtl_selfcheck.py --ppm 42                  # 已知频偏
```

## 6. 兜底：rtl_tcp 网络模式

当 Crostini 的 USB 透传不稳定，或你想让多台设备（手机、另一台电脑）共用同一根棒时，
在**能直接访问棒的环境**里启动服务：

```bash
rtl_tcp -a 0.0.0.0 -p 1234
```

然后在 Chromebook 的 Crostini 里：

```bash
python3 scripts/rtl_selfcheck.py --tcp 127.0.0.1:1234
```

> 若 `rtl_tcp` 跑在另一台机器，把 `127.0.0.1` 换成那台机器的 IP。
> rtl_tcp 模式不依赖本机 librtlsdr 直接打开 USB，跨平台最稳。

软件后端同样支持：`RTLSDRBackend(host="192.168.x.x", port=1234)`。

## 7. 桌面界面

Crostini 默认支持 Wayland/X11 转发，Linux GUI 应用会自动出现在 ChromeOS 启动器里：

```bash
# 需要 PySide6（Crostini 是 x86_64，有现成 wheel）
pip3 install PySide6
python3 desktop/main.py
```

无设备时可先用模拟器看界面：`python3 desktop/main.py --sim`。

---

## 故障速查

| 现象 | 处理 |
|---|---|
| `lsusb` 看不到棒 | 没做 USB 透传，回第 2 步勾选设备 |
| `rtl_test` 找不到设备 | 重插并重新透传；确认 VID 0bda PID 2838 |
| 设备被占用 | 关闭其它 SDR 程序；取消勾选再重勾；必要时重启 Linux |
| FM 段全是平噪 | 接天线、换 USB 口、确认频点在当地广播频率 |
| 频率整体偏移 | 廉价棒晶振偏差，用 `--ppm` 校正（可由 AI 对照已知信标估计） |
| 想收短波 (<24MHz) | 需 direct sampling 或上变频器，自检/后端已支持 `set_direct_sampling` |
| GUI 起不来 | 先 `--sim` 验证界面；确认 PySide6 装好、Crostini GPU 无异常 |
