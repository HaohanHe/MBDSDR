# 事故记录：并行探针改写用户真实设置（2026-11-13）

状态：已定位；原件已找回；修复方案待用户批准，**真实配置文件尚未改动**。

## 一、时间窗

- 2026-11-11 21:31–22:18：`D:\mbdsdr\output\tmp\mbdsdr-theme-*/mbdsdr/desktop_settings.json` 等探针副本生成，内容与真实配置逐字节一致（1207 字节 / sha256 `c8cc6266…`）。
- 2026-11-12 21:32：守卫最后一次记录真实配置写前状态 = 1207 字节 / sha256 `c8cc6266…`，与上述副本一致。
- 2026-11-13 01:42:44：`D:\mbdsdr\.tmp\rrprobe-wtcyq2c8` 目录创建。
- 2026-11-13 01:42:46：目录内录下 `baseband_20261113_014246.iq`（元数据 `"device": "stub"`、`StubBackend`、0.4 s、100 MHz）；同秒真实配置被原子替换为 1414 字节 / sha256 `a3968ca7…`。

## 二、证据链

- 真实配置文件创建时间 = 修改时间 = 01:42:46：`os.replace` 原子替换，旧 inode 不保留。
- 替换前后**只差三处**：`record_dir` `""` → `"D:\\mbdsdr\\.tmp\\rrprobe-wtcyq2c8"`；新增 `settings_expanded_groups`（六组全展开）；新增 `right_column_expanded_section` = `"status"`。其余 45 个键逐字节相同。
- `record_dir` 指向的目录 01:42:44 才创建，内容是一次桩设备录制，不可能是用户选择。
- 原件找回：`output\tmp\mbdsdr-theme-real-1zy5s6gd\mbdsdr\desktop_settings.json` 等 **29 份**副本与 1207 字节 / `c8cc6266…` 完全一致，可逐字节还原。
- 守卫 `_file_state`（`tests/conftest.py`）只存 size / mtime_ns / sha256，**不存内容**：能报警，不能自证、不能还原。这是守卫的缺陷。

## 三、性质

**并行探针写用户真实配置**。不是单元测试逻辑错误，也不是设置面板写盘缺陷；是测试/探针没有隔离 `%APPDATA%\mbdsdr`。

## 四、后果

- 已证实被改：`record_dir`（下次录音会写进 `.tmp\rrprobe-wtcyq2c8` 探针目录）、两个面板展开态键。
- 其余 45 个键与找回的原件逐字节一致 → **用户偏好未丢失**。
- `frequency_hz = 0.0`、`squelch_default_db = -64`、`perf_profile = "quality"` 等在找回的原件中就已存在，不是本次事故造成；但 `frequency_hz = 0.0` 仍不像用户会设的值，列入待查，不擅自改。

## 五、下一个守卫的规格（本事故反推）

1. 测试/探针在 import 期就把 `APPDATA` / `HOME` / `USERPROFILE` 重定向到 pytest tmp；或对 `%APPDATA%\mbdsdr` 做写保护，写入直接失败。
2. 守卫在会话开始把真实配置**内容**备份到 `.locks/real_config_snapshots/<ts>.json`，会话结束比对不一致时自动还原并报错——不能只记哈希。
3. 探针脚本不得直接使用默认构造的 `DesktopSettings()` / `default_config_path()`；确需真实配置时必须显式传 path。
