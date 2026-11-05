# 限制登记补充：conftest 真实配置守卫在「重定向 HOME」下被既有用例误红

> 目标文件：`docs/limitations-register.md` · 文末追加「附 T」（本文件**不直接改**
> 共享台账，交由限制总账 owner 合并）。
> 生成时间：2026-11-06；执行人：解码流水线 P5。
> 归属：这是 `tests/test_sdr_pipeline.py` 既有的 fixture 使用缺口，与同日
> FC0012 写频上界修复无关，请勿按产品缺陷或本轮回归销账。

## 1. 结论与判据

- **现象**：`python -m pytest tests/test_sdr_pipeline.py -q` 用例本身
  `13 passed`，但会话结束另报 1 条 ERROR；断言来自
  `tests/conftest.py::_real_config_guard_fixture`（session 级 teardown），
  报 `$APPDATA/mbdsdr/desktop_settings.json` 由「不存在」变为 1091 字节。
- **根因**：`isolated_profile` 是 module 级**可选** fixture（非 autouse）。
  `tests/test_sdr_pipeline.py::TestMainWindowRecordingFlow` 只声明
  `usefixtures("qtapp")`，却直接 `mw.MainWindow()`；构造期会
  `DesktopSettings.load()`、建 `QSettings("MBDSDR","Desktop")` 与全局
  `layout_store()`，后续 `settings.set(...)` 防抖写盘落到真实 `%APPDATA%`。
- **判据**：
  1. 报错路径是 `$APPDATA/mbdsdr/desktop_settings.json`（及 `.lock`），
     属用户配置面，不是仓库内文件；
  2. 用例 `0 failed`，唯一 ERROR 挂在 session fixture teardown；
  3. 单独跑不构造 `MainWindow` 的本线测试（`tests/test_sdr_tuner_limit.py`）不红；
  4. 只给该类补 `isolated_profile`、不删任何断言，同命令转绿且配置目录零新增。
- **实测（2026-11-06，本机）**：未补 fixture `13 passed, 1 error`；
  补 `isolated_profile` 后 `13 passed, 0 error`。验证用的临时 pytest 插件
  放在仓库外，未入库。

## 2. 复现命令（PowerShell，路径按本机替换）

```powershell
$env:MBDSDR_NULL_AUDIO='1'; $env:MBDSDR_NO_HW='1'; $env:QT_QPA_PLATFORM='offscreen'
$env:HOME='<稳定目录>'; $env:USERPROFILE='<稳定目录>'
$env:APPDATA='<稳定目录>\AppData\Roaming'; $env:LOCALAPPDATA='<稳定目录>\AppData\Local'
# 前置：删掉 <稳定目录>/AppData/Roaming/mbdsdr/desktop_settings.json 及其 .lock
python -m pytest tests/test_sdr_pipeline.py -q
```

## 3. 建议修法（测试侧一行，不动产品代码）

```python
@pytest.mark.usefixtures("qtapp", "isolated_profile")
class TestMainWindowRecordingFlow:
```

不要改守卫本身——它是「测试禁止写用户真实配置」的机械护栏。

## 4. 统一 diff（可直接 `git apply`，行号以当次文件末尾为准）

> 用四反引号包裹，避免补丁内的 ``` 代码围栏提前闭合；提取时取 ` ````diff `
> 到行首 ` ```` ` 之间的内容。

````diff
diff --git a/docs/limitations-register.md b/docs/limitations-register.md
index a6413ac..56e0df4 100644
--- a/docs/limitations-register.md
+++ b/docs/limitations-register.md
@@ -788,3 +788,74 @@ python -c "from mbdsdr_ai.core.mcp_server import MCPToolSet; print(len(MCPToolSe
 
 
 
+
+
+## 附 T：conftest 真实配置守卫在「重定向 HOME」下被 TestMainWindowRecordingFlow 误红（既有测试卫生问题，2026-11-06 解码流水线 P5 追加）
+
+> 归属说明：本条与 2026-11-06 的 FC0012 写频上界修复无关，是该测试文件既有的
+> fixture 使用缺口。登记目的是让后来人不要把它当成产品缺陷或本轮改动回归。
+
+**现象**：`python -m pytest tests/test_sdr_pipeline.py -q` 的 13 条用例全部 passed，
+但会话结束时另报 1 条 ERROR，断言来自 `tests/conftest.py::_real_config_guard_fixture`
+（session 级 teardown）：
+
+```
+[mbdsdr-guard] 测试会话改动了用户真实配置，必须把 HOME/USERPROFILE/APPDATA/LOCALAPPDATA
+重定向到 tmp_path：
+  <APPDATA>/mbdsdr/desktop_settings.json: 大小 0 -> 1091 (+1091 bytes)；
+      sha256 None -> <hash>；mtime_ns 0 -> <ns>
+  <APPDATA>/mbdsdr: 新增=['desktop_settings.json', 'desktop_settings.json.lock'] 消失=[]
+```
+
+**复现条件**（需同时满足）：
+
+1. 会话级守卫已启用——`settings.default_config_path()` 能定位到
+   Windows `%APPDATA%/mbdsdr/desktop_settings.json`（Linux/macOS 为
+   `~/.config/mbdsdr/desktop_settings.json`）。
+2. 跑测前把 `HOME/USERPROFILE/APPDATA/LOCALAPPDATA` 重定向到一个**稳定目录**
+   （不是 pytest 的 `tmp_path`），且该目录下 `mbdsdr/desktop_settings.json`
+   跑前**不存在**（存在时则须与结束时字节不同，否则守卫未必触发）。
+3. 按定向测试纪律设环境：`MBDSDR_NULL_AUDIO=1`、`MBDSDR_NO_HW=1`、
+   `QT_QPA_PLATFORM=offscreen`。
+4. 执行（PowerShell，路径按本机替换）：
+
+```powershell
+$env:MBDSDR_NULL_AUDIO='1'; $env:MBDSDR_NO_HW='1'; $env:QT_QPA_PLATFORM='offscreen'
+$env:HOME='<稳定目录>'; $env:USERPROFILE='<稳定目录>'
+$env:APPDATA='<稳定目录>\AppData\Roaming'; $env:LOCALAPPDATA='<稳定目录>\AppData\Local'
+python -m pytest tests/test_sdr_pipeline.py -q
+```
+
+**判据**（如何确认这是测试卫生问题，而不是产品缺陷/本轮回归）：
+
+- 报错路径是 `$APPDATA/mbdsdr/desktop_settings.json`（及 `.lock`），属用户配置面，
+  不是仓库内文件；
+- 用例本身 `13 passed`、`0 failed`，唯一 ERROR 挂在 session fixture 的 teardown，
+  收集与执行阶段无异常；
+- 单独跑不构造 `MainWindow` 的本线测试（如 `tests/test_sdr_tuner_limit.py`）不红；
+- 只给该类补 `isolated_profile`、不删任何断言，同命令转绿，且
+  `$APPDATA/mbdsdr/desktop_settings.json` 不再被创建。
+
+> 2026-11-06 实测（解码流水线 P5）：未补 fixture 时 `13 passed, 1 error`；
+> 补 `isolated_profile` 后 `13 passed, 0 error`，配置目录零新增。验证用临时
+> pytest 插件放在仓库外，未入库。
+
+**根因**：`tests/conftest.py::isolated_profile` 是 **module 级可选 fixture**，
+不是 autouse。`MainWindow()` 构造期会 `DesktopSettings.load()`、建
+`QSettings("MBDSDR","Desktop")` 与全局 `layout_store()`，`settings.set(...)`
+的防抖写盘随后落到真实 `%APPDATA%`。`tests/test_sdr_pipeline.py` 的
+`TestMainWindowRecordingFlow` 只声明了 `usefixtures("qtapp")`，直接
+`mw.MainWindow()` 却未请求 `isolated_profile`，因此必然污染真实配置。
+
+**建议修法**（测试侧一行，不动产品代码）：
+
+```python
+@pytest.mark.usefixtures("qtapp", "isolated_profile")
+class TestMainWindowRecordingFlow:
+```
+
+不要改守卫本身——它是「测试禁止写用户真实配置」的机械护栏
+（纪律见 `docs/COORDINATION.md`「测试纪律」）。
+
+**为什么本轮不直接改**：`tests/test_sdr_pipeline.py` 不属本线文件，按
+`AGENTS.md` 与协调台账的边界原则只登记、不代改。
````

## 5. 验证记录

- `git apply --check` 在干净副本（同名相对路径）上 `rc=0`。
- 追加内容统一 LF；补丁上下文保留原文第 790 行（该行本身是 CRLF 空行），
  合并时若该文件已被其他会话追加内容，请按文末上下文重新定位。
