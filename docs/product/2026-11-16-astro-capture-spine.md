# 采集与教学骨架（2026-11-16 提案）

> 这份文档要解决的不是「下一步写什么功能」，而是「这条产品线的骨架对不对」。
> 每条结论都标了证据等级：**【文档】**= 平台/标准文档明确写的；**【实测】**= 公开实测或研究数据；**【推断】**= 由前两者推出来的，需要现场验证。
> 提案不构成实现承诺。可以推翻，但推翻要给证据。

---

## 0. 一页结论

### 0.1 这个项目是什么（主轴）

一句话：**把无线电、北斗、星空、AI 缝在同一条可回溯的时间线上**——软件定义、AI 定义、时空定义。

- 北斗给**绝对时空基准**（位置 + 授时）；
- SDR 给**信号在时空中的轨迹**（多普勒、TLE、过境）；
- 星空给**惯性参考系**（最古老、最不漂的方位与时刻基准）；
- AI 给**缝合与表达**（把三源变成人能懂、能复述的东西）。

课上那句判断决定架构：**单一时刻的数据没有价值，价值在序列。** 由此推出四条硬约束：

- 解算不求单帧最优，求**时间上自洽的轨迹**（IMU 积分 + 帧间约束 + 漂移建模），不是每帧独立解一次；
- 主产物是**一次观测的时间轴与档案**，不是一张截图；
- Stellarium 那 22 项不是装饰，是「时空可被人任意设定与回放」的不同切面；
- 「教学相长」不是软话：最终产物是**人**。

### 0.2 六条落地结论

1. 手机贴目镜之后，**主摄负责解算、长焦负责特写**，不是二选一，也不是「模型说切就切」。
2. 解算不自己造：**tetra3rs（MIT）或 cedar-solve（Apache-2.0）**，有先验姿态时 3 颗星即可；星表要 10–11 等、50–70 MB 级。
3. 长曝不可行（第三方 App 约 1 s 上限，且进光由 90 mm 物镜与 2.25 mm 出瞳决定），走**短曝 + 多帧堆栈**。
4. 时间对齐不是最硬的骨头（Android 相机与 IMU 同 `CLOCK_BOOTTIME`，CTS 要求 <1 ms）；**曝光与畸变才是**。
5. 解算不是差异化：Celestron StarSense Explorer 已用手机相机做到 0.25°，且申请专利。差异化只能在**中国星官 + 讲解 + 教学档案**。
6. 产品主指标从「解算成功率」换成「**24 小时三问复述率**」与「**二次打开率**」——一次球幕课只能推动约一半人，且惊叹会制造错误记忆。

---

## 1. 为什么现在要换骨架

- 现有计划是线性排期（M1 手机天球层 → M2 反向通道 → M3 取景/解算 → M4 叙事），每一段都假设「采集面已经存在」。实际上手机端一行相机代码都没有。
- 真正的耦合点在采集面：它同时决定姿态精度、曝光策略、标定方式和解算可行性。采集面不定，解算写出来也是废的。
- 因此这一版骨架把**采集面提到最前面**，并把「教学档案」提到与解算同级——因为它是留存与复述的唯一载体。

---

## 2. 事实基线

### 2.1 平台能力

| 事实 | 等级 | 对设计的影响 |
| --- | --- | --- |
| Android API 29+ 起，物理相机 id **可能不在 `getCameraIdList()` 里**，也不保证能 `openCamera(物理 id)` | 【文档】 | 不允许假设「能直接打开长焦」 |
| 官方路线是逻辑相机 + `OutputConfiguration.setPhysicalCameraId()`；一个逻辑流可换两个物理流，但仅 Android 9 强制保证、不含 reprocess | 【文档】 | 双流要做能力探测，拿不到就单流 |
| 验证当前实际镜头依赖 HAL 3.5+ 的 `LOGICAL_MULTI_CAMERA_ACTIVE_PHYSICAL_ID`（`TotalCaptureResult.getPhysicalCameraResults()`） | 【文档】 | 老机型上「切了」与「主摄裁了」不可区分，必须用角分辨率旁证 |
| CameraX 只有只读 `getPhysicalCameraInfos()`，**没有选物理镜头的 API** | 【文档】 | 采集层必须写 Camera2 / AVFoundation |
| iOS 可用 `builtInTelephotoCamera` 探测，可用 `primaryConstituentDeviceSwitchingBehavior = .locked` 锁定 constituent；`virtualDeviceSwitchOverVideoZoomFactors` 给出切换阈值 | 【文档】 | iOS 可控性优于 Android，但切换后内参/主点变化 |
| 曝光/ISO 可自定义：`setExposureModeCustom(duration:iso:)`，duration 受 `activeMaxExposureDuration` 限制；第三方 App 常见上限约 1 s | 【文档】+【实测】 | 长曝路线直接排除 |
| 锁无穷远：`setFocusModeLocked(lensPosition: 1.0)` | 【文档】 | 对焦必须锁，否则拉风箱 |

### 2.2 光学

| 事实 | 等级 | 影响 |
| --- | --- | --- |
| 出瞳 = 口径 ÷ 倍率：90 mm ÷ 40× ≈ **2.25 mm** | 【推断，可现场量】 | 决定手机能拿到多少光 |
| 潜望长焦实焦 16–24 mm、光圈 f/2.5–f/4.9，入瞳 4.8–7.4 mm | 【实测，机型规格】 | 入瞳全大于出瞳 → 手机光圈不改变曝光，曝光由望远镜决定 |
| 目镜后有效焦距 = 手机焦距 × 倍率（如 16.3 mm × 40 ≈ 652 mm，等效 f/7.2） | 【文档】 | 像素尺度可预算 |
| 长焦对角视场约 22°，小于目镜视场 50–60° → 只截像圈中心，实得约 0.5° | 【推断，可现场量】 | 长焦不适合解算 |
| 主摄视场远大于目镜 → 画面是圆形像圈，约占直径 65% / 面积 42% | 【推断，可现场量】 | 解算前要裁内接圆 |

### 2.3 解算与检测

| 事实 | 等级 | 影响 |
| --- | --- | --- |
| tetra3rs：MIT，Rust + Python，含质心/畸变/WCS/协方差；先验姿态模式**最少 3 颗星**；2048² 质心 15–26 ms 单线程 | 【文档】 | 首选 |
| cedar-solve / CedarDetect：Apache-2.0，RPi4 级 ARM **<10 ms/MP**，含热像素与拖尾剔除 | 【文档】 | 备选/检测层可独立采用 |
| astrometry.net GPL-2.0、ASTAP MPL-2.0 + Delphi | 【文档】 | 商用出局 |
| 窄视场 1–2° 需要 10–11 等星表、50–70 MB 级索引；0.5° 以下实测常失败 | 【文档】+【实测】 | 解算只用主摄 |
| 星表体积：BSC5 9,110 星（>10° 勉强）、Hipparcos 118k（>3°）、Tycho 1.06M | 【文档】 | 必须上 Tycho 密度 |

### 2.4 竞品与教学

| 事实 | 等级 | 影响 |
| --- | --- | --- |
| Sky Guide / Star Walk / Stellarium Mobile / Star Tracker 全部走罗盘，无相机解算 | 【文档】 | 相机解算仍是少数派 |
| SkySafari 官方承认罗盘「可能差 10° 以上」 | 【文档】 | 纯 IMU 达不到 1° 级 |
| Celestron StarSense Explorer 用手机相机解算，标称 0.25°，已申请专利 | 【文档】 | 解算有专利风险，差异化必须上移 |
| AstroHelm：≥8 星才解、曝光锁 1 s、<1 s 出结果 | 【实测】 | 可用性基线 |
| Stellar Finder：Apache-2.0，CameraX + 内嵌 ASTAP，1–256 帧堆栈，含畸变校正 | 【文档】 | 堆栈路线有先例 |
| 一次球幕课约 50% 学生理解升一级；一年后仍显著保持（有复习锚点） | 【实测】 | 必须留下可复习物 |
| 惊叹会提高对**未出现内容**的错误记忆（N=131） | 【实测】 | 每屏配一句可复述的话 |
| 拍照会降低展品细节记忆 | 【实测】 | 先讲后拍 |
| iNaturalist：人工核验 3.5 min/条，52% 正确、35% 不确定，冷门类群断供 | 【实测】 | 不抄众包鉴定 |
| Merlin 86% vs 人 92%；「This Is My Bird」回流训练 | 【实测】 | 抄「加权纠错」 |
| Seek：离线、不出设备、无账号 | 【文档】 | 抄隐私形态 |
| 星官计数本身有版本差异：皇祐 283 官/1464 星、仪象考成 277 座/1319 星、Stellarium chinese 300 官/3240 星 | 【文档】 | 讲解必须标底本 |
| Stellarium 星官数据 GPL-2.0；NADC 古天文参考星表 15,537 颗 CC BY 4.0 | 【文档】 | 可交叉校验，不可并入发布物 |

---

## 3. 六个决策

### D1 双流分工：主摄解算，长焦特写

- **决策**：默认用主摄拿全像圈做解算与主叠加；长焦仅在「讲解特写」时启用，且启用前必须通过角分辨率验证。
- **依据**：长焦视场约 22°，小于目镜视场，只截像圈中心 → 实得约 0.5°，落在解算失败区；而主摄虽然像素尺度粗，但能覆盖 1.2° 全像圈。
- **否掉的替代**：「切到潜望镜提高分辨率」——分辨率确实提高（14 mm 对 4.2 mm），但天区缩小到解算不可靠，得不偿失。
- **停机条件**：现场实测主摄在目镜后星点数 < 3 颗可用（说明进光或对焦不达标），则先解决曝光/对焦，不上解算。

### D2 采集层自建，能力探测输出三态

- **决策**：Android 用 Camera2、iOS 用 AVFoundation；能力清单字段一律 `can / cannot / unknown` 三态，`unknown` 必须运行时探测后才允许用。
- **依据**：CameraX 无法选物理镜头；物理 id 不保证可开；验证依赖 HAL 版本。
- **否掉的替代**：CameraX 一把梭——省事但不能满足「镜头可解释切换」。
- **停机条件**：目标机型上连「主摄 + 锁焦 + 手动曝光」都拿不到 → 该机型直接进黑名单，不硬撑。

### D3 短曝 + 多帧堆栈，放弃长曝

- **决策**：单帧曝光上限按 `activeMaxExposureDuration` 取，默认 1/4–1 s；星点检测在堆栈后的合成帧上做。
- **依据**：第三方 App 曝光上限约 1 s；进光由出瞳决定；Stellar Finder 已验证 1–256 帧堆栈可行。
- **否掉的替代**：长曝 + 跟踪——XA90P 是纯手动赤道仪，无电机无编码器，无法跟。
- **停机条件**：堆栈后在 1.2° 内凑不到 3 颗可用星 → 降级为「认天区」，不报单星。

### D4 解算接现成库，先验姿态模式

- **决策**：优先 tetra3rs，备选 cedar-solve；一律用「IMU 先验 + 局部匹配」，不做全天盲解；索引到 10–11 等。
- **依据**：先验模式最少 3 星；窄视场盲解需要更大星表且更慢；IMU 已能给到几度，正好缩小搜索空间。
- **否掉的替代**：自研 quad/tetra 哈希——没有收益，且容易在窄视场踩坑。
- **停机条件**：先验误差 > 5° 时解算成功率 < 门限 → 先修 IMU/罗盘，不调解算参数。

### D5 教学档案与复述卡是产品主产物

- **决策**：一次观测自动产出「照片 + 看到的星官 + 我纠正过什么 + 三问复述卡」；24 小时后推送复习；主指标为复述率与二次打开率。
- **依据**：单次球幕课仅约一半人提升；一年后保持依赖复习锚点；惊叹制造错误记忆；拍照降低细节记忆 → 先讲后拍。
- **否掉的替代**：以「识别成功率」「叠加帧率」为主指标——那是技术指标，不是产品指标。
- **停机条件**：如果用户连续两次不打开复述卡，说明产物形态不对，重做而不是加推送。

### D6 星官内容标底本，外部数据只校验不并入

- **决策**：每官标注出处（皇祐 / 仪象考成 / 续编）；与 Stellarium chinese（300 官 / HIP 映射）和 NADC 星表（CC BY 4.0）做交叉校验，不并入发布物。
- **依据**：星官计数本身有版本差异，讲人文的产品不能在这个问题上含糊；Stellarium 数据是 GPL-2.0。
- **否掉的替代**：「以 Stellarium 为准」——许可与底本都不合适。
- **停机条件**：交叉校验出现系统性坐标偏差 > 0.2°，先查自己的坐标链，不改数据。

---

## 4. 五个契约（结构草案，不实现）

```text
DeviceProfile
  cameras[]: { physical_id, role(hint), focal_mm, f_number, sensor_diag_mm,
               max_exposure_s, iso_range, focus_lock, can_open_directly }
  imu:       { rate_hz, timebase, can_align_to_camera }
  gnss:      { has_pps }
  probed_at, probe_version

FrameEnvelope
  { frame_id, lens_id(logical|physical), t_capture_ns, exposure_s, iso,
    focus_locked, rolling_shutter_skew_ns, intrinsics_id, width, height, image }

CalibrationRecord          # 键 = 手机型号 × 镜头 × 目镜 × 装夹指纹
  { key, center_px, focal_px, distortion[], roll_deg,
    residual_deg, valid_since, expires_at, sample_count }

CaptureIntent              # 模型只能产出这个，不许碰硬件
  { action: select_lens|set_exposure|stack,
    want, reason, candidates[], fallback, confidence }

ObservationArtifact        # 一次观测的产物
  { frames[], star_names[], corrections[], retell_cards[],
    site_name, t_start, t_end, schema_version }
```

规则：
- 没有 `t_capture_ns` 的帧不进解算。
- 没有 `intrinsics_id` 的帧只能画天区，不能画单星。
- `CalibrationRecord` 过期或装夹指纹变化即失效，界面上必须显示「已失效」。
- `CaptureIntent` 的执行结果必须回读验证（物理 id 或实测角分辨率），失败要带原因返回。

---

## 5. 阶段与验收

| 阶段 | 做什么 | 验收（可观察） | 明确不做 |
| --- | --- | --- | --- |
| S0 设备探针 | 输出 `DeviceProfile`；用一对已知角距的星实测视场与像素尺度 | 同一台机连跑两次，视场实测值差 < 2% | 不写相机功能、不做解算 |
| S1 帧信封 | 单流抓帧 + 时间戳 + 曝光/对焦锁定 + 堆栈 | 能在画面上标出「这一帧是几时几分几秒的天空」，堆栈后星点数 ≥ 3 | 不做多摄 |
| S2 标定账本 | 装夹后现场解一次相似变换，存残差与有效期 | 同一装夹重复装夹，残差 < 0.3°；换装夹能识别失效 | 不做自动装夹检测 |
| S3 采集策略 | `CaptureIntent` 的产出、执行、验证、回滚 | 故意请求不可用的镜头，系统给出可解释拒绝而非静默降级 | 不做模型训练 |
| S4 解算 | tetra3rs/cedar-solve 接入，先验姿态模式 | 先验误差 ≤ 5° 时，1.2° 视场解算成功率达标（门限现场定） | 不做盲解 |
| S5 教学档案 | 分轨记录 + 复述卡 + 回放 | 一次真实观测后，能生成一份可给别人讲的档案 | 不做社交分享 |

顺序不能换：S0 的输出是 S1–S4 的输入；S5 依赖 S1 的帧信封。

---

## 6. 风险

| 风险 | 现状 | 处理 |
| --- | --- | --- |
| 专利 | StarSense Explorer 已有「手机相机解算定位」专利 | 只做「叠加 + 讲解 + 档案」，解算用开源库；必要时做专利检索 |
| 许可 | astrometry.net GPL、Stellarium 星官 GPL | 解算用 MIT/Apache；星官只做交叉校验 |
| 隐私 | 观测地点、未成年人 | 默认不出设备；分享卡不含坐标；学 Seek 不学 iNaturalist |
| 物理上限 | 90 mm + 2.25 mm 出瞳 + 手机小底 | 接受「拍得到星点、拍不出大片」；定位是认星教具，不是深空摄影 |
| 机型碎片 | 物理镜头行为无契约 | 能力探测 + 黑名单；宁可少支持机型 |
| 内容准确性 | 星官版本差异 | 标底本 + 交叉校验 + 不确定就说不确定 |

---

## 7. 待裁决

1. **目标机型范围**：只保自己手上机型（可硬编码白名单），还是要面向同好（需能力探测 + 降级矩阵，工作量约三倍）。
2. **手机形态**：装夹在目镜后（窄视场、锁参数），还是手持（宽视场、认天区）。两条路标定与交互完全不同。
3. **是否出货**：出货要处理 iOS/Android 双端手动模式差异与机型矩阵；自用可先单端跑通。

---

## 8. 与仓库现状的映射

| 能力 | 现状 | 位置 |
| --- | --- | --- |
| 手机相机采集 | 无 | `mobile/lib` 无任何相机代码、pubspec 无相机依赖 |
| 手机姿态 | 有（罗盘 + 加速度计 + 磁力计，欧拉角） | `mobile/lib/services/orientation_service.dart` |
| 手机→桌面通道 | 有，但只有请求-响应，无高频流 | ControlHub |
| 桌面帧输入 | 无 | — |
| 星点检测 | 无 | — |
| 标定落盘 | 无 | — |
| 星官数据 | 有（287 官） | `mbdsdr_ai/skyengine/xingguan.py` |
| 天球坐标链 | 有 | `mbdsdr_ai/celestial_geometry.py` |
| 叠加数据层（T08） | 有，未接热路径 | `mbdsdr_ai/overlay_asterisms.py` |
| 指向融合契约 | 有，无生产调用 | `mbdsdr_ai/pointing_fusion.py` |
| 录像/抓帧 | 无 | — |

---

## 附：外部参考（按用途）

- 多摄与物理镜头：<https://developer.android.com/media/camera/camera2/multi-camera>、<https://source.android.com/docs/core/camera/multi-camera>
- iOS 虚拟设备与 constituent：<https://developer.apple.com/documentation/avfoundation/avcapturedevice/primaryconstituentdeviceswitchingbehavior-swift.enum>
- 时间戳：<https://developer.android.com/reference/android/hardware/camera2/CameraMetadata#SENSOR_INFO_TIMESTAMP_SOURCE>
- 解算库：<https://tetra3rs.dev/>、<https://github.com/smroid/cedar-detect>
- 教学研究：<https://juliaplummer.com/papers/Plummer-Small-2018.pdf>
- 中国星官：<https://nadc.china-vo.org/wwt/why/chineseheaven.htm>、<https://github.com/Stellarium/stellarium/tree/master/skycultures/chinese>

---

## 9. 与 Stellarium 的功能对照（2026-11-16 实测盘点）

判定口径：**有** = 实现 + 真实调用点；**部分** = 有数据/半截实现但缺关键环节；**无** = 仓库内 grep 不到，或只有注释/占位。
星表与 DSO 规模以 `docs/star-catalog.md`、`docs/dso-catalog.md` 为准。

| # | 项 | 判定 | 证据 | 缺什么 |
|---|---|---|---|---|
| 1 | 默认星表 ≥60 万 | 无 | `docs/star-catalog.md` | 实有 83,479（V≤9.0） |
| 2 | 附加星表 ≥2.2 亿 | 无 | — | 无 Gaia 级附加表 |
| 3 | 默认 DSO ≥8 万 | 无 | `docs/dso-catalog.md` | 实有 117 条 |
| 4 | 附加 DSO ≥100 万 | 无 | — | — |
| 5 | 星座 >40 文化 | 无 | `mbdsdr_ai/skyengine/xingguan.py` | 仅 IAU 88 + 中国 287 |
| 6 | 日历 >35 文化 | 无 | `web/product/src/20-i18n.js:30` | 只有文案键，无实现 |
| 7 | 星云图像（梅西叶） | 无 | `web/product/src/30-sky.js:446` | 只有类型符号，无图像 |
| 8 | 逼真银河 | 无 | `web/product/src/00-core.js:58` | 显式 `milkyway:false` + 空态 |
| 9 | 大气散射 | 部分 | `30-sky.js:472-494` | 径向渐变，自述非辐射标定 |
| 10 | 6D 天体测量 | 部分 | `14-astro-frame.js:194` | 自行有，无视向速度 |
| 11 | 双星轨道建模 | 无 | `30-sky.js:446` | 只画双星符号 |
| 12 | 行星及其卫星 | 部分 | `mbdsdr_ai/solar_system.py` | Python/桌面有，Web 未接 |
| 13 | DSS / HiPS 巡天 | 无 | `web/product/docs/06-compliance.md:32` | 明确未引入贴图/星表 |
| 14 | 平滑缩放 | 有 | `31-celestial.js:573-583` | 乘因子 + 缓动 |
| 15 | 时间控制 | 部分 | `mbdsdr_ai/new_spacetime.py:142-190` | 桌面可倒放/跳时，Web 只暂停/倍率 |
| 16 | 多语言 | 部分 | `web/product/src/20-i18n.js` | 三语（zh/ja/en） |
| 17 | 脚本接口 | 无 | — | 天象侧无用户脚本 |
| 18 | 鱼眼投影 | 部分 | `mbdsdr_ai/celestial_geometry.py:697` | 桌面有 az-eq-dist，Web 仅 2 种 |
| 19 | 球面投影（圆顶） | 无 | — | 无球面镜几何校正 |
| 20 | 键盘 + GUI | 有 | `desktop/` 主窗口快捷键 | — |
| 21 | HTTP / 远程 API | 有 | `web/product/src/10-data.js:138`、`mbdsdr_ai/remote_control.py` | 面向 SDR 控制 |
| 22 | 望远镜控制 | 部分 | `mbdsdr_ai/gimbal.py` | rotctld 云台，无 GOTO/同步 |
| 23 | 多种坐标网格 | 部分 | `web/product/src/30-sky.js:356-380` | 地平 + 赤道，无黄道 |
| 24 | 岁差圈 | 无 | `14-astro-frame.js:82` | 有矩阵，无可视化 |
| 25 | 星体闪烁 | 无 | — | — |
| 26 | 流星 | 无 | — | — |
| 27 | 彗尾 | 无 | — | — |
| 28 | 掩食模拟 | 无 | — | — |
| 29 | 新星 / 超新星 | 无 | — | — |
| 30 | 系外行星位置 | 无 | — | — |
| 31 | 目镜视场模拟 | 无 | — | 有换算函数，无视场圈 |
| 32 | 3D 地景 | 部分 | `30-sky.js:114-128` | 2D 高度场示意图 |
| 33 | 插件系统 | 部分 | `mbdsdr_ai/plugin_manager.py` | 仅 SDR 侧，`plugins/` 为空 |
| 34 | 在线添加太阳系天体 | 无 | — | — |
| 35 | 自定义导入 | 无 | — | — |

**统计：有 3 / 部分 10 / 无 22。**

### 9.1 这 22 项不是「不做」，是「尚未定义化」

上一版这里写过「约 20 项明确不做」——那句话是错的，已删除。项目自己的定义是
**软件定义 + AI 定义 + 时空定义无线电**（`docs/learn/2026-11-14-radio-ai-training-lecture-actionable-list.md:90`）。
「时空定义」意味着天体的位置、文化、视域、事件都由**数据与模型**定义，人来做定义的主体。
把 22 项划成「别人的赛道」，等于把这个项目的定义权让出去。

正确做法不是写 22 套代码，是把它们收敛到**四个定义层**，每层一个 schema + 一个注册表 + 一个加载顺序：

| 层 | 定义什么 | 承载的条目 | 现有底座 | 缺什么 |
|---|---|---|---|---|
| L1 天体定义 | 有什么天体、在哪、长什么样 | 1–4、7、10、11、12、30 | `mbdsdr_ai/data/full_sky_hyg_v41.npz`、`solar_system.py`、`skyengine/*` | 分级星表包、`OrbitModel` 统一接口、按视场按需加载 |
| L2 文化定义 | 怎么划分、怎么命名、怎么纪年 | 5、6、34 | `skyengine/xingguan.py`、`20-i18n.js` | `skyculture/` 包格式（星座+星名+故事）、`CalendarSpec` 解释器 |
| L3 视域定义 | 看见什么、怎么投影、什么底图 | 8、9、13、18、19、23、24、31、32 | `PROJECTION_INFO` 注册表、`celestial_geometry`（4 种投影） | `TileSource`（巡天瓦片）、`LandscapeSource`、`AtmosphereModel`、视场圈 |
| L4 时序定义 | 随时间发生什么 | 15、17、25–29 | `new_spacetime.py` 时间轴、SGP4 轨迹 | `EventModel.next_state(t)` 统一调度 |

映射后每条都变成「一份定义数据 + 一个解释器」，例如：
- 40+ 星座文化 = L2 的 `skycultures/<id>/*.json` 包，加载器 + 校验，不是 40 套画线代码；
- 35+ 日历 = L2 的历法参数 + 节日表，一个解释器；
- 2.2 亿星 = L1 的分级数据包（按星等分层、按视场按需载入），不是一次性加载；
- DSS/HiPS = L3 的 `TileSource` 接口，先接本地瓦片目录也能验；
- 流星/彗尾/掩食/新星/系外行星 = L4 的事件函数，统一按时刻求值；
- 双星轨道 = L1 的 `OrbitModel`，与行星共用接口；
- 插件/在线添加/自定义导入（33–35）= L1–L4 注册表对外开口，同一个机制。

**所以结论改判：22 项全部在范围内，但落地方式是「4 个定义层 + N 个定义包」，不是 22 段硬编码。**
第一刀不是挑一项做，而是把现有数据（IAU 88、中国星官 287、117 DSO、4 种投影）改成定义包形式加载、行为不变、测试锁住——这是唯一能同时开门又不返工的做法。

## 10. 现在必须靠真机才能验证的六项（离线部分已先行）

这些是**验收条件**，不是停工理由。能离线证明的部分（能力契约、采集导演、
光学换算、原始证据采集）已经先写先测；真机只补证据与解析器。

| 阻塞 | 原因 | 解除条件 |
|---|---|---|
| 手机端相机采集 | 本机无 Flutter / Dart，写了无法运行验证 | 装 Flutter 工具链，或改用原生工程 |
| 真机能力档案 | 无 adb 在线设备 | 插上手机 + 打开 USB 调试，跑 `tools/probe_device.py collect` |
| dumpsys 解析器 | 无真机原始输出，格式随厂商/版本变 | 同上，拿到原始件后按真格式写 |
| 两星实测视场 | 需要真实星空照片 | 一次户外拍摄 + 已知星对角距 |
| Web 端行星/卫星 | 星象仪未接 `solar_system.py` | 定投影契约后接线 |
| 星表加深到 10–11 等 | 需 Gaia/Tycho 子集与许可确认 | 决定目标星等与体积上限 |

已经先行的离线部分：

- `mbdsdr_ai/camera_capability.py`：三态能力契约 + 镜头意图裁决 + 光学换算；
- `mbdsdr_ai/capture_plan.py`：采集导演（选镜头、算倍率、缺镜头时说明代价）；
- `mbdsdr_ai/camera_tools.py`：模型工具面 `camera_capture_plan`（只读规划）；
- `mbdsdr_ai/gnss_sky_reference.py`：GSV 卫星 SNR → 粗指向拟合 + 与 IMU 的一致性检查
  （分辨力不足时拒绝，不硬给角度）；
- `mbdsdr_ai/gnss_tools.py`：模型工具面 `gnss_sky_fit`（吃 GSV 帧 + IMU 角度，只读计算）；
- `tools/probe_device.py`：真机原始证据采集（无设备诚实 SKIP）。

