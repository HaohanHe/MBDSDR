# MBDSDR 桌面端

AI 定义无线电（MBDSDR）桌面客户端，基于 PySide6。MIT License（独立实现）。

## 安装依赖

```bash
pip install -r requirements.txt
```

## 启动方式

### 普通启动（带控制台日志）

```bash
python main.py              # 默认模式；未接硬件时显示诚实「未连接」空状态
python main.py --theme dark # 可选主题：default / dark / high_contrast
python main.py --host 192.168.4.1 --port 81   # 连接真实硬件
python main.py --import-probe desktop.main_window  # 只做导入自检，不建窗口
```

### 无控制台启动

| 平台 | 方式 |
|------|------|
| Windows | 双击 `run_gui.bat`，或双击 `run_gui.pyw`（由 pythonw 运行，不弹黑框） |
| Linux / macOS | `./run_gui.sh`（后台运行，日志丢弃） |

`main.py` 只接受 `--host` / `--port` / `--theme` / `--import-probe` 四个参数，
**没有 `--sim` 开关**：无硬件时不会默认生成合成 IQ，界面直接呈现未连接空状态。

## 应用图标

图标资源位于 `assets/`：

- `assets/icon.png` — 256x256 主图标（Linux 标题栏 / 任务栏）
- `assets/icon.ico` — Windows 多尺寸图标（16/32/48/256）
- `assets/icon.svg` — 矢量版
- `assets/generate_icon.py` — 图标生成脚本（修改设计后重新生成：`python3 assets/generate_icon.py`）

配色：米白 `#F5F3EF` / 蓝灰 `#5B7B8C` / 橙 `#C4845C`（日式低饱和）。

## 打包为独立可执行文件（PyInstaller）

配置文件：`MBDSDR.spec`（onedir 模式，`--windowed` 无控制台，内嵌图标）。

- Windows：双击 `build_windows.bat`
- Linux / macOS：`./build_linux.sh`

打包产物输出在 `dist/MBDSDR/`，其中 `MBDSDR.exe`（Windows）或 `MBDSDR`（Linux）即为可执行程序。

## 导出头显观测包

「文件 → 导出头显观测包...」把一份 `*.observation.json` 导出成 PICO 头显可读的
`mbdsdr.spatial/1` 离线包（`bundle.json`；可选音频由 CLI 的 `--audio-out` 生成）。

- 实现与 `scripts/export_spatial_bundle.py` **同一份**：桌面只做壳（选档案/输出目录），
  不重写星表、频谱与解调逻辑。
- 口径：头显只读离线包，**不含实时接收**；失败时弹 CLI 的原始报错，不谎报已导出。
- 默认输出到档案同级 `<档案名>_spatial/`。

## 观星指数（星象仪顶部按钮）

星象仪顶部「今夜」右邻的「观星指数」按钮：点一下按**当前站址**后台取云量、
并用本地历表算采样时刻的月光，给出 0–100 的指数与档位（如 `79.4/100 良好`）。

- 口径：云量为主、月光最多扣 35%，是**本项目经验权重**，不对应外部产品算法，
  也不是科学评分；只代表采样时刻，不外推整夜。
- 诚实分支：未设站址 → 直接「未知（未设置站址，不按别处云量给分）」；
  没云量 → 不给分并写明原因；缺月光 → 只按云量计分并标注「未计入月光」。
- 不自动联网：只有点按钮才取数；站址变更后旧指数立刻作废。
- 跨站点复用 15 分钟快照前先验站址（`snapshot_station_matches`），
  别站的云量不会写成你头顶的天空。

## 未完成录音提示（M5）

启动后约 1.5 s 扫一次录音目录（只扫当前目录，不递归）：

- sidecar 显式 `complete: false`（录制中止）与 `*.part/*.partial/*.tmp` 残片
  会被列出来，工具栏右侧出现「未完成录音 N」+ 状态栏提示；没有则不出现。
- 点击只**只读展示**原因，另给「打开录音目录」；**不会自动删除或修改文件**。
- 中止的录音仍可回放（回放前会提示是半成品）；确认不要的请自行在目录里删除。

## 快捷键

- `Ctrl+C` 连接设备 / `Ctrl+D` 断开 / `Ctrl+M` 模拟模式
- `Ctrl+R` 录音开关 / `Ctrl+Q` 退出 / `F11` 全屏
