# 指向即识别：望远镜 AR 寻星落点设计（2026-11-17）

> 这是设计稿，供手改。项目定位与原则优先于具体实现顺序。

## 0. 项目定位（不随模块变化）

MBDSDR = 软件定义 + 人工智能定义 + 时空定义无线电。无线电给信号与链路，时空给星表/TLE/过境/GNSS 基准，人工智能负责编排与解释；交互从图形化界面推到空间化界面（可上 VR/6DOF）。目的是降低认知门槛，驱动力是浪漫。

望远镜 AR 不是新项目，是同一系统的第三个入口（桌面 MBDSDR、手机端、望远镜前视）。

## 1. 目标

镜头指向哪里，屏幕就认出哪里、讲到哪里：

1. GNSS 给**时间与位置**（不给航向）；
2. IMU 给**粗姿态**（方位/俯仰/横滚）与精度；
3. 星表 / TLE 给**先验**（此刻此地在那个方向应该有什么）；
4. 相机星点匹配做**二级校正**；
5. 叠加星座/星官/卫星，按识别结果讲解；
6. 全部能力**工具化**，模型可编排；设备只是可替换的 provider。

## 2. 非目标

- 不绑定某一款望远镜/手机（极蜂 XA90P 只是第一台可负担设备）；
- 不写死设备型号、示例值、伪代码常量；
- 不做第二套星图交互（必须复用现有 ViewState/投影/星官表）；
- 不伪造识别与解算结果；
- 本期不做新的常驻服务，不引入新运行时依赖。

## 3. 已核实的事实边界（决定架构）

| 事实 | 数字/结论 | 影响 |
|---|---|---|
| 手机 IMU 方位精度 | 户外常态 5–15°，近金属可 >30°；俯仰/横滚约 1° | IMU 只能粗对准，必须手动微调 + 解算回写 |
| 单天线 GNSS | 只有位置/时间，**没有航向** | 航向不能按 GNSS 设计 |
| plate solving 最小输入 | FOV/像素尺度先验 + ≥8–10 颗星 + 秒级曝光 | 必须传 FOV，不能盲解 |
| 目镜后手机 | 真 FOV≈1°，星少、暗角、对焦无标定，公开产品都绕开 | 不作为唯一路径；镜筒旁广角相机为主，目镜后为可选增强 |
| 解算库许可 | tetra3 Apache-2.0；ASTAP MPL-2.0；astrometry.net GPL v2 | 优先 tetra3/ASTAP；GPL 只做独立进程或服务 |

## 4. 架构（四层 + 一条工具面）

```
providers（GNSS / IMU / Camera）
   ↓  PoseObservation(t_utc, az, alt, roll, accuracy, source)
fusion  sky_pointing：时间对齐 + 加权融合 + 陈旧/低精度拒绝
   ↓  SkyPose(az, alt, roll, fov, accuracy, valid_until)
solve   sky_solve：先验 FOV + 星点 → 修正量（可选，缺库就如实不可用）
   ↓  correction(az_delta, alt_delta, roll_delta) 或 unavailable(reason)
render  现有 ViewState / 投影 / 星官表 / sky_whats_here
   ↓  屏幕叠加 + 讲解
tools   工具面：模型编排以上能力
```

设计不变量：

1. **一个 SkyState**：时间、站点、视向、FOV、投影只有一份，桌面与望远镜共用；
2. **一个交互逻辑**：桌面怎么指、望远镜就怎么指；
3. **device-agnostic**：provider 注册制，换设备不改上层；
4. **可降级**：粗对准 → 精解算 → 手动，任一级缺失都必须明说当前级别；
5. **无置信度不宣称**：识别结果必须带来源与误差。

## 5. 工具契约（名字先定，实现可后补）

| 工具 | 输入 | 输出 | 失败语义 |
|---|---|---|---|
| `sky_pose_fuse` | 观测列表（source/t_utc/az/alt/roll/accuracy） | 融合位姿 + 各级来源 + 剩余误差 | 全部陈旧/缺失 → `unavailable`，不猜 |
| `sky_pose_view` | 位姿 + fov + 屏幕尺寸 | 可直接套用的 ViewState 参数 | 位姿非法 → `invalid` |
| `sky_identify_at` | 屏幕点或 az/alt + 时间/站点 | 目标（恒星/星官/行星/卫星）+ 名称 + 误差 | 无匹配 → `no_match`，不返回最近邻凑数 |
| `sky_solve_frame` | 星点像素 + FOV 先验 | 修正量 + 解算星数 + 残差 | 库缺失/星不足 → `unavailable` + 原因 |

（`sky_identify_at` 复用现有 `pick_object` / `sky_whats_here`，不另写星表。）

## 6. 天文通反编译结论（只作功能参考）

- 包名 `com.twtapp`，3.6.0，Flutter + Rust；仅 arm64。
- 本地有 **skysolve**（tetra Gaia 10–45 模式库，加密）+ 云端 astrometry 兜底：双通道解算，与我们的"先本地后云端"一致。
- 星表 HYG v42（115,570 颗）、hip2 距离、Messier/Caldwell、**中国星官线段 CSV**、星座插画、38 个流星雨、79 个观星点、光污染瓦片。
- 功能面：观星指数/视宁度、15 日预报、云图雷达、光污染与 Bortle、Stellarium WASM 星图、相机 AR 叠加与 FOV 标定、卫星/ISS/星链过境、极光、日月食、月面、极轴镜模拟、器材库、红灯模式。
- 我们已有：HYG v4.1 星表 + 星官查询 + 桌面星图 + 投影/拾取 + sky_whats_here；TLE/过境/极坐标已有。
- 缺口：传感器融合、解算适配、FOV 标定、HUD 讲解的一体化。

## 7. 落地切片（每片可独立验收）

1. **位姿融合（纯逻辑）**：**已落地**〔证：`18d1a06b` — `mbdsdr_ai/sky_pointing.py:103` — `def fuse_poses`〕。`fuse_poses` / `view_params` / `identify_direction`；`tests/test_sky_pointing.py` 16 条。
2. **解算适配（契约 + 接入）**：**已落地（替身验证）**〔证：`18d1a06b` — `mbdsdr_ai/sky_solve.py:305` — `def solve_tetra3`〕。`solver_status` / `solve_frame` / `solve_tetra3` / `solve_astap`；星点 < 8、超时、乱码等分支有测试；真实解算与真实夜空未验证（缺真实相机/星点）。
3. **桌面指向模式**：**已落地（离屏）**〔证：`30f62a49` — `desktop/rf_sky_view.py:2193` — `self._sky_pointing_mode`〕。HUD 按 L0–L4 差异化；`RFSkyView` 开关默认关，只改 `rf_sky_view.py`，未改 `main_window.py`；真实相机/IMU 未验证（缺硬件）。
4. **望远镜前视**：**未验证**。无真实手机/望远镜/IMU；目镜后仅作可选增强。

### 已知阻塞

窄视场已解决〔证：`18d1a06b` — `mbdsdr_ai/sky_interaction.py:215` — `min_fov`〕：`ViewState` 支持按视图覆盖 `min_fov`（0.5–180°），桌面默认仍 30°。
目镜 2.3° 可原样渲染（`tests/test_sky_view_fov_override.py`）。

真实硬件（相机/IMU/GNSS 模块/望远镜）与真实星点解算仍未在夜空验证，相关项在
checklist 保持 `not_verified`（缺真实相机/IMU/望远镜与夜空窗口，无法验证）。



## 8. 借用与许可

- 只学机制不抄代码：Stellarium Web（投影/叠加）、aladin-lite（相机/目录渲染）、astrometry.net（GPL，仅算法与进程边界）。
- 可直接依赖（未来）：tetra3（Apache-2.0）、ASTAP（MPL-2.0）。
- 星表：HYG v4.1 已是 CC BY-SA 4.0，随仓分发；天文通 v42 不复制。

## 9. 本期不做

- 不做设备驱动 SDK、不做云端解算服务、不做手机 App 壳；
- 不改 `desktop/main_window.py`（改前先报备等点头）；
- 不引入新第三方依赖，不联网取星表。
