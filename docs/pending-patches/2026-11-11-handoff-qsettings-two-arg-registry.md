# 交接单：`QSettings("MBDSDR","Desktop")` 两参构造恒写注册表（2026-11-11 实测）

**给下一个碰 `MainWindow` / 桌面测试 / 一次性探针的人：这条不读会踩。**

## 事实（本机实测，不是推断）

1. 产品里窗口几何与分栏尺寸走两参构造 `QSettings("MBDSDR", "Desktop")`。
   Windows 下这个写法**恒为 NativeFormat**，落到
   `HKCU\Software\MBDSDR\Desktop`。
2. `QSettings.setDefaultFormat(QSettings.IniFormat)` + `setPath(...)` **不会**
   重定向两参构造。刚做的实测（`d:\mbdsdr\output\tmp\qsettings-probe` 为 setPath 目标）：

   ```
   two-arg: \HKEY_CURRENT_USER\Software\MBDSDR\Desktop
   no-arg : D:/mbdsdr/output/tmp/qsettings-probe/Unknown Organization.ini
   ```

   即：只有零参构造吃到了 IniFormat + setPath，两参构造照样打注册表。

## 影响面（关键）

- `tests/conftest.py::isolated_profile` 用的是
  `setDefaultFormat(IniFormat)` + `QSettings.setPath(...)` 这套办法，
  **对两参构造无效**。所以「请求了 isolated_profile 就绝对不碰用户注册表」
  这个假设不成立：`MainWindow` 构造/`close` 期间写窗口几何，仍会写
  `HKCU\Software\MBDSDR\Desktop`（值可能被测试窗口的尺寸覆盖）。
- 一次性脚本（`output/tmp` 里的探针）同理：只重定向
  `HOME/USERPROFILE/APPDATA/LOCALAPPDATA` 不够，注册表那一路照写。

## 已验证有效的两种做法（择一）

1. **脚本侧换类名**（`scripts/ui_narrow_breakdown.py` 的做法，产品代码零改动）：
   在 `import main_window` **之前**把 `PySide6.QtCore.QSettings` 换成适配器，
   两参构造改走 `IniFormat` + `UserScope` + 临时目录。反证方式与哈希证据见
   `docs/packaging.md` §5（跑前/跑后 `reg export` 两次导出逐字节相同）。
2. **测试里显式构造**：`QSettings(path, QSettings.Format.IniFormat)`，
   不要用两参构造；或统一走 `config_schema` 的存储入口。

## 处置口径

- 跑桌面测试前后可先 `reg export HKCU\Software\MBDSDR\Desktop` 取证；
  发现被写脏，按 `docs/research/2026-11-11-roadmap.md` 的 R-02 口径清理并登记。
- 本条不要求产品改代码：先把「测试/探针会写用户注册表」当既定事实，
  在隔离层解决。要动产品入口另开工单，别顺手改。

## 相关记录

- `docs/packaging.md` §5「读数隔离」（两参构造 → NativeFormat，脚本侧适配 + 哈希反证）。
- `docs/research/2026-11-11-roadmap.md` R-02（实测污染并清理，建议口径）。
- `tests/conftest.py::isolated_profile`（现有隔离手段的边界，见上「影响面」）。

## 附：工具缺陷——`scripts/safe_commit.py` 不支持带删除

写这份交接单与清点在途补丁时踩到：`safe_commit.py --files <path>` 会把每个文件
的期望状态当成 `M`，一旦该文件在工作区是删除（`D`），直接拒绝：

```
多 ['D\tdocs/pending-patches/2026-11-11-connect-timeout-race.patch']
缺 ['M\tdocs/pending-patches/2026-11-11-connect-timeout-race.patch']
```

这是工具能力缺口，不是操作错（`git rm` 本身没问题，只是不能用安全提交脚本把
这次删除单独提交）。

**绕过方式（本次采用）**：不删文件，保留补丁并在**文件头**标注状态：

```
# 已应用 84e166cc（fix(connect): 超时后保持「上一次仍在收尾」），勿再贴。
```

已实测文件头不影响补丁解析：`git apply --check -R <patch>` 通过（说明补丁内容
与工作区一致、且前导注释被正常忽略）。后续再遇到「已应用补丁」时，同样按
「保留 + 头部标注提交号」处理，别试图用 safe_commit 删。

真需要删除文件时（**未实测，仅静态推断**）：`git commit -- <path>` 只记录该路径
的当前内容、不动共享 index 里别人暂存的内容；用前先 `git status --porcelain`
核对暂存区，并在提交说明里写明为何绕过 `safe_commit.py`。
