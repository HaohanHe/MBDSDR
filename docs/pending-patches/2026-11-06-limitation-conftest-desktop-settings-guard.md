# 限制登记补充：conftest 真实配置守卫在「重定向 HOME」下被既有用例误红（已修）

> 目标文件：`docs/limitations-register.md` · 文末追加「附 T」（本文件**不直接改**
> 共享台账，交由限制总账 owner 合并）。
> 生成时间：2026-11-06；执行人：解码流水线 P5；状态：**已修**。
> 归属：这是 `tests/test_sdr_pipeline.py` 既有的 fixture 使用缺口，与同日
> FC0012 写频上界修复无关；已按测试卫生问题直接修复（commit `f55ff6d4`），
> 不是产品缺陷，也不算本轮改动回归。

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
- **修法（已落地）**：`tests/test_sdr_pipeline.py` 的
  `TestMainWindowRecordingFlow` 类装饰器补 `isolated_profile`：

  ```python
  @pytest.mark.usefixtures("qtapp", "isolated_profile")
  class TestMainWindowRecordingFlow:
  ```

  守卫本身未改——它是「测试禁止写用户真实配置」的机械护栏
  （纪律见 `docs/COORDINATION.md`「测试纪律」）。

- **修复后判据**：
  1. 同命令输出 `13 passed`，会话末不再出现 `_real_config_guard_fixture`
     的 ERROR；
  2. `$APPDATA/mbdsdr/desktop_settings.json`（及 `.lock`）不再被创建/变更；
  3. 反证：把 `isolated_profile` 从类装饰器去掉，同命令回到
     `13 passed, 1 error`。
- **实测（2026-11-06，本机）**：未补 fixture `13 passed, 1 error`；
  补 `isolated_profile` 后 `13 passed, 0 error`，配置目录零新增。

## 2. 复跑命令（PowerShell，路径按本机替换）

```powershell
$env:MBDSDR_NULL_AUDIO='1'; $env:MBDSDR_NO_HW='1'; $env:QT_QPA_PLATFORM='offscreen'
$env:HOME='<稳定目录>'; $env:USERPROFILE='<稳定目录>'
$env:APPDATA='<稳定目录>\AppData\Roaming'; $env:LOCALAPPDATA='<稳定目录>\AppData\Local'
# 前置：删掉 <稳定目录>/AppData/Roaming/mbdsdr/desktop_settings.json 及其 .lock
python -m pytest tests/test_sdr_pipeline.py -q
```

预期 `13 passed`（会话末无 ERROR）。

## 3. 统一 diff（可直接 `git apply`，行号以当次文件末尾为准）

> 用四反引号包裹，避免补丁内的 ``` 代码围栏提前闭合；提取时取 ` ````diff `
> 到行首 ` ```` ` 之间的内容。本条已按「已修」改写，附 T 内含复跑命令与反证。

````diff
diff --git a/docs/limitations-register.md b/docs/limitations-register.md
index 8a2e392..7a0bd29 100644
--- a/docs/limitations-register.md
+++ b/docs/limitations-register.md
@@ -791,3 +791,59 @@ python -c "from mbdsdr_ai.core.mcp_server import MCPToolSet; print(len(MCPToolSe
 
 
 
+
+
+## 附 T：conftest 真实配置守卫在「重定向 HOME」下被 TestMainWindowRecordingFlow 误红（已修，2026-11-06 解码流水线 P5）
+
+> 归属说明：本条与 2026-11-06 的 FC0012 写频上界修复无关，是该测试文件既有的
+> fixture 使用缺口；已按测试卫生问题直接修复，不是产品缺陷，也不算本轮改动回归。
+
+**状态：已修（2026-11-06，commit f55ff6d4）**。
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
+**根因**：`tests/conftest.py::isolated_profile` 是 **module 级可选 fixture**，
+不是 autouse。`MainWindow()` 构造期会 `DesktopSettings.load()`、建
+`QSettings("MBDSDR","Desktop")` 与全局 `layout_store()`，`settings.set(...)`
+的防抖写盘随后落到真实 `%APPDATA%`。`tests/test_sdr_pipeline.py` 的
+`TestMainWindowRecordingFlow` 只声明了 `usefixtures("qtapp")`，直接
+`mw.MainWindow()` 却未请求 `isolated_profile`，因此必然污染真实配置。
+
+**修法**（测试侧一行，不动产品代码、不动守卫本身）：
+
+```python
+    @pytest.mark.usefixtures("qtapp", "isolated_profile")
+    class TestMainWindowRecordingFlow:
+```
+
+守卫保持原样——它是「测试禁止写用户真实配置」的机械护栏
+（纪律见 `docs/COORDINATION.md`「测试纪律」）。
+
+**复跑命令**（PowerShell，路径按本机替换）：
+
+```powershell
+$env:MBDSDR_NULL_AUDIO='1'; $env:MBDSDR_NO_HW='1'; $env:QT_QPA_PLATFORM='offscreen'
+$env:HOME='<稳定目录>'; $env:USERPROFILE='<稳定目录>'
+$env:APPDATA='<稳定目录>\AppData\Roaming'; $env:LOCALAPPDATA='<稳定目录>\AppData\Local'
+# 前置：删掉 <稳定目录>/AppData/Roaming/mbdsdr/desktop_settings.json 及其 .lock
+python -m pytest tests/test_sdr_pipeline.py -q
+```
+
+**修复后判据**：
+
+- 同命令输出 `13 passed`，会话末不再出现 `_real_config_guard_fixture` 的 ERROR；
+- `<稳定目录>/AppData/Roaming/mbdsdr/desktop_settings.json` 不再被创建/变更；
+- 反证：把 `isolated_profile` 从类装饰器去掉，同命令回到 `13 passed, 1 error`。
+
+> 2026-11-06 实测（解码流水线 P5）：未补 fixture 时 `13 passed, 1 error`；
+> 补后 `13 passed, 0 error`，配置目录零新增。
````

## 4. 验证记录

- 2026-11-06 实际修复：`mbdsdr_ai` 侧未改，仅 `tests/test_sdr_pipeline.py`
  一行 fixture 声明；同命令 `13 passed, 0 error`。
- 本补丁的「统一 diff」在 `core.autocrlf=true` 的临时副本上
  `git apply --check` `rc=0`（同名相对路径 `docs/limitations-register.md`）。
- 合并时若 `docs/limitations-register.md` 已被其它会话在文末追加内容，
  请按文末上下文重新定位；hunk 只追加、不修改任何既有行。
