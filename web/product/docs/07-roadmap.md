# 07 · 路线图与验收门槛

> 原则：**让 MBDSDR 更硬、更可证、更可复现的事优先于纯工程整洁度**。可一键复现的真实链路 > 界面漂亮。

## 阶段 A · 已完成（本版本）

- [x] 双形态产品骨架、摄像机锁星、天动星静、动态地景
- [x] 三语国际化（zh-CN / ja-JP / en-US）与文案单一来源
- [x] 分层架构（下层内核适配契约 / 中层状态与文案 / 上层视图）
- [x] 能力矩阵 9 位可开关、模型现场提问、定轨去功能化
- [x] 文档与 wiki 体系、产品内文档中心、离线自包含
- [x] 构建脚本 + 静态/运行时自动校验（含铁律守卫）
- [x] 设计语言 .figma 全量对齐、AGPL 合规纪律
- [x] 写工具确认门事实源对齐（C++ 宿主开关 / Python `confirm_token`），
      模型自报 `arm_manual` 的前端放行判据已清除
- [x] 阶段 B 的 Python 侧桥接层 `mbdsdr_ai/web_bridge.py` + 16 条跨语言对拍用例

## 阶段 B · 接真内核（Python 侧已落地，宿主注入待接）

> 状态（2026-11-06）：`mbdsdr_ai/web_bridge.py` 已把 `ToolRegistry` 映射成
> `window.mbdsdrBridge` 的形状，写工具门与内核 S13 红线对齐，离线可测。
> **仍未接通**：宿主注入、`confirm_channel` 接 `mcp_server.call_tool`、
> 真实硬件。在此之前写工具恒返回 `gated`，产品仍以诚实空态为主。
> 阶段 A 不宣称已接线；下列验收门槛一条都还没判定通过。

| 项 | 落点 | 验收门槛 |
|---|---|---|
| 启用 ControlHub（`?hub=1`）或宿主注入桥 | `cpp/src/control/control_http_server.cpp` | 状态栏从「未连接 SDR」变为「ControlHub 已连接」，工具调用有真实回执 |
| 宿主注入 `window.mbdsdrBridge` | `desktop/`（**操作逻辑线在途，勿顺手改 `main_window.py`**）+ `mbdsdr_ai/web_bridge.py` | 干净机器上 `as_bridge_dict()` 注入后 8 个契约方法全部可调用，`getCapabilities()` 与运行时 `tools/list` 一致 |
| 写工具确认通道接线 | `confirm_channel` → `mcp_server.call_tool` | 带真实外部签发的 token 才执行，自报 `arm_manual` 与无 token 一律 gated，`gated` 不被显示成失败 |
| TLE / SGP4 接真 | Celestrak 缓存 + 内核传播 | 卫星位置与过境时刻可被第三方预报复算；无 TLE 时仍如实提示 |
| 站址校准 | GNSS / 手动输入 + 真北基准 | `site.trusted=true` 后才允许锁定/过境结论；偏差留痕 |
| 真 IQ → 频谱瀑布 | `rtl_tcp` / `MBDSDR_RTL_TCP` | 频谱与已知信号金样对拍无系统性偏差 |
| 解调链接真 | `mbdsdr_ai/lrpt_*.py` 等 | 解出的帧过 ASM/RS 校验，产物留金样 |

**门槛**：每接一项，都要能在干净机器上"打开 → 一键复现"，且失败时给出可诊断的诚实回执（不吞异常、不伪装成功）。

## 阶段 C · 能力位落地（按真实域与审计结论重排）

| 能力 | 现状（以仓库代码为准） | 验收口径 |
|---|---|---|
| 冻结工具域 / Python 扩展域 | 已由 `_TOOL_DOMAIN` 抽取并钉扎 | 域集合与运行时 `tools/list` 一致；空域保持空态 |
| 云台协议层（rotctld / board_pwm） | 协议层可用；**闭环层是空壳**（`docs/audit_r2/27_gimbal_pose.md`） | 给定 az/el 后实测回读误差在容差内 |
| GNSS / 授时 | 卫星点与解算工具已在仓库；站址守卫已生效 | 1PPS 与采样时钟偏差可量化并留痕 |
| 6DOF 位姿融合 / AR 实景 | **规划未验证**：IMU 修正算出来即丢弃，无驱动 | 位姿残差可复现，附输入帧 |
| 视觉校准 | **规划未验证**：`read_pose()` 恒返 0° | 参数可导出、可在另一时段复验 |
| VR / XR | **规划未验证**：`feature:xr-sky` 仅在设计稿 | 帧率与坐标基准在头显端核对通过 |

规划项在 UI 里禁用开关并标 `not_verified`；不宣布落地，直到有可复跑的验收记录。

## 阶段 D · 产品化

- Qt 端天空模式移植：沿用本工程的 token、状态契约、i18n 词典（词典可导出为 JSON 供 C++ 侧复用）。
- 科教教培场景页：把赛前培训 PPT、SDR 遥测教程、通信对抗材料包（learnSDR + PlutoSDR）的内容灌进可交互课程。
- 便携包：真机构建打包，零缺失 DLL 弹窗（仓库已有 `deploy_win_runtime` 布防）。
- 一键复现证据包：真实 IQ → 解码 → 图像 → 金样的完整链路，产物零个人信息、可对外出示。

## 明确不做

- 把能力写死成向导/流程；
- 在 UI 上添加定轨入口（见 [01-product-spec.md](01-product-spec.md) 铁律 4）；
- 用"看起来成功"掩盖未验证项；所有未验证项写进 `not_verified`。

## 已落地（2026-11-04 全天球星象仪）

- HYG v4.1 真实全天星表 25,792 颗（真下载 + SHA256 + CC BY-SA 4.0 署名）
- 相机在球心：≤88° 针孔透视 / 更大立体投影鱼眼，**全屏无圆形遮罩**（2026-11-04 重做）
- 地平大圆 + 逐列二分求分界的 360° 地景（随视角动、不随时间转）
- 星位固定 J2000、LST 刚性旋转、FOV 缩放、grab-the-sky / zoom-to-cursor
- 星座 27 座 / 中文星官 / 117 DSO / 时空主导 HUD / 银河诚实空态
- 待办：接内核 TimeEngine、完整岁差章动坐标链、天空亮度→极限星等模型、银河数据层
