# 「文案声称 vs 实际能力」核对清单（只报不改）

- 日期：2026-11-11
- 范围：`MBDSDR/desktop/**` 中用户可见文案对**能力**的断言（help / tooltip / 空态 / 状态栏 / 设置说明 / 面板摘要）
- 性质：**只报不改**。本文件不含任何代码改动
- 快照：`HEAD 2397c683` + 当日工作区（行号按当前工作区；其他线合入后可能漂移）
- 姊妹篇：错误出口与术语见 `docs/research/2026-11-11-gap-copy.md`；本文件只回答一个问题——**界面说的能不能做到**

## 0. 方法与边界

1. 用 AST 扫 `desktop/*.py` 的字符串常量（排除 docstring、`logger.*`、`print`），按「支持 / 自动 / 实时 / 一键 / 无需 / 尚未 / 不支持 / 仅支持 / 会…」抽候选，得 **213 条**断言候选。
2. 每条回到实现处读代码判定：调谐是否下发、按钮是否置灰、数据 tap 是否有可见性门、异常分支是否真的切换成功。静态判定，**未接真机**。
3. 判定口径：
   - **A 类**：文案说能做，实际做不到（或只在文案里成立）
   - **B 类**：文案说做不到 / 做不到全部，实际能做
   - **待复核**：静态看不出，或要真机/运行态才能判定（不硬判）
4. 严重度：P0 会让用户对硬件动作产生错误预期；P1 会让用户白等或走错入口；P2 指路/用词不实；P3 文档级。

## 1. 结论摘要

- **最贵的一对矛盾在 AI 面板**：能力目录说「AI 工具面不暴露发射工具」（A1），AI 面板又说「未配 Key 时仅支持手动控制面板操作」（B1）。两句都不对，且方向相反：AI 工具表里确实注册了 `radio_ptt` / `radio_send_cw`（有 `tx_guard` 硬门，但「不暴露」是假的）；未配 Key 时也确实会走本地工具协商执行工具（「仅支持手动」也是假的）。
- **「实时」是这个项目最容易被写坏的一个词**：天气云图面板的按钮 tooltip 说能解实时信号（A2），面板摘要说 5 种卫星「用实时数据解码出图」（A3），同一份注册表另一处又写「不是实时边收边解」。实现只有离线/录制文件一条路。
- **A 类 11 条、B 类 4 条、待复核 5 条**。B 类少不是好消息——说明文案整体偏向高估。
- 初稿里另有两条（地图下载全失败仍宣称可离线查看、卫星解码失败仍显示等待）在**当前工作区已被其他线修好**（`location_panel.py:76-88` 的 `_download_message()`、`satellite_image_panel.py:315/341/376` 的 `SAT_FAILED_TEXT`），已销去；只保留其未修干净的残余（A5）。
- 两类根因：① 能力文案写在实现之前，实现改了文案没跟（A1/B4）；② 文案写死了实现细节（空态指向不存在的设置项、错误文案断言已自动切换）。

## 2. A 类：文案说能做，实际做不到

| # | path:line | 文案（逐字／摘录） | 实际 | 最省事的反证 | 等级 |
|---|---|---|---|---|---|
| A1 | `desktop/capability_catalog.py:101` | 「需硬件支持发射；AI 工具面不暴露发射工具，避免误触」 | 假。AI 工具注册表里有 `radio_ptt`、`radio_send_cw`（`mbdsdr_ai/sdr_tools.py:2665`、`:2681`），并列入写工具模式表（`mbdsdr_ai/tool_registry.py:483`）。有 `tx_guard` 硬门，但「不暴露」不成立 | `git grep -n "radio_ptt" -- mbdsdr_ai` | P0 |
| A2 | `desktop/weather_panel.py:546` | 「开始解码当前文件或实时信号；完成后右侧出图…」 | 假。实时档位下该按钮恒置灰，只给录音指引（同文件 `:654-660` 置灰分支、`:668-670` 状态文案） | 数据源切「实时 SDR」，看「开始解码」是否可点 | P1 |
| A3 | `desktop/panel_registry.py:82` | 「GOES/GK-2A/Meteor/NOAA/FY 用真实录制或实时数据解码出图」 | 半假。「实时数据解码出图」不存在；同一注册表 `:114` 自认「离线 IQ 出图，不是实时边收边解」，`weather_panel.py:668` 也写「实时档位不在此面板解码」 | `git grep -n "不是实时边收边解" -- desktop` 对照 `:82` | P1 |
| A4 | `desktop/aprs_panel.py:129` | 「调到 144.390 MHz（FM），等附近台站发报后会自动列出」 | 半假。APRS 数据 tap 要求 `aprs_dock.isVisible()`（`main_window.py:9420`），面板收起期间不解码、不缓存；用户照提示等，打开后仍空 | 收起「APRS 数据包」面板，等 5 分钟后打开看列表 | P1 |
| A5 | `desktop/satellite_image_panel.py:383-385` | 「等待卫星下行信号」（解码抛异常时仍显示） | 残余假。异常分支只写状态行「解码失败: …」就 return，`image_label` 不动，仍停在等待；其余失败分支已改用 `SAT_FAILED_TEXT`（`:315/324/341/376`） | 给一个损坏的 APT 音频文件看图片区文案 | P1 |
| A6 | `desktop/main_window.py:11841` | 「可在『设置 → 卫星与多普勒 → 地面站经纬度』手填位置」 | 假。设置里没有「地面站经纬度」，实际项叫「观测地点纬度 / 经度」（`config_schema.py:432/440`），用户按图索骥找不到 | 打开设置 → 卫星与多普勒，搜「地面站」 | P1 |
| A7 | `desktop/main_window.py:5427` | 「无法保存布局，请重试。」 | 半假。磁盘满/目录只读时重试必然复现；文案既没给路径也没给原因分类 | 把布局目录设为只读后点「保存」 | P2 |
| A8 | `desktop/config_schema.py:598` | 「星象仪底栏已有四个图层开关（rf_sky_view.py:6944-6957）」 | 假（引用失效）。该行段现在是信息卡的绘制代码（关闭按钮 ×、标题行），不是图层开关 | `sed -n '6944,6957p' desktop/rf_sky_view.py` | P2 |
| A9 | `desktop/panel_registry.py:102` | 「无数据显『--』」 | 半假。状态页实际用全角破折号「—」（`status_panel.py:391/397/411` 一系），字面并非 `--` | 无设备时看状态页占位符 | P3 |
| A10 | `desktop/main_window.py:1636` | 「切换布局预设（可自由拖动，自动持久化）」 | 半假。「拖动」只在退出时存三栏比例；自定义预设必须点「保存」（`:1642` tooltip 自认），拖动中途强退不保存 | 拖动后直接杀进程再启动看是否保留 | P2 |
| A11 | `desktop/help_panel.py:188` | 「本目录由注册表实时生成……新增能力会自动出现」 | 半假。目录 = 部分注册表 + 静态表 `STATIC_CAPABILITIES`（`capability_catalog.py:91`、`:289`）；静态条目（发射/录制/书签等）新增不会自动出现 | `git grep -n "STATIC_CAPABILITIES" -- desktop/capability_catalog.py` | P2 |

## 3. B 类：文案说做不到，实际能做

| # | path:line | 文案（逐字／摘录） | 实际 | 最省事的反证 | 等级 |
|---|---|---|---|---|---|
| B1 | `desktop/ai_panel.py:950` | 「当前仅支持手动控制面板操作。」 | 假。未配 Key 时走本地能力协商，能匹配并执行本进程真实工具（同文件 `:780` 明说「没配 API Key 时走本地工具协商」，`:956-958` 注释同义）。用户被告知只能手点，实际可让本地协商执行 | 不填 Key，输入「调谐到 145.5 MHz」或「扫频 87-108」 | P0 |
| B2 | `desktop/adsb_panel.py:73` | 「请在 ~/.mbdsdr/config.json 设置 ground_station_lat / lon」 | 过时。设置界面已有「观测地点纬度 / 经度」（`config_schema.py:432/440`）。用户被指去手改 JSON，低估了产品能力 | 打开设置搜「观测地点」 | P1 |
| B3 | `desktop/rf_sky_view.py:3677` | 「观测站位置未设置，无法计算跟踪频点（先设 observer_lat/lon）」 | 同 B2：指向内部键名，实际可在「更改地点…」或设置里设（`location_panel.py:119/139`） | 点星象仪「更改地点…」 | P2 |
| B4 | `desktop/capability_catalog.py:213` | 「decoder_registry 尚未接主调度；此入口走 decode_digital_mode」 | 过时。`decode_router.DIGITAL_MODE_CAPABILITIES` 已接 POCSAG / ACARS / NAVTEX 等（`mbdsdr_ai/decode_router.py:219/245/320`，路由函数 `:510`） | `git grep -n "\"pocsag\"" -- mbdsdr_ai/decode_router.py` | P2 |

## 4. 待复核（不硬判）

| # | path:line | 断言 | 为什么现在判不了 | 最小验证 |
|---|---|---|---|---|
| C1 | `desktop/capability_catalog.py:99` | 「只有具备发射能力的设备才会亮起」 | 静态能看到 `_tx_block_reason()` / `_refresh_tx_button_state()`（`main_window.py:5025/5093`），但没有一台无 TX 能力的设备实跑 | 接 RTL-SDR（仅接收）看发射按钮是否置灰且 tooltip 给原因 |
| C2 | `desktop/panel_registry.py:88` | 「500ms 更新方位/仰角/多普勒并自动调谐」 | 频率写死在文案里，需核定时器常量与实际调谐节流 | `git grep -n "500" -- 'desktop/sat_track*' desktop/rf_sky_view.py` 后实跑看调谐频率变化 |
| C3 | `desktop/main_window.py:1699` | 「设备插上不会自动播放」 | 未找到自动播放调用；但启动自动连接（`_auto_enumerate_and_connect`）之后是否恢复上次「收听」状态未验证 | 上次以「收听」退出，插设备启动，听是否有声 |
| C4 | `desktop/main_window.py:6627` | 「该采样率不被设备支持，已自动切换到最近合法档位」 | 后端确有最近档回退（`mbdsdr_ai/sdr_backend.py:2343/2366`、`rtltcp_client.py:786`），但这句话由错误字符串匹配器统一吐出，对不回调的后端也照样断言「已自动切换」 | 用不支持目标速率的后端触发连接错误，看是否真的重试成功 |
| C5 | `desktop/capability_catalog.py:145` | 「本机 FC0012 类 tuner 实测上限约 948.5 MHz」 | 「本机实测」是硬件结论，当前无设备 | 接 FC0012 类 tuner，从 900 MHz 向上扫到失锁 |

## 5. 复算与后续

- 断言候选复算：用 AST 取 `desktop/*.py` 的 `ast.Constant` 字符串，排除 docstring 与日志调用，再按关键词筛（本文件 §0 第 1 步）。
- 本清单只登记，不改字。若后续要修，建议顺序：**A1/B1（AI 能力描述互斥）→ A2/A3（实时解码）→ A6（指路失效）→ 其余**。
- 共同根因提醒：凡是「已自动 / 会自动 / 支持实时」这类**结果性断言**，写法必须与代码里那条分支同源（引用常量或同一条状态机），否则实现一变文案就变成谎话。
