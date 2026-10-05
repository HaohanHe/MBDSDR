# Phase40 块2（CI 域）ci.yml 核对笔记

> 基线：本文件与 `.github/workflows/ci.yml`、`scripts/run_all_tests.sh` 同批改动。
> 红线：云端无 GitHub runner，**未经 runner 实测，待配置验证**——下文所有"通过/失败"
> 均指本地静态核对与 YAML 解析，不是 Actions 真跑结果。

## 1. 核对方法

逐项比对 `scripts/run_all_tests.sh`（本地一键脚本，真实存在、本地跑过）与
`.github/workflows/ci.yml`（云端未跑过）：

- pytest 五个根目录是否一致；
- offscreen 环境变量 `QT_QPA_PLATFORM` 是否都设了；
- 版本钉版（Python 3.12 / Flutter 3.47.5 / Qt 6.8.2 基线）注释是否对得上；
- 系统依赖（apt 包）是否覆盖脚本运行所需；
- CI 是否真的装了脚本注释里声称的全部依赖。

## 2. 核对发现

### 2.1 一致项（无需改）

| 项 | run_all_tests.sh | ci.yml | 结论 |
|---|---|---|---|
| pytest 五根 | `experiments/tests mbdsdr_ai/tests tools/onboarding tools/hw_selfcheck tests/`（L142） | 同序（L84-89） | 一致 |
| offscreen | Python 段 L152/L155 设 `QT_QPA_PLATFORM=offscreen` | python job L88、cpp job L191 | 一致 |
| Flutter 目录 | `MOBILE_DIR=$ROOT/mobile`（L44） | `working-directory: mobile`（L101） | 一致 |
| Flutter 版本 | 脚本用本机 SDK，无钉版；注释基线 3.47.5 | `flutter-version: '3.47.5'`（L117） | 一致（CI 钉版） |
| ctest 命令 | `ctest --test-dir <build> --output-on-failure`（L85） | 同形（L192） | 一致 |
| 硬件用例自 skip | L30 注释 | L43-45 注释 | 一致 |

### 2.2 发现并修复的不一致

1. **漏装 desktop/requirements.txt（真缺口）**
   - 原 ci.yml L36 注释声称依赖"见根 requirements.txt 与 desktop/requirements.txt"，
     但安装步骤只 `pip install -r requirements.txt`。
   - 而 `requirements.txt`（根）**不含 PySide6**；PySide6 在 `desktop/requirements.txt`。
     pytest 的 offscreen 用例直接 import PySide6，缺了不会报错、只会被
     `importorskip` 整批跳过——CI 看似绿，实际没真跑 UI 用例。
   - 修复：ci.yml L83 增加 `pip install -r desktop/requirements.txt`（该文件全为
     manylinux wheel，无需 `|| true`）。

2. **pytest 无墙钟兜底**
   - 脚本对每个根目录套 `timeout -k 20 900`（PYTEST_TIMEOUT=900，L46/L151）防卡死。
   - 原 ci.yml 五个根一次性 `pytest -q`，无 timeout、job 也无 `timeout-minutes`
     （GitHub 默认 360min）。
   - 修复：python job 加 `timeout-minutes: 60`（L58），注释说明本地脚本的
     900s/根 与 CI job 级 60min 的关系。

### 2.3 刻意保留的差异（非缺陷，记录备查）

- **Flutter 步骤顺序**：脚本是 test → analyze（L110/L123），CI 是 analyze → test。
  两者都不要求顺序，CI 先 analyze 早失败更省时间，保留 CI 顺序。
- **CI 多一步 `flutter pub get`**：脚本假设本地已 `pub get`；CI 是全新 checkout，
  必须先解析依赖——CI 正确，不改。
- **C++ ctest 的 LD_LIBRARY_PATH**：脚本 L84 注入 `$QT_LIB_DIR`（本机 aqtinstall
  路径）；CI 用 apt Qt6（/usr/lib，标准搜索路径）故不注入。若日后切到
  aqtinstall 固定 6.8.2，按 ci.yml L187-189 注释补 env。
- **CI 不调用 run_all_tests.sh 本身**：CI 需要自己的 checkout/setup-python/apt
  前置，脚本是"本地一键"入口，两者语义不同，不强行复用。

## 3. 本次文件改动（file:line）

- `.github/workflows/ci.yml`
  - L4：头注释改为明确"未经 runner 实测，待配置验证"。
  - L46-51：python job 注释补"无音频环境自 skip"与"墙钟兜底"两条说明。
  - L58：新增 `timeout-minutes: 60`。
  - L80-84：安装步骤新增 `pip install -r desktop/requirements.txt`。
  - L178-180：cpp 构建步骤加 OOM 提示（降 `-j2` / 单目标构建）。
  - L186-189：cpp ctest env 上方加 aqtinstall 切换时的 LD_LIBRARY_PATH 提示。
- `scripts/run_all_tests.sh`：**未改动**（核对后无需微调）。
- `docs/learn/phase40/ci-notes.md`：本文件（新建）。

YAML 校验：`python3 -c "yaml.safe_load(...)"` 通过；jobs = [python, flutter, cpp]，
各自 steps=5，`timeout-minutes: 60` 被正确解析。

## 4. 未实测项清单（上 runner 前不要当绿）

1. 整个 workflow 从未在真 GitHub runner 上触发过——三个 job 的 checkout /
   setup-python / flutter-action / apt 组合均**未实测**。
2. **cpp job 最可能先挂**：runner 上 apt 的 `qt6-base-dev` 版本大概率 < 6.8
   （本地基线 6.8.2），`find_package(Qt6)` 可能在 configure 阶段失败——
   这是"待 runner 配置验证"的明确信号，届时按 ci.yml L130-148 注释走 aqtinstall。
3. **python job 新装的 desktop/requirements.txt**：PySide6 manylinux wheel 在
   ubuntu-latest 能否装上、装上后 offscreen 跑起来是否真收集到 UI 用例——
   未实测。
4. **audio 自 skip 是否真的"带原因 skip"而非硬失败**：依赖 A 域（cpp/tests）的
   修复是否落地，本域不验证。
5. **flutter-action@v2 + Flutter 3.47.5 stable**：能否在 runner 上缓存命中、
   `flutter analyze` 是否 0 issue——未实测。
6. **OOM 阈值**：`-j$(nproc)` 在 ubuntu-latest 实际内存下是否 OOM——未实测；
   真 OOM 按 L178-180 注释降级。

## 5. 上 runner 时的检查清单

1. push 后 Actions 页首次触发，先看三个 job 是否都进入 in_progress（语法/触发
   层面即过）。
2. **python job**：
   - apt 装 libgl1 libegl1 libxkbcommon0 libdbus-1-3 是否成功；
   - `pip install -r desktop/requirements.txt` 是否真装上 PySide6（看日志里
     `Successfully installed PySide6-...`）；
   - pytest 收尾行：确认 UI 用例是"collected & passed"而不是"全部 skipped"
     （若全 skip = PySide6 没生效，回查第 2 点）；
   - 根 tests/ 大套件若 wall-clock 超时，按 docs/learn/phase10/P4-stability.md 排查。
3. **flutter job**：
   - `flutter pub get` 无版本冲突；`flutter analyze` 0 issue；`flutter test` 全过。
4. **cpp job**：
   - 若 configure 阶段 `Could not find a configuration file that matches Qt6 6.8`
     → 切 aqtinstall（见 ci.yml L130-148）；
   - 若 build 阶段 OOM kill → `-j2` 或单目标构建；
   - ctest 收尾应见 `100% tests passed, 0 tests failed out of 127`（基线），
     无音频/无硬件用例为 skipped 而非 failed。
5. 三者全绿后，把本文件第 4 节对应条目标记为"已 runner 验证（日期/run 链接）"。
