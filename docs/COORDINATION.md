# 多智能体协作台账（WorkBuddy × TRAE）

> **本文件是并行干活的唯一协调面。开工第一件事读它，收工前更新它。**
> 谁办啥事、手上在途什么、下一件干什么、边界在哪——全在这，别靠猜。
>
> 会话约定：**WorkBuddy 会话 = 「我」**（协议真值/CI/台账线），**TRAE = 「翠」**
> （构建打包/新功能线）。两边都会读写本文件。

---

## 一、分工边界（谁办啥事）

### WorkBuddy 会话（我）
- **协议真值 / golden 对拍线**：FT8、ADS-B 等解码链的外部真值验证、跨实现
  对拍、fixture 管理（血规：代码与金样必须同一提交走）
- **CI 红灯清盘 + 钉扎契约**：typing 守卫、控件盘点快照、漂移红归因、
  tests/(A)(B)/mbdsdr_ai 基线维护
- **审计台账口径**：capability-map / settings-surface-audit /
  cpp-tool-ui-reverse-audit 的销账与 not_verified 如实记录
- **运行库布防 / 弹窗根治**：构建树 DLL 部署（`deploy_win_runtime`）、
  裸跑零弹窗验收
- 本协调台账 + 会话记忆维护

### TRAE（翠）
- **C++ 构建/打包线**：CMake 重构（含 `deploy_win_runtime` 模块化拆分）、
  ctest 全量收口、Qt 插件/辅助运行库部署
- **便携包交付线**：`package_windows.ps1` + windeployqt（注意：旧 zip 曾是
  RTL-SDR **stub 构建**、USB 直连没编进去——交付前必须验真机构建）
- **新协议/功能线**：DVBS2、FT4/WSPR、GPS 测向、TC-CLTU 等新能力开发
- **C++ UI / 功能开发**：星象仪等

> **边界原则：谁在途谁所有。** 对方有未提交编辑的文件一律不碰；
> 共有文件（如 `cpp/CMakeLists.txt`）按 **hunk 归属**拆分提交，只提交自己的 hunk。

---

## 二、当前在途（2026-11-03 22:40 更新）

### 我这边
- **[用户指令 23:00]** UI 形态原型（Web）已产出：`D:/mbdsdr/output/ui-prototype/index.html`
  （仓库外，原型阶段）——双形态（星象仪：星空炸开+摄像机锁星+信息卡铺开
  +模型协商；专业模式：频谱瀑布+工具树+协作栏）+ 能力矩阵（能力而非
  流程）。用户产品要求已钉：两形态一套系统、摄像机锁星（天动星静）、
  点追踪/接收由**模型提问**（有 SDR/云台/GNSS 吗）不预制流程、6DOF/
  视觉校准/VR·XR 均为能力位。参考素材：`D:/mbdsdr/reference/stellarium/`
  （"天空炸开"=Stellarium）、定轨demo（多普勒 RLS 链路）、赛前培训 PPT×2、
  SDR 遥测教程 PDF、通信对抗材料包（learnSDR+PlutoSDR）。
- **LRPT 队列#9 实验结果（4sps 矩阵，已测未入库）**：2/4sps × RRC ×
  Costas 带宽 6 配置均**不敌基线**（2sps 前馈 269/91/16 仍最优；4sps
  bw.005 ASM=108 但 VCDU=10）——"细定时网格"假设证伪，天花板是信道。
  Gardner 环修复版已改进 lrpt_modem.py（中点插值+归一+钳制+热启动，
  默认关未验）；擦除辅助 RS（2e+f≤32，救 75 条部分帧）设计已论证但
  未实现。矩阵脚本在 output/tmp/lrpt_4sps_matrix.py。
- 前批落定：LRPT 图像链破层批 **已推（7d04a3d）**；COORDINATION.md
  结构守卫已提（d655dae，待推）。
- **[11-04 18:30] UI 原型机制深化（仓库外，不占别人文件）**：
  `D:/mbdsdr/output/ui-prototype/index.html`——把子 Agent 精读
  Stellarium Web Engine 的笔记（同目录 swe-study-notes.md）落到实码：
  ①**时间/视角分离**（此前把周日视运动当视角推，地景跟着时间转是物理
  错误；现 TIME.angle 只转星点/银河，地景/方位字/轨道弧固定地平系）；
  ②大气与地平辉光（加法混合、雾带、白天恒星消隐、黄昏暖辉光）；
  ③grab-the-sky 钉点拖拽 + zoom-to-cursor；④解除锁定不再弹回视角；
  ⑤时间流速开关 1×/60×/600×/静止（可直观演示"天在动、地不动"）；
  ⑥**定轨去功能化**（按用户 11-04 铁律：按钮/能力卡/工具树/开场话术/
  卫星读数全拔，唯一触发=用户在 AI 抽屉对话里开口说）。
  验证：node --check + DOM 引用零缺失 + 无头 mock DOM/Canvas 实跑
  drawSky/drawWaterfall 共 10 帧无异常（output/tmp/verify_ui_runtime.js）。

- **[11-04 19:20] UI 原型 → 完整产品工程（仓库外独立目录，未占任何在途文件）**：
  `D:/mbdsdr/output/mbdsdr-product/`。按用户「文案/国际化/底层上层/wiki/文档
  都要整」的要求落地：①**分层** 00-core（状态/事件/持久化/投影/契约）→
  10-data（目录 + MockSource/NativeSource 内核桥）→ 20-i18n → 30/40/50/70
  视图 → 60-app 引导；②**三语** zh-CN/ja-JP/en-US 218 词条，静态反查 +
  动态 t() 插值，偏好持久化；③**文档体系** 26 篇（规格/指南/架构/i18n/
  设计语言/合规/路线图 + 9 能力词条 + 3 场景 + 术语/FAQ + 机制笔记），
  构建时编译进产品内文档中心，站内跳转；④**单文件发布** dist/index.html
  （208KB 自包含，file:// 直接开）；⑤**自动守卫** verify_product.py
  （语法/DOM/词条/硬编码/**定轨铁律**/文档链）+ verify_runtime.js（无头
  实跑 37 帧/三语/文档/协商）。真实浏览器（Edge headless）五态截图验收，
  修掉搜索胶囊被顶栏 backdrop-filter 糊掉、能力矩阵压信息卡、瀑布空板等
  旧 UI 病根。**未提交**：工作树全是别的会话在途 M/??，避免卷入；
  合并入库见队列 #11。
- **[11-04 20:25] 产品工程对齐原仓库事实源（本轮主攻，仓库外）**：用户指出
  「你参考一下原项目呀」——查实上一版 6 处自说自话：能力位凭空编、桥接
  通道虚构、MockSource/预填充瀑布违反「绝不伪造数据」、站点用真实地名
  （身份红线）、时间倍率与桌面端不一致、把 6DOF/VR 规划项画成已上线。
  已改为**让产品从原仓库代码长出来**：`build/sync_from_repo.py` 只读
  AST/正则解析 mcp_server.py（14 工具域 / 82 工具→域 / 79 写工具模式）、
  desktop/tokens.py（figma 主题令牌）、rf_sky_view.py（5 档时间倍率）、
  tokens.h（ControlHub :50732）、sat_tracker.py（内置 TLE/下行频率/禁用
  精确过境策略）→ 生成 `src/05-repo-facts.js` 与 `docs/09-repo-alignment.md`。
  产品全链对齐：能力矩阵由真实域生成（规划项标「规划·未验证」且禁用）、
  DataSource 走 ControlHub + arm_manual 人在环门（gated 与失败分开）、
  中性站点 0.0,0.0 且不可信拒给结论、频谱/瀑布改诚实空态、工具名换
  get_status/set_frequency 等真名、离线回执明写「未伪装成功」。
  文档 wiki 与守卫同法改造（红线守卫：真实地名/能力数字标语/假数据/定轨）。
  **一键复现** `run_all.py`：同步→骨架→文档→单文件→静态守卫→无头运行时
  →Edge 七态截图→`VERIFY-REPORT.md`（含 sha256、not_verified 7 条、
  零个人信息）。实测 ALL PASS：dist 282KB / 35 篇文档 / 17 能力位 /
  240 词条 / 37 帧。**未入库**：工作树全是别的会话在途 M/??，不碰不提交。
  19:20 条目的 MockSource/26 篇/208KB 口径已过期，以此条为准。

- **[11-04 22:25] 全天球星象仪重做（用户两次纠偏，仓库外）**：用户指出
  ①「星星从中心唰地飞散」违背科学（星本来就在全天球固定，动的是视角/
  FOV/时间）；②只守仓库 141 颗亮星是闷头造车，**可上网下载真实星表**；
  ③平面线性投影把球形天空摊成「横向带鱼屏」，应「天圆地圆」。
  已落：`build/fetch_hyg.py` 用 git 稀疏克隆取回 **HYG v4.1 全天星表**
  （HTTP 大文件被网关掐 4 次，换 git 协议一次过；34 MB / 119,626 颗 /
  J2000 / CC BY-SA 4.0，SHA256 登记）；`build/sync_sky_catalog.py` 只读
  AST 解析仓库 141 命名星 + 27 座连线 + 117 DSO + 中文星官，叠加 HYG
  **25,792 颗（V≤7.5）** → `src/12-sky-catalog.js`。
  `src/31-celestial.js` 重写天球：固定 J2000 → JD/GMST/LST → 地平 az/alt
  → **球面立体投影 stereographic**（gnomonic 下大圆投影成直线，正是横条
  根因；stereo 下大圆成弧且保形，也是 Stellarium Web 默认）→ 圆形视场；
  30-sky.js 全层统一投影（星/星座/DSO/网格/地平大圆弧/地景/方位/卫星）。
  删净：Math.random 假星 820 颗、burstT 飞散、每星独立角速度、装饰银河
  渐变带、平面 worldToScreen。入场改「圆形视场渐开 + 暗适应（真实星等
  依次浮现）」，星位一帧不动。缩放改 fov_deg，grab-the-sky / zoom-to-cursor。
  **两个主导**：星象仪=时空主导（时空 HUD：模拟时刻/JD/LST/FOV/视轴/
  显示门限/视场内可见星数/投影/星表口径/时间源/not_verified）；专业模式
  =SDR·频谱主导（诚实空态不变）。时间唯一缺省改 simUnix（TimeEngine 契约），
  删除自动锁星、默认演示起始时刻设站址当地夜间（正午白天整层消隐门会
  关掉所有星，属观测条件设定且 HUD 如实标注）。
  验证：run_all.py 一键全绿（静态守卫新增「禁随机星点/禁飞散/禁平面投影/
  禁装饰银河/星表≥10000 颗/HYG SHA+许可/not_verified/圆形视场/HUD」），
  Edge 真实截图四态亲眼验收：广角 100°、窄视场 30°（门限 6.09→7.06 等、
  可见星数与天区同步变化）、时间快进 3h（LST 19.16°→59.28°，星空刚性转、
  地平弧与地景纹丝不动）、改视角（地平弧升降、换天区）。专业/日语/文档/
  模型协商四态回归全绿。dist 946KB 自包含，36 篇文档，VERIFY-REPORT.md 含
  sha256 与 not_verified。**未入库**：仓库工作树仍是别的会话在途 M/??。

- **[11-04 23:00] 天球投影链重做（第三次纠偏，仓库外）**：用户指出「我说的
  球体不是视野，你参考人家原本项目——像 insta360 那样，天空是球体但展示出来
  是三维空间里的 2D 投影」，并否掉了上一版「圆形视场盘 + 圆边框 + 屏幕径向
  渐变」（那是把天空塞进球里）。已回读原项目 `reference/stellarium-web-engine`
  的真实链路：`projection.c` 的 `project_to_win()` 把 NDC 乘 `window_size`，
  `painter.c` 只判 x,y 是否在 `[0,w]×[0,h]`——**整条链没有任何圆形遮罩**；
  `proj_perspective.c`（原样进 4×4 透视，max_ui_fov=120°）与
  `proj_stereographic.c`（`x'=2x/(1−z)`，max_ui_fov=185°）可插拔；
  地景是 FRAME_OBSERVED 下的球面纹理（w=0，只随视线转）。
  已改：`31-celestial.js` 换「相机在球心 → 视图空间(X/Y/Z) → 全屏平面投影」，
  ≤88° 针孔透视 / 更大立体投影；**删圆形 clip、圆边框、屏幕径向渐变**；
  地平线透视下成直线（大圆在相机里的正确像）、鱼眼下成弧。`30-sky.js` 地景
  改**逐屏幕列**求 `alt(y)=h(az)` 首个穿越点再整体填充（上一版按方位×高度分块，
  球冠跨相机平面被裁后留阶梯空洞，广角截图已实证）；地景绕 360°、随视角动
  不随时间转。太阳位置由「sin(localHour) 假模型」换成真解析式（Meeus 简化
  ±0.5°，只判昼夜）。新增 `tests/proj_selftest.js` 并挂进 run_all：投影↔反投影
  互逆 0.000px、透视下地平直线 0.00px、立体下成弧 28.9px、158° 四角 4/4 可反投影。
  顺带修一个真 bug：立体投影下屏幕四角本就落到相机背后（θ>90°），旧判据照搬
  透视的 `Z>NEAR` 把四角的星和地景整片丢掉了；改成只排除背极点 Z→−1。
  文案/文档同步（03 架构、07 路线图、README、swe-study-notes → 08 机制笔记），
  静态守卫改为「禁圆形视场盘 / 禁屏幕中心径向渐变 / 必须有 projectSegment」。
  **未入库**：仓库工作树仍是别的会话在途 M/??。

- **[11-05 22:00] 产品工程阶段 A 入库（5a977026，本线已提交）**：详见队列 #11。
  开工前撞到 C 盘 195G 满、余 455MB，`Grep` 工具写临时缓存 ENOSPC；
  定位为长期累积（WinSxS 13G / Program Files 31G / AppData 下各类 SDK 与
  AI 工具缓存 ~90G / hiberfil 6.3G），**未擅自删任何系统盘文件**，只做只读诊断，
  清理需用户拍板。本笔产物全落 D 盘，不受影响。
  另外把 `swe-study-notes.md` 与 v1 原型 HTML 两个仓库外依赖收进
  `web/product/{docs/source,prototype}/`，`NOTICE.md` 逐条登记
  HYG / OpenNGC（CC BY-SA 4.0）与代码 MIT 的边界。
  **未碰**：别线在途的 11 个 M 文件与 `.audit_tmp/`；提交用 `--only` 拆范围。

### TRAE 那边（观察到的在途，未打扰）
- `scripts/smoke_exe_no_popup.ps1`（??）——弹窗冒烟脚本在途
- CMake 布防模块化（deploy_runtime.cmake）已落（1b675e7）
- 便携包线待收（windeployqt / package_windows.ps1）
- 天空模式会话：71881a0 摄像机概念/卫星锁定坐标系 + 27b8b69 工作流重构

### 巡检会话（2026-11-04 02:40 登记，无人值守流水线督办）
- **流水线四段盘点**：①解码器真实度盘点＝capability-map 体系（常设）；
  ②真样本端到端钉扎＝LRPT 图像链/dump1090/FT8 跨实现（11-03 已收）；
  ③DVB-S2/GPS 硬链路补真值＝DVB-S2 侧 BCH/LDPC KAT 已落（347d09f/49dc00d），
  **GPS 侧本窗口收口（3f225db）**：IS-GPS-200E Table 3-I 内置
  `gps_ca.ca_code`，官方 PDF 脚本提取 + gps-sdr-sim 比特级对拍 + octal 37/37
  + Gold 指纹，金样 `tests/golden/gps_ca_prn_vectors.json`；材料
  GPScacodes.py 裁决更正（标准码的反极性版本，原"非标准"判词有误）；
  ④健壮性与长跑＝**已备好待启动**：`scripts/soak_truth_battery.py`
  （真值电池 15 文件 174 测试/轮，单轮试跑全绿，JSONL 落 output/soak/）。
- **第四段启动前提（派单）**：工作区无他人在途编辑真值线文件时，
  `python scripts/soak_truth_battery.py --rounds 20` 起长跑去；长跑期间
  勿动 tests/golden/** 与 mbdsdr_ai/{gps_ca,lrpt_*,dvbs2_*}——当前天空/
  AIS 会话仍在高频改代码，本窗口不抢跑。
- **红线告警（已销账，2026-11-04 14:55 复核）**：test_identity_redline 曾报
  2 处命中——`tests/test_doppler_link.py` 与 `tests/test_sky_camera_lock.py`
  的居所/行踪类措辞（30e0f36/6205972 两批带入）。复核：两文件现均已 0 命中
  （对应会话已随手修掉）。**本条原先照抄了规则字面量，反倒成了门禁唯一的
  残留命中（AGENTS.md 第 1 条「规则字面量用拼接构造，门禁自身不命中自己」），
  现改为只描述类别不复述字面量。** 全仓 `git grep` 现已零命中。
- **本窗口所有物**：`mbdsdr_ai/gps_ca.py`、`mbdsdr_ai/gps_tracking.py`、
  `tests/test_gps_prn.py`、`tests/golden/gps_ca_prn_vectors.json`、
  `scripts/soak_truth_battery.py`、capability-map 的 GPS 段。
  未提交项（`docs/COORDINATION.md` 本段登记 + `mbdsdr_ai/lrpt_modem.py`
  Gardner 实验）属 WorkBuddy 窗口，本窗口不代提。

### 星象仪/彩带会话（本窗口，2026-11-03 登记）
- **已收工**（按时序）：
  1. SDR++ 频段彩带 v2 补强（`dc62be0`，已推）；
  2. A3 删除预制工作流与触发短语（`27b8b69`，已推）——`_preset_*` ×6 与
     `match_trigger`/`workflow_trigger_match` 全清，引擎只服务用户/AI 自定义
     JSON，回归锁 `tests/test_workflow_engine_no_presets.py`；
  3. A1 摄像机概念与「卫星锁定」坐标系（本提交）——`rf_sky_view` 新增
     `_camera_lock`（Stellarium StelMovementMgr::flagTracking 语义：每 33ms
     tick 真 TLE+sgp4 重算目标 az/el，k=0.25 指数趋近贴视图中心；拖拽/
     双击/触屏/重置/反选/落地/数据失效全路径解锁），「📷 摄像机」按钮 +
     锁定 HUD + 术语统一（重置视角→重置摄像机）；
     B2 TLE 可追溯：目录条目带 tle_source/tle_epoch 进信息卡，
     `get_satellite_pass`/`PassInfo` 加同名字段，`orbit.tle_epoch_iso` 单一
     来源；回归锁 `tests/test_sky_camera_lock.py`（21 项）。
  4. 云台跟随替身欠账销账（`5770ba5`）——97683ab 多普勒改动给跟踪循环加
     `downlink_freq_hz()` 回退，替身 SimpleNamespace 未同步致两红；
  5. 瀑布顶缘频段彩带（`9bcafec`）——bandplan.json schema「谱面+瀑布色带」
     瀑布半边兑现：4px 顶缘条复用既有几何/色/开关机制，3 新守卫全绿；
  6. 红账归因（2026-11-04）：`test_identity_redline` 红=本文件未提交稿的
     提醒文字里出现了行踪类措辞字面量（即扫描器自己的规则词），被自家
     扫描器自指命中（HEAD 干净，`git grep` 无命中）；本条已改写为不引字
     面量，第 79 行那处属对方在途稿，按规则 2 未代改。
     `test_dvbs2_datafield_vcdu` 红=未跟踪在途文件。
     `test_gui_real_hardware` 红=**归因两次修正，最终结论：并发抢占的环境态，
     非产品缺陷**。演化：① 先判"上次 fatal crash 遗留 USB 句柄卡死、需物理
     重插"——错，杀僵进程后设备立刻可开，已撤回；② 再判"挂死进程里泄漏的
     `mbdsdr-ring-producer` 线程攥着设备"——只对那一次成立，不是通因；
     ③ 2026-11-04 分离实验定案：
     - X 建窗无辜：零次 open 前提下建 MainWindow，随后连开两次全 OK
       （我先前"构造期攥设备"的说法一并撤回）；
     - `RTLSDRBackend._probe_tuner_type` 无辜：它确实用 ctypes 直接
       `rtlsdr_open/close`（所以钉 Python 层 `RtlSdr.open` 抓不到），但
       probe→立刻 open 连试 6/6 全 OK；
     - 安静环境失败率 0/20（enumerate→open 循环），独占单跑
       `test_gui_real_hardware` 1 passed / 16.3s；
     - 失败只出现在有并发负载的时刻（A/B 两组并跑期间、以及紧随其后）。
     **诚实保留**：实验 Y 里出现过 1 次 enumerate→open 的间歇 ACCESS(-3)
     （3 次中 1 次），故不能完全排除存在低概率竞争窗；但当前证据不支持
     "产品必现缺陷"，按环境抢占记账。**给 hw 巷道的建议**：接入层若要对
     ACCESS(-3) 做状态词区分，"设备刚被枚举/被别的进程短暂占用"与"权限
     不足"值得分开报，前者可短退避重试。教训同前：ACCESS(-3) 先查有没有
     别的进程/线程/并发跑测占着，别先怪 USB 状态或物理重插。
- **所有物**：`desktop/bandplan.json`、`desktop/spectrum_widget.py`、
  `tests/test_spectrum_bandplan.py`、`desktop/rf_sky_view.py`、
  `mbdsdr_ai/workflow_engine.py`、`mbdsdr_ai/agent.py`（工作流注册段）、
  `mbdsdr_ai/scheduler.py`（文档串）、`mbdsdr_ai/core/controller.py`、
  `mbdsdr_ai/orbit.py`、`tests/test_workflow_engine_no_presets.py`、
  `tests/test_sky_camera_lock.py`
- **禁区（对方在途，绝不碰）**：`cpp/CMakeLists.txt`、`scripts/package_windows.ps1`、
  `cpp/cmake/*`、`scripts/smoke_exe_no_popup.ps1`、`docs/WINDOWS_POPUP_PLAYBOOK.md`、
  `.github/workflows/ci_windows.yml.disabled`、各 build 树、批量 exe 冒烟；
  不跑有声/弹窗的东西
- **观察到的 CI 红（65eea2d，均非本会话引入）**：
  1. ~~cpp/ctest `engine_integration`（slow）~~ **已销账（fbbe5c8）**——
     本地单跑 3 次全过、全量 ctest 162/162 绿，判定 CI runner 环境特有
     flaky，非代码真错；
  2. ~~tests/(B) `test_identity_redline` 误伤~~ **已修（fe43c74，WorkBuddy）**——
     49dc00d 入库的金样 `tests/golden/dvbs2_ldpc_vectors.json` 里 SHA-256
     摘要十六进制串内嵌 11 位号码形态（摘要巧合，非 PII）；扫描器学会忽略
     长 hex/base64 token 内的数字巧合（散文里的真号照咬，双向钉扎）
- **收工状态**：全量 pytest 六面全绿（数字见提交说明）；彩带 v2 补强
  待推送（本文件提交后随第二提交 fast-forward）；真机肉眼验收未做

- **2026-11-04 增补（本窗口）**：
  1. `5770ba5` 修 `tests/test_gimbal_follow_wiring.py` 的替身欠债——
     WorkBuddy `97683ab` 后主循环在多普勒补偿关闭时会调
     `_loop_sat_tracker.downlink_freq_hz()`，替身没复刻该接口 → 2 红。
     已补，11 passed（monkeypatch 纪律：替身必须复刻原函数副作用）。
  2. `9bcafec` 瀑布顶缘频段彩带（bandplan.json schema 承诺的"瀑布色带"
     半边）+ 3 条像素级测试；`tests/test_spectrum_bandplan.py` 38 passed。
  3. `8fae7d5` **根治 tests/ 大套件同进程整跑二次方挂死**（归因全程实测，
     非推测）：py-spy 两次采样都落在 `Theme.apply → app.setStyleSheet`，
     但样式守卫本身是好的（实测 apply 仅 0.5ms，触屏后缀未击穿）；真因是
     **已关窗口被两条全局单例钉子钉住不回收**——`ui_actions/sky_actions`
     的 `bus.executor`（窗口绑定方法）+ `PanelRegistry._widgets`（10 块面板
     强引用）。实测每建一个 MainWindow，topLevelWidgets 净增 39 且永不回落
     （0→39→…→234），构造耗时 2.8s→16.6s 线性上涨。
     产品侧在 `_release_runtime_resources` 对称摘钉（三件事→五件事）+
     `PanelRegistry.detach_widget` 条件摘除；测试侧恢复 AGENTS.md 对策①
     丢失的模块级回收 fixture，并修正其机制：`deleteLater+processEvents`
     **实测完全无效**（39→39 空转，DeferredDelete 按投递层级延后），改
     `sendPostedEvents(None, DeferredDelete)` 后清零（78→0）。
     验证：原挂死组合 47 passed（此前 300s 被杀在 57%）；模块级窗口
     fixture 两文件 36 passed / 14.7s 未被误拆；接缝五文件 91 passed；
     新增 5 条钉扎 + 反证探针确认修复前语义下断言会红（非恒绿）。
  4. **全量 A/B 分组回归跑通，无挂死无 crash**（对策②，两个独立进程并跑，
     `-p no:randomly`，日志全落盘 D:/mbdsdr/output/_full_A.log / _full_B.log）：
     A 组 **1561 passed / 4 skipped / 0 failed**（12:50），
     B 组 1484 passed / **2 failed**（14:15）——两条红均已归因，非本窗口引入：
     `test_identity_redline`=未提交稿字面量自指（HEAD `git grep` 全仓干净，
     CI 按提交内容判是绿的）；`test_gui_real_hardware`=并发抢占同一根
     物理棒子（安静环境独占复跑 1 passed / 16.3s，见上条 ③ 的完整证据链）。
     **踩坑记账**：`scripts/ci_pytest_groups.py` 打印的是 Windows 反斜杠 +
     CRLF 路径，直接喂 pytest 会得到 exit 4「no tests ran」——必须先
     `tr -d '\r' | tr '\\' '/'`。
  5. **B 组单独重跑（无 A 并跑，15:07 收官）+ 两条红的最终归因**
     （日志 D:/mbdsdr/output/_full_B2.log）：**1440 passed / 2 skipped /
     2 failed，11:05**——比并跑版更快（14:15→11:05），说明二次方挂死确已根治。
     但两条红**照旧复现**，据此把归因改准（先前"A/B 并跑抢占"的说法太窄，
     现予以更正）：
     - `test_gui_real_hardware`：ACCESS(-3) 在 B 组**独占**跑时依然出现，
       同一时刻你们正在跑 hw 车道测试（该窗口内连推 12 个提交）。故真因是
       **任何并发进程持棒**，不限于我自己的 A/B 并跑。跑完后安静环境独占
       复跑：**1 passed / 13.75 s**（连同前次 16.30 s，两次全绿）。
       **对策写进 AGENTS.md**：真机用例只在大套件静默时单独验，别拿大套件
       的红去改产品代码。
     - `test_identity_redline`：命中已从"两处测试文件"收敛成**唯一一处＝台账
       自己那一行**（`docs/COORDINATION.md:79` 照抄了规则字面量）。复核
       `tests/test_doppler_link.py` / `test_sky_camera_lock.py` 现均 0 命中
       （对应会话已修），全仓 `git grep` 仅剩台账自指。已按 AGENTS.md 第 1 条
       「规则字面量用拼接构造，门禁自身不命中自己」改写为只描述类别；
       改完 `tests/test_identity_redline.py` **3 passed / 12.13 s**。
       注意这条红只在工作树存在（HEAD 干净），但本地跑测会一直红，
       所以不能只靠"CI 绿"放着不管。
     **结论**：真机在插时，大套件里的真机用例会因并发占用随机假红；
     CI 无硬件自然 skip，不受影响。
  6. **`f754d8c` 行星接入恒星同一条星等→亮度模型**（SWE §8.1 第 7 条
     `core_get_point_for_mag` 语义，顺着 `_stars.star_draw` 已有接缝扩，
     未自创框架）：修复前 `_draw_planets` 恒画固定 4px 实心点 + 固定 alpha
     标签，金星(-4.70) 与海王星(+7.83) 视觉同大、裸眼不可见的行星照样画
     （视觉编码说谎）。现在走 `star_draw(有效星等+消光, Bortle)`，超极限
     如实不画，历表缺星等不画（不冒充亮度）。配套去掉 `star_draw` 的 frac
     上限 clamp——恒星从不触发（最亮 -1.44），行星一接就全触发；恒星域
     逐点非回归对拍（-1.5..8.0 × 四档 Bortle，760 点）钉死不变。
     **实测**（真历表 2026-10-07T21:00Z）：木星 2.39px/bright=1.00 > 火星
     2.19/0.64 > 土星 2.16/0.61 >> 天王星 1.10/0.06，海王星 7.81>极限 6.50
     不画；肉眼验收图 D:/mbdsdr/output/_planet_pair.png（木星明显更亮带辉光）。
     **钉扎 9 条**（7 像素/源码 + 2 模型），反证探针确认旧语义下三条像素断言
     必红（旧码金星=土星=30384、旧码海王星 30384、旧 star_draw 金星 r==木星 r）。
     本车道 13 文件 164 passed / 14.15s。
  7. **`2421f21` 拾取门与绘制门合一**（承上条：绘制门生效后，"看见的才点得中"
     必须同步）：`_find_body_at` 的可见性门槛此前自成一套——恒星写死
     `vmag > 4.5` 剔除（真星表 Graffias 4.84 在 Bortle 3 画得出却点不中，
     "点星即听"在 4.5..极限 的暗星上静默失灵），行星完全不设门（海王星 7.83
     如实不画，点它所在像素却弹出信息卡）。现在按 kind 走绘制层同一条规则
     （恒星 alt>=0 / 行星 alt>=-1 / DSO 是用户主动选中的目标不设门）+
     `star_draw(vmag+消光, bortle)` 的 r>0；顺手堵住历表缺星等时候选带着
     None 进评分算式的潜在 TypeError（绘制层早已"缺星等就不画"）。
     **反证**（旧实现从 git 对象里 exec 出来对拍，不改仓库；探针
     D:/mbdsdr/output/_probe_pick_gate_reverse.py）：四处行为翻转，全部朝
     诚实方向——海王星@B3 planet→None、天王星@B9 planet→None、
     假星 5.20@B3 None→star、假星 4.84@B3 None→star；对照组（金星@B3、
     天王星@B3、5.20@B9）新旧不变。**钉扎 6 条**
     （`TestPickGateMatchesDrawGate`）；本文件 13 passed，sky/拾取车道
     7 文件 106 passed / 7.19s，身份红线门 3 passed。
     另：`f754d8c` 后的接缝五文件回归 **83 passed / 7:48**
     （D:/mbdsdr/output/_verify_seams2.log），无回归。
  8. **`188a42c` 月球视直径单位纠正**（承第 6/7 条同一"视觉编码不说谎"主线）：
     `planets.compute_moon` 把角直径写成 `3474.8/(dist_km/384400)`——分子量纲是
     km（月球物理直径），却被塞进 `angular_diameter_arcsec` 当角秒用，缺 206265
     弧度→角秒因子，平均距离下报 ~3475″、真值 ~1865″（31′），虚高 1.86×；
     消费方 `rf_sky_view._build_body_info:2963` 信息卡「视直径」当场说谎。改成
     `206265*3474.8/dist_km`，与行星支（`_ANGULAR_DIAMETER_ARCSEC_AT_1AU/dist_au`）
     同口径。**反证**（两年每三天扫一点共 244 点，探针实测）：旧式全落
     3261..3706″（每点都 >2100，钉扎必红），新式全落 1750..1989″（覆盖近/远地点
     真实极值 2010″/1760″）。钉扎 `tests/test_planets.py::test_moon_angular_
     diameter_in_true_arcsec_range`；本文件 5 passed/1 skipped，sky/planet +
     solar_system 车道 66 passed/1 skipped/4.79s，身份红线门 3 passed。
     **观察到未触碰**：月球在 `_find_body_at` 里作为 kind=planet/key=moon 候选可
     拾取（compute_all 含 moon），但 `_PLANET_STYLE` 无 moon 项 → 信息卡标题回退
     英文 "moon" 而非"月亮"，且 `_draw_planets` 不画它（另由 `_draw_celestial_
     bodies` 画大标记）。是既有妆面小缺口、非本次引入——**已随即在第 9 条处理**。
  9. **`02ecce0` 月球信息卡正名 + 照度行**（承第 8 条观察）：`_build_body_info`
     行星支对 key=="moon" 显式补齐标题"月亮"/类别"Moon"/别名"Luna"，不塞进
     `_PLANET_STYLE`（塞进去会让 `_draw_planets` 给月球多画一颗行星点，与
     `_draw_celestial_bodies` 大标记重叠）；desc 补「照度 N%」＝0.5*(1+cos 相位角)
     （直接从历表已有相位角导出，不引入新模型），理论名如实标"Meeus ch.47 月球
     理论"。**反证**（探针 D:/mbdsdr/output/_probe_moon_card_reverse.py，旧实现
     从 git 对象 exec 对拍）：四字段翻转 title moon→月亮 / kind_label Planet→Moon /
     aliases ['Moon']→['Luna'] / desc 增照度行。**钉扎 2 条**
     （test_moon_pick_card_is_moon_not_planet + 对照 test_planet_pick_card_still_
     planet：火星仍标 Planet、不混进照度行）；sky/拾取车道 6 文件 62 passed /
     17.09s，身份红线门 3 passed。
- **✅ 已销账：`README.md` 0 字节事故（本窗口 14:53 复原）**：14:35 发现工作树被
  截成 **0 字节**（HEAD 为 297 行），当场按协调规则 2「谁在途谁所有」只登记不覆盖。
  此后 18 分钟内你们连推 12 个 hw 车道提交、无一触碰 README，0 字节文件也不可能
  承载在途工作，遂 `git checkout -- README.md` 复原（17780 字节，工作树干净）。
  **信息损失为零**（内容全在 HEAD）。若你们本就在准备重写 README，直接覆盖即可，
  本条不影响。留此记账是为说明：不是我改的，是复原的。

### 真机七环验收会话（本窗口，2026-11-03 19:00 登记）
- **收工**：**6 环 PASS + 1 环 UNVERIFIABLE**，无阻断缺陷。报告
  `docs/real_hw_acceptance_20261103.md`，可复跑脚本 `scripts/real_hw_acceptance.py`
- 环 6（数字模式 OTA）不可验原因：FC0012 上限 948.6 MHz（ADS-B 物理不可达）+
  会话窗内无 APRS 台活动/无卫星过境；`--ring 6` 可在有过境时复跑
- ~~登记待办（一般）：`get_satellite_pass` 不透出 TLE source/epoch，建议补字段~~
  **已销账（2026-11-04 15:10 复核）**：`controller.py:969-970` 已透出
  `tle_epoch`（`orbit.tle_epoch_iso(line1)`）与 `tle_source`（内置回退表标明
  来源），`rf_sky_view.py` 三条来源路径（用户 TLE 文件 / Celestrak 在线缓存 /
  内置表）各自如实标注并在信息卡显示「TLE 来源」；钉扎在
  `tests/test_sky_camera_lock.py:322`（含 `assert "builtin" in out["tle_source"]`）。
- **观察到未触碰**：`cpp/CMakeLists.txt`（M）、`cpp/tests/test_lrpt_real_cross.cpp`
  （??）在途，归属待认领者登记
- **禁区**：同星象仪会话禁区 + 彩带三文件；未改任何公共构建/冒烟脚本

### 悬账备忘（不属于任何人，认领者标注）
- ~~dump1090 全量解码清单对拍~~ **已闭环（9d3efea）**：dump1090 本尊编译当
  裁判，10/10 参考帧 + 全帧 CRC 语义钉死
- 本机自采 OTA 解码记录仍为零（负证据不撤销，对外表述按 capability-map 口径）
- 便携包需验证「真 USB 直连构建」打出的包（非 stub）
- ~~bch 独立 KAT（dvbs2_bch_vectors.json）仍 workspace-only~~ **已销
  （347d09f）**：BCH 独立 KAT + 标准参数摘录入库
- ~~LRPT 图像段重组真录链——被连帧能力阻塞~~ **阻塞已解除（本批）**：
  16 条 VCDU/宽窗素材已足（全过境约 2-3 倍）；重组器
  （VCDU→M_PDU→空间包→JPEG 段）待认领/下一轮
- LRPT 连帧的两个诚实未启用件：RRC 匹配滤波（sps=2 前馈下反而降 VCDU）、
  Gardner 定时环（原型 τ 发散）——留档 lrpt_modem.py 开关默认关，若做
  4+ sps 重采样 + 修好的 Gardner 可再战（预期再抬 VCDU 上限）
- **月相渲染（下一战线，方案已备未动工）**：`_compute_sun_moon` 已把
  `illum`（被照比例）送进 `self._moon`（rf_sky_view:4113），但
  `_draw_celestial_bodies`（:2475-2482）画月球仍是固定 6px 实心圆，illum 到
  绘制层即被丢弃——新月/满月画得一模一样（同"视觉编码说谎"族，本批第 6/7/8/9
  条的延续）。方案（标准天文渲染，非自创）：亮区多边形＝亮缘半圆
  (r·cosψ, r·sinψ) + 晨昏线半椭圆 (−r·cosα·cosψ, r·sinψ)，ψ∈[−90°,90°]，
  cosα=2·illum−1（满→整圆、弦→半圆、新月→零面积）；朝向＝屏上月→日方向角
  rotate。**关键风险**：夜间月球在地平上时太阳在地平下，其屏幕投影可能落在
  相机背后/非有限 → 朝向须用 az/alt 空间方向换算而非直接投影差，需多工况肉眼
  验收（满/弦/新月 × 日夜）。照度信息卡行（第 9 条）已先落地，形状渲染待做。

---

## 三、接下来要办啥（队列）

1. **[TRAE]** `scripts/smoke_exe_no_popup.ps1` 收口提交
2. **[TRAE]** 便携包线收口：package_windows.ps1 验真机构建打包，旧 stub zip
   问题闭环
3. ~~[我] dump1090 全量解码清单对拍~~ **已闭环（9d3efea）**
4. ~~CI 观察项：d1efe19 mbdsdr_ai 红~~ **已销（49dc00d）**
5. **[两边]** 共有文件再冲突时按 hunk 拆分提交（脚本先例：
   extract_hunk 姿势，见 2026-11-02 会话记录）
6. ~~[我] C++ LRPT 双实现对拍~~ **已闭环（8b66410）**
7. **[认领]** P2 前端缺口（gaplist 第三批）：频率轴滚轮缩放、FFT 仪器化
   （RBW/瀑布时间跨度）——文件在 spectrum_widget.py，星象仪/彩带会话
   若要做手势线请认领，否则协议真值线下一轮接手
8. ~~[我] LRPT 图像段重组真录链~~ **已闭环（本批）**：真录图像出图
   （40-80s 38 包/13 行 + 全过境 49 包/17 行/551 块），结构真值与
   woven 约定裁决见 capability-map
9. **[我，实验完待收尾]** LRPT 连帧上限再抬：4sps 矩阵已测——**全配置
   不敌 2sps 基线**（结果在 output/tmp/lrpt_4sps_matrix.log），剩余杠杆：
   ① Gardner 修复版实测（代码已改默认关）② 擦除辅助 RS（2e+f≤32，
   从 75 条部分帧救 VCDU，设计已论证）。图像覆盖率 3-8% 要抬靠 ②。
10. **[认领]** 图像链收尾件：部分-RS 帧的包级利用（形状判据过滤）、
    合成链约定统一到 woven（改 lrpt_pipeline.build_cadu 往返切分 +
    decode_cadu 默认翻转 + C++ lrptDecodeCadu 同迁 + 全部 concat 钉
    随迁）、图像内容级真值对拍（对照第三方产物）

11. ~~**[我] 产品工程阶段 A 合入仓库**~~ **已闭环（5a977026，11-05 22:00）**：
    `web/product/` 76 文件 / 36 MB（分层前端、构建器、静态守卫、投影自检、
    36 篇文档、HYG v4.1 源 CSV + SHA256、自包含 dist、NOTICE.md），
    `docs/product/README.md` 只作索引不复制漂移副本。
    入库前按纪律重跑 `run_all.py`，抓到 HEAD 漂移：仓库 `f0c8fa25`/`dbf2a3e7`
    把 DSO 换成 OpenNGC 的 `dso_catalog.json`、退役 `_DSO_ROWS`，
    产品同步链已随之改读 JSON（校验 schema/许可、RA 已是度、未知量保持 null）。
    全部脚本经 `paths.py` 解析 ROOT/REPO，仓库内 `cd web/product && python run_all.py`
    九步 ALL PASS；`.gitignore` 否定仓库根全局 `build/` 规则保住构建源码。
    提交走 `--only -F - -- web/product docs/product`（stdin，不落 C 盘临时文件），
    机械闸放行，提交后 `git diff HEAD -- <本次路径>` 为空，别线在途 M/?? 一个没碰。
12. **[认领] 阶段 B：`window.mbdsdrBridge` → `mbdsdr_ai/tool_registry.py` 运行时桥接**。
    仓库里目前**没有** `mbdsdrBridge` / QWebChannel 实现（grep 无命中），
    产品 `MB.CONTRACT` 8 个方法在离线时走诚实空态。要做的是新建桥接层 +
    `arm_manual` 人工门 + 对拍测试，**不要顺手改 desktop/main_window.py**
    （操作逻辑线在途）。这是独立一笔，不算进阶段 A。

---

## 四、协调规则（血泪换来的，别再踩）

1. **开工三件套**：读本文件 → `git log --oneline -8` → `git status`，
   分清哪些是对方在途、哪些是自己的。
2. **谁在途谁所有**：不碰对方未提交编辑；不确定归属就不提交那个文件。
3. **共有文件按 hunk 拆**：`cpp/CMakeLists.txt` 这类高频混账文件，
   只把属于自己的 hunk `git apply --cached` 进暂存区。
4. **代码与金样必须同一提交走**：改编解码语义时 fixture 重生成物（JSON/LLR/
   IQ raw/期望向量）随代码一起交，否则 CI 拿旧金样打新代码必红（一天 5 起）。
   测试引用的金样**要么入库（tests/golden/），要么缺席必须诚实 skip**——
   收集期硬读仓库外路径是 CI 整包团灭的固定姿势（2026-11-03 又中一回）。
5. **push 会取消在跑的 CI run**：需要裁决就等结论再推；连续推会让裁决永远出不来。
6. **HELLO 脏提交**（另一会话周期性 `git add -A; git commit -m HELLO`）：
   动手前后核对 HEAD 与作者；未推送的 HELLO 按先例弃置重提。
7. **弹窗/DLL 问题**：三棵真构建树（build_cpp / cpp/build / cpp/build-local）
   已布防运行库，直跑零弹窗；`.tmp/dll_audit/sandbox` 是审计道具（故意裸奔），
   不要往里补 DLL。
8. **收工必须更新本文件**「当前在途」和「队列」两节——这是下一轮的开工状态。

### 测试纪律（2026-11-05 落地，硬规矩）

1. **测试里禁止真模态**。`tests/conftest.py` 在会话级 autouse 夹具里默认全局
   打桩 `QDialog` / `QMessageBox` / `QFileDialog` / `QInputDialog` 的 `exec()`
   与 `QMessageBox.warning/information/critical/question/about`、
   `QFileDialog.getOpenFileName/getSaveFileName/getExistingDirectory`、
   `QInputDialog.getText/getInt/getDouble/getItem`：被调到就记一条
   「测试里出现真模态：<类名>」并立即返回 `QMessageBox.No` / 空路径。
   需要真弹窗的用例必须显式 `@pytest.mark.allow_modal`，**默认禁止**。
   自检：`tests/test_modal_dialog_guard.py`。
   代价：offscreen 下 `QMessageBox.exec()` 永不返回，已两次把整条 Qt 互斥锁
   占死（第一次 50 分钟：`_show_error_dialog -> box.exec()`；第二次录音用例
   只打桩 `QMessageBox.warning`，真实路径根本不走 warning）。
2. **测试禁止写用户真实配置**。直接 `MainWindow()` 的 fixture 必须请求
   `tests/conftest.py::isolated_profile`——它把 `HOME/USERPROFILE/APPDATA/
   LOCALAPPDATA`、`QSettings`（IniFormat + 临时 path）与 `layout_store`
   单例全部重定向到 `tmp_path`。会话级 autouse 守卫在 `pytest_configure`
   对真实 `desktop_settings.json` 取 `sha256 + mtime_ns + 目录清单`，
   会话结束有任何变化即失败，并打印文件与字节差。
   守门：`tests/test_ui_settings_isolation_guard.py::test_session_guard_is_active`。
   现场：`test_record_dir_consumption.py` 曾把
   `...pytest-1486\...\rec`、`Z:\nope` 写进用户配置，用户每次启动看到
   「录音目录不可写」假报警。
   现场（HIGH，2026-11-05 守卫实跑）：
   - `tests/test_ui_stability_resources.py`：`mw` 没请求 `isolated_profile`，
     直接 `MainWindow()` 且 `close()` 触发 `settings.flush()`。守卫实测
     1074→1074 bytes、sha256 不变、仅 mtime 变（**+0 bytes 的同内容写入
     同样判红**）。**已修 `f0f68cc6`**（`mw(qapp, isolated_profile)`），
     连跑两次守卫绿、真实配置 sha/长度/mtime 全不变；**没有把守卫改成
     忽略 mtime**。
   - `tests/test_device_switch_flow.py`：**不是写配置的文件**。单文件复跑
     mtime 不变；当时红是 `ModulePanel` 在途改名（`_on_device_selected` 缺失）
     导致用例失败，会话级守卫错误只是挂在该会话最后一条用例上——归因时
     按「文件路径 + 字节差 + fixture 链」对，不能看报错挂在谁头上。
3. **构造 Qt 控件必须声明 `qapp`**（或模块级 `qapp` fixture）。子集单跑没有
   QApplication 时 Qt 直接 abort `0xC0000409`（`SettingsPanel(...)` 已中过）。
4. 新增/修改测试后，至少跑一次**目标模块单文件**（不是只跑全量）——顺序依赖
   与缺 `qapp` 只在子集单跑时暴露。

---

## 五、变更日志

- 2026-11-03 00:20 初建（WorkBuddy）。背景：FT8/ADS-B 真值对拍 + CI 全绿 +
  DLL 布防收口后，用户要求多会话并行工作模式下立协调台账。
- 2026-11-03 10:30（WorkBuddy）：销 mbdsdr_ai 红（49dc00d，DVB-S2 LDPC
  金样入库 + 仓库外金样收集期炸裂根治）；队列/TRAE 在途刷新。
- 2026-11-03 10:55 事故记录（用户投诉扬声器放测试音 + CMD 刷屏）：TRAE 的
  smoke 全量 run（10:35 起，旧版脚本）裸跑 test_engine_audio_e2e 打真声卡
  （中 C+两八度脉冲音），161 exe 串行每个冒一个 CMD。TRAE 10:38 已给
  smoke_exe_no_popup.ps1 打补丁（强制 MBDSDR_NULL_AUDIO=1 + WindowStyle
  Hidden），但旧 run 在内存里继续跑旧代码——WorkBuddy 10:5x 掐掉僵尸 run。
  **硬规矩追加：任何批量直跑测试 exe 的脚本必须强制 `MBDSDR_NULL_AUDIO=1`
  （可同时保持 PATH 干净做 DLL 审计）**，改脚本后必须等旧 run 结束/掐掉
  再起新 run，否则用户机器上双鬼拍门。
- 2026-11-03 19:50（WorkBuddy）：LRPT 连帧破层批完成本地验证
  （Costas 闭环 + FFT 帧同步 + Viterbi 向量化 + 松阈值/ASM 验证，
  宽窗 16 条 VCDU 对旧 2 条）；悬账销 2 项（BCH KAT 入库 347d09f、
  C++ LRPT 对拍 8b66410）；队列加图像段重组（可证级硬通货）与
  连帧上限再抬两条。
- 2026-11-03 22:40（WorkBuddy）：LRPT 图像链破层批——lrpt_image.py
  全链 + **woven 约定裁决**（decode_cadu order 参数化，concat=合成链
  口径/woven=真录真值序）+ 真录图像出图 + 图像金样 6 项。CI 9dd272d
  全绿（行尾根治生效）。队列 #8 销账，收尾件拆出 #10。

- 2026-11-05（接续线）：`mbdsdr_ai/agent.py` 自编程四工具从 arm_manual 自报改为外部确认一次性 `confirm_token`（`tx_guard` 单一事实源），schema 移除 arm_manual；`desktop/mcp_protocol.py` 仅改 schema 注入（执行层真人确认不变）。**报备审计线**：本次动到 `agent.py`，但只动 `_register_code_editor_tools` 段（约 3078-3244），未碰 catch-all；若审计棘轮同段在途，按 hunk 归属拆分，别整文件覆盖。
- 2026-11-05（接续线，**给操作逻辑线**）：PTT 置灰问题已修，按钮状态可复位。`sdr_backend.SDRBackend` 新增
  `supports_tx()`（单一事实源：设备声明 ∧ 本类确有 `write_tx` ∧ 运行时就绪）
  与 `tx_capability()`（declared/implemented/ready/verified 四态）。HackRF / USRP /
  Pluto 三个后端已实现真实写流（libhackrf `hackrf_start_tx` 回调队列 / UHD
  `get_tx_stream+send` / IIO `Buffer.write+push` 或 Soapy `writeStream`）。
  **三者 `device.tx_verified=False`：代码路径未上真机，UI 必须显示「未验证」而不是「支持」**；
  `SDRBackendManager.list_devices()` 已带 `supports_tx` + `tx_capability` 供 UI 取值。
  RX-only 后端（RTL/AI-SDR-mini/Soapy/FileIQ/NetworkIQ）保持 `supports_tx=False`。

- 2026-11-05（硬编码与参数治理线）：新增 `docs/hardcoded-parameters.md`
  （110 条语义常量普查 + A/B/C 逐条裁定）。收口 2 处：把裸眼极限星等公式从
  3 份副本收成 `stars.naked_eye_limit_mag()`（`star_catalog` 改为导入同一函数对象），
  并把 `bortle_dim_factor` / `MAG_SPAN_TO_FULL_BRIGHT` / `DEFAULT_BORTLE` 各收一处；
  差分探针 242,011 个 (mag,bortle) 点与 HEAD **逐点 0 差异**。
  新增 `tests/test_hardcoded_parameter_registry.py`（16 passed：单一来源断言 +
  B 类锁定值 + C++ tokens.h 对拍）。**报备**：只改
  `mbdsdr_ai/skyengine/{stars,star_catalog}.py`，未碰 main_window / rf_sky_view /
  spectrum_widget（那三个只出 patch 建议 P-1..P-7）。
  发现 11 组"同一件事两套数"，最严重三条待裁定：静噪门限五套数且量纲不同、
  过境最低仰角 5° vs 10°（tracker.next_pass 把 5 传进 predict_passes 的 10）、
  峰值判定 6dB/8 个 vs 15dB/12 个。未推送。

- 2026-11-05（可发现性/帮助线）：新增 `desktop/capability_catalog.py`、
  `desktop/shortcuts_panel.py`、`desktop/help_panel.py`，`PanelSpec` 加 `summary`；
  `shortcuts.py` 只追加 5 条（`view.all_sky`/`view.reset_position`/`help.*` ×3，
  **未删任何既有键位**）。能力目录 53 项（可用 47 / 需条件 1 / 暂不可用 5）
  全部由面板/解调/数字解码/长尾解码器/AI 工具域注册表实时生成；
  `tests/test_discoverability_help.py` 15 条逐 source 反查注册表，
  `test_shortcuts_guard.py` 10 条全绿（含原 2 条存量红灯：A 键与重置窗口位置）。
  **报备 main_window（按 hunk 拆提交，未带走同文件在途的设置面板改动）**：
  `QKeySequence("A")` → `_key("view.all_sky")`；新增「帮助」菜单 3 项；
  新增 `_show_help_panel` / `_show_shortcuts_panel`（非模态）。
  未改 `rf_sky_view.py` / `spectrum_widget.py` / 设置面板。盘点见
  `docs/discoverability.md`。未推送。

- 2026-11-05（硬编码与参数治理线，裁定落地 `12a43a0`）：三轮裁定已落地两条、
  受阻一条。
  ① **静噪**统一到「解调后音频 RMS」：新增 `mbdsdr_ai/squelch.py` 唯一常量
  （`-100..-20`，默认 `-50`）；`receive_pipeline` 的 WFM（原全带宽 IQ 功率）
  与窄带（原 VFO 信道 IQ 功率）**都改为先解调、再按音频 RMS 门控**；
  `control_panel` 滑杆改范围/默认并标量纲（"静噪(音频)" + tooltip）。
  `analog_demod` 仅加注释：其 IF 域门控是另一概念，**未改行为**。
  C++ `kSquelch*` 本已同域，未改。**报备待办**：`main_window._squelch_db=-80`
  仍是权威初值（P-8a），旧配置迁移提示归设置线（P-8d）。
  ② **过境仰角**：`sat_passes` 拆 `VISIBLE_MIN_ALT_DEG=5` / `PASS_MIN_ALT_DEG=10`；
  `next_pass()` 默认 5→10（不再"5 调默认 10"）；时空面板/控制器/卫星面板改引用
  常量，列表旁明示「按峰值 ≥10° 筛选」。跟踪退出仍 5°，算法/采样未动。
  **残留**：`sat_consistency.py:141` 仍传 5（P-2c）；`rf_sky_view` 私有 10° 字面量
  待 P-2b。③ **默认采样率 2.4 MS/s 未落地**：值在 `sdr_backend.py`（他线在途）
  且被基准测试钉住，必须与"能力回落 + 实际值回显 + `osmosdr_source` 1.024 收口"
  同批（P-8b），否则就是新一代滑杆骗人。
  另三条结论：峰值两侧基准同为 median → 统一 15 dB（Python 待 P-1，C++ 不动）；
  dB 默认量程两侧同为 dBFS，仅视窗差异 → 默认交自动量程、各自锁夹取边界；
  无数据哨兵 `-120` 落在量程内 → Python 改 NaN（P-8c），C++ `-100` 是真实读数。
  文档 §10 记录每处"口径变了/没变"。测试：注册表 19 passed，合并套件 69 passed。
  **报备**：只动 `squelch.py` / `receive_pipeline` / `control_panel` / `analog_demod`
  （仅注释）/ `sat_passes` / `sat_tracker` / `sat_track_panel` / `new_spacetime_panel`
  / `core/controller` / 注册表测试 / 本文档；未碰 main_window / rf_sky_view /
  spectrum_widget。未推送。

- 2026-11-05（硬编码线，补交 `6ed9ee7`，接上条 `12a43a0`）：③ 采样率已落地，
  不再是受阻项。`sdr_backend.DEFAULT_SAMPLE_RATE_HZ` 2.048M → **2.4 MS/s**
  （"首选，不是保证"）；设备不支持时 `nearest_sample_rate()` 回落，
  `status.sample_rate_hz` 回报实际值。三套 RTL 基线收口：`osmosdr_source`
  1.024M → 2.4M、`RtlTcpBackend` 硬编码 2.048M → 引用同一常量、
  `desktop/constants` fallback 同步。调谐步进三方本已一致（D-2 撤回），未改。
  HackRF/USRP/Pluto 甜点默认未动，RTL 11 档明细未动。
  测试：受影响 7 套件 **100 passed**（新增/更新期望 5 处）；本线核心套件
  95 passed，agent 采样相关 21 passed。文档 §10-3 已改为"已落地"。
  **报备**：本提交仅 `sdr_backend` / `constants` / `osmosdr_source` /
  `rtltcp_client` / 5 个测试期望 / 文档；未碰 main_window / rf_sky_view /
  spectrum_widget。未推送。
  另：`control_panel.py` 的 UX 线阻塞已在 `12a43a0` 解除，该文件工作区干净。

- 2026-11-05（可发现性/快捷键线，本笔修复 HEAD ImportError）：**同文件并发事故**。
  `desktop/main_window.py` 被另一条线整文件提交时，带走了本线尚未提交的
  `owns_local_shortcuts` 导入，而 `desktop/spectrum_widget.py` 侧实现仍在
  工作区 → HEAD 上 `python -m desktop.main_window` 直接 ImportError，应用起不来。
  本笔把控件侧实现、`shortcuts.py`（spectrum scope 13 条局部键）与测试同批落盘，
  并用 `tests/test_shortcut_focus_priority.py::test_main_window_imports_without_shim`
  钉住「两侧符号必须同时存在」。
  **新增纪律：同一文件同一时间只允许一条线写；谁先占谁写，占之前先在工作区
  声明（在本台账或会话里写明文件名与占用时段）。** 共有文件按 hunk 拆分提交时
  只允许 `git apply --cached` 自己的 hunk，**禁止整文件 `git add`**；
  也不得为抢文件执行 stash / checkout 他人改动（咨询线已因此险些丢活）。

- 2026-11-05（硬编码/参数线：棘轮修复 + 静噪重做）：
  ① 撤销 `12a43a0` 在 `DemodWorker.run` 新增的 `except Exception: continue`：
     改窄异常 `(ValueError, IndexError, FloatingPointError)` + 2 s 限流
     `log.warning` + `demod_failed(str)` 信号；其余异常（编程错误）直接上抛。
     `scripts/lint_swallow_all.py` 对 `desktop/receive_pipeline.py` 回落到
     **ERROR 18 / WARN 3**（基线）。
  ② 静噪二次裁定落地：滑杆仍是一个、刻度 `-100..-20`（默认 -50），语义改为
     「相对该模式噪声基线的余量」（-50 ≈ 6 dB）；`BaselineSquelch` 自动标定
     （模式切换 / `gain_db` 变化 / 管线重建），标定失败 `calibration_failed` +
     `squelch_state` 信号 + warning，不静默套默认值。WFM 与窄带同口径，
     **不回 IQ 功率**。6 模式 × {纯噪声/弱可读/强} × 默认 -50 实测
     `0 % / 开 / 开`；增益 +6 dB 读数 Δ`0.00 dB`。详见
     `docs/hardcoded-parameters.md` §10-1。
  ③ 遗留（不在本笔）：`gain_db` 需设置线写入 1 行；`squelch_state` /
     `demod_failed` 需操作逻辑线接状态栏；`test_swallow_guard.py` 仍有 2 项
     **他线**越线（`spectrum_widget.py` WARN 5>4、`tests/test_shortcut_focus_priority.py`
     新文件 1 ERROR），本线未触碰。
  **报备**：本笔只动 `desktop/receive_pipeline.py`、`mbdsdr_ai/squelch.py`、
  `tests/test_squelch_baseline.py`、`docs/hardcoded-parameters.md`、
  `desktop/control_panel.py`（仅 2 行注释）；未碰 main_window / rf_sky_view /
  spectrum_widget。未推送。

---

## 六、共享文件占用锁（file_claim，2026-11-05 落地）

### 6.1 当前占用

改共享文件前先写一行，**提交后删行**。表里没有行不代表可以改——先跑
`python scripts/file_claim.py status` 查操作系统锁（表靠人记得写，锁不会忘）。

| 文件 | 线名 | 开始时间 | 预计提交 hash |
|---|---|---|---|
| _（空）_ | | | |

> 归档：2026-11-05 天空线历表照度反转修复已入库 `056e073`
> （`mbdsdr_ai/solar_system.py` 三后端收敛为 `moon_illum_from_elongation`，
> 渲染层 `cosα = 2·illum − 1` 未改；新增
> `tests/test_moon_illumination_real_ephemeris.py`）；
> 上述两文件锁均已 release。

> 归档：2026-11-05 卫星过境线三笔已入库并 release：
> `08a4500`（过境仰角设置真正生效：列表门读 settings，低仰角可列出）、
> `4faddb2`（采样步长收敛到 `sat_passes` 单一事实源；ISS 24h 峰值偏差
> 旧 300/30 = 13.1 s / 0.40°，新 60/2 = 0.9 s / 0.004°；另修正
> `new_spacetime_timeline._gmst_rad` 漏项，AOS 由偏 16110 s 回到 +29.8 s）、
> `886244d`（`pass_window_hours` 真正生效：6 h / 24 h 实测 1 条 vs 5 条，
> 1 h = 0 条）。
> 涉及 `desktop/new_spacetime_panel.py` / `desktop/sat_track_panel.py` /
> `mbdsdr_ai/sat_passes.py` / `desktop/rf_sky_view.py` /
> `mbdsdr_ai/new_spacetime_timeline.py` / `tests/test_pass_gate_wiring.py` /
> `tests/test_pass_step_alignment.py` / `tests/test_pass_window_hours.py`，
> 锁均已释放；参数账本同步于 `d804cb4`（#25 接线、A-1 红表清零）。

> 归档：2026-11-05 审阅线五笔已入库并 release：
> `65163cf`（dB 默认量程统一 -100..-20）、`d4b2a80`（瀑布深度六档枚举）、
> `8d940b7`（无数据哨兵 NaN）、`0e73848`（`kFreqFineStepHz` 改名粗调 + 七档补 1 Hz）、
> `e501555`（C++ 静噪 domain 守卫 + 自动档禁用滑杆/真下发 auto + applied_by 回写）。
> 涉及 `tokens.h` / `settings_dialog.cpp` / `spectrum_widget.py` /
> `config_schema.py` / `control_panel.py` / `spectrum_engine.h` / `signal_watch.cpp` /
> `main_window.cpp` / `control_hub.cpp` 及对应 Python/C++ 测试，锁均已释放。

> 归档：2026-11-05 机制线在交付 `f197678` 期间持有 `docs/COORDINATION.md`、
> `scripts/file_claim.py`、`scripts/check_commit_scope.py`、`tests/test_file_claim.py`；
> commit 成功后已 release。实时锁以 `status` 为准。
>
> 归档：2026-11-05 设置线提交 `b742f89`（A5/A7/A8 + 导入迁移/全部恢复默认钉扎）、
> `4ef3a15`（采样率统一 2.4 MS/s，schema 派生自 constants）、`605e89d`（FFT 收紧
> 1024/2048/4096，旧 8192/16384 回落并记原因）、`f59bfe9`（A8 端到端星数用例 +
> 参数统一附录 A）；`config_schema.py` / `settings.py` /
> `settings_panel.py` / `tests/test_settings_schema.py` 已 release。
>
> 冻结表缺口复核（C）：`tests/test_hardcoded_parameter_registry.py` 在 `5ece441`
> 已同时冻结 `kPeakThresholdDefault=15` 与 `kPeakRelDb=6.0`，设置线只读复核通过，
> 未改动该文件（归频谱线）。

> 归档：2026-11-05 卫星过境线 `ff219b5` 已入库并 release：
> AI/MCP 工具面 `controller.get_satellite_pass` 去掉写死 10°/24h，改为显式注入
> `min_elevation_deg` / `window_hours`，缺省回落 `sat_passes.PASS_MIN_ALT_DEG` /
> `PASS_WINDOW_HOURS`（AI 侧单一默认源，不反向依赖 desktop settings）；
> MCP 工具 schema 同步暴露两参数并返回 `passes_in_window`；`rf_sky_view` 过境
> 标题窗口随设置显示。实测同一工具调用 5°=6 条 vs 10°=5 条、24h=5 条 vs 72h=14 条。
> 涉及 `mbdsdr_ai/core/controller.py` / `mbdsdr_ai/core/mcp_server.py` /
> `mbdsdr_ai/sat_passes.py` / `desktop/rf_sky_view.py` /
> `tests/test_controller_pass_gate.py` / `tests/test_pass_window_hours.py` /
> `tests/test_sky_readouts.py` / `docs/mcp_tools.md` / `docs/parameter-unification.md`，
> 锁均已释放。

> 归档：2026-11-05 卫星过境线 `44371b4` 已入库并 release：
> 新时空面板「刷新过境」三种空态各有可断言文案——未获取 TLE / 未配置观测站 /
> 窗口内无过境（N/X/Y 取 effective_* 实际值）；站址缺失时不再白跑 TLE 请求；
> 同步加载期间按钮置灰且拒绝重入。涉及 `desktop/new_spacetime_panel.py` /
> `tests/test_new_spacetime_panel_feedback.py` / `tests/test_interaction_wiring.py`，
> 锁均已释放。

> 归档：2026-11-05 卫星过境线 `1f0b19c` 已入库并 release：
> 删除面板里 `(0,0)` 这个「未配置站址」哨兵（几内亚湾是真实坐标，与可测范围
> 相交），改用显式 `_observer_configured` 状态位；`set_observer(None, None)`
> = 未配置（不发 TLE 请求），`set_observer(0.0, 0.0)` = 合法站址正常计算。
> 固定 ISS TLE / 2008-09-20 06:00Z / 24h / 10° 夹具下 (0,0) 实测 **2 条**过境；
> 修前同一夹具 rowCount=0 且误显「未配置观测站位置」。涉及
> `desktop/new_spacetime_panel.py` / `tests/test_new_spacetime_panel_feedback.py`，
> 锁均已释放。

> 归档：2026-11-05 卫星过境线 `78b990f` 已入库并 release：
> 站址读写边界拆分。`mbdsdr_ai/station.py` 只有一份判定 `_evaluate`，
> 新增 `trust_for_calculation()`（显式 gnss/user/config 的 (0,0)/海上坐标
> 降为 warning + 照常计算）与 `trust_for_tx()`（保持严格）；
> `evaluate_station()` 与 controller `station_status()` / tracker
> `current_position()` 对外语义不变（仍严格）。14 个只读消费点改走计算口径，
> 写/控制点（tune_satellite、多普勒补偿开启、doppler_track、main_window
> 跟踪轮询）不动。固定夹具下显式 (0,0) user/gnss 各算出 **2 条**过境并带
> `station_warning`；同坐标 `tune_satellite` 仍 `station_untrusted` +
> 「拒绝按错误位置做多普勒调谐」。涉及 station/controller/agent/orbit/
> sdr_tools/sat_tracker/rf_sky_view 与 4 个测试文件，锁均已释放。

> 归档：2026-11-05 卫星过境线 `b7e905f` 已入库并 release：
> 频率未知不再用 `0.0` 兼任哨兵。`FreqOrbitPoint.frequency_mhz` 改
> `Optional[float]`（`None`=未知）并加 `has_frequency`；散点图
> `plottable_points()` 只画已知频点，未知点不再落到坐标 0（全未知显示
> 「无频率数据」）。涉及 `mbdsdr_ai/new_spacetime_timeline.py` /
> `desktop/new_spacetime_panel.py` / `tests/test_freq_orbit_unknown.py`，
> 锁均已释放。

### 6.1.1 接管过期 claim 前必须查工作区（2026-11-05 事故后立规）

**TTL 过期 ≠ 对方停手。** `expired_steal` 只说明心跳停了，不说明对方工作区
干净——对方可能正握着未提交改动，只是没再跑心跳。

接管前必须做：

1. `git status --porcelain -- <该文件>`：出现 ` M` / `??` 即存在在途改动；
2. 有在途改动时**禁止整文件 `git add` 后 `--only` 提交**（会把他人的未提交
   内容一起卷走）；只能按 hunk 进 index：`git diff -- <文件> > 补丁` → 剔除
   他人 hunk → `git apply --cached 补丁`，或用 `GIT_INDEX_FILE` 建独立 index；
3. 拿不准就先问对方，别赌。

实测依据（今天两次，各一句）：

- 事故 1：参数治理线对 `docs/parameter-unification.md` 的 claim 心跳停在取得
  时刻，**679 s（约 11 分钟）**后被判 `expired_steal` 并由卫星过境线接管——
  超时只反映心跳，不反映人是否还在改。
- 事故 2：审阅线同一文件的 claim 心跳停在取得时刻，**3666 s（约 61 分钟）**
  后被判 `expired_steal`；本线接管时未查工作区，把其未提交的附录 D.1–D.5
  约 78 行并入 `ba238bc5`（内容未改，归属错位，已在
  `docs/parameter-unification.md` 加注记）。

### 6.2 共享文件清单（今天实际撞过的）

- `desktop/main_window.py`
- `desktop/spectrum_widget.py`
- `desktop/control_panel.py`
- `mbdsdr_ai/agent.py`
- `docs/COORDINATION.md`
- `docs/limitations-register.md`

清单只是高频雷区，不等于「清单外的文件可以并发改」。新增高频文件时往这里加一行。

### 6.3 claim 与 release 的时机

```
python scripts/file_claim.py claim   desktop/main_window.py --owner 操作逻辑线 [--ttl-min 10]
python scripts/file_claim.py release desktop/main_window.py --owner 操作逻辑线
python scripts/file_claim.py status  [desktop/main_window.py]
```

- **claim 在第一次 Edit 之前**（不是"看完代码准备改"，是动手那一刻）。
- **release 在 `git commit` 成功之后**（commit 失败就还持有着，别提前放）。
- 每次写该文件前可再 `claim` 一次续租（同 owner 直接成功，并刷新心跳）。
- 超过 TTL 没续租视为过期，别人可夺取；夺取会打印
  「原持有者 X 已超时 N，请确认它真的停了」。
- `release` 只有原持有者能执行；别人 release 必失败（互相解锁等于没有锁）。
- 状态文件 `D:\mbdsdr\.locks\claims.json`，占位文件 `D:\mbdsdr\.locks\claims\*.claim`，
  与既有 `qt_test.lock` 同目录；不写 C 盘。
- 互斥由操作系统裁决：占位文件 `O_CREAT|O_EXCL` 原子创建，续租/夺取/释放都在
  文件字节锁（Windows `msvcrt.locking`）里完成，不是"读 JSON 再写 JSON"。

### 6.4 被拒绝时怎么办

**只有两个合法动作：等，或者换文件。**

- 等：`status` 看剩余 TTL；到期后对方续租就继续等，确实停了才夺取（先确认它真停了）。
- 换：先做不冲突的部分，别在别人占着的文件里抢改。

**禁止**：`git stash`、`git checkout -- <别人的文件>`、`git reset`、整文件 `git add`。
这些动作会把别人的在途改动丢掉或卷进自己的提交——今天三次事故就是这么来的。

### 6.5 提交范围检查：能做的与做不到的

```
python scripts/check_commit_scope.py <hash>
```

- 能：列出该提交改动的全部文件；查 claim 台账，标出提交时刻被 ≥2 条线同时持有的
  文件（`MULTI`）；附「整文件替换」提示（新增行数 = 新版本总行数）。
- 不能：**从 `git show` 判断某个 hunk 属于哪条线**。Git 不记录逐行作者，任何这么
  声称的检测都是假的，本工具不做。台账只在机制落地后积累，更早的提交一律
  `NO_RECORD`，不伪造结论。

### 6.6 Qt 锁超时：先查并行 pytest，不是本线回归

4 个并行 pytest 会抢 `D:\mbdsdr\.locks\qt_test.lock`；报「等待 5s 仍未拿到 Qt 测试
互斥锁」时先查是不是并跑/残留进程，**不要**据此改本线代码。

### 6.7 本次归属争议记录（2026-11-05 13:00）

`docs/COORDINATION.md` 工作区 +400/−12 不是一条线的改动，是至少 5 条线的追加：
WorkBuddy 台账线（"我这边" UI 原型/产品工程/星象仪）、巡检会话（新增段）、
星象仪/彩带会话（11-04 提交记录段）、接续线（`agent.py` confirm_token / PTT）、
硬编码与参数治理线（三笔变更日志）。**没有单一 owner**；按「谁在途谁所有」
不整文件提交、不删除任何一段。本次只提交机制线自己追加的 hunk，其余原样留在
工作区，由各自线自行提交。

### 6.8 共享 index 也会被卷走（本笔亲历，2026-11-05 12:53）

机制线提交自己的 2 个文件时，`git diff --cached` 里已被另一条线 `git add` 进了
8 个文件；不带 pathspec 的 `git commit` 把它们一起提交了。发现后立刻
`git reset --soft HEAD~1` 撤回、只提交自己的 2 个文件（该错误提交未推送，内容
没丢——8 个文件仍在工作区，只是回到未暂存）。

规则：**提交前先跑 `git diff --cached --name-only`**，只应出现自己的文件；
出现别人的文件 → `git restore --staged <别人文件>`（只动 index，不动别人工作区
内容）后重提，绝不带着走。

### 6.9 提交机械闸（2026-11-05 17:03 落地，23:30 改写源头规矩）

**源头规矩（2026-11-05 23:30，比「别用 `--only`」更上游）**

**任何人、任何时候都不许对共享 index 执行 `git add` / `git apply --cached` /
`git restore --staged`**——唯一例外是清理「自己刚提交过的文件」的陈旧条目（§6.9.4），
且必须三条件全做。

**不变量：共享 index 任何时刻都应等于 HEAD。** 判据 = `git diff --cached --name-only`
必须为空。**提交前先查这一条；非空即报警**——先查明是谁写的、通知对方，不许直接提交，
也不许顺手 `reset` 清掉（§6.9.2）。

提交一律走独立 `GIT_INDEX_FILE`（§6.9.2）：`git read-tree HEAD` → 只打自己的 hunks →
`git commit`（**不带 `--only`**）。`--only` 是上一代做法，现在只在「独占文件、且确认
共享 index 为空」时才安全，本仓不再默认使用。

机械闸（`scripts/install_git_hooks.py` 安装，本仓已装）：

- `pre-commit` 调 `scripts/check_commit_scope.py --staged`：暂存区涉及
  **≥2 个仍在 TTL 内的 claim owner** 时直接拒绝提交，并提示
  `git commit --only -- <paths>`。
- `pre-commit` 同时打印 `git diff --cached --name-only`，事后可核对。
- `post-commit` 提交后 5 秒内对本次提交的每个路径跑 `git status --porcelain` /
  `git diff HEAD`：工作区仍有改动 = 这次吃进的是旧索引内容（漏带自己的编辑），
  记一条 `STALE_INDEX_SUSPECT`（commit、路径、剩余 diff 行数 + 前 10 行）。
  只记录不阻断（提交已发生），且只看本次提交的路径、不看别线文件。
- 放行时写 `D:\mbdsdr\.locks\hook_ok\<tree>.json` 标记；`post-commit` 消费标记。
- `git commit --no-verify` 仍可用（不隐藏），但 `post-commit` 会把
  commit/tree/author/files 追加到 `D:\mbdsdr\.locks\git_hook_bypass.log`。
- 校验：`python scripts/install_git_hooks.py --check`；卸载 `--uninstall`。

**两头都要，不能只防一半**：提交前贴 `git diff --cached --name-only` 防夹带
别人的改动；提交后查 `git diff HEAD -- <本次提交的路径>` 防漏带自己的改动。
今天两种事故都发生过——`d4b2a80`/`2614c7f`（多带别人）、`14a6dc7`（自己的
11:47 编辑没被 11:48 的提交吃进，用旧索引漏掉）。


实测（临时仓、D 盘）：两 owner 暂存 → 拒绝；单 owner → 放行；`--only` 单路径
→ 放行；`--no-verify` → bypass 日志 1 条；旧索引提交 → `STALE_INDEX_SUSPECT`
1 条，正常 `--only` 提交 → 0 条（`tests/test_git_hooks_stale_index.py` 2 passed）。

**闸门边界（2026-11-05 实测，不能假装能拦）**：闸门只拦「一次提交里出现
≥2 个仍在 TTL 内的 owner」，其余漏网按设计放行。

| 场景 | 闸门行为 | 留痕 |
|---|---|---|
| 文件不在台账（NONE），两个会话同时在改 | **放行**（`owner=-（无活跃 claim）`） | 无 bypass 记录 |
| claim 提交瞬间超时并被他人盗走 | **放行**（只剩 1 个 owner，`owner=线B`） | 无 bypass；事后归属看起来像盗走方，要配合台账 `history` 才能还原 |
| claim 超时但无人接手 | **放行**（视同无 claim） | 无 bypass |
| 3 个 owner 的文件用 `--only` 拆 3 笔 | 三笔全放行，每笔只含 1 个文件 | 各自输出 `owner=线A/线B/线C`；无 bypass |
| 3 个 owner 的文件一次提交 | **拒绝**（MULTI，提示 `--only` / `--no-verify`） | 提交未发生，无 bypass |

结论：能拦的只有最后一行；前三种是机制边界，**不能假装能拦**——靠「claim 先于
动手 + 提交前贴 name-only + 提交后查 `git diff HEAD`」三条纪律兜底。
`--no-verify` 绕过仍会写 bypass 记录（含当时 claim 快照），事后可核对有没有夹带。


#### 6.9.1 提交说明一律走 stdin（禁止往 C 盘落临时文件，2026-11-05 追加）

用户点过的铁律：**提交说明不落文件**。`-F <临时文件>` 会在 `%TEMP%`（C 盘）留下
残留，本次清理就在 `%TEMP%` 找出 100+ 个 `mbdsdr_*` 遗留（含 20 多个
`*_msg.txt` / `commit_*.txt`），属历史欠账。改用管道喂 stdin：

```powershell
$msg = @'
<提交说明，多行>
'@
$msg | git commit --only -F - -- <自己的路径...>
```

- `-F -` 从 stdin 读说明，**不创建任何临时文件**；`--only -- <paths>` 的纪律不变
  （`-F -` 与 `--` 的顺序照本段，paths 必须在 `--` 之后）。
- PowerShell 5.1 的中文/引号咬断问题由 **here-string `@'...'@` + 管道**规避，
  这正是当年改用 `-F 文件` 的原始理由，现在用 stdin 同样满足（本仓 AGENTS.md §4
  的「commit message 走 `-F 文件`」按本条收口：**不许再新建临时文件**）。
- 一次性脚本同理：只允许写仓库内或 D 盘工作区，**不得写 C 盘**；已经落在
  `%TEMP%` 的产物用完即删，删后 `Test-Path` 复核并在回报里写「C 盘无可复用残留」。
- 清理 `%TEMP%` 时只按前缀 + 时间筛选（`mbdsdr_*`、mtime 早于 2 小时），
  不动目录、不动非本前缀文件；删除被占用而失败的不强删，列出文件名与占用情况。

#### 6.9.2 共享 index 必须保持干净：一律用独立 `GIT_INDEX_FILE`（2026-11-05 23:20 追加）

**结构性要求**（比「别用 `--only`」更根治）：

- 所有线一律用独立 `GIT_INDEX_FILE` 提交：`git read-tree HEAD` → `git apply --cached`
  （或 `git diff ... --output` 出 patch 再 `git apply --cached`）只打自己要提交的
  hunks → 用该 index `git commit`。**共享 index（仓库默认 `.git/index`）里不许出现
  任何人的暂存内容**。这样「提交被卷走」与「要不要 reset 别人的 index」这两个问题
  都不再存在。
- **发现别人在共享 index 里有暂存内容时**：不得 `git reset` / `git restore --staged`
  清掉别人的暂存状态——内容虽在工作区没丢，但对方的**提交意图被抹掉**，会引发重复
  造改动或误提交。正确做法是：不动共享 index，用自己的临时 index 提交；然后把对方的
  暂存内容导出成 patch（落 `D:\mbdsdr\.tmp\`），通知对方 owner 并确认重放。
- 参考操作（PowerShell，临时 index 落 D 盘，提交说明走 stdin）：

```powershell
$env:GIT_INDEX_FILE = 'D:\mbdsdr\.tmp\idx_<线名>'
git read-tree HEAD
git diff --output=D:\mbdsdr\.tmp\me.patch -- <自己的路径...>
git apply --cached D:\mbdsdr\.tmp\me.patch
git diff --cached --name-only            # 核对：只有自己的路径
$msg | git commit -F -                   # 不带 --only
Remove-Item $env:GIT_INDEX_FILE; Remove-Item Env:GIT_INDEX_FILE
git read-tree HEAD                       # 把共享 index 刷新到新 HEAD（原共享 index 须为空）
```

**三道闸（每次提交依次通过，任一失败即停）**

1. **共享 index 必须为空**：`git diff --cached --name-only` 无输出。非空 = 有人对共享
   index 暂存了内容，先查明是谁写的并通知，**不许直接提交、不许 reset 清掉**。
2. **私有 index 清单精确相等**：在 `$env:GIT_INDEX_FILE` 下，`git diff --cached --name-only`
   必须**逐字等于**本次要提交的路径清单——多一个、少一个都不许提交。
3. **提交后零残留**：`git diff --numstat HEAD -- <本次每个文件>` 必须为空；非空即说明
   提交吃进的是旧快照（事故形态三，§6.9.3），立即报主会话。随后跑
   `python scripts/git_sync_index.py --paths <本次每个文件>`，把共享 index 中这些路径
   同步到新 HEAD（§6.9.5）——不同步就会留下一批「反向暂存」。

**23:30 巡检实测**：共享 index 2658 项与 `git ls-tree -r HEAD` 逐条比对，哈希/模式差异
**0**，HEAD 有而 index 未收录 **0**；`D:\mbdsdr\.tmp` 与 `output\tmp` 下无 `idx_*` 残留。
（parity 干净；事故形态三的复发风险点只剩「复用旧私有 index」这一条路径。）

**反例存档（今日实测，行为记在这里、不点名）**

- 参数治理线 23:04 用临时 index 提交**是对的**；但事后 `git reset HEAD` 复位了另一条
  在共享 index 里的 **23 行暂存**（U-02 扫描器缺值保护）——这是不该做的那半步。对方
  的改动还在工作区，但「已暂存、准备提交」的意图被抹掉。
- **不碰别人 index 也可能伤人**：23:08:12 `f664638a`（test(fixtures)）用旧索引提交，
  把本文件里刚入库的 §6.10「前置规矩」整段写回 `448d465f` 的旧版本（blob `9563ffff`），
  只能重新补回。同类事故的另一种表现：没动别人的 index，却把别人**已入库**的同文件
  内容覆盖成旧版。提交前 `git diff --cached --name-only` + 提交后 `git diff HEAD`
  这两步对「同文件多人」仍然必须做。

#### 6.9.3 事故形态三：陈旧 index 回退已入库内容（2026-11-05 23:30 立条）

前两种形态是「多带别人的」与「漏带自己的」；第三种**不丢文件也不丢行数**，丢的是
「同一文件里别人刚写进去的段落」——提交把该文件整体写成旧 blob，`git diff --numstat`
只相对旧 HEAD 显示差异，看上去像一次正常编辑。

**机制**：私有 index 建一次就反复复用；HEAD 前进后，那份旧快照里该文件的 blob 仍是旧版。
用它提交，就把别人已入库的内容回退。**必要时例外不是「复用旧 index」，而是「清理你
自己刚提交过的文件的陈旧条目」**（§6.9.4）。

**四条规矩**：

1. 临时 index **每次**从**当前** HEAD `git read-tree HEAD` 重建，不复用上次的 index 文件；
   用完即 `Remove-Item`，不在 `.tmp` 留 `idx_*` 残骸。
2. 提交前过 §6.9.2 的**三道闸**（共享 index 空 → 私有清单精确相等）。
3. 提交后复核：`git diff --numstat HEAD -- <每个文件>` 必须为空；共享文档再加一条
   `git log -S '<本次新加的关键短语>' -- <文件>`，确认新短语仍在最新提交里。
4. 同一文件多人写时，**每次提交前重算该文件的索引项**，不继承任何缓存。

**可复现证据（`16f8cd09` 加 → `f664638a` 删 → `4132d46f` 补回）**：

```powershell
git log -S'前置规矩' --oneline -- docs/COORDINATION.md
#   4132d46f（补回） / f664638a（删除） / 16f8cd09（新增）
git show 16f8cd09:docs/COORDINATION.md | git hash-object --stdin   # 487f3598…
git show f664638a:docs/COORDINATION.md | git hash-object --stdin   # 9563ffff…（=448d465f 版本）
git show --numstat --stat f664638a -- docs/COORDINATION.md         # +2 / −28
```

`f664638a` 的提交说明自述「本笔仅含测试文件的 hunk、用临时索引精确装配」，但 stat 里
带着 `docs/COORDINATION.md | 30 +-`——**说明与实际 stat 矛盾**，唯一解释是私有 index 里
还留着提交前的 COORDINATION 条目。这就是「陈旧 index」的形状。

#### 6.9.4 必要例外：清理「自己刚提交过的文件」的陈旧条目（2026-11-05 23:30）

共享 index 里出现**你刚提交过的文件**的陈旧条目（例：你 10 分钟前用独立 index 提交了
`docs/X.md`，共享 index 仍停在提交前的版本）时，**允许且必须由你处理**，但三条件全做：

1. 先把该文件当前 index 内容导出成 patch，落 `D:\mbdsdr\.tmp\<topic>.staged.patch`；
2. 通知与这些 hunks 相关的 owner 并确认重放（对方不在线就写进本台账并留 patch 路径）；
3. 汇报里写明「复位了哪几个路径、导出到哪个 patch」。

**不许悄悄做，也不许对别人正在写的、你从未提交过的文件做这件事。**

反例存档（两次都是「陈旧条目」形态，不是「抢别人暂存」）：

- 参数治理线 23:04 用临时 index 提交后，对共享 index 做了 `git reset HEAD -- <自己的 4 个路径>`；
- 文案审计线在 `ff964183` 之后，对共享 index 做了 `git reset HEAD -- <自己的 4 个文件>`。

两次都落在本例外，但**都没走「导出 patch + 通知 owner」两步**，留痕不足——例外可以，
流程不许省。

#### 6.9.5 提交后必须同步共享 index（第五种形态：私有 index 提交的副作用，2026-11-06 00:15）

**结构性后果（月相线实测）**：用私有 `GIT_INDEX_FILE` 提交后，共享 index 仍停在旧 HEAD，
于是本次提交的**每一个文件**都会在共享 index 里变成「反向暂存」——`git status` 显示
`M ` / `MM`，`git diff --cached` 列出 N 个文件（N = 本次提交文件数），看上去像有人要把
这些文件退回旧版。今天全员改用私有 index，所以这个脏是**必然发生**、且随提交量线性增长；
如果下一道闸只喊「`git diff --cached` 必须为空」，它会天天报红，最后被当成噪音忽略，
规矩作废。

**规矩：提交完成后，必须把共享 index 中本次提交的路径同步到新 HEAD。**
`git read-tree HEAD` 只重写 index，不动工作区、不动历史，**不是** `git reset`
（后者是被 §6.10 明令禁止的那条路径）；但**默认只按本次提交的路径精确改写**
（`update-index --cacheinfo`），别人的条目一个不动——全量 `read-tree` 会抹掉别线
正在准备的暂存内容，那是 §6.9.2 禁止的动作。

**可复跑（不靠自觉）**：

```powershell
python scripts/git_sync_index.py --paths <本次每个文件>   # 默认：按路径同步并校验
python scripts/git_sync_index.py --all                   # 共享 index 为空时全量 read-tree
python scripts/git_sync_index.py --check                 # 只校验，不改 index
```

- `post-commit` 钩子已接：每次提交后自动跑（剥掉 `GIT_INDEX_FILE` 确保同步的是**共享**
  index，不是刚用完的私有 index）；失败只打印醒目警告、不阻塞；若发现**他人暂存条目**，
  只报警不动（exit 2，交由 owner 按 §6.9.2 处理）。
- 安装/更新：`python scripts/install_git_hooks.py`；校验：`--check`。

**「共享 index 干净」的唯一判据（两条都要满足）**：

1. `git diff --cached --name-only` 为空；
2. `git ls-files -s` 与 `git ls-tree -r HEAD` 逐条比对，**模式与哈希一致**、条目数一致。

只看第 1 条不够：路径集合相同的「内容级反向暂存」要靠第 2 条兜住。

**00:15 巡检实测**：共享 index 2658 项 / HEAD 2658 项，`staged=0`、`parity_diff=0`——
月相线手工对齐的 3 条已清零，其他线也没有残留反向暂存。`.tmp` 与 `output\tmp` 下无
`idx_*` 私有余留。

**反例存档（本形态首次实测）**：月相线用私有 index 提交后，共享 index 对这 3 个文件
出现反向暂存 `MM`，它只能用 `git update-index --cacheinfo` 手工对齐；钩子接上后，
这一步由 `git_sync_index.py` 自动完成，不再依赖自觉。

### 6.10 历史改写事件（2026-11-05，主分支被 reset 改写；今日累计 10 次破坏性 reset + 1 次 no-op）

**前置规矩（上游真因，比「禁止 reset」更早一步）**

误用 `git commit --only <paths>` 卷走他人改动后，自行 `git reset` 回退重打属违规。
`--only` 提交的是这些路径在**工作区**的内容，不是暂存区内容；只要文件里还有他人
未提交的改动，`--only` 必然一起扫走。正确做法：把要提交的 hunks 单独进 index
（`git apply --cached` 或独立 `GIT_INDEX_FILE`），然后**不带 `--only`** 直接
`git commit`。今天 7 次破坏性 reset 中**至少 4 次**是「`--only` 扫走别线内容 →
reset --mixed 回退 → 同标题重打」（12:05 / 12:24 / 12:54 / 22:39:59）；16:35:59
形态相同（重提漏掉 `main_window.py`），但「扫走了谁的内容」无法证明，只按无法定人记。

**铁律（立即生效，不是建议）**

1. **禁止用 `git reset` 移动 `main`**：含 `--soft` / `--mixed` / `--hard`，也含无参数
   `git reset` 与 `reset HEAD` 这类 no-op（no-op 说明有人习惯性在跑，必须一起戒）。
   撤销已提交内容只有两个合法动作：
   - 已推送 / 已被别人依赖：`git revert <hash>`（保留历史、留痕）；
   - 未推送且内容本身要改：补一笔新提交修正。**不许改写历史。**
2. 确需清理未推送的错误提交（内容本身不该存在），也**必须先报主会话**，由主会话
   确认没有其他会话正在写相关文件后才可动手；任何线不得单方面 reset。
3. **误操作后立刻报主会话**（时间、reset 目标、被移出 hash、当前 HEAD），
   **不许自己悄悄重提把痕迹盖掉**。内容可以补，事件必须报——悄悄重提会让台账与
   归属失真，别的线也无法判断自己的工作是否被移出。
4. 恢复统一入口：先 `git branch rescue/<short> <hash>`（只加引用，不动 main 与
   工作区，防 gc），再决定 cherry-pick / 重提；`reset --hard` 到旧提交重建**一律禁止**
   （会丢后续提交）。

**事件 1（上午，`becc596`）实测时间线**

| 时间 | 事件 | HEAD 变化 |
|---|---|---|
| 16:35:18 | `becc596d` 提交（fix(tuning)，6 文件） | → becc596d |
| 16:35:59 | `reset: moving to HEAD~1` | becc596d → 250a4ec7 |
| 16:37:59 | `0e738489` 重提同标题 | → 0e738489 |

- 对象：`git cat-file -t becc596` / `0e738489` = commit，均在库（becc596 现仅 reflog 引用）。
- 内容：重提的 5 个文件 `git diff --numstat becc596 0e738489 -- <5 paths>` 为空
  （**字节等价**）；`desktop/main_window.py` 不在重提内，后续多线持续改该文件，
  无法仅凭 blob 证明已吸收，需该线自证。
- 归属：reset 时点（16:35:59）台账在持有的是 卫星过境线（main_window.py 16:30:39）、
  操作逻辑线（status_panel.py 16:31:23）；被 reset 的 4 个文件由审阅线 16:38:17 才
  claim，**晚于 reset，不能作为操作者证据**。结论：**无法定人**。

**事件 2（晚间，`f3ef1cbb` + `948ff803`）实测时间线**

| 时间 | 事件 | HEAD 变化 |
|---|---|---|
| 22:29:00 | `0ba9e75f` 提交（release 自检） | → 0ba9e75f |
| 22:31:11 | `reset: moving to HEAD`（no-op） | 0ba9e75f → 0ba9e75f |
| 22:31:51 | `948ff803` 提交（U-02 RSSI，10 文件） | → 948ff803 |
| 22:31:52–53 | `STALE_INDEX_SUSPECT` ×4（948ff803） | — |
| 22:33:30 | `f3ef1cbb` 提交（参数台账附录 D，1 文件） | → f3ef1cbb |
| 22:33:31 | `reset: moving to HEAD~1`（同秒另有 `moving to HEAD` no-op） | f3ef1cbb → 948ff803 |
| 22:34:00 | `reset: moving to 0ba9e75f`（同秒另有 `moving to HEAD` no-op） | 948ff803 → 0ba9e75f |
| 22:34:49 | `dc0d1a65` 重提附录 D（父=0ba9e75f） | → dc0d1a65 |
| 22:39:29 | `d42a5cc3` 提交（U-02「完整版」，10 文件 +345/−84） | → d42a5cc3 |
| 22:39:59 | `reset: moving to dc0d1a65` ×2 | d42a5cc3 → dc0d1a65 |
| 22:42:02 | `84b83758` 重提 U-02（`STALE_INDEX_SUSPECT` ×4） | → 84b83758 |
| 22:45:54 | `ebc897fa` U-03（吃进 U-02 剩余 sdr_tools / test） | → ebc897fa |
| 22:49:09 | `817b2a77` APRS 接线（**当前 HEAD**，取证时点） | → 817b2a77 |

- 被移出主分支且未再入库：`f3ef1cbb`、`948ff803`（`git log HEAD..f3ef1cbb` 恰这两笔）；
  `d42a5cc3` 属窗口内「提交 30 秒后即被 reset」，不在该 diff 内，同样已不在 main。
- 对象：三笔 `git cat-file -t` 均为 commit，**全部存在**；但 `git fsck --unreachable
  --no-reflogs` 把它们列为 unreachable，即**只靠 reflog 支撑**——gc 前必须先建 rescue 引用。
- 内容：**本轮无内容彻底丢失**，逐笔走向见 §6.10.1。
- 归属推断（git author 全为归一身份，只能靠 claim 台账 + 时间窗 + 提交内容）：
  - 22:33:31 / 22:34:00 两次 reset：**审阅线**（`docs/parameter-unification.md` claim
    22:30:20–22:35:16；f3ef1cbb 22:33:30 与 dc0d1a65 22:34:49 都落在其窗内；
    reset 到 0ba9e75f 后只有它重提该文档）。**置信中高**。
    注：主会话口径称这笔属「频谱/内核线」，与台账 owner「审阅线」不一致，需主会话裁决。
  - 22:39:59 reset 到 dc0d1a65：**文案审计线**（10 个 U-02 文件 claim 22:15:49–22:46:32，
    是该时刻唯一活跃 owner；d42a5cc3 被移出后 2.5 分钟由其重提 84b83758）。**置信高**。
    动机可解释：d42a5cc3 的 `desktop/main_window.py` 扫进了 ux-basis 未提交的窄屏改动
    （99 行 vs 948ff803 的 43 行），它 reset 后重提时只带回自己的 U-02 内容
    （旧索引，故 `STALE_INDEX_SUSPECT` ×4）。内容上是对的，**行为上仍违规**：
    同窗口里 `948ff803` 已被 22:34:00 那次移出主分支。
- bypass 日志：22:25–22:45 只有 948ff803 / 84b83758 的 `STALE_INDEX_SUSPECT`，
  **没有 `--no-verify` 记录**——reset 不经钩子，bypass 日志天然查不到它，别拿「无 bypass」当清白证据。

**今日 reset 全表（`git reflog --all` 实测）**

| 时间 | 目标 | 性质 |
|---|---|---|
| 22:39:59 | dc0d1a65 | 破坏性（移出 d42a5cc3） |
| 22:34:00 | 0ba9e75f | 破坏性（移出 948ff803） |
| 22:33:31 | HEAD~1 | 破坏性（移出 f3ef1cbb） |
| 22:31:11 | HEAD | no-op |
| 16:35:59 | HEAD~1 | 破坏性（移出 becc596d） |
| 12:54:09 | HEAD~1 | 破坏性 |
| 12:24:39 | HEAD~1 | 破坏性 |
| 12:05:13 | HEAD~1 | 破坏性 |

即 **7 次破坏性 reset + 1 次 no-op**；22:33:31 / 22:34:00 / 22:39:59 同秒的
`moving to HEAD` 与重复目标均为 no-op，不计。

**23:xx 续（后追加，实测）**：23:09:43 / 23:10:40 / 23:10:55 又 3 次
`reset: moving to 926280d5`——扫描线把自己 23:09–23:10 的同标题提交回退重打，
最终以 `aaaa0de2` 入库。**今日累计 10 次破坏性 reset + 1 次 no-op**
（= 上表 7 次 + 23:xx 这 3 次）。

**12:xx 三次的定人与形态（补查，23:05）**

- **三次均「无法定人」**：claim 台账首条记录是 12:49:06，`12:05:13` / `12:24:39`
  时点完全无记录；`12:54:09` 时点相关文件（`config_schema.py` / `rf_sky_view.py` /
  `settings_panel.py` / `spectrum_widget.py` / `docs/settings-inventory.md`）的
  claim 最早也到 12:56:54 之后。**没有台账证据就不写人名**，只能按提交内容猜归属。
- **三次同一形态**：提交后 4–34 秒 `reset: moving to HEAD~1`，随即以同标题重提
  （`a59f98f8→db87425d`、`9798ece0→95087ccc`、`ceb086b9→fba509bd`），且**重提都
  比原提交少了内容**——原提交扫进了别线的在途改动，重提把它剥掉：
  - 12:05 那次：`spectrum_widget.py` 重提相对原提交 −84/+27（剥掉他人快捷符号绑定）；
  - 12:24 那次：`main_window.py` −15/+9（剥掉他人 tooltip 文案改动）；
  - 12:54 那次：跨 8 文件 −414/+53（`config_schema.py`、`rf_sky_view.py`、
    `settings_panel.py`、`spectrum_widget.py`、`docs/settings-inventory.md`、
    `star_catalog.py`、两个测试）。
  这正是本节「前置规矩」的实例：真正的错在 `--only`/宽索引扫走他人内容，reset 只是事后补救，
  但补救方式选择了改写历史。

**处置要求**

- 任何线发现 HEAD 与自己的提交不符：先 `git reflog` 查 `reset` / `rebase` / `amend`，
  再报主会话；不要先猜、更不要自己重提后再报。
- 主会话收到报告：立刻 `git branch rescue/<short> <hash>` 保命（只加 ref），
  再 `git status` 确认无人在写被移出文件，最后才决定 cherry-pick / 重提。
- 台账（`check_commit_scope` / claim history）只作**归因参考**，不是证据本身；
  结论必须标置信度，不得伪造逐行作者。

#### 6.10.1 晚间事件逐笔去向（取证时点 HEAD=`817b2a77`，22:53）

| 被移出提交 | 作者时间 | 对象 | 改动现在在哪 |
|---|---|---|---|
| `f3ef1cbb` | 22:33:30 | 在（仅 reflog） | **已重提**为 `dc0d1a65`；`docs/parameter-unification.md` blob `f182778a…` 在 f3ef1cbb / dc0d1a65 / HEAD 三处完全相同 |
| `948ff803` | 22:31:51 | 在（仅 reflog） | **已入库**：10 文件中 7 个 blob 与 HEAD 完全相同（含 `desktop/main_window.py` = `5c0ae888`）；`sdr_backend.py` 1/1 是 U-03 的「MHz→MS/s」更名；`sdr_tools.py` / `test_copy_units_audit.py` 由 `ebc897fa` 吃进（HEAD 为更新版本，非丢失） |
| `d42a5cc3` | 22:39:29 | 在（仅 reflog） | 9/10 文件 blob 与 HEAD 相同；`desktop/main_window.py` 多出的 44 行（QToolButton 窄屏图标化、UTC 进 tooltip 等）**不是 U-02 内容**，是 ux-basis 的在途改动被该提交扫入；这部分仍在工作区未提交，现由 ux-basis 持有（claim 22:46:20 起 ACTIVE） |

结论：**U-02 一行没丢**；`desktop/main_window.py` 当前工作区 +49/−14 是 ux-basis 的
窄屏工作（不是文案审计线的 U-02 欠账），何时提交归 ux-basis 决定。

#### 6.10.2 回退事件表（第四类形态：陈旧 index 回退已入库内容，2026-11-05 23:35 补查）

**扫描口径**：6 个共享文档（`COORDINATION.md`、`parameter-unification.md`、
`limitations-register.md`、`data-inventory.md`、`design-basis.md`、
`error-code-visibility.md`）各取最近 80 个提交，逐个取 `rev:path` 的 blob，
判据 `blob[i] == blob[i+2] && blob[i+1] != blob[i]`（写回旧版）。

| 提交 | 文件 | 被抹掉的内容 | 是否已补回 | 定人 |
|---|---|---|---|---|
| `f664638a`（23:08:12） | `docs/COORDINATION.md` | §6.10「前置规矩」整段（+2/−28，blob `487f3598` → `9563ffff`） | **已补回**（`4132d46f` 23:18:25） | **操作逻辑线**（置信高，见下） |

- 其余 5 个共享文档**零命中**；`git log -S'前置规矩'` 恰是「`16f8cd09` 加 →
  `f664638a` 删 → `4132d46f` 加回」，与表一致。
- 「标题 A 类却改 B 类共享文档」的提交另有若干（`fba509bd`、`f1976784`、`7d04a3de`、
  `71881a09`、`27b8b69e`、`3938fcf1` 等），逐笔用同一判据复核后**均非回退**——它们是
  各线在收口时顺带登记本台账（正常编辑），删除行是替换旧措辞而非抹掉他人内容。
  需注意：这类「顺手改台账」正是陈旧 index 最容易伤到本文件的地方，故纪律上要求
  **每次改本文件都重读当前 HEAD 版本**（§6.9.3 第 4 条）。

**`f664638a` 定人（由「无法定人」升级）：操作逻辑线**

- 文件集逐一吻合：该提交 14 个文件中 13 个是 `tests/` 下的 fixture/UI 测试，
  这 13 个文件在台账里**全部**由同一个 owner 于 **20:59:07–20:59:09** claim
  （`tests/test_agc_speed.py`、`test_app_mode.py`、`test_bookmark_tags.py`、
  `test_desktop_hotplug_autorecovery.py`、`test_device_switch_flow.py`、
  `test_gimbal_follow_wiring.py`、`test_mode_switch_preset.py`、
  `test_record_replay_config.py`、`test_sky_capability_negotiation.py`、
  `test_sky_track_wiring.py`、`test_three_column_layout.py`、
  `test_ui_layout_actions.py`、`test_ui_layout_locks.py`）；
  同 owner 还 hold 着 `tests/test_gimbal_track_chain.py`——正是提交说明里
  「已由云台线自行收口、本笔不重复改」的那个文件。
- 时窗成立：该 owner 的 claim 心跳持续到 **22:43:37**，TTL 40 分钟，有效期覆盖
  **23:08:12**（提交时点）；窗口内无第二个 owner 持有这些文件。
- 工作流同型：`94063db8`（20:57:45，「fixture 收尾不再静默吞异常，棘轮两文件 E1→E0」）
  与 `f664638a` 同一件事的续批，claim 恰在 `94063db8` 之后 74 秒建立。
- 排除项：可发现性线（其两笔为 `3f190dfa`、`a13d1db7`，文件集不交）。
- 陈旧快照的年龄也可反推：被写回的 blob `9563ffff` **首次出现在 `448d465f`（22:57:55）**，
  即该私有 index 建于 22:57:55–23:06:52 之间，不是「隔夜缓存」。
- 作者字段为全仓统一归一身份，**不构成归属证据**（按 AGENTS.md 第 1 条不落字面量）。

#### 6.10.3 文案审计线 4 文件 index 复位通告（2026-11-05 23:36 补流程）

按 §6.9.4 的三条件，把 `ff964183` 之后那次 `git reset HEAD -- <4 路径>` 的留痕补齐。
**复位执行方**：文案审计线；**复位时间**：`ff964183`（23:20 前后）之后、`a13d1db7` 之前。

**① 复位了哪几个路径（4 个）**

`desktop/troubleshooting.py`、`desktop/satellite_image_panel.py`、
`desktop/weather_panel.py`、`tests/test_copy_units_audit.py`。

复位后复核：`git diff --cached --name-only` 为空；4 个路径的工作区
`git diff --numstat` 为空；**未触碰任何「本线从未提交过」的他人文件**。

**② patch 落点（均在 `D:\mbdsdr\.tmp\`）**

| patch | 内容 | 生成命令 |
|---|---|---|
| `b5-index-before-reset.patch`（6654 B） | 复位前共享 index 持有的 4 文件快照（= 本线提交前的旧 blob） | `git diff ff964183 8ddf0f60 -- <4 路径>` |
| `b5-otherline-troubleshooting-17lines.patch`（4504 B） | `troubleshooting.py` 帮助路径文案那 17 行 | `git show 2b7a71b9 -- desktop/troubleshooting.py` |

**③ owner 通知（本台账即通知通道）**

- 那 17 行的 owner 经证据判为 **可发现性线（discoverability-line）**，**不是**扫描线 /
  操作逻辑线：该内容由 `2b7a71b9`（`docs(help): 帮助条目的操作路径与真实控件对齐`）
  **已入库**，其配套守门 `tests/test_help_path_truthfulness.py` 的 claim 持有者为
  discoverability-line（23:12:30 起）。
- 该线已**无需重放**：`2b7a71b9` 提交说明明写「同文件内并行的 `2.048 MHz → 2.048 MS/s`
  单位修正原样带入」，当前 worktree 该文件零 diff。上方第二个 patch 仅作为
  「万一需要重放」的可执行凭据保留。
- 操作逻辑线排除：其 23:15:51 起的 claim 在 `desktop/main_window.py`，**不在**本次复位
  的 4 个路径内，与本次复位无关。
- 请 discoverability-line 复核两点：`troubleshooting.py` 是否有本笔 17 行之外的
  未入库 hunk；若有，用 `b5-otherline-troubleshooting-17lines.patch` 对照重放。
