# MBDSDR Spatial（PICO 空间观测台）

MBDSDR 的 PICO Spatial（Android/Kotlin）入口。它不是另一个产品：四块内容与桌面、手机共用同一套语义，数据只来自导出的 **spatial bundle**，不联网、不接 SDR、不伪造星空。M4 的只读网关客户端已在 `net/DeviceGatewayClient.kt` 就绪，但四窗 UI 尚未接线（见下文「多设备网关」）。

```
头顶  —— 真实星表 + 站址/时刻算出的 alt/az
听见  —— 真实录制 IQ 的频谱帧（只读回放）+ 解调出的 WAV
回看  —— 观测档案 timeline（调谐/增益/解码命中/读空）
此刻  —— 站址、时间来源、证据与阻塞项
```

## 版本基线（实测可构建）

| 项 | 版本 |
| --- | --- |
| Spatial BOM | 6.2.8 |
| AGP / Kotlin | 8.8.2 / 2.0.0 |
| compileSdk / minSdk / targetSdk | 35 / 35 / 35 |
| abiFilters | arm64-v8a |
| Gradle | 8.10.2 |

依赖仓库包含 `https://artifact.bytedance.com/repository/Volcengine`（Spatial 制品）。

## 构建

```powershell
# 用 Android Studio 自带 JBR，不要用 PATH 上的 Oracle java
$env:JAVA_HOME = '<Android Studio>\jbr'
$env:GRADLE_USER_HOME = '<你的 Gradle 缓存目录>'
cd spatial
.\gradlew.bat testDebugUnitTest --no-daemon
.\gradlew.bat :app:assembleDebug --no-daemon
```

产物：`app/build/outputs/apk/debug/app-debug.apk`。`ANDROID_HOME` 需指向含 platform 35 的 SDK；不要为此工程设置 `http.proxy`。

## 数据：spatial bundle

由仓库根目录的 Python 侧导出（**头显不跑 Python**）：

```powershell
python scripts/export_spatial_bundle.py <档案>.observation.json -o <输出目录> `
  --frames 48 --bins 128 --stars-limit 2000 `
  --audio-out <输出目录>\audio.wav --demod wfm
```

产出 `bundle.json`，字段：

- `site / time`：站址与时刻及其来源（`gnss|manual|system|none`）
- `sky.stars[]`：`name/ra_deg/dec_deg/mag/hyg_id/alt_deg/az_deg/visible`（alt/az 为**几何值、不折射**，北 0 东 90）
- `hyg_id`：HYG v4.1 星编号（随仓星表必有；外部 `--stars-json` 没给时为 `null`），用于查自行侧车
- `sky.altaz_pm_applied` / `sky.altaz_pm_missing`：参与地平计算的星里，自行传播成功 / 未传播（无 `hyg_id`、查不到或侧车缺失）的条数；站址或时刻不可用时都为 0
- `ra_deg/dec_deg` 仍是 J2000；只有 `alt/az` 含自行传播，与桌面同源，几何、不含折射
- `spectrum`：`center_freq_hz/sample_rate_hz/total_samples/frames[{at_s, db[]}]`（真实能量，Hann+FFT+分箱）
- `audio[]`：`path/label("demod:<mode>")/utc`（从真实 IQ 解调，非合成）
- `timeline[]`：与观测档案逐条一致
- `evidence / blockers`

缺什么就写进 `blockers`，界面对应窗口显示原因，不用默认值填充。

## 演示脚本（约 3 分钟）

1. **0:00** 启动应用：四窗空态，逐字显示「还没有加载观测包」——不画装饰星空。
2. **0:20** 点「加载观测包」，选一份 `bundle.json`：头顶出现星点，听见出现频谱，回看列出事件，此刻显示站址来源。
3. **1:00** 抬头看星空：指出一颗有名字的星，念出 `alt/az`；说明「这是按档案里的站址与时刻算的，没联网」。
4. **1:30** 点那颗星：显示「名称 · 星等 · alt/az」；回看只在某条事件的 `detail` 里确有同名目标时才用 `◆` 标出（当前导出的调谐/增益等频谱事件通常没有，所以不会高亮——不把两套东西硬绑在一起）。
5. **2:00** 点「播放录音」：播放从这次观测 IQ 解调出的 WAV；标签显示录音时间与来源。
6. **2:30** 打开回看：指出 `调谐 / 解码命中 / 读空 / 数据间断`，说明「时间轴里的事件和录音文件、SigMF annotations 是同一份」；点一条 `调谐`，「听见」会跳到该时刻（秒数由 `samples / sample_rate` 换算，和 CLI 的 `replay_plan` 同口径）。事件中文名与桌面 `replay_timeline` 同一份词表（跨语言钉扎测试锁死）。
7. **2:50** 收尾：说明边界——离线、只读包、不接 SDR、Mesh/ML 未验证。

## 多设备网关（只读客户端，M4 数据层）

`app/src/main/java/com/mbdsdr/spatial/net/DeviceGatewayClient.kt` 是头显侧的
只读网关客户端，与手机/桌面共用同一 HTTP 契约：

- `GET /health`（免 token）、`POST /pair`（配对码换 token）
- `GET /roster`、`GET /panorama`、`GET /poses`、`GET /fuse`
- `POST /pose` 仅 `lens/eyepiece`；`handheld/headset` 在**本地即拒绝**，不发请求

传输可注入（`GatewayTransport`），JVM 单测完全离线；默认实现是
`HttpURLConnection`（6 s 超时）。**诚实边界**：目前只到数据层——四窗 UI 尚未
接网关、`/passes` 增量端点未实现、也未在 PICO 真机上验证；应用默认仍是
「只读 bundle、不联网」。

## 与官方样本的关系

参照了 `PICO-Spatial-SDK-Sample-SpatialUI` / `SpatialAudio` / `WelcomeSpace` 的**调用方式**（`launch(::mainApp)`、`DefaultWindowContainer`、`WindowContainer` + `Placement`、`PicoTheme`、manifest 的 `pico.spatial.windowcontainer.*`）。样本为 Apache-2.0；本工程**未复制**其图片、3D 图标、模型或页面代码，也未引入 Coil/导航/Spotless 等示例模块。

## 参赛说明（旧项目的空间化升级）

按 PICO Space Program 2026 规则，本作品属于「已有项目的重大空间化升级」，不是重复参赛：

- **赛前已有**：MBDSDR 桌面端（PySide6）、CLI、手机端（Flutter）、AR 指天；观测档案 `*.observation.json` 与时间轴、真实录制/解调、星表与过境。
- **赛期内新增**：`spatial/` 工程（Kotlin Spatial）、`mbdsdr_ai/spatial_bundle.py` + `scripts/export_spatial_bundle.py`、Kotlin 侧 bundle 解析/投影/四窗/选星/档案/站址/过境/播放与 JVM 单测。
- **复用而非重做**：星表 HYG、解调链、观测档案 schema、噪声与空态规则全部来自既有内核；头显只读离线包，不另造数据格式。

## 诚实边界

- 本机构建与 JVM 单元测试可验证；**PICO 模拟器在本机（Intel UHD）启动即崩**，真机未到手，因此四窗实际显示、手势、空间音频均**未运行验证**。
- 频谱是回放，不显示任何「实时/正在接收」字样。
- `alt/az` 为几何值，不做大气折射；站址缺失时不渲染星空。
- 空间音频（声源绑定方向）需要 PICO 真机 SDK，当前用 `MediaPlayer` 平面播放。声源世界锁定所需的坐标数学（地平坐标 → 世界单位向量、头相对旋转、距离衰减）已在 `audio/SpatialPlacement.kt` 用 JVM 单测固定；SDK 节点绑定与实际出声**未验证**。
- 未在真实夜空、未在 PICO 设备上做端到端验收。
