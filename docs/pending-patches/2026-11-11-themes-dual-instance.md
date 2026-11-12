# themes 双实例：裸导入 × 包导入 = 两套全局状态

日期：2026-11-11（2026-11-12 补记入仓与归属）
状态：**只出单 + 只加测试，未改任何产品文件**（`desktop/themes.py` 一步未碰）
复现脚本：`D:\mbdsdr\output\tmp\repro_dual_themes_orders.py`、`verify_alias_patch.py`、
`probe_desktop_keys.py`、`test_prefix_order_repro.py`
配套测试（本线新增，已入仓，见 §7）：
`tests/test_import_identity_guard.py`（全局复位 + `desktop.*` 白名单守卫，`ba87bba4`）、
`tests/test_font_scale_note_contract.py`（字号提示契约，`53196a75`，`xfail(strict=True)`）
适用：任何后续接手 themes / 字号带 / 设置页提示的人

---

## 0. 一句话结论

同一份 `desktop/themes.py` 可以同时以 `themes`（裸名）和 `desktop.themes`（包名）
被 Python 各执行一次，产生**两个模块对象、两套模块级全局**。
`font_scale()` / `fit_scale_to_screen()` / `font_scale_note()` 全部读写模块级变量，
所以双实例等于**两个真值源**：一侧降档，另一侧看不见；一侧写提示，另一侧读到空串。

**现状更新（2026-11-11 23:28，commit `6c1064b1`）**：本单写完后，`desktop/themes.py`
已被另一条线加入别名实现（21 行，非本线所写），并新增
`tests/test_themes_single_instance.py`（同 commit 入库）。实测该用例 2 passed、两种导入顺序
均为 `same object: True`。因此 §3 的候选补丁**不要再落**，否则就是第二套实现；
本单保留缺陷证据、取法与影响面，供验收和回归使用。

本缺陷与 2026-11-11 隔离线那条红无关（那条红的成因是设置线「调用与方法定义
分两次写盘」），不要拿它当那条红的解释。

---

## 1. 两种导入顺序的实测输出

脚本只做真实链路会做的事：`main_window.py` 内部是裸导入 `import themes`，
测试/工具侧是 `import desktop.themes`。两种顺序各起一个进程。

> 下面 1.1 / 1.2 是**修复前**（本日 22:5x，`themes.py` 尚无别名）的输出；
> 1.3 是同一脚本在**别名已入库**（`6c1064b1`）后的输出。两份都保留，作为
> 「缺陷成立」与「修复生效」两端证据。

### 1.1 包先（先 `desktop.themes`，再裸 `themes`）

```
=== order=pkg ===
first  = desktop.themes | file: desktop\themes.py | id: 0x21bf7130090
second = themes | file: desktop\themes.py | id: 0x21b8e81ab60
same object: False
sys.modules['themes'] is sys.modules['desktop.themes']: False
write via desktop.themes -> read via 'from themes import': ''
write via themes -> read via desktop.themes: '当前屏幕不足以按 150% 渲染，已按 100% 显示。'
label.text = '当前屏幕不足以按 150% 渲染，已按 100% 显示。' | label.isHidden = False
pkg copy note still: '当前屏幕不足以按 150% 渲染，已按 100% 显示。'
```

读法：用包副本写提示（`''` 那一行是 `from themes import font_scale_note`，即
`settings_panel.py:1228` 的真实语句），产品侧读到空串；反向用裸副本写、包副本读，
读到的还是包副本自己的旧值 —— **两套状态互不可见**。

### 1.2 裸先（先 `themes`，再 `desktop.themes`）

```
=== order=bare ===
first  = themes | file: desktop\themes.py | id: 0x244af8b7f60
second = desktop.themes | file: desktop\themes.py | id: 0x244cbfb0b30
same object: False
sys.modules['themes'] is sys.modules['desktop.themes']: False
write via themes -> read via 'from themes import': '当前屏幕不足以按 150% 渲染，已按 100% 显示。'
write via desktop.themes -> read via themes: '当前屏幕不足以按 150% 渲染，已按 100% 显示。'
label.text = '当前屏幕不足以按 150% 渲染，已按 100% 显示。' | label.isHidden = False
pkg copy note still: '当前屏幕不足以按 150% 渲染，已按 125% 显示。'
```

读法：最后两行是分叉证据 —— 设置页显示 100%，包副本里仍留着 125%。
顺序不影响「分裂」这个结论，只影响哪一份先初始化。

（`id` 每次进程不同；`same object: False` 与两行提示不一致是可复现的稳定结论。）

### 1.3 修复后（commit `6c1064b1`，23:28）

```
=== order=pkg ===
first  = desktop.themes | file: desktop\themes.py | id: 0x1ce55e50bd0
second = desktop.themes | file: desktop\themes.py | id: 0x1ce55e50bd0
same object: True
sys.modules['themes'] is sys.modules['desktop.themes']: True
write via desktop.themes -> read via 'from themes import': '当前屏幕不足以按 150% 渲染，已按 100% 显示。'
write via desktop.themes -> read via desktop.themes: '当前屏幕不足以按 150% 渲染，已按 125% 显示。'
label.text = '当前屏幕不足以按 150% 渲染，已按 100% 显示。' | label.isHidden = False
pkg copy note still: '当前屏幕不足以按 150% 渲染，已按 100% 显示。'

=== order=bare ===
first  = themes | file: desktop\themes.py | id: 0x139b5800860
second = themes | file: desktop\themes.py | id: 0x139b5800860
same object: True
sys.modules['themes'] is sys.modules['desktop.themes']: True
write via themes -> read via 'from themes import': '当前屏幕不足以按 150% 渲染，已按 100% 显示。'
write via themes -> read via themes: '当前屏幕不足以按 150% 渲染，已按 125% 显示。'
label.text = '当前屏幕不足以按 150% 渲染，已按 100% 显示。' | label.isHidden = False
pkg copy note still: '当前屏幕不足以按 150% 渲染，已按 100% 显示。'
```

注意：`second` 的 `__name__` 已经变成先导入那一份的名字 —— 这正是别名生效的
标志，不是脚本 bug。仓内自带回归用例同口径验证：

```
tests/test_themes_single_instance.py ..                                   [100%]
2 passed
```

---

## 2. `same object: False` 的取法

```python
import sys

# 最直接：两个键指向同一对象吗
same = sys.modules["themes"] is sys.modules["desktop.themes"]

# 抗别名/抗 reload 的取法：先确认两个键都在，再比 id 与 __file__
a, b = sys.modules["themes"], sys.modules["desktop.themes"]
same = id(a) == id(b)
assert a.__file__ == b.__file__  # 同一个文件
assert a is not b                # 却被执行了两次

# 状态取法：不要只比 is，要拿一个可变量做「写一侧、读另一侧」
a._FONT_SCALE_NOTE = "probe"
assert b._FONT_SCALE_NOTE != "probe"   # 证明两套全局
```

只断言 `a is b` 是不够的诊断：一旦有人用 `sys.modules` 别名修好，`is` 会变 True，
但**修好了什么**要能说清，所以同时记录 `__file__`、两份 `id`、以及
`_FONT_SCALE_NOTE` 的写入可见性。

> 上面两行 `assert` 是**缺陷存在时**的取法。修复后同一个探针应改为
> `assert a is b`，并且「写一侧、读另一侧」必须读得到 —— 两端都要能反向验证，
> 只测其中一端会把"别名只接了一半"当成修好（`6c1064b1` 中途就是这样红过，见 §3.0）。

---

## 3. 别名补丁：仓内已实现（`6c1064b1`），不要再加第二套

### 3.0 仓内现状（commit `6c1064b1`，非本线所写）

`desktop/themes.py:22-35` 现有实现 = 「3 行 `setdefault` 别名 + 父包属性绑定」：

```python
import sys as _sys
_sys.modules.setdefault(
    "desktop.themes" if __name__ == "themes" else "themes",
    _sys.modules[__name__])
if __name__ == "themes":
    try:
        import desktop as _desktop_pkg
    except ImportError:   # 脚本上下文（python desktop/main.py）没有仓库根
        _desktop_pkg = None
    if _desktop_pkg is not None:
        setattr(_desktop_pkg, "themes", _sys.modules[__name__])
```

配套新增 `tests/test_themes_single_instance.py`（同 commit 入库）：两种顺序各起子进程，
断言 `same object: True` 且跨名字读写可见。实测 `2 passed`（§1.3）。

**中途失败记录（重要，防止后来者抄半套）**：23:2x 该实现首次出现时只有前两句
`setdefault`、没有父包 `setattr`，用例的 `bare_first` 参数直接红：

```
AttributeError: module 'desktop' has no attribute 'themes'
1 failed, 1 passed
```

成因：`import desktop.themes` 一旦命中 `sys.modules`，导入器就不再执行
「给父包 setattr」那一步。补上父包绑定后才转绿。

### 3.1 候选 3 行的验证输出（`verify_alias_patch.py`，仅作方向证明）

```
============================================================
CONTROL(no patch)
-- order=pkg
same object: False
a.name=desktop.themes note='NOTE-FROM-desktop.themes' | b.name=themes note=''
-- order=bare
same object: False
a.name=themes note='NOTE-FROM-themes' | b.name=desktop.themes note=''

============================================================
PATCHED
-- order=pkg
same object: True
a.name=desktop.themes note='NOTE-FROM-desktop.themes' | b.name=desktop.themes note='NOTE-FROM-desktop.themes'
-- order=bare
same object: True
a.name=themes note='NOTE-FROM-themes' | b.name=themes note='NOTE-FROM-themes'
```

### 3.2 若要把同一手法用到别的模块（前置条件）

1. `themes.py` 目前**没有相对导入**（全是裸导入 `from tokens import tokens`）。
   别名把包名键指向一个 `__package__ == ""` 的模块；一旦该模块改成
   `from .tokens import ...`，裸上下文会 ImportError。目标模块有相对导入时，
   裸导入本来就失败（见 §6.2 的 `orbit`/`sdr_backend`），不需要别名。
2. 别名只对「两边都 import 同一文件」的进程有效；它不修被间接依赖的模块，
   也不修 §6 表里的其它模块 —— 每模块单独评估。
3. **不要再给 themes 加第二套**（`6c1064b1` 已入库）。要扩展先与该作者对齐，
   避免出现两种别名写法（只 setdefault、不绑父包属性）。

---

## 4. 会让哪些既有测试变成假绿 / 假红

### 4.1 修复前（`6c1064b1` 之前）

全仓 `desktop/*.py` 与 `tests/*.py` 中 themes 的导入**全部是裸名**
（`from themes import ...` / `import themes`），没有一处 `import desktop.themes`。
因此当时没有用例真的踩到双实例 —— 这是**潜在缺陷**，不是既有红的成因。
（唯一的包式导入是后来新增的回归用例 `test_themes_single_instance.py` 自己，
它在子进程里显式制造两种顺序，属于哨兵，不是产品路径。）

由此产生两类假绿：

- **假绿 A（写包名、读裸名）**：任何未来的用例 `import desktop.themes` 后写
  `_FONT_SCALE_NOTE` / `set_font_scale_override()`，断言"提示为空/未降档"会通过，
  但产品侧裸副本根本没被改过 —— 断言的是自己造的一份空状态。
- **假绿 B（读包名、产品读裸名）**：用例用 `desktop.themes.font_scale()` 算期望值，
  产品实际按裸副本渲染；两副本一旦不同步（`_PLATFORM_FONT_PT` 缓存先后不同），
  用例仍绿，界面却错。

### 4.2 别名落地后（`6c1064b1` 之后）

- 既有 `test_theme_*` 用例全部走裸名，别名不改变它们的对象身份 → **结论不变**。
- 原本靠"包副本吸收全局写入"实现的隔离（当前为 0 处）会失效：`_FONT_SCALE_OVERRIDE`
  与 `_FONT_SCALE_NOTE` 变成真正的进程级单例，**跨用例顺序依赖**会显形。
  全仓检索确认：`tests/` 里没有任何 autouse reset 这三个全局
  （`_FONT_SCALE_OVERRIDE` / `_FONT_SCALE_NOTE` / `_PLATFORM_FONT_PT`），
  而 `fit_scale_to_screen()` 在构造 `MainWindow` 的用例里会改写 override。
  **已补（2026-11-11，本线）**：`tests/conftest.py::_restore_font_scale_globals`
  （autouse，用例前后各复位三个全局一次）。证据（同一批两条用例、两种顺序）：

  ```
  # 修前：把同一批用例放到不加载 tests/conftest.py 的路径下跑（等价于无复位）
  #      D:\mbdsdr\output\tmp\test_prefix_order_repro.py
  order A（leaker -> pristine）: 1 failed, 1 passed
      assert 1.0 is None   # themes._FONT_SCALE_OVERRIDE
  order B（pristine -> leaker）: 2 passed          <-- 同一批用例，顺序一变就变色

  # 修后：tests/ 树下（conftest autouse 生效）
  order A: 2 passed
  order B: 2 passed
  ```

  这正是"进程级单例 + 无复位 = 顺序依赖随机红"的可复现形态：修前不是全红，
  而是**取决于谁先跑**。`test_prefix_order_repro.py` 只 import 同两个用例体，
  断言口径与仓内一致，可反复复跑。
- 与之配套的可执行事实在 `tests/test_import_identity_guard.py`：
  `test_font_scale_globals_start_pristine`（起点必须干净）+
  `test_fit_scale_writes_process_wide_state`（故意写脏、交给 autouse 收尾）。
- `monkeypatch.setattr("themes._platform_font_pt", ...)` 语义不变（裸名仍是产品侧）。
- 若将来出现断言 `themes is not desktop.themes` 的用例，会从绿转红 —— 那是它该红。

---

## 5. 字号提示：test-only 契约 + 结构守卫（原 wrapper 补丁已撤回）

### 5.1 为什么撤掉 wrapper / showEvent 方案

`3a04ee8b`（23:13）已把这条能力定成 **「`SettingsPanel` 构造先刷一次 +
`desktop/main.py` 唯一入口」**：

- `desktop/settings_panel.py:1222` 定义 `refresh_font_scale_note()`，`:1202` 构造时先刷；
- `desktop/main.py:779-783` 是生产侧唯一刷新点。

原先准备的 `output/tmp/2026-11-11-font-scale-note-no-main.patch` 里加了
`showEvent` 补刷 + 末尾给 `themes.fit_scale_to_screen` 套 wrapper。那等于在
`main.py` 之外再造第二套入口（wrapper 是隐式入口，比显式调用更难查），
已按裁定**整份删除**（同批删除 `verify_contract_glue.py` / `build_patch.py` /
`patch_build/`）。本单不再提供任何产品侧补丁。

### 5.2 契约测试：只钉「直接调 `fit_scale_to_screen` → 提示必须出现」

文件：`tests/test_font_scale_note_contract.py`。不经过 `main.py`、不套 wrapper、
不在 `showEvent` 里补刷，构造 `MainWindow` 后直接：

```python
themes.set_font_scale_override(None)
monkeypatch.setattr(themes, "_platform_font_pt", lambda: 15.0)  # 请求 150%
win.screen = lambda: _Screen()                                   # 可用宽 800px
chosen = themes.fit_scale_to_screen(win)
assert label.text() == themes.font_scale_note()
assert not label.isHidden()
```

**当前状态：红，且红在预期断言上**（2026-11-11 实测）：

```
AssertionError: 状态变了但设置页还是旧文案：缺「状态变更广播」。
  themes 里是：'当前屏幕不足以按 150% 渲染，已按 100% 显示。'
  设置页里是：''
assert '' == '当前屏幕不足以按 150% 渲染，已按 100% 显示。'
1 failed
```

读法：`fit_scale_to_screen` 确实降档并写出了说明（`themes` 侧非空），
但已建好的设置页还是空串 —— 缺的是**状态变更广播**（themes 改完通知已存在的
消费者）。这条广播由主题线负责补；补上之前本用例保持红，**不要在测试里绕**
（加 wrapper、手动调 `refresh_font_scale_note()`、补 `showEvent` 都算绕，
都会把"产品没广播"这件事藏起来）。

**入仓裁定（2026-11-12，`53196a75`）**：上图的红作为证据保留，但用例本身标了
`pytest.mark.xfail(strict=True)` 并入仓，不再以裸红挂在测试集里。`strict=True`
让它在产品侧补上广播那天变成 **XPASS 失败**——那时必须来摘标，这正是提醒方式；
不标 xfail 直接进红仓会让「红」变成常态，比不进库更糟。理由与范围同步写进
该提交说明。实跑 `1 xfailed`。

### 5.3 结构守卫：`desktop.*` 包名键白名单

文件：`tests/test_import_identity_guard.py::test_desktop_package_import_whitelist`。
构造一次 `MainWindow()`，断言**本次构造新增**的 `desktop.*` 包名键全部落在
白名单里；出现第 4 个就红，报错文案：

> 新增了 desktop 包式导入点，先确认是否会造成双实例：
> 判据：该模块有没有字典/集合/缓存这类可变全局；有，两份副本就是两个真值源。

白名单（当前 3 个）：`desktop` / `desktop.constants` / `desktop.themes`。
真实入口实测（`output/tmp/probe_desktop_keys.py`，offscreen + 临时配置目录）：

```
before: []
after import main_window: ['desktop', 'desktop.constants', 'desktop.themes']
after MainWindow():        ['desktop', 'desktop.constants', 'desktop.themes']
```

**口径修正（与"键集合恰好等于白名单"的差别，必须说明）**：`sys.modules` 是
进程级的，全量跑时别的测试文件先 `import desktop.main_window` 就会留下
`desktop.main_window` 这个键。实测：

```
# 严格等于白名单 -> 单跑绿、混合跑红（假红）
pytest tests/test_desktop_panels.py::TestMainWindowIntegration tests/test_import_identity_guard.py
1 failed, 4 passed
  多出：['desktop.main_window']
```

所以守卫只看「本次构造新增的键」（`after - before`），避免把别人的导入记到
这次构造头上；同时保留严格分支：**构造前一个 `desktop.*` 都没有时，仍要求
新增集合恰好等于白名单**（白名单漂了照样红）。两种跑法实测均绿：

```
单跑                       : 3 passed
与 test_desktop_panels 混跑: 5 passed
```

---

## 6. 同类风险（本次只普查，不动手）

普查口径：`desktop/*.py` 与 `mbdsdr_ai/*.py` 中同时出现两种导入写法，且模块级存在
可变容器；运行时逐对验证（包名先导入，再裸名导入，写一侧读另一侧）。

### 6.1 桌面端：真实代码里两种写法都出现的模块

| 模块 | 两副本（运行时） | 可变全局状态 | 后果 |
|---|---|---|---|
| `constants` | **是（产品运行即分裂）** | 无 | `config_schema` 走 `desktop.constants`，其余走裸 `constants`：两份常量对象。数值相同，无功能后果，但证明分裂链是活的 |
| `themes` | 能（**已修，`6c1064b1`**，见 §3） | `_PLATFORM_FONT_PT` / `_FONT_SCALE_OVERRIDE` / `_FONT_SCALE_NOTE` | 修复前：降档提示空/旧、字号倍率两个真值源；修复后单实例，残留测试全局泄漏见 §4.2 |
| `settings` | 能（裸优先，包仅回落分支） | `DEFAULTS`（派生后只读） | `DesktopSettings` 类两份 → 跨副本 `isinstance` 失败；两份默认值 dict |
| `config_schema` | 能 | `SCHEMA` | 校验/导入导出读到不同 schema 对象 |
| `mode_registry` | 能 | `_REGISTRY`（运行时可注册） | 一侧注册的新模式另一侧看不到 → 下拉项与后端校验集合不一致 |
| `rf_sky_view` | 能 | `_star_pen_cache`、`_star_brush_cache`、`_TWILIGHT_*`、`COMMON_STAR_ALIASES` | 星等笔刷缓存翻倍；`RFSkyView` 类两份 → 跨副本 `isinstance`/is-a 判断失败 |
| `spectrum_widget` | 能 | `_LOCAL_KEY_CODES`、`BANDPLAN_CATEGORY_COLORS`、`WATERFALL_COLORS*` | 键码表/配色表两份；一侧改动不共享 |
| `main_window` | 能 | `_WARN_THROTTLE_LAST`（运行中改写） | 告警节流分表 → 同一告警可能打两遍；`MainWindow` 类两份 |
| `shortcuts` | 能 | `BY_ID`、`_OVERRIDES` | 快捷键重绑定不共享 → 冲突检测失效 |
| `ai_panel` | 能 | `QUICK_COMMANDS`、`_AI_NEXT_STEPS` | 预设表两份，功能影响小 |
| `control_panel` | 能 | `PRESET_FM_STATIONS`、`PRESET_AM_STATIONS`、`SDR_BAND_PRESETS`、`_PANEL_MODE_ORDER` | 预设表两份 |
| `status_panel` | 能 | `DECODE_ERROR_LABELS/REMEDY`、`DECODE_EVIDENCE_LABELS` | 文案表两份 |
| `weather_panel` | 能 | `SATELLITES`、`ENHANCE_OPS` | 卫星/增强表两份 |
| `sat_track_panel` | 能 | `_TIER_ALIAS` | 档位别名表两份 |
| `module_panel` | 能 | `_TYPE_COLORS` | 配色表两份 |
| `device_panel` | 能 | 无 | 仅类对象两份 |
| `adsb_map_panel` | 能 | 无 | 仅类对象两份 |
| `new_spacetime_panel` | 能 | 无 | 仅类对象两份 |
| `ntrip_panel` | 能 | 无 | 仅类对象两份 |
| `right_column` | 能 | 无 | 仅类对象两份 |
| `settings_panel` | 能 | 无 | 仅类对象两份 |

真实入口实测（裸 `import main_window`，offscreen，临时设置目录）：
`sys.modules` 里只有 `desktop`、`desktop.constants`、`desktop.themes` 三个包名键；
逐对比对 —— `themes` 同对象（别名生效）、`constants` **两份不同对象**、
`settings` / `mode_registry` / `shortcuts` / `rf_sky_view` / `main_window` /
`spectrum_widget` 只有裸名一份。也就是说：**今天产品进程里真正分裂的只有
`constants`**（无状态，无害）；其余模块是"能分裂但还没人踩"。

补充：`desktop/__init__.py` 的路径垫片（把 `desktop/` 插进 `sys.path`）正是让两种
名字都**必然可解析**的机制。没有它，包导入上下文里的 `from settings import` 会失败，
反弹到 `from .settings import`；有了它，裸名永远优先成功，双副本只在有人显式
`import desktop.X` 时出现。`tests/conftest.py` 同时插 `_ROOT_DIR` 与 `_DESKTOP_DIR`，
所以**跑测进程天然具备分裂条件**，缺的只是某个用例改用包名。

### 6.2 `mbdsdr_ai`：14 个模块两种写法都有，但裸名是回落分支

`audio_resampler`、`builtin_tle_meta`、`decoders`、`gnss_monitor`、`hal`、
`new_spacetime`、`new_spacetime_tle`、`orbit`、`sat_passes`、`serial_gnss`、
`spectrum_processor`、`time_basis`、`tle_integrity`、`tool_registry`。

它们的裸导入统一长这样（`new_spacetime.py:816` 为例）：

```python
try:
    from .decoders import compute_satellite_position, BUILTIN_TLE, SATELLITE_FREQUENCIES
except ImportError:
    from decoders import compute_satellite_position, BUILTIN_TLE, SATELLITE_FREQUENCIES
```

包名/相对导入在前，裸名只在「脚本直接运行」时兜底。所以包内运行是单实例；
`orbit`、`sdr_backend` 等因为自身有相对导入，**裸导入直接失败**，根本形不成第二份。
真正的可变全局仍是两个真值源，值得登记：

| 模块 | 可变全局 | 后果（脚本方式运行时） |
|---|---|---|
| `orbit` | `_TLE_CACHE`、`_TLE_FETCHER` | TLE 缓存两套 → 网络/磁盘重复取 |
| `decoders` | `_TLE_FAIL_UNTIL`（运行中改写） | 失败冷却两套 → 重复重试 |
| `mbdsdr_ai.config` | `_LAST_ERROR`、`DEFAULT_CONFIG` | 最近错误两份 → 报错读到旧值 |
| `sdr_backend` | `_WARNED_ONCE` | 同一"未安装驱动"告警重复刷屏 |

`config`、`sdr_backend` 在当前仓库**没有任何裸导入点**（只有
`from mbdsdr_ai.config import ...` 这类包导入），因此现在不会分裂。
（对照：桌面端 `settings` 有 40 处裸导入，4 处包导入全是 `except ImportError`
回落，测试进程里裸名总是先成功 —— 结论同样是「能分裂，当前未分裂」。）

### 6.3 处置建议（不在本单执行）

1. `themes` 已修；下一个值得防的是 **`mode_registry._REGISTRY` 与 `main_window._WARN_THROTTLE_LAST`**
   （今天只有裸名一份，但一旦有人包式导入就会分裂）：前者是可用性（模式注册丢失），
   后者是噪音（重复告警）。**本条只登记为待办，本次不动代码。**
2. 结构性修法只有一条：**统一成一种导入名**。短期用 `sys.modules` 别名止血（每模块 3 行），
   长期把桌面端改成包相对导入（`from .themes import ...`），并删掉 `desktop/__init__.py`
   的路径垫片 —— 但那是跨 61 个模块的改动，必须单独立项、单独回归。

---

## 7. 归属：哪些是实现、哪些是本线测试

2026-11-12 清账时补记。**docstring / 提交说明里出现的提交号是「实现出处」，
不是作者归属依据**——本单曾因为测试注释里带着 `6c1064b1` / `3a04ee8b`，被误认为
那两次提交也属于本线。实际分工如下，以后按这张表判，不要再按提交号猜。

| 文件 / 片段 | 落地提交 | 谁写的 | 本线做了什么 |
|---|---|---|---|
| `desktop/themes.py` 的 `sys.modules` 别名块（§3.0，21 行） | `6c1064b1` | **不是本线**（主题线） | 只复验：两种导入顺序 `same object: True`，并把证据记进 §1.3、§3.0 |
| `tests/test_themes_single_instance.py` | `6c1064b1` | **不是本线** | 只跑，记 `2 passed` |
| `desktop/settings_panel.py::refresh_font_scale_note()` + 构造时先刷一次（`:1202/:1222`） | `3a04ee8b` | **不是本线**（字号降档线） | 只认它是唯一入口，据此撤掉 wrapper/showEvent 方案（§5.1） |
| `desktop/main.py:779-783` 唯一刷新点 | `3a04ee8b` | **不是本线** | 同上；本单不提供任何产品侧补丁 |
| `tests/conftest.py::_restore_font_scale_globals`（`+1040,29`） | `ba87bba4` | **本线** | 新增 autouse 复位三个进程级全局 |
| `tests/test_import_identity_guard.py` | `ba87bba4` | **本线** | 新增；`desktop.*` 包名白名单 + 全局起点干净 + 写脏反证 |
| `tests/test_font_scale_note_contract.py` | `53196a75` | **本线** | 新增；`xfail(strict=True)` 入仓，理由见 §5.2「入仓裁定」 |

「测试跟着实现走、实现不由本线写」这句话的**准确覆盖范围**：

- 覆盖：上表后三行（`conftest.py` 的复位 fixture 与两个新测试文件）。
  它们依附于 `6c1064b1`（单实例前提）与 `3a04ee8b`（唯一入口前提）而存在，
  是给那两次实现配的回归/契约；实现改了，这三份要跟着改。
- 不覆盖：`desktop/themes.py`、`desktop/settings_panel.py`、`desktop/main.py`
  与 `tests/test_themes_single_instance.py` —— 本线一行未改、也未提交。

提交纪律（本线）：单文件串行、私有 index、`MBDSDR_EXCLUDE_HUNKS` 排除他线 hunk、
不用 `--no-verify`。`ba87bba4` 与 `53196a75` 均由 pre-commit 闸门放行（各 1 个
活跃 owner，声明清单与实际暂存一致）。
