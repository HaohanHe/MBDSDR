# 事故记录：并行探针改写用户真实设置（2026-11-13）

状态：已定位；**已从 29 份同哈希副本整份逐字节还原，损失为零**（2026-11-13 02:33，校验通过）；守卫缺陷保留待堵。

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

- 事故期间被改三处：`record_dir`（会写进 `.tmp\rrprobe-wtcyq2c8` 探针目录）、新增 `settings_expanded_groups`（六组全展开）、新增 `right_column_expanded_section`。
- **已整份还原**：真实配置恢复为事故前一秒的 1207 字节 / sha256 `c8cc6266…`，其余 45 个键与原件逐字节一致 → **用户偏好没有丢失，损失为零**。两个新增键随整份替换一并消失。
- `frequency_hz = 0.0` 在找回的原件中就已存在，不是事故产物，是用户自己的状态；污染怀疑撤回，不动。

## 五、还原执行（2026-11-13 02:33）

1. 事故现场留证：污染文件先另存为 `.locks/real_config_snapshots/desktop_settings.20261113-023311-after-incident.json`（1414 字节 / `a3968ca7…`），不覆盖、不修改。
2. 整份 `os.replace`：以 `output\tmp\mbdsdr-theme-real-1zy5s6gd\mbdsdr\desktop_settings.json`（1207 / `c8cc6266…`）替换真实文件，不是逐键改写。
3. 校验通过：1207 字节，sha256 `c8cc6266…`，与守卫记录的写前哈希一致。
4. 五分钟复验通过：2026-11-13 02:38:49 复验同一文件，仍为 1207 字节 / sha256 `c8cc6266…` / mtime 11-11 22:18:38（`RECHECK_OK`）。期间并行探针仍在跑用例，说明还原后没有被第二个探针立刻写脏。

## 六、下一个守卫的规格（本事故反推）

1. 测试/探针在 import 期就把 `APPDATA` / `HOME` / `USERPROFILE` 重定向到 pytest tmp；或对 `%APPDATA%\mbdsdr` 做写保护，写入直接失败。
2. 守卫在会话开始把真实配置**内容**备份到 `.locks/real_config_snapshots/<ts>.json`，会话结束比对不一致时自动还原并报错——不能只记哈希。
3. 探针脚本不得直接使用默认构造的 `DesktopSettings()` / `default_config_path()`；确需真实配置时必须显式传 path。
