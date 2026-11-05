# 术语表（zh-CN / 日本語 / English）

| 简体中文 | 日本語 | English | 说明 |
|---|---|---|---|
| AI 定义无线电 | AI 定義無線 | AI-Defined Radio (AIDR) | 以人在回路为前提、SDR 功能工具化为本质差异的无线电系统 |
| 人在回路 | 人間がループ内 | Human in the loop | 写动作需人工确认，不宣称 AI 完全自主 |
| 工具面 | ツール面 | Tool surface | 运行时 `tools/list` 给出的可调用工具集合，数量不写死 |
| 工具域 | ツールドメイン | Tool domain | `_TOOL_DOMAIN` 的分组；冻结域与 C++ 跨语言钉扎 |
| ControlHub | ControlHub | ControlHub | C++ 桌面引擎的回环 HTTP 控制通道（无鉴权、只绑 127.0.0.1） |
| MCP | MCP | Model Context Protocol | 外部 agent 通过 `tools/list` / `tools/call` 调用工具的协议 |
| 三通道 | 三チャネル | Agent / ControlHub / HTTP | 行为一致、写动作统一过人工门 |
| arm_manual 人工门 | 人工承認ゲート | Human gate | 写工具未确认返回 `gated:true`，不是执行失败 |
| 诚实空态 | 正直な空状態 | Honest empty state | 无硬件/无信号显式报空，绝不 mock 假数据 |
| 真机实证 | 実機検証 | Real-hardware evidence | 有实测记录才写「已验证」，云测/仿真不算 |
| 内置 TLE 策略 | 内蔵 TLE 方針 | Built-in TLE policy | 合成/凑整 TLE 仅供离线演示，禁止精确过境 |
| 站址可信度 | 観測地点の信頼性 | Site trust | 中性示例站点 `0.0, 0.0` 未校准时拒绝锁定与过境 |
| 星象仪模式 | プラネタリウムモード | Planetarium Mode | 打开即全天空展开的形态 |
| 专业模式 | プロフェッショナルモード | Professional Mode | 传统 SDR 工具化 + 模型协作栏 |
| 能力，而非流程 | 機能ではなく能力 | Capability, not workflow | 产品第一原则 |
| 模型现场能力协商 | モデルによるその場の能力確認 | On-the-spot capability negotiation | 模型问设备，按回答装配任务 |
| 摄像机锁定 | カメラ固定 | Camera lock | 天动星静：卫星钉画面中心 |
| 地景 | 地景 / ランドスケープ | Landscape | 固定地平系，随相机走、不随时间转 |
| 周日视运动 | 日周運動 | Diurnal motion | 星空随时间绕极旋转 |
| 时间倍率 | 時間倍率 | Time rate | ⏸ / ▶ / ×60 / ×300 / ×1800，与桌面端一致 |
| 视场角 | 視野角 | FOV | 缩放改的是它，不是相机推拉 |
| 抓天拖拽 | 天空をつかむドラッグ | grab-the-sky | 按下点钉在光标下 |
| 定轨（仅对话） | 軌道決定（対話のみ） | Orbit determination (dialog only) | **UI 无入口**，只有用户说出口才触发 |
| 规划 · 未验证 | 計画 · 未検証 | Planned · unverified | 6DOF / AR / VR 等设计愿景的诚实标注 |
| 金样 | ゴールデンテスト | Golden test | 外部真值对拍，防自证 |
| not_verified | 未検証 | not verified | 明示未验证项 |
| 数据源契约 | データソース契約 | DataSource contract | UI ↔ 内核唯一接缝 |
| 灰阶热力 | グレースケールヒートマップ | Greyscale heatmap | .figma 设计语言下的瀑布色彩 |
