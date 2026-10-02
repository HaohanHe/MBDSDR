# P1 真机首次运行手册：插上 SDR，一条命令出成果

> 目标读者：第一次在真机（Linux 桌面/笔记本）上跑通 MBDSDR 的人。
> 全程只敲文中给出的命令；每一步都告诉你"看到什么算成功"。
> 配套依赖细节见同目录 [P1-dependency-checklist.md](./P1-dependency-checklist.md)。
>
> 全链路：插设备 → 组权限 → udev 规则 → 一键自检 → 诊断向导 → onboard 一条命令 → 回填论文。

## 0. 准备

**硬件**

- RTL-SDR 电视棒（RTL2838UHIDIR，USB ID `0bda:2838`；兼容 `0bda:2832`）
- 配套天线（ADS-B 用 1090 MHz 天线；跑别的模式换对应天线）
- （可选）GNSS 模块（USB-TTL 桥）：用于给录制打上 GNSS UTC 时间戳；不接也能跑

**软件**

- Linux（Debian/Ubuntu 实测；其他发行版把 `apt` 换成对应包管理器）
- 已 clone 本仓库，命令都在**仓库根目录**下执行
- Python ≥ 3.8，装好 numpy：`python3 -m pip install --user numpy`

---

## 第 1 步：插上设备

把 RTL-SDR 插到**主板后置 USB2.0 直连口**（不要用 USB3.0 Hub、不要用前置面板口，这是丢包/枚举失败的第一大根因）。

确认内核看到了它：

```bash
lsusb | grep -E '0bda:(2838|2832)'
```

**成功**：输出类似 `Bus 001 Device 005: ID 0bda:2838 Realtek ... RTL2838UHIDIR`。
**失败**：没有任何输出 → 换线、换口；`dmesg | tail -20` 看 USB 枚举报错。详见依赖表 P-1。

---

## 第 2 步：当前用户加入读写组

```bash
sudo usermod -aG plugdev,dialout $USER
```

**关键**：必须**注销并重新登录**（或重启）组权限才生效。`plugdev` 管 USB 设备访问，`dialout` 管 GNSS 串口。

验证（重新登录后）：

```bash
groups | tr ' ' '\n' | grep -E 'plugdev|dialout'
```

**成功**：能看到 `plugdev` 和 `dialout`。

---

## 第 3 步：安装 udev 规则

不装规则时，`lsusb` 能看到设备，但你的用户进程可能打不开它（`Permission denied`）。一次性写入：

```bash
sudo tee /etc/udev/rules.d/99-mbdsdr-rtlsdr.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0660", GROUP="plugdev"
EOF
sudo udevadm control --reload && sudo udevadm trigger
```

**成功**：无报错输出。这一步只做一次，以后重插/重启都不用再管。

---

## 第 4 步：一键自检（只读，不改系统）

```bash
python3 tools/hw_selfcheck/selfcheck.py
```

它会逐项检查 6 件事：USB/udev 枚举、rtl_test 能不能打开 tuner、rtl_sdr 实读 3 秒有没有丢包、声卡、GNSS 串口、Python/系统依赖。

**成功**：末尾汇总里 `FAIL=0`（WARN 可以有，不阻断）。
**失败**：记住退出码——有任何一项 FAIL 时自检退出码为 1，属正常提示，接着跑第 5 步。

---

## 第 5 步：诊断向导（把问题翻译成可复制命令）

自检常常会报 FAIL/WARN，但"报了之后该敲什么"不需要你猜：

```bash
python3 tools/hw_selfcheck/selfcheck.py --json | python3 tools/diag_wizard.py
```

向导会逐行打印每项问题的**结论 + 一行带 `$` 的命令**，直接复制粘贴执行即可。修完后重跑这条命令，直到：

- 汇总 `FAIL=0`；
- 末尾出现「**设备就绪！一条命令跑通首条链路**」。

**云 VM / 没插设备时会怎样**：向导会明确告诉你"本报告来自未插设备的机器，这些 FAIL 属预期"——这不是你操作错了，到真机上再跑即可。

坏输入提示：向导只吃 `selfcheck.py --json` 的输出；贴错了（比如贴成 onboard 的报告、纯聊天文本）会以退出码 2 拒绝并说明该贴什么。

---

## 第 6 步：onboard 一条命令出成果

设备就绪后，直接跑（ADS-B 飞机报文最容易出成果）：

```bash
python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb
```

这条命令自动完成五步：detect（自检设备）→ capture（rtl_sdr 实采）→ record（转 SigMF cf32_le）→ decode（CRC 校验解 ADS-B 帧）→ output（报文/频谱图/manifest 落盘）。

**成功标志**：

- 屏幕末尾打印「全链路通过 ✓」；
- 产物目录 `paper/experiments/onboarding_adsb_<时间戳>/` 里有：
  - `*.sigmf-data` + `*.sigmf-meta`（标准 SigMF 录制）
  - `*_messages.txt` / `*_messages.json`（解码出的飞机报文）
  - `spectrum__captured__adsb__*.png`（频谱图）
  - `*_manifest.json`（口径与参数清单）

**其他模式速查**：

| 目标 | 命令 |
|------|------|
| NOAA 气象卫星云图 | `--freq 137.5e6 --mode apt --n 4800000`（需等卫星过境） |
| APRS 位置报文 | `--freq 144.39e6 --mode ax25 --n 4800000` |
| CW 莫尔斯电报 | `--freq 7020000 --mode cw --n 2400000` |

接了 GNSS 模块并导出过 NMEA 日志时，加 `--gnss <nmea.log>`，SigMF 时间戳会标 `time_source=gnss`；不加则如实标 `system`。

---

## 第 7 步：回填论文 recorded 口径指标

把第 6 步的产物目录喂给回填脚本，自动出 CSV + 图（数字全部来自你的真实录制，不合成）：

```bash
python3 experiments/exp_ota_run.py \
    --recordings-dir paper/experiments/onboarding_adsb_<时间戳>
```

产物：

- `paper/experiments/ota_recorded_metrics.csv`——逐段录制一行：采样率、估算 SNR、ADS-B 前导候选数 / CRC 通过数、Wilson 置信区间、AMR 预测、多普勒观测数；
- `paper/experiments/figures/ota_recorded_success_vs_ebn0*.png`——解码成功率 vs 估算 Eb/N0（未标定，图注如实标注"需真机 SNR 校准"）；
- `paper/experiments/manifest_ota_run.json`——口径声明（`data_origin=recorded`）。

到这里，"插上 SDR → 一条命令出成果 → 论文有数据"闭环完成。

---

## 附：故障排查速查

按症状找根因（完整表见 [P1-dependency-checklist.md](./P1-dependency-checklist.md) 第三节）：

| 你看到的 | 先查这个 |
|----------|----------|
| `lsusb` 没有 `0bda:2838` | 换 USB2.0 直连口 / 换线（P-1） |
| `rtl_sdr` 报 Permission denied | 第 2、3 步没做或没重新登录（P-2） |
| `rtl_test` 报 No supported devices found | `sudo modprobe -r dvb_usb_rtl28xxu`（P-3） |
| capture 写出空文件 | 设备被 rtl_tcp/gqrx/dump1090 占用（P-4） |
| stderr 刷 `lost at least N bytes` | 换 USB2.0 口（P-5） |
| 采样率/频率越界报错 | `--sr 2400000`、`--freq 1090e6` 单位（P-6/P-7） |
| decode 0 帧 | 接天线、调增益、确认频率上有信号（P-8） |
| 云 VM 里全 FAIL | 无硬件属预期，换真机（P-9） |

## 附：退出码约定

| 命令 | 退出码 | 含义 |
|------|--------|------|
| `selfcheck.py` | 0 / 1 / 2 | 0=无 FAIL；1=有 FAIL；2=脚本自身错误 |
| `diag_wizard.py` | 0 / 1 / 2 | 0=报告有效且无 FAIL；1=有 FAIL（已给修复命令）；2=坏输入 |
| `onboard.py` | 0 / 1 | 0=全链路通过；1=有步骤失败（看对应步的修复建议） |
| `exp_ota_run.py` | 0 | 恒 0；无录制时写空态 manifest 并说明 |
