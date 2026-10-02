# P1 · 包名定稿与 release 构建指引

> 本批把脚手架默认包名 `com.example.mbdsdr_mobile` / `com.example.mbdsdrMobile`
> 统一改成正式包名 **`mbdsdr.app`**（Android `applicationId`/`namespace`、iOS
> `PRODUCT_BUNDLE_IDENTIFIER` 两端一致；RunnerTests → `mbdsdr.app.RunnerTests`），
> 并给出「密钥永不入库」的 release 签名流程。
>
> 验收硬项：`flutter analyze` 0 issue、`flutter test` 288 基线不破、
> `flutter build apk --debug` 云内真编译通过。

---

## 1. 本批改了什么

| 端 | 文件 | 改动 |
|---|---|---|
| Android | `mobile/android/app/build.gradle.kts` | `namespace`/`applicationId` → `mbdsdr.app`；清理脚手架 TODO；release 签名改为「`key.properties` 存在才启用」（见 §4） |
| Android | `mobile/android/app/src/main/kotlin/...` | Kotlin 包目录 `com/example/mbdsdr_mobile/` → `mbdsdr/app/`；`package mbdsdr.app`；USB 权限 action 常量改 `mbdsdr.app.USB_PERMISSION` |
| Android | `mobile/android/app/src/main/AndroidManifest.xml` | `.MainActivity` 相对引用随新 namespace 自动解析为 `mbdsdr.app.MainActivity`（无需改）；`android:label` → `MBDSDR` |
| iOS | `mobile/ios/Runner.xcodeproj/project.pbxproj` | 全部 `PRODUCT_BUNDLE_IDENTIFIER` → `mbdsdr.app`（RunnerTests → `mbdsdr.app.RunnerTests`） |
| iOS | `mobile/ios/Runner/Info.plist` | `CFBundleName` → `MBDSDR` |
| 签名 | `tools/android/gen_keystore.sh`（新） | 生成 keystore 的脚本，密码交互输入、不进命令行/日志 |
| 签名 | `mobile/android/key.properties.example`（新） | 签名配置模板（占位变量 + 说明） |
| 忽略 | `.gitignore` | 增补 `*.jks`/`*.keystore`/`key.properties`（`mobile/android/.gitignore` 模板本已忽略，根目录再补一层兜底） |

> 历史文档里的 `com.example` 字样（`docs/learn/phase4/audits/B3-*.md`、
> `docs/learn/phase6/_PHASE6_SPEC.md` 的「现状确认」）是对当时状态的如实记录，
> 不改写历史；`mobile/lib/audio/platform_pcm_sink.dart` 里提到的
> `com.example.flutter_pcm_player` 是**被我们弃用的第三方包**自己的命名空间，
> 是选型说明的事实依据，亦保留。

---

## 2. 环境（云 VM 已搭通）

- JDK 21：`/home/user/jdks/jdk-21.0.12.1+1`（Gradle 9.x 需 17+）
- Android SDK 36：`/home/user/.local/android-sdk`（已写进 `mobile/android/local.properties` 的 `sdk.dir`）
- Flutter 3.47.5：`/home/user/tools/flutter`
- Gradle 9.3.1：已缓存于 `~/.gradle/wrapper/dists/`

> `local.properties` 本身已被 `.gitignore` 忽略（每台机器本地一份，不入库）。

---

## 3. debug 构建（默认、无需签名）

```bash
cd mobile
flutter pub get
flutter analyze          # 期望 0 issue
flutter test             # 期望 288 通过（基线）
flutter build apk --debug
```

产物：`mobile/build/app/outputs/flutter-apk/app-debug.apk`。
debug 包用 Android 调试证书自动签名，任何人 clone 后无需任何密钥即可编译/装包试跑。

---

## 4. release 签名配置（密钥永不入库）

### 4.1 设计取舍（二选一里选了哪一个、为什么）

本批在两种方案里选了 **「`key.properties` 存在才启用 release 签名，否则 release 回退 debug 签名」**：

- ❌ 方案 A（默认不改 build.gradle，文档另写签名说明）：最保守，但 release 永远用 debug 签名，
  容易让人误把 debug 签名的包拿去上架。
- ✅ 方案 B（**已采用**，build.gradle.kts 条件接入）：在 `build.gradle.kts` 里
  `rootProject.file("key.properties").exists()` 为真时才读取 `signingConfigs.release`；
  为假时 `buildTypes.release.signingConfig` 退回 `debug`。
  - **好处**：① CI / 他人 clone 没有 `key.properties` 也能 `flutter build apk --release` 跑通（不会因缺文件报错）；
    ② 一旦真发布者把 `key.properties` 放到 `mobile/android/`，同一份代码立即自动切换成正式签名，无需手改 gradle；
    ③ keystore 与密码只在本地 `key.properties`（已被 gitignore），仓库零敏感信息。
  - **代价**：缺 `key.properties` 时打出的 release 包是 debug 签名的——只能本机试跑，**不可上架**。
    脚本与本文档都明确提示这一点。

> 注意：**debug 构建（§3）根本不碰 release signingConfig**，所以验收项
> `flutter build apk --debug` 与有无 `key.properties` 完全无关。

### 4.2 生成你自己的 keystore（只在本机跑一次）

```bash
tools/android/gen_keystore.sh                 # 默认输出到 ~/keystores/mbdsdr.jks
# 或指定目录：tools/android/gen_keystore.sh /your/safe/dir
```

脚本要点：
- 用 JDK 自带 `keytool`，RSA 2048、有效期 10000 天；
- 密码由 `keytool` **交互式**输入（不回显、不进命令行参数 / shell 历史）；
- 生成后 `chmod 600`，并打印一段填好路径的 `key.properties` 内容供你粘贴；
- 若目标 `.jks` 已存在，脚本**拒绝覆盖**（避免误删发布身份）。

> ⚠ keystore 文件 + 两个密码 = 你这个 App 的唯一发布身份。丢了/忘了密码就永远
> 无法给同一 App 发更新。请离线备份到密码管理器 / 加密 U 盘，不要只留一台机器。

### 4.3 配置 key.properties

```bash
cp mobile/android/key.properties.example mobile/android/key.properties
# 编辑 key.properties，填入 storeFile / storePassword / keyAlias / keyPassword
```

`key.properties` 已被 `.gitignore`（`mobile/android/.gitignore` 与根 `.gitignore` 双保险）忽略，
**不会**被 `git add` 收进仓库。模板 `key.properties.example` 本身入库，仅含占位变量。

### 4.4 出正式 release 包

```bash
cd mobile
flutter build apk --release            # key.properties 存在 → 自动正式签名
# 或分 ABI：flutter build apk --split-per-abi --release
```

产物：`mobile/build/app/outputs/flutter-apk/app-release.apk`。
用 `keytool -list -v -keystore ~/keystores/mbdsdr.jks` 可查 SHA-256 指纹，按商店要求登记。

---

## 5. 常见问题（FAQ）

- **Q：clone 后 `flutter build apk --release` 报「缺签名」？**
  A：不会。没有 `key.properties` 时 release 自动回退 debug 签名，构建成功；只是这个包是 debug 签名、不可上架。要上架请先按 §4.2/§4.3 配好 keystore。
- **Q：我不小心把 `key.properties` / `.jks` 放进仓库目录了？**
  A：已被 gitignore，`git status` 应看不到它们。若曾误 `git add`，立刻 `git rm --cached` 并轮换密码（视为已泄露）。本批未做任何此类提交。
- **Q：iOS 怎么改包名？**
  A：本批已把 `project.pbxproj` 里所有 `PRODUCT_BUNDLE_IDENTIFIER` 改成 `mbdsdr.app`（RunnerTests → `mbdsdr.app.RunnerTests`）。iOS 真机签名需在 Xcode 里用你的 Apple 开发者账号配置（云 VM 无 Xcode，未验证）。
- **Q：改了包名，老的 debug 包/数据会丢吗？**
  A：`applicationId` 变了等于系统眼里的一个新 App，旧 `com.example.mbdsdr_mobile` 包不会被覆盖升级，需卸载后重装。正式上架前注意这是首发、无历史升级路径。
- **Q：`flutter build apk --debug` 首次很慢？**
  A：正常。首次要下载 Gradle/Android 依赖、编译引擎，本云 VM 实测约 10–25 分钟；之后增量构建很快。

---

## 6. 验收记录

见本批交付回报：`flutter analyze` issue 数、`flutter test` 通过数、
`flutter build apk --debug` 产物路径与大小、总耗时。
