# D1-deferred-cycle：延迟 import 的理由制度 + 机检设计

> 日期：2026-11-13；形态：**设计稿，未落地**；落地目标：`tools/axioms/survey.py`
> `tools/axioms/selftest.py`、`docs/axioms.md`、`docs/axioms-baseline.json`。
> **本文件是设计稿，落地时以当时工作区为准**（行号、环成员、占用状态都会变）。

## 一、一句话结论

模块级 import 图曾经 0 环，而同一批文件加上「函数体内 import」后出现
21 模块与 5 模块两个环——**环没有消失，只是搬到了检查器原本看不见的地方**。
所以规则必须是「函数体内的 import 必须写理由，理由只有三种，『避免循环依赖』
不是理由，是缺陷」。

## 二、规则正文（可直接粘进 `docs/axioms.md`）

### D1-deferred-cycle 延迟导入必须带可核理由

**规则**：`mbdsdr_ai/` 与 `desktop/` 里任何写在函数体内的 `import` /
`from ... import ...`，必须在同一行或紧邻上一行写`# deferred-import: <reason>`，
`<reason>` 只认三种：

| reason | 适用 | 附加要求 |
| --- | --- | --- |
| `startup-order` | 只有换顺序才能避免半初始化（模块 A 的初始化要读 B 的运行时状态） | 注释写清「谁先谁后」一行 |
| `optional-dep` | 依赖可能未安装（scipy、硬件 SDK、可选后端） | 必须被 `try/except (ImportError, ModuleNotFoundError)` 包住 |
| `startup-cost:<N>ms` | 该 import 会显著拖慢入口冷启动 | `N` 必须是实测毫秒；< 5 ms 视为给不出，不许用 |

**明确不算理由**：`避免循环依赖` / `避免循环导入` / `circular import` /
`成环` / `半初始化` 作为*函数体 import* 的理由，一律判缺陷。依赖关系没有变，
延迟只是让检查器看不见；该拆的是环（下沉叶子、依赖注入），不是 import 的位置。

**豁免**：已登记在基线里的存量条目（见第五节）不报；新增一律报。

## 三、为什么单独一条规则

`D1-cycle` 已经把函数体内 import 算成边（否则环会被系统性漏报），但它只报
「环存在」，不报「你在用延迟 import 掩盖环」。两者要分开：

- `D1-cycle` = **结构问题**，修法是拆环；
- `D1-deferred-cycle` = **过程问题**，修法是把理由写对，或承认它在掩盖。

分开的好处：拆环是大工程，理由制度可以立刻冻住增量——新代码再写一句
「局部导入避免循环依赖」，当天红灯，不用等环拆完。

## 四、机检设计（可直接照抄实现）

### 4.1 挂载位置

- 实现：`tools/axioms/survey.py` 新增 `collect_d1_deferred(repo) -> Dimension`，
  在 `collect_d1()` 之后被同层调用；rule id = `D1-deferred-cycle`。
- 理由：`collect_d1` 已经有图与 `_imports_of`（含函数体 import），但**没有**
  「这个 import 在不在函数里」的信息，必须单独做一次 AST 遍历，不要改动
  `_imports_of` 的返回值（`D1-cycle` 依赖它的现有语义）。
- 扫描范围：`repo.py_units(("mbdsdr_ai", "desktop"))` 出 findings；
  `tests/` `scripts/` `tools/` 只在 facts 里报数量，不出 finding（用例与治理
  脚本的延迟 import 是隔离手段，不是依赖设计）。

### 4.2 判据（按顺序，命中即记）

1. **候选**：`ast.Import` / `ast.ImportFrom`，且存在
   `FunctionDef`/`AsyncFunctionDef` 祖先（`ClassDef` 不算函数体）。
2. **形态特判 `re-export-shim`**：函数名是 `__getattr__`（PEP 562）、函数体
   由「`if name != X: raise AttributeError` + 单个 import + 写回 `globals()`」
   组成时，归类为导出投影，仍**必须**写 `# deferred-import: re-export-shim`；
   没写照报。
3. **上下文窗口** = import 行前 8 行 + **import 行自身行尾注释** + 所在函数的
   `ast.get_docstring(func)`。三处任一出现 tag 都算写了理由。
   （行尾注释必须算——现有 3 处 `ToolResult` 延迟 import 就是写在行尾的。）
4. **缺陷词表**（出现在窗口内即报 `kind=cycle-disguise`）：
   `循环依赖|循环导入|避免环|避免循环|成环|半初始化|circular|import cycle`
   （大小写不敏感）。**词表命中优先于 tag**：写了 tag 又写「避免循环」照样报。
5. **无 tag** → `kind=no-reason`。
6. **tag 的 reason 不在 allowlist** → `kind=unknown-reason`。
7. **`optional-dep`**：import 节点必须位于 `Try` 的 body 内，且该 `Try` 至少
   一个 handler 捕获 `ImportError` / `ModuleNotFoundError`（含元组），否则
   `kind=optional-not-guarded`。
8. **`startup-cost:<N>ms`**：`N` 必须是正整数；缺失或不是数字 →
   `kind=startup-cost-unnamed`。测量口径见 4.4。
9. **`startup-order`**：只要求 tag + 文字理由，语义无法机判，不做控制流校验。

### 4.3 输出与计数

- `Finding(rule="D1-deferred-cycle", path, lineno, detail=f"{func}() 内 import {module}",
  why="依赖关系被搬到检查器看不见的位置…", fix="写 # deferred-import: <reason>，或拆环/上提")`
- facts 三行（全量，不截断）：函数体内 import 总数 / 带合规理由数 / 缺陷数，
  再按 `kind` 分项计数。展示层沿用现有 `--max-per-dim` 截断，但计数全量。

### 4.4 `startup-cost` 的可验证口径（写进规则正文，别只写在这）

- 测量命令（必须写进注释或相邻行，下同）：

  ```powershell
  python -X importtime -c "import <entry_module>" 2>&1 |
    Select-String "<entry_module>"
  ```

  取该模块的 **self 时间**（最后一段 `import time` 列是自身耗时，含子模块由
  上一列区分）。跑 5 次取中位数；仓库层面要求冷启动（清 `__pycache__` 后首次
  运行）与热启动各跑 2 次。
- 判定：**< 5 ms 视为给不出**，必须上提；≥ 5 ms 才允许用该理由，且注释里
  必须出现数字（`# deferred-import: startup-cost:37ms`）。
- 只认「延迟这一条 import 带来的增量」，不能把整个模块的 import 时间算进去。

## 五、现状清单（9 处，2026-11-13 03:2x 取证）

取证命令（只读，仓外脚本）：`mbdsdr_ai/` 函数体内 import 共 954～967 处
（口径差异见附录），其中「前 8 行含环类词」的 9 处如下：

| # | 位置 | 目标 | 现在写的理由 | 是否真环 | 处置 |
| --- | --- | --- | --- | --- | --- |
| 1 | `agent.py:2029` | `mbdsdr_ai.sdr_backend.DEFAULT_SAMPLE_RATE_HZ` | 局部导入避免循环依赖 | **否**（`agent` 不在任何 SCC，且同文件 `:47` 已模块级 `from .sdr_backend import SDRBackendManager`） | 并入 `:47` 那一行，删注释 |
| 2 | `device_manager.py:678` | `.core.errors`（两个异常） | `mbdsdr_ai.core 会 import 本模块` | **否**（`core/__init__` 只 import `.errors`+`.controller`，`controller` 的 `device_manager` 边在函数体内，不构成导入期环） | 上提；若因 `core/__init__` 连带 `controller`+numpy 拖慢启动，改写成 `startup-cost:<N>ms` 并给实测数字——**现在的理由文字与事实不符，必须改** |
| 3 | `goes_lrit.py:858` | `.tool_results.ToolResult` | 延迟导入避免循环 | **否**（`tool_results` 是叶子，零 `mbdsdr_ai` 依赖） | 上提模块级；注释随理由一起删 |
| 4 | `goes_lrit.py:888` | 同上 | 同上 | 否 | 同上 |
| 5 | `new_spacetime_tle.py:468` | `.orbit.BUILTIN_SATS` | docstring：`orbit → tle_integrity → 本模块` 半初始化 | **是**（同在 21 模块 SCC），但形态是 PEP 562 `__getattr__` 导出投影 | 短期打 `# deferred-import: re-export-shim`；根治=把 `BUILTIN_SATS` 下沉到叶子常量模块，投影改直接 re-export |
| 6 | `rtklib_adapter.py:1003` | `.rtcm3_decoder.RTCM3Decoder` | 延迟导入避免循环依赖 | **否**（`rtcm3_decoder` 只 import numpy） | 上提模块级 |
| 7 | `rtl433_decoder.py:781` | `.tool_registry.ToolResult` | 延迟导入避免循环依赖 | **否**（单向入边，`rtl433_decoder` 不在 5 模块 SCC） | 先改向 `.tool_results`，再上提模块级（门面改造的同一条尾巴） |
| 8 | `sdr_backend.py:6565` | `.spyserver_client.SpyServerBackend` | 懒导入避免 `sdr_backend <-> spyserver_client` 循环导入 | **是**（`spyserver_client.py:43` 模块级 `from .sdr_backend import SDRBackend, SDRDevice, _ThreadedRingReader`） | 真重构：把这三个符号下沉到 `sdr_backend_base`（叶子），两侧都指它；或 spyserver 只依赖传入的后端接口 |
| 9 | `tle_integrity.py:644` | `.orbit.BUILTIN_SATS` | docstring：延迟导入避免循环依赖 | **是**（同在 21 模块 SCC） | `builtin_entries(sat_map=...)` 已支持注入：默认值改为调用方传入，或同 5 把 `BUILTIN_SATS` 下沉叶子 |

补充（不在 9 处口径内，但新规则会命中）：
`goes_lrit.py:927` 同样在函数体内 `from .tool_results import ToolResult`，
**没有任何理由注释**——属 `no-reason`，与 3/4 一并上提。

归类小结：**5 处假环可直接清**（1、3、4、6、7），**1 处理由失真但非环**（2），
**3 处真环需单独立项**（5 为投影形态、8 为 2 模块环、9 在 21 模块 SCC 内）。

## 六、落地步骤（照抄即可，不需要再想）

1. `tools/axioms/survey.py`：加 `collect_d1_deferred()`（按第四节），
   rule id `D1-deferred-cycle`；facts 给出 §4.3 三行计数。
2. `tools/axioms/selftest.py`：加 4 个 `Case`（下节），跑
   `python scripts/axioms.py --self-test-only`。
3. `docs/axioms.md`：八维表 D1 行的「检查名」列追加 `D1-deferred-cycle`，
   并把§二规则正文与§4.4 测量口径贴进对应小节。
4. `python scripts/axioms.py --rule D1` 全量跑一遍，确认 9 处 + `goes_lrit:927`
   全被报出（预期 10 条），再 `--accept-current` 收进
   `docs/axioms-baseline.json`——**只减不增**，之后新写一句「避免循环」就红灯。
5. 清账顺序：先改 1/3/4/6/7 + `goes_lrit:927`（纯上提/改向，不需要设计），
   再处理 2（给 ms 或上提），最后 5/8/9 各自单独立项（真重构，别混进上提那笔）。

## 七、自检（反证必须是「旧结构必红」）

| Case | 输入（正例：必须命中） | 反证（负例：必须 0 命中） | match |
| --- | --- | --- | --- |
| 用「避免循环」当理由 | `mbdsdr_ai/a.py`：`def f():\n    # 延迟导入避免循环依赖\n    from .b import X\n`（`b.py` 存在） | 同上，但 import 提到模块级 | `D1-deferred-cycle` |
| 函数体内 import 没写理由 | `def f():\n    from .b import X\n` | 加 `# deferred-import: startup-order` + 一行顺序说明 | 同上 |
| 可选依赖必须被 try 包住 | `def f():\n    # deferred-import: optional-dep\n    from .b import X\n` | 用 `try:\n        from .b import X\n    except ImportError:\n        X = None` | 同上 |
| 结构反证：上提即消失 | （同第一条正例） | 同一段代码把 import 挪到模块顶层、删注释 | 同上 |

注意：负例不能只改注释——必须有一条「结构变了（import 上提）才变绿」的反证，
否则这条规则会退化成「注释检查器」。

## 八、不要做的事

- 不要为了过检把函数体 import 挪回模块级——如果目标真在同一 SCC，这会制造
  真环（`D1-cycle` 立刻红）。先确认目标不在同一 SCC 再上提。
- 不要用检查器白名单替代修环；白名单只用于冻结存量，条目必须能指到
  `docs/COORDINATION.md` 里的待办。
- 不要顺手把 `tests/` 的延迟 import 也纳入报告——那是隔离手段，会淹掉真信号。

## 附录：本次取证口径

- 图：源码自建（AST），节点 = `mbdsdr_ai` + `desktop` 下全部 `.py`；
  边 = 模块级 + 函数体内 `ImportFrom`（`import x` 形态另行归一）。
  实测 SCC：**21 模块**（不含 `tool_registry`）与 **5 模块**
  （`tool_registry / fengyun_sat / orbit_determination / orbit_dynamics /
  orbit_perturbation`），与门面改造后的 `[21, 5]` 一致。
- 函数体内 import：967 处（含 `__init__` 相对导入）；与 21/5 环同 SCC 的
  延迟边 **92 条**——这就是「环被藏起来」的规模。
- 9 处口径：`mbdsdr_ai/` 下、函数体内、import 前 8 行内命中环类词表。
  该口径在 `goes_lrit:927` 处漏检（无注释），故新规则改用「行尾 + 前 8 行 +
  docstring」三重窗口。
