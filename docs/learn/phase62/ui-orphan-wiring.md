# Phase62 三组孤儿后端的最小 UI 接线（desktop surface）

> 范围：把 `orphan-features.md` 里已侦察、**后端能力完整但桌面 UI 无入口**的三组后端接到
> 真实界面上。全程 offscreen/CLI，未操控 GUI；全部接真实数据源，零 mock；
> 活动参数不硬编码（时长由小弹窗问、中心频率读当前选中 VFO、扫描配置复用扫描页既有控件）。

## 总览

| 孤儿 | 后端（已存在，直接复用） | UI 落点 | 接线方式 |
|---|---|---|---|
| A 导出原始 IQ 段 | `SpectrumEngine::exportIqSegment` | 录制库页按钮行下方独立一行 | 时长小弹窗 → 真实采样数 → 当前 VFO 中心 → recDir |
| B 网络音频外送 | `NetworkAudioSink` + `engine::setNetworkAudioSink` | 左栏 SpyServer 组下方"网络音频外送"组 | host/port/协议输入 + 开始/停止 + 诚实状态 |
| C 活动扫描链 | `ScanActivityLink` | 扫描页"频率扫描"下方"活动扫描链"组 | 启用复选框 + 状态标签，50 ms tick 喂真实 RSSI |

三项都**没有**走工具三通道——UI 直调后端；工具面维持现状。

## A. export_iq_segment —— 导出原始 IQ 段

- UI：`recLibIqBtn_`（`main_window.cpp` 录制库组，按钮行 `rBtnRow` 之下独立一行，
  避免窄轨下与既有 6 个按钮挤一行）。
- 流程：点击 → 小模态 QDialog 问**时长秒数**（0.1–30 s，默认 1.0 s）→
  `doRecLibExportIq(seconds)`：
  - `sampleCount = seconds × lastSampleRateHz_`（真实实时采样率；引擎内部仍 clamp 1024..16M）；
  - `tuneHz` = 当前**选中 VFO** 的绝对频率（`engine->vfoMarkers()` 快照里 `selected` 标记；
    未解析到则 -1 = 保持当前源中心）；
  - 输出路径由引擎在 `recordingDir()` 下自取（带冲突后缀，不用文件对话框）。
- 诚实状态：成功 → `recLibPlayStatus_` 显示"已导出 IQ: N 样本 · 文件名"并 `refreshRecLib()`
  让新 SigMF 对出现在列表；失败 → 原样显示引擎错误串（无数据/源产不出/路径不可写）。
- harness：`harnessExportIq(seconds)` 跳过弹窗直调 `doRecLibExportIq`，
  `harnessRecLibStatus()` 读状态标签。

## B. network_audio_sink —— 网络音频外送

- UI：左栏 `gSpy` 之后新增"网络音频外送"组（host 行 / 端口行 / UDP·TCP 下拉 / 开始·停止 / 状态）。
- 线程语义（按后端契约接线）：`start()/stop()` 在**本 UI 线程**调用；
  数据写路径在 DSP 线程；挂接用 `engine->setNetworkAudioSink(std::move(sink))`
  做**并行 tap**——本地播放与录制链不被切走。`setNetworkAudioSink(nullptr)` 即摘挂并销毁 sink。
- 空态诚实：未配置 host → 开始按钮拒绝并提示"未配置目标 host"，不安装 tap；
  未开始 → 停止按钮禁用。
- 失败诚实：`start()` 失败 → 状态显示 `lastError()`（真实 socket errno），不伪造"外送中"。
- 状态 1 s 定时器轮询真实计数：UDP 显示 `UDP → host:port · N B`；
  TCP 显示 `TCP 监听 :port · 等待客户端/客户端已连接`；发送错误计数>0 时追加。
- 窄轨修正：QLineEdit 默认 sizeHint(~220 px) 会把整卡撑出 640 视口——host 输入框
  限 `min=64/max=128`（tokens 弹性缩放），各行尾随 stretch 紧凑左排，
  使所有控件右缘 ≤ 165 px（视口 192 px）。

## C. scan_link —— 活动扫描链

- UI：扫描页"频率扫描"组下方新增"活动扫描链"组（启用复选框 + 状态标签）。
- 配置**复用扫描页既有控件**（起始/停止/步进/驻留/门限/保持模式/保持时长）——
  不引入第二套硬编码扫描参数。
- 驱动：`scanLinkTimer_` 50 ms tick，喂 `engine_->rssiDbfs()`（真实 RSSI；
  首块数据前读数 -100 = 安静带，绝不伪造命中）。
- 三路 seam 绑真实动作：
  - `onRetune` → `engine->onSetCenterFreq(hz)`；
  - `onActivityFound` → `engine->startRecording()`（仅成功才记 `scanLinkRecording_`，
    无数据时引擎诚实拒绝）；
  - `onDwellEnded` → 若本链接已武装则 `stopRecording()`。
- 状态标签：未启用=空闲（未启用）；启用后 空闲/扫描中/停驻·解码/停驻·录制中，
  停驻时带 parked 频率与累计命中数。

## 测试（既有 target `test_ui_integration` 内新增 3 槽，未动 CMake）

- `exportIqSegmentHonestFailureWithoutData()`：关掉合成源回到诚实空源 →
  `harnessExportIq(1.0)` 必须返回 false 且状态标签含"导出失败"。
- `networkAudioSinkEmptyStateAndToggle()`：空态停止禁用/未开启；清空 host 开始被拒；
  UDP 回环开始 → `engine->networkTapActive()==true`；停止 → 回未开启。
- `scanLinkToggleDrivesStateLabel()`：勾选→状态离开"未启用"且引擎链接在
  Scanning/Dwell；取消→回到 Idle。
- 全套计数：**14 passed / 0 failed**（原 11 + 新 3）。offscreen 日志里扫描链接真实
  走完"88.000 MHz 命中 → 启动 SigMF 录制 → 收尾"闭环。

## 快照验证（ui_shot_narrow，offscreen）

三档宽 × 三个入口视图 = 9 张（MBD_TAB/MBD_SCROLL env 切页/切左轨）：

- 640 / 960 / 1920 × {net（左轨滚到网络音频组）, scan（扫描/书签页）, rec（录制库页）}。
- 结论：三个新入口全部可见；**0 裁切 0 叠字**；间距符合弹性语言（4 pt 栅格）；
  空态如实（未开启 / 空闲（未启用）/ 暂无录音 + 未加载）。

## 改动文件

- `cpp/src/ui/main_window.{h,cpp}`：三组 UI + 三个 tick/状态定时器 + harness 访问器 + 析构摘挂。
- `cpp/tests/test_ui_integration.cpp`：3 个新测试槽（既存 target，零 CMake 改动）。
- `cpp/tests/ui_screenshot_narrow.cpp`：`MBD_TAB`/`MBD_SCROLL`/`MBD_DUMP` 可选 env，
  便于同一 harness 复查每个入口在每个宽度的几何。
- 未新增工具；工具计数不变。
