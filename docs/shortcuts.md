# 快捷键一览（生成物）

> 本文件由 [`scripts/gen_shortcut_table.py`](../scripts/gen_shortcut_table.py) 从 [`desktop/shortcuts.py`](../desktop/shortcuts.py) 的 `SHORTCUTS` 注册表生成，**不要手改**。改键位请改注册表后重跑脚本；`tests/test_shortcut_table.py` 会校验表里条目数与注册表一致，防止表腐烂。
>
> 共 **46** 条登记：配键 **35** 条、故意不配键 **11** 条（后者在「备注」里写明原因）。
>
> **生效范围**：全局 = 主窗恒生效；星象仪形态 / 频谱获焦 = 局部，只有该形态或频谱控件自身获焦时才生效（**焦点在哪，局部优先**，因此允许与全局条目同键）。
> **改键**：「设置 → 快捷键」写的是覆写层，本表只列出厂键位。

## 文件

| 登记 id | 键位 | 动作 | 生效范围 | 备注 |
|---|---|---|---|---|
| `file.connect` | `Ctrl+C` | 连接设备... | 全局 | 焦点在文本框时放行复制 |
| `file.disconnect` | `Ctrl+D` | 断开连接 | 全局 |  |
| `file.quit` | `Ctrl+Q` | 退出 | 全局 |  |

## 视图

| 登记 id | 键位 | 动作 | 生效范围 | 备注 |
|---|---|---|---|---|
| `view.waterfall` | `Ctrl+Shift+W` | 显示瀑布图 | 全局 |  |
| `view.fullscreen` | `F11` | 全屏 | 全局 |  |
| `view.reset_position` | —（不配键） | 重置窗口位置 | 全局 | 窗口跑到屏幕外时的救援入口，低频操作，只从「视图」菜单点击 |
| `view.all_sky` | `A` | 全天模式（160°） | 全局 | 焦点在频谱时该键归频谱的 A=AM（局部优先）；频谱无焦点时才进入全天模式。规则见本文件顶部「焦点在哪，局部优先」 |
| `view.planetarium` | `Ctrl+Shift+P` | 星象仪模式 | 全局 |  |
| `view.planetarium_exit` | `Esc` | 星象仪形态退出 | 星象仪形态（局部） | 只在该形态启用；先关 AI 回复卡再退出形态 |

## 星象仪

| 登记 id | 键位 | 动作 | 生效范围 | 备注 |
|---|---|---|---|---|
| `planetarium.search` | `Ctrl+F` | 找天体（星象仪） | 星象仪形态（局部） | 只在该形态启用：把天空搜索框抬到画面底部并聚焦；IDE 形态不劫持 Ctrl+F，留给文本/列表自身的查找语义 |

## 工具

| 登记 id | 键位 | 动作 | 生效范围 | 备注 |
|---|---|---|---|---|
| `tools.sweep` | `Ctrl+Shift+F` | FM 扫频找台 | 全局 |  |
| `tools.record` | `Ctrl+R` | 开始/停止录音 | 全局 |  |
| `tools.ntrip` | `Ctrl+Shift+N` | NTRIP 设置 | 全局 |  |
| `tools.gnss_connect` | `Ctrl+Shift+G` | 连接 GNSS... | 全局 |  |
| `tools.gnss_disconnect` | —（不配键） | 断开 GNSS | 全局 | 与连接成对，重复配键会与连接抢同一次按键 |
| `tools.sat_start` | —（不配键） | 开始跟踪选中卫星 | 全局 | 需先选卫星，低频操作 |
| `tools.sat_stop` | —（不配键） | 停止卫星跟踪 | 全局 | 与开始跟踪成对 |
| `tools.sat_tle` | —（不配键） | 更新卫星 TLE (Celestrak) | 全局 | 联网操作，避免误触 |
| `tools.about` | `F1` | 关于 MBDSDR | 全局 |  |

## 帮助

| 登记 id | 键位 | 动作 | 生效范围 | 备注 |
|---|---|---|---|---|
| `help.quick_start` | —（不配键） | 三步上手 | 全局 | 帮助菜单常驻入口；低频操作，不占系统帮助键 F1 |
| `help.catalog` | —（不配键） | 能力目录 | 全局 | 帮助菜单常驻入口；从注册表实时生成，低频操作 |
| `help.shortcuts` | —（不配键） | 快捷键一览 | 全局 | 帮助菜单常驻入口；内容由本表生成，低频操作 |
| `help.command_palette` | `Ctrl+K` | 命令面板 | 全局 | 一个搜索框定位菜单命令/面板页签/能力/快捷键；想不起入口在哪时用 |

## 面板

| 登记 id | 键位 | 动作 | 生效范围 | 备注 |
|---|---|---|---|---|
| `panel.capability_matrix` | —（不配键） | 能力矩阵（◧ 事实源） | 全局 | 能力只读清单，低频入口，不占快捷键 |
| `panel.reset_layout` | —（不配键） | 恢复出厂布局 | 全局 | 破坏性操作，要求点击确认 |

## 布局

| 登记 id | 键位 | 动作 | 生效范围 | 备注 |
|---|---|---|---|---|
| `layout.save` | `Ctrl+Shift+S` | 保存 | 全局 |  |
| `layout.delete` | —（不配键） | 删除 | 全局 | 破坏性操作，内置布局本就置灰 |

## VFO

| 登记 id | 键位 | 动作 | 生效范围 | 备注 |
|---|---|---|---|---|
| `vfo.new` | `Ctrl+Alt+V` | + VFO | 全局 | 避开输入框里的 Ctrl+Shift+V 粘贴 |
| `vfo.cycle` | `Ctrl+Tab` | 切换主听 | 全局 |  |
| `vfo.mute` | `Ctrl+M` | 静音 | 全局 |  |
| `vfo.replay` | `Ctrl+Shift+R` | 回放 | 全局 |  |

## 频谱

| 登记 id | 键位 | 动作 | 生效范围 | 备注 |
|---|---|---|---|---|
| `spectrum.mode.am` | `A` | AM 解调 | 频谱获焦（局部） | 频谱获焦时 A=AM；焦点不在频谱时 A 归「全天模式」 |
| `spectrum.mode.nfm` | `N` | NFM 解调 | 频谱获焦（局部） | 频谱获焦时生效 |
| `spectrum.mode.fm` | `F` | FM 解调 | 频谱获焦（局部） | 频谱获焦时生效 |
| `spectrum.mode.wfm` | `W` | WFM 解调 | 频谱获焦（局部） | 频谱获焦时生效 |
| `spectrum.mode.cw` | `C` | CW 解调 | 频谱获焦（局部） | 频谱获焦时生效 |
| `spectrum.sideband.lsb` | `S` | LSB 解调 | 频谱获焦（局部） | 频谱获焦时生效 |
| `spectrum.sideband.usb` | `Shift+S` | USB 解调 | 频谱获焦（局部） | 频谱获焦时生效 |
| `spectrum.tune.left` | `Left` | 频率 −1 格（Shift ×10 / Alt ×0.1） | 频谱获焦（局部） | 频谱获焦时生效；按网格步进向下调谐 |
| `spectrum.tune.right` | `Right` | 频率 +1 格（Shift ×10 / Alt ×0.1） | 频谱获焦（局部） | 频谱获焦时生效；按网格步进向上调谐 |
| `spectrum.tune.up` | `Up` | 频率细调 +（1/10 格） | 频谱获焦（局部） | 频谱获焦时生效 |
| `spectrum.tune.down` | `Down` | 频率细调 −（1/10 格） | 频谱获焦（局部） | 频谱获焦时生效 |
| `spectrum.filter.narrow` | `,` | 滤波收窄一档 | 频谱获焦（局部） | 频谱获焦时生效；按当前模式的带宽预设表步进 |
| `spectrum.filter.wide` | `.` | 滤波放宽一档 | 频谱获焦（局部） | 频谱获焦时生效；按当前模式的带宽预设表步进 |
| `spectrum.zoom.in` | `Ctrl+=` | 频谱跨度放大 | 频谱获焦（局部） | 频谱获焦时生效；与滚轮缩放同一路径，锚点取谱面中心 |
| `spectrum.zoom.out` | `Ctrl+-` | 频谱跨度缩小 | 频谱获焦（局部） | 频谱获焦时生效；下限 48 kHz，上限为采样率 |
