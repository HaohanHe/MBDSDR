# MBDSDR 桌面端

AI 定义无线电（MBDSDR）桌面客户端，基于 PySide6。呼号 BI4MIB。GPL-3.0。

## 安装依赖

```bash
pip install -r requirements.txt
```

## 启动方式

### 普通启动（带控制台日志）

```bash
python main.py              # 默认模式
python main.py --sim        # 模拟模式（无硬件）
python main.py --host 192.168.4.1 --port 81   # 连接真实硬件
```

### 无控制台启动

| 平台 | 方式 |
|------|------|
| Windows | 双击 `run_gui.bat`，或双击 `run_gui.pyw`（由 pythonw 运行，不弹黑框） |
| Linux / macOS | `./run_gui.sh`（后台运行，日志丢弃） |

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

## 快捷键

- `Ctrl+C` 连接设备 / `Ctrl+D` 断开 / `Ctrl+M` 模拟模式
- `Ctrl+R` 录音开关 / `Ctrl+Q` 退出 / `F11` 全屏
