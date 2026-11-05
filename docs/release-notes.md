# 发布说明：分发形态 / 用户数据落点 / 校验和 / 安装器决策

状态：2026-11-06，安装器裁定 **B（绿色包，不提供安装器）**。本文是机检
`scripts/release_manifest.py` 的 `installer` 项与版本项的引用出处；改动结论需同步
改机检文案，否则文档与实现掰腕子按 bug 处理。

## 1. 分发形态：绿色包（解压即用，删目录即卸载）

- **不提供安装器**：全仓 glob `*.iss` / `*.nsi` / `*.wxs` / `*.msix` /
  `*.appxmanifest` / `*.pkgproj` **0 命中**。这是发布决策，不是缺件——
  机检状态因此记为 `BY-DESIGN`（理由见 `docs/packaging.md` §9.4），而不是 `SKIP`。
- **解压即用**：程序是 onedir 目录，`MBDSDR.exe` 与 `_internal/` 必须放在一起。
  只拷 exe、或删 `_internal` 里的文件，启动时会给出「安装包解压不完整」的原文提示
  （见 `docs/packaging.md` §4.1）。
- **删除目录即卸载**：不写注册表键、不建快捷方式、不起服务，没有卸载程序可运行。
  卸载 = 关闭程序后删除整个解压目录。
- **两条产物线的现状**：
  - C++ 原生端：`cpp/build-local/dist/mbdsdr-<ver>-win64-portable.zip`，由
    `scripts/package_windows.ps1` 生成（`cmake --install` + `windeployqt` + 干净
    PATH 冒烟 + 压 zip + 写 `.sha256`）。
  - Python 桌面端：`scripts/release_check.py` 的 build 步产出
    `<dist>/MBDSDR/MBDSDR.exe` + `_internal/`（默认 `.tmp/pyi-distN`）。**该链路
    当前不生成 zip 与 `.sha256`**；对外发布时须由发布者按 §3 的格式压缩并补登
    侧车，否则下载页没有可校验值。

## 2. 用户数据落点（删除程序目录前先看这里）

| 数据类型 | 落点（按实际代码顺序） | 删除程序目录的影响 |
|---|---|---|
| 配置 | Windows：`%APPDATA%\mbdsdr\desktop_settings.json`；Linux/macOS：`~/.config/mbdsdr/desktop_settings.json`。读取损坏时备份为 `desktop_settings.json.corrupt-<时间戳>` | **不受影响**（在用户目录，不在程序目录） |
| 录音 | 默认 `<用户目录>\MBDSDR\recordings`（与程序目录无关）。设置「存储与日志」可手选任意目录；手选到程序目录内时，首次录音强制弹窗确认（默认按钮是「改用用户目录并录音」），选「仍用当前目录」才记入 `record_dir_inside_program_ack` 并不再追问 | **不受影响**（默认在用户目录）。只有用户手选到程序目录内并明确确认「仍用当前目录」时，录音才会随程序目录删除 |
| 启动日志 | 顺序：环境变量 `MBDSDR_STARTUP_LOG` → 第一个非 C 盘 `X:\mbdsdr\logs\mbdsdr_startup.log` → `<程序目录>\logs\mbdsdr_startup.log` → `%PROGRAMDATA%\MBDSDR\logs\mbdsdr_startup.log`；全部不可写则只进内存（200 行），错误出口注明「本次无持久日志」 | 落在 `<程序目录>\logs` 时会被删除；落在盘符根或 ProgramData 时保留 |
| 审计日志 | `<程序根>\logs\tx_audit.jsonl`（写不进去会告警） | 落在程序目录时会被删除 |

以上就是「删除目录即卸载」的完整边界：**配置不会丢，录音默认也不会丢**（默认在
用户目录）。唯一例外是用户把录音目录手选到程序目录内、并在首次录音弹窗里选了
「仍用当前目录」——这种情况下删除程序目录会一并删掉录音。这一条写进
机检文案，不允许再说「删除程序目录不会误删这些数据」。

**决策（2026-11-06）**：录音默认目录从「程序目录内」改到「用户目录」。三方案
对比：A 只改默认（改动小，但老配置仍可能指向程序目录内，且不会提醒用户）；
B 只加首次提示（默认位置仍然危险，用户在提示上点错的代价就是丢录音）；
C 两者都做（默认安全 + 手选/旧配置再拦一道）。选 **C**。迁移代价：旧配置里的
相对路径（旧语义 = 程序目录旁）统一按旧根解析成绝对路径，不随启动 cwd 漂移；
旧绝对路径原样保留，首次录音时命中提示；已有录音文件**不搬迁、不复制**（避免
跨盘拷贝失败与误删），要挪由用户自己挪或在提示里改目录。

## 3. 校验和怎么验

发布附件约定：`<包名>.zip` + `<包名>.zip.sha256`，侧车内容为
**小写 64 位十六进制 + 两个空格 + 文件名**（`scripts/package_windows.ps1`
用 `Set-Content -Value "$sha  $(Split-Path $zip -Leaf)"` 生成）。

验证（Windows PowerShell，任选一条）：

```powershell
# 1) 直接比对侧车
Get-Content .\mbdsdr-<ver>-win64-portable.zip.sha256
(Get-FileHash .\mbdsdr-<ver>-win64-portable.zip -Algorithm SHA256).Hash.ToLower()

# 2) 只用系统自带工具（无 PowerShell 环境时）
certutil -hashfile .\mbdsdr-<ver>-win64-portable.zip SHA256
```

两个值逐字一致才解压运行；不一致说明下载被截断/篡改，**不要解压，不要运行**。
校验的对象是压缩包本身（先校验再解压），不是解压后的单个文件。

## 4. 安装器 A 路（Inno Setup）决策记录

- **当前不启动**。若以后启动，必须**同时**满足下面两个触发器条件：
  1. 出现**企业部署或自动更新**需求（需要注册表登记/快捷方式/静默安装/升级覆盖）；
  2. **代码签名方案落地**（安装器与主程序 exe 都有可验证签名，否则安装器的
     「信任外观」与未签名载荷自相矛盾）。
- 启动后的硬性要求：
  - 安装器版本号从 `mbdsdr_ai/VERSION` 派生，不得手写；新增的版本派生点必须登记到
    `docs/packaging.md` §9.1 的清单，否则机检 FAIL。
  - 必须补「装 → 启动到窗口出现 → 卸载后残留检查」冒烟。
  - 仓里一旦出现安装器描述文件而没配冒烟，`release_manifest.py` 的 `installer`
    项直接 FAIL（防止靠删位置置换绿）。
