# 云端 8 笔再整合 + 全量复验 + 3.11 兼容缺陷清扫（phase65）

> 日期：2026-10-19  作者：MBDSDR 工程轮
> HEAD：本地 `main` = 云端 `origin/main` @ c35bc08（byte-exact 重建后追加 2 笔本地修复）
> 上游基线：origin/main @ c35bc08（用户 10-19 凌晨推送的 8 笔）
> 性质：交付/复验报告。所有数字均为当日沙箱实测快照；不吹不贬，非能力标语。

## 1. 触发与拉取

用户于 10-19 凌晨向云端推送 8 笔提交（54379f7..c35bc08，110 文件 +520/-2510），
要求拉取并继续推进。沙箱网络分区依旧：`github.com`（git 协议）连接超时，
`api.github.com` 与 `codeload.github.com` 可达。沿用 phase64 验证过的
API 重建通道：

1. **内容**：codeload tarball 一次取全部最终内容（10.7MB，2004 项），
   `git hash-object -w` 写对象库；
2. **元数据**：`compare.json` 拿 8 笔的 tree/parents/author/committer/message（0 次 API）；
3. **tree**：`GET /git/trees/{tree}?recursive=1` 拿每笔全文件清单（含 mode），
   470KB 大响应中途断流，加重试（8 次、timeout 120s、指数退避）续成；
4. **重建**：`git update-index --index-info` → `write-tree --missing-ok`
   → `commit-tree`（env 注 author/committer 日期）。

两个必须踩过的坑（phase64 已归档，本轮再次验证）：

- API 日期归一成 UTC 丢时区 → 转回 HaohanHe 的 `+0800` 墙钟再注；
- API 的 message 剥掉结尾换行 → 补单个 `\n`（git 提交 message 规范）。

两者齐了 SHA 才精确命中：8 笔全部 byte-exact，本地 `main` fast-forward 至
c35bc08，历史与云端逐字节一致。中途踩过匿名限流（60/h 用尽，403）与
连接器 token 过期（401），用 PAT 认证（5000/h）解封。

## 2. 云端 8 笔审查

| 提交 | 内容 | 审查结论 |
|---|---|---|
| 54379f7 | 红线清扫：诚实空态/身份脱敏/一次性产物/硬编码四线并治 | 合格。CHANGCHUN/BEIJING/SHANGHAI 实例坐标→SAMPLE_SITE（solar_system.py:915），随附测试断言按中性坐标正确重算，未放宽 |
| b2b4814 | 录制 max_bytes 软上限（帧对齐截断） | 合格。sdr_backend.py:586 默认 2GB，写前按样本帧裁到剩余额度、不写半个样本，截断量如实记 sidecar |
| f45f6c9 | 录制分析补齐五格式 + 修两个预存必崩 bug | 合格。sdr_tools.py:4352 `_analyze_recording` 现支持 .cf32/.cs16/.cu8/.wav/.csv；顺手修了调用不存在 API 的必崩路径（`detect_signals`→`find_signals`、`peak_freq_hz`→`peak_freq`） |
| 5c99e97 / a11cc99 / d3667f5 | HELLO ×3 | 空提交（allow-empty），无代码改动 |
| f1677c4 | 解码器输入形状校验 | 合格。multimon/minimodem 6 处 samples 类型护栏 + reshape(-1)，标量/字符串诚实拒绝不崩溃 |
| c35bc08 | 恢复被误清空的 README（253 行） | 合格。 |

mbdsdr_ai 产品改动共 6 文件 +84/-34，无 mock 化、无断言放宽、无身份残留。

## 3. 本轮实测发现并修复的缺陷（拉取前不存在于本地，拉取后现形）

### 3.1 两处仅 Python 3.12+ 可解析的 f-string（潜伏缺陷，3.11 一跑即现）

拉取后按 AGENTS.md 基线命令（`python -m pytest mbdsdr_ai/tests -q`，
沙箱 `python` = 3.11.1）复跑，**89 个测试模块 collection error**——
`import mbdsdr_ai` 直接 SyntaxError：

- `mbdsdr_ai/new_spacetime.py:897`（blame 至 c6217d9，phase64 再整合带入）
- `mbdsdr_ai/sdr_tools.py:5103`（同样 c6217d9 带入）

根因是 PEP 701 语法：外层单引号 f-string 内再嵌同引号字典下标
（`f"{f', {d['a']}' ...}"`）。Python 3.12+ 才允许，3.11 及以下编译期即炸。
phase64 的 914 全绿是在 3.12 环境测的，此类问题在该环境不可见——
**同一份代码换个解释器就是全红，属于典型的环境盲区**。

修复（行为不变，兼容 3.8+）：条件片段提为前置变量再插值，两处均
实测输出字符串与修复前逐字一致（有/无 accuracy_m 两条路径都验过）。

### 3.2 语法兼容编译守卫（防回归）

新增 `mbdsdr_ai/tests/test_syntax_compat_guard.py`：在当前运行解释器下
编译包内全部 373 个 .py。用聚合单断言而非逐文件参数化——否则 373 个
元测试用例会把产品回归基线计数稀释 375，基线是产品锨点，不能被元测试
注水。负向验证过：临时注入一个 3.12-only 语法文件，守卫当即失败并
指名文件与 SyntaxError；删除后恢复通过。

### 3.3 fy3_pass_curve.npy 退出 git 跟踪（红线补漏）

`tests/test_fengyun.py:191` 注释本就声明该 npy"不进 git"（测试一次性
产物），但 c6217d9 误提交，此后每次跑测试都重写它、工作树随之变脏。
按红线补 `.gitignore` 规则（`mbdsdr_ai/artifacts/*.npy`，同段既有
`syn_*.npy` 先例）并取消跟踪，磁盘文件保留供本地画图。

## 4. 全量复验（当前 HEAD，全绿）

| 面 | 命令 | 结果 |
|---|---|---|
| mbdsdr_ai（3.11.1） | `QT_QPA_PLATFORM=offscreen python -m pytest mbdsdr_ai/tests -q` | **915 passed / 5 skipped** |
| mbdsdr_ai（3.12.8） | 同命令，/opt/python312 | **915 passed / 5 skipped**（双解释器同数） |
| tests/ 根（3.12，含桌面 UI） | `QT_QPA_PLATFORM=offscreen pytest tests -q` | **1490 passed / 4 skipped / 0 failed，exit=0** |
| C++ | `QT_QPA_PLATFORM=offscreen ctest` | **151 passed / 1 skipped（e2e_smoke）/ 0 failed**，共 152 |
| onboarding + hw_selfcheck | `pytest tools/...` | **39 passed / 4 skipped / 0 failed** |
| experiments | `pytest experiments/tests -q` | **34 passed** |

基线因此上档：AGENTS.md 901 → **915/5**（901→914 来自云端新特性测试，
+1 来自本轮编译守卫；3.11/3.12 双解释器实测同数）。

## 5. 环境备注（非仓库缺陷）

3.11 pyenv 环境此前缺 sgp4/astropy/skyfield（均为 requirements.txt 声明
依赖，3.12 环境早有）——已按依赖清单补齐，与仓库无关，仅沙箱供给问题。
tests/ 根的 21 个桌面 UI 测试模块需要 PySide6（模块级 import，无
importorskip 护栏），3.11 环境未装 PySide6，故这些模块在 3.11 下
collection error；该套测试的既有验证一直在 3.12（PySide6 6.12）环境
进行，与 conftest 注释（tests/conftest.py:3）一致。

## 6. 诚实边界

- **Flutter 端仍未实测**：沙箱无 >=3.9 SDK，跨端一致性只到 C++/Python 两侧。
- **tests/ 根的通过数取自进度字符而非 summary 行**：conftest 的
  `os._exit` 规避（tests/conftest.py:22-26）在 terminal_summary 打印前
  退场，summary 行被吞；本轮以全量进度字符（无 F/E/x）+ exit=0 为据，
  计数 1490 passed / 4 skipped。这是已知取舍，不是新问题。
- PAT 已在对话中两次明文暴露，已建议轮换；未轮换前继续仅用于 API 通道。
- 3.11 环境的 PySide6 缺席属沙箱供给，不构成"测试全绿"的反例；
  若要在 3.11 也跑桌面套件，需先 `pip install PySide6`（重量级，未做）。
