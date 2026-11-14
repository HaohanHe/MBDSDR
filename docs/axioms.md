# 工程治理总纲（八维 + 14 条公理）

> 这份文件不是"读一遍就算"的规范。每一条规则都由 `scripts/axioms.py` 在仓库上真跑，
> 违规会报出文件:行号、为什么违反、怎么修。规则本身也在反证自检里被验证"不是空过"。
>
> 输入口径：**只用仓库自身内容**——源码 AST、测试里的 skip/xfail 元数据、文档里的
> 路径与提交号、`git cat-file`/`git rev-list`。不 import 项目模块、不启动 Qt、不连硬件。

## 一条命令

```bash
python scripts/axioms.py --prioritize \
  --json docs/axioms-first-run.json \
  --report docs/axioms-first-run.md
```

| 用法 | 作用 |
|---|---|
| `python scripts/axioms.py` | 八维合规报告（含 49 条反证自检 + 棘轮） |
| `--prioritize` | 追加按派活规则排序的清单 |
| `--self-test-only` | 只验"反证是否空过" |
| `--rule A03` | 只看某条规则 / 某个维度 |
| `--fail-on must-fix` | 接入门禁：存在应修违规即退出 1 |
| `--max-per-dim N` | 只影响**展示**：每维最多打印几条；计数、判定与棘轮始终是全量，报告会写明「已隐藏 N 条」 |
| `--no-ratchet` | 跳过棘轮比对 |
| `--accept-current` | 把当前状态写为新基线；**必须带 `--reason`**，且会把接受人/提交/指纹写进 `docs/axioms-accepted.json` |
| `--baseline PATH` | 指定基线文件（演练/测试用） |

退出码：`0` 测绘完成且棘轮绿；`1` 反证自检失败、棘轮出现新增/升级、或 `--fail-on` 命中；`2` 工具错误。

## 计数口径（采集层不截断）

检查器**全量采集、全量判定**，只在展示层截断；展示层必须写明「已隐藏 N 条」。
一个会截断的检查器和一个会漏检的检查器一样不可信：「应修 0」如果只代表
「窗口内前 N 条是 0」，它就不能当合规判据。

### 当前计数（工具生成，按基准 sha 钉住，勿手改）

<!-- axioms-current-stats:begin（由 python -m tools.axioms.current_stats 生成；基准 8be5af66496d7010d1ec0715a2622b36676eb821 生成于 2026-11-14T12:27:51+08:00） -->
违规 656 条：应修 72，复核 425，豁免 159。
D1 7 / D2 171 / D3 248 / D4 28 / D5 138 / D6 64 / D7 0 / D8 0。
校验指纹 sha256:410ffc3302c436065d9e1b8a414891fa6f13e502341c4e1d24719c0274f96f11
<!-- axioms-current-stats:end -->

`tests/test_axioms_doc_current_stats.py` 只做三种检查，且**不跟 HEAD 比**：

1. 块内数字与块内「校验指纹」一致——手改一个数字即红；
2. 基准 sha == HEAD 且工作树干净时，再与工具当次输出逐字比对——那才是真错；
3. 基准 sha != HEAD（别人又提交了）时默认**不判红**，只提示重跑生成器；但
   落后超过下面「过期上限」后升级为红，避免长期无人重生成把旧数当当前。

「当前」永远指**某一棵基准树**（块内记下的 sha + 生成时刻），不是浮动的 HEAD。

**什么时候该重生成**（改完、提交前）：

- 改动任何被 `tools/axioms/survey.py::collect_all` 扫描的源码；
- 新增/销账「已知红」，或改 `docs/axioms-waivers.json`（豁免集合）；
- 公理/检查器本身变更导致 D1–D8 计数变化。

以上任一发生，跑 `python -m tools.axioms.current_stats`：它会把基准 sha、
生成时刻、指纹与计数正文一起写回本块，和你的代码改动放在同一笔提交。
仅改文档措辞、或改与本域无关的文件，不需要重生成。

**过期上限**（与本条同处，双条件先到者）：基准落后超过 **24 小时**，或落后
超过 **200 笔提交**，判据就从「提示」升级为**红**，并报出落后的时长或提交
数；未超上限时仍只提示、退出码不变。两个上限是
`tools/axioms/current_stats.py` 里的 `MAX_STALE_HOURS` / `MAX_STALE_COMMITS`
常量，取值理由写在该常量的注释里（24 小时≈一次正常重生成节奏；200 笔≈两天
以上的代码面变化，此时基准树与 HEAD 已不是一回事）。只改「计数块本身」与
「§6.1 登记面（`docs/COORDINATION.md`）」的提交不计入「落后的提交数」
（清单在 `current_stats.py::NON_COUNTING_PATHS`，理由写在该常量注释里），
所以重生成本块的那一笔、以及它必须附带的登记行，都不会把提示重新点亮。

**本块之外的所有计数都是历史快照（非当前）**，只用于对账，不作为当前存量。

历史教训（2026-11-13 对账）：`survey.py` 曾对 D6/D3/D2/D1 的明细做 10–40 条截断，
于是总数变成「窗口内条数 + 窗口位置」的函数——清掉几条，后面的浮上来，
总数可能不降反升；两次跑出 272 与 244 对不上，就是窗口在动，不是仓库在退化。
去掉截断后，同两棵树的真实总量是 **593（`ab4d36df`）/ 543（`c67e1943`）**，
而窗口内报的是 272 / 240。

历史快照（非当前）——实测点 `19ceab18`（2026-11-13 第二轮）：**违规 558 / 应修 67 / 复核 437 / 豁免 54**；棘轮基线 513 条
（豁免项不占额度，由 D8 到期机制管理）。与基线定版处 `c182aba4` 的 548/68/428/52 相比，
差额不是仓库退化，而是原先「按名字/字样/改名放过」的位置现在被看见了。基线按指纹记录这些新增，
并在 `docs/axioms-accepted.json` 里写清是谁、在哪个提交、为什么接受。
同一棵树连跑两次，条目集合与条数完全一致——数字可复核，才是准绳。

指纹本身也不许依赖会漂移的东西：`D1-cycle` 曾把「环里有几个模块」写进片段，
并行线修掉一个模块后，同一个环从 21 变 20，棘轮就报成「新增 1 / 消失 1」。
现在环的身份是代表模块，成员数只写在说明文字里。

## 检查器自证：判据不能被被测代码关掉

**总纲：判据不得依赖会漂移的东西。** 名字会被改、关键字会被顺手写进注释或理由、
行号会被无关改动推走——凡是"靠名字判身份、靠关键字判行为、靠行号判位置"的检查，
都只是把判定权交给了被检查的那段代码。判据要么落在结构上（AST/数据流/文件系统事实），
要么明确标为"需人工复核"并计数，不许静默放过。这条统管 A13（名字当身份）、
A03/A04（关键字当行为）、A14（行号当位置）。

「没报」有两种：真的没有违规，和检查器主动没看。两者必须分开计数——
否则「A04 = 0」既可能是修好了，也可能只是检查器把整个模块跳过了。

### 判据必须是结构性的，不是字样

反面教材（2026-11-13 发现并修掉）：A04 曾用「模块里出现 `corrupt|backup` 就整模块跳过」。
后果是**修得越好、检查器越看不见**；更糟的是，任何人加一个空函数
`def _backup_corrupt(p): pass` 就能让这个模块永久免检。

现在 A04 判的是结构：读失败分支里有可达的备份调用（`shutil.copy` / `os.replace` 等，
或调用模块内**函数体确实做拷贝**的函数）才放过；空壳函数不算。
反证补了一条：模块里加空壳 `_backup_corrupt`，检查必须仍然红。

同类收紧：A10 的「不是提交」、A11 的「已标历史」、A07 的「展示控件」，
都不再允许「整行/整页里出现某个词就跳过」，只认紧贴该条目的语境（±24 字符或同一句）。

### 判据清单：哪些能被改名/注释关掉

2026-11-13 逐条审查 A01–A14，按两种失效方式分开计数（两数不重叠），
随后把当时标为「仍按名字/关键字」的 7 条逐条改成行为判据：

| 判据 | 改前（可被关掉的方式） | 改后（行为判据） | 反证 |
|---|---|---|---|
| A07 | 接收者名字像控件（`display/label/panel…`）就跳过 | 只看调用形态：接收者在模块内被绑定成 Qt 控件构造结果才算展示；并把同文件同方法其余站点除行号写进同一条 | 「改名成 panel 也不能当展示」：`self._vfo_panel.set_sample_rate()` 红；先 `= QLabel()` 再调用绿 |
| A09 | skip 理由命中「文件/环境/缺失」等锚点词就放过 | 只看 skip 形态：①在宽泛 `except` 里；②无条件 skip 且**跳过之前**没有断言（同模块另有带断言的用例覆盖同一行为则降为复核）；③理由文本不再参与判定 | 条件 skip + 前置断言绿；无条件无断言红 |
| A10 | 同一行含「例如/示例」就整条放过 | 只有落在**围栏代码块**里的 hash 才算示例；正文里的不算 | 正文含 hash 仍红；代码块内跳过并记账 |
| A11 | 同一句含「历史/修完」就当真 | 必须同时给出可复核物证：命令、提交号、或日期；只有「历史」没有日期仍是红；绿色声明只有命令/日期时记账为「可复核但未绑定提交号」。**目录不再是开关**：`docs/learn|research|audit*|pending-patches|incidents/` 不再自动免检，确实属于历史快照的，按文件在 `docs/axioms-waivers.json` 登记（理由/到期/批准人），过期即回红 | 只有「修完」不给日期 → 红；带日期 → 复核记账；放进 `docs/incidents/` 但不在豁免表 → 仍红；登记且未过期 → 绿；过期 → 红 |
| A05 | 阻塞调用名表（`recv/read/get/poll`） | 主判据换成控制流：线程体里 `while True` 无 `break/return/raise` 即红；次判据只对 socket/serial 这类**仓外 API** 实例上的阻塞读、且未设超时，才成立 | 「改为无界循环后仍要报」：`while True: self._pump()` 红；加 `break` 绿 |
| A12 | 按函数名前缀 `fmt/format/render` 配对比较 | 按函数体**骨架**（抹掉标识符与常量值）分组，跨 ≥2 模块、正文含单位字面量、且口径（精度/单位）不同才报 | 名字完全不同（`_fmt_level` vs `render_gain`）仍红；口径一致绿 |
| A13 | 只把 `owner/holder/serial` 命名的变量当身份 | 只看记录 schema：模块里出现带 `pid/serial/start_time` 等唯一字段的记录，却拿另一个同名字段比较才算；豁免判断也只看**比较式本身**是否含唯一字段（同函数别处写 `'pid'` 不算）。**找不到带唯一字段的记录时**，不再退回 `owner/holder/identity` 字样判断，而是报 `A13-undecidable`（复核）并在理由里写出模块名与该模块实际可见的字段集合 | dict 记录 + `item['claimant'] == payload['claimant']` 红；比 `pid` 绿；只有 `owner` 字样、无唯一字段记录 → 报「无法判定」 |

**两个数**（分子/分母均为原「7 条可绕」）：

- **改前 7 条可被关掉 → 改后 0 条**。上一轮改到 6 条时，残留的 A13 会在
  「模块里找不到带唯一字段的记录」时退回 `owner|holder|identity` 字样判断，
  换个字段名仍可绕过。2026-11-13 第三轮把这一段改成 `A13-undecidable`：
  判据不再假装能判定，而是把「没找到唯一字段记录」这个缺口本身报出来
  （理由含模块名与模块里实际可见的字段集合）。至此 7 条全部有「改名/包一层仍红」的反证。
- 跳过条数（防止用放宽换绿，实测点 `8d7447c8`）：A03 603（不变）、A04 1、A09 55（53 条形态合法
  待复核 + 2 条有同模块覆盖）、A10 84（代码块 13 / 邻近说明 66 / 上游版本 5）、
  **A11 35（带命令/日期 16 / 转绿判据 14 / 历史带日期 5；「历史目录」这一类已随目录开关一起删除）**、
  D6 13。
  **A09 的 55 条全部写进账本并标「需人工复核」，不是静默放过**；这类计数暴涨时先看理由分布，
  再判断是放宽还是把原先藏起来的「没看」显式化了。A07 补认常量、D6 降级路径过期都没有新增跳过。

两条补充（不在 A01–A14 计数内，同族）：D3 的 `bw/bandwidth` 别名表按名字配对，D6 的占位路径
按「看起来像文档占位符」跳过——都是"名字/写法即开关"，尚未改成结构判据。

**A07 的第二处同族缺陷**（2026-11-13）：范围守卫只认数字字面量，于是
`desktop/scanner_panel.py:119` 的正解（用 `desktop/constants.py` 的 `FREQ_MIN_HZ/FREQ_MAX_HZ`）
反而判红——唯一能变绿的写法是把 `10_000.0` / `6.0e9` 再抄一遍。现已补认：
`ast.Name` / `ast.Attribute` 能解析到模块级数值常量（同模块顶层字面量赋值、`from 模块 import 常量`、
或 `模块.常量` 且来源是字面量）即算加了约束；`float(x)` / `int(x)` 包装的实参还原到 `x`。
解析不出来（运行时计算、参数传进来的阈值）仍报红，并把表达式写进理由标明「无法静态确认」。
反证四条：常量做上界 → 绿；字面量做上界 → 绿；`if x < some_runtime_value` → 红且带注记；
常量改成运行时计算（`int(os.environ[...])`）→ 回到红。
顺带修正：`if <call> is False:` 这类对**返回值**的布尔判断不再算入参范围守卫
（`bool` 是 `int` 子类，旧判据把它当成数值上界），因此新暴露 3 处本就没有入参约束的调用路径，
按「判据变准暴露的存量」入账。

### 目录/字样不再是开关（2026-11-13 第三轮）

三处「读了登记却按路径/字样放行」的地方改成显式登记 + 语义判定，每条都有反证：

- **D4 门禁登记**（`survey.py`）。过去门禁名只要在某文档任意位置出现就算登记，
  写一行「这个门禁不能用」也能通过。现在登记必须指名**可执行入口**，且入口要能解析：
  `scripts/xxx.py`（文件存在，且文件名就是门禁或文件内容确实调用它）、
  `--steps xxx`（`scripts/release_check.py` 里有 `_step_xxx` 且正文提到该门禁）、
  `.git/hooks/<hook>`（钩子文件存在且内容提到该门禁）。一个入口成立即算登记；
  一个都解析不到报 `D4-gate-unresolvable`（应修），只有名字没有入口也报同一条。
  反证：只写名字 → 红；写真实脚本路径 → 绿；写不存在的路径 → 红。
- **D4 门禁 / 共享库分家**（`survey.py`）。`scripts/` 下按命名收进来的脚本先按
  「有没有 `def main(`」分成**门禁**与**共享库**：门禁必须有入口（钩子 / 可解析的文档登记 /
  被测试以 subprocess 或 import 实际执行），共享库（如 `guard_common.py`）没有自己的入口，
  不能要求「谁跑它」——那只能逼人编一行假登记。库的判据是「必须被某个已真的
  有入口的脚本引用」，否则报 `D4-unreferenced-lib`（复核）。
  「被测试执行」只认测试文本里出现 `xxx.py` / `scripts.xxx` / `from scripts import xxx`
  三种可执行引用，注释里提一句名字不算。反证：库无人引用 → 红；被已登记门禁 import → 绿。
- **A11 历史快照**（`checks.py`）。删掉 `ARCHIVE_DOC` 目录正则与 `docs/learn/` 整篇跳过，
  路径不再决定是否免检。确实属于历史快照的文件在 `docs/axioms-waivers.json` 逐文件登记
  （文件 + 理由 + 到期 + 批准人），走现成豁免机制：未过期 → 绿，过期 → 应修。
  反证：放进 `docs/incidents/` 但不在豁免表 → 仍红；登记且未过期 → 绿；过期 → 红。
  迁移时只登记「原本会被旧目录规则整篇跳过、且只包含无凭据绿声明」的 105 个文件；
  8 个文件因为混有「绑定到本仓不存在提交」或「标了历史却没有日期」的条目而**不登记**，
  宁可让那 12 条重新可见，也不让文件级豁免顺手盖掉它们。
- **COORDINATION.md 提及统计**。`D5` 里保留 `claim` 提及次数，但结论行明确写
  「只统计提及次数，不做语义判定；不是白名单」——它是事实描述，不是准入校验。

同轮修正一个会静默吞结果的实现缺陷：`D1-cycle` 构造 `Finding` 时多传了一个位置参数，
`waived` 被赋成字符串 `"review"`（真值），于是**所有模块级 import 环一直被当成已豁免**，
从报告与棘轮里消失。修正后首次看到 2 个强连通组（代表 `builtin_tle_meta`、
`fengyun_sat`）。逐边核对后确认：**这两个组的闭环边全部位于函数体内**，模块顶层
import 没有环（实测 0 个）——它们是延迟导入组，不构成初始化顺序环。因此 `D1-cycle`
改为只认模块顶层执行的 import；延迟组保留在事实行里，成员全列，供人核对。
教训同属「判据必须可自证」：`Finding` 的位置参数一旦错位，判定结果会以「豁免」的
形态静默消失，比报错更难发现；而把延迟 import 算成环，则是反过来把正常写法报成
不可解的结构问题。两种都让「看见」失真。

### 初始化顺序环只认模块顶层 import（2026-11-13 第三轮）

**技术依据：Python 先把模块对象登记进 `sys.modules`，再执行模块体。** 模块顶层的
import 会在模块体执行期间发生，两个模块在顶层互相 import 时，后者拿到的是尚未执行完
的模块对象——这才是初始化顺序环。函数体内的 import 只在函数被调用时执行，那时导入方
模块已经完成初始化，因此**不构成初始化期环**。

旧口径把「依赖关系」与「初始化顺序依赖」混为一谈，后果有两个，且方向相反：
一是**用延迟 import 正当破环的手法会被永久报红**（依赖还在，但初始化期已经安全）；
二是真正的顶层环被淹没在同一批条目里，修复优先级无从判断。

保留一条防藏匿规则：**同一对模块，一个方向是顶层 import、另一个方向是函数内延迟
import（混指），仍报 `D1-cycle`**。这正是「把真环的一条边挪进函数体」的动作——
依赖没有变，失败只是从导入期推迟到第一次调用，不能因为写法改变就从报告消失。

延迟组的可见性：日常输出（不是 `--verbose`）的 D1 事实行必须给出
**组成员数 / 内部边数 / 最小割**，并列出全部成员；最小割按
`min over s≠t maxflow(s,t)` 计算，表示「至少删几条边才能打断这一块」。
反证自检覆盖三种边界：顶层双向 → 红；顶层 + 反向延迟 → 红；双向都延迟 → 绿。

### 判据对象的字段类型就是判据（2026-11-13 第三轮）

**规矩：`Finding` 构造时 `waived` 只接受 `bool`，任何非布尔值（字符串、数字、其他真值）
一律抛 `TypeError` 并让检查器报错退出；`severity` 只接受 `must-fix` / `review`，
其他值抛 `ValueError`。**

这条不是只修 `D1-cycle` 那一处调用：对于「豁免」这种会让条目从报告和棘轮里一起消失的
字段，不能依赖调用点自觉按关键字传参。硬断言落在 `Finding.__post_init__`（`model.py`），
覆盖所有构造点——包括未来新增的检查。把字符串 `"review"` 当成 `waived=True` 之所以危险，
是因为它产生的不是错误，而是一份**看起来在跑、实际什么都没判**的报告。

反证（`selftest.py`，必须全部抛错）：`waived` 传字符串 → `TypeError`；`waived` 传 `1` → `TypeError`；
`severity` 传 `True`（把第 7 个位置参数写成布尔）→ `ValueError`。这条与棘轮同属 R1 级自证：
判据对象的构造必须先于判据本身被验证。

### 判据必须报出它分析的树（2026-11-13 第三轮）

**规矩：分析入口先打印「分析树 = 根目录 + HEAD + 未提交改动数」；每条 finding 的 `path`
必须存在于这棵树里，`file:line` 的行号不得超过该文件行数。不满足就地把规则名改写成
`-unknown-path` / `-line-out-of-range` 并升级为应修，而不是删掉条目。**

事故形态：报告里出现过 `mbdsdr_ai/skyfield_compat.py` 与
`docs/learn/phase9/_PHASE9_FINAL_SUMMARY.md` 两个路径。按 records 核对：本仓、
29 个 worktree、`git log --all` 都没有它们，也没有任何一次检查器输出产生过它们——
它们是汇报时手写的，不是分析结果。**「报了一个不存在的东西」比漏报更伤**：
读报告的人会以为看见了证据。

落地方式（`survey.validate_finding_locations`，在 `collect_all` 出闸前统一执行）：
路径不存在 → `unknown-path`；行号超过文件行数 → `line-out-of-range`；
`.git/` 前缀（钩子台账）与 `<inline:...>`（反证片段）不属于工作树，豁免。
D6 的事实行会给出「核对 N 条 / unknown-path 0 / line-out-of-range 0」，让这条不变式
自己的执行也可复核。反证：真路径正常、不存在路径必须被改写、行号越界必须被改写。

这一条与「判据对象先验类型」是同一类：判据不仅要证明结论，还要证明**它看的是哪棵树**。

### 登记表必须由检查器执行

`docs/truth-sources.json` 是「每个量的合法产生点」的登记表。D2 曾经完全无视它——
登记了也只是装饰品，于是出现第二个死真值源。现在语义由检查器执行：

- 未登记的量：每个产生文件各报一条 `D2-multi-producer`，理由里列出全部 N 处产生位置。
  「只报字母序第一个」会让改掉那处后报告挪到下一个文件，形成**永远关不掉的红**。
- 已登记的量：登记文件之外的产生者各报一条；登记文件自身算合法。
- 登记失真（路径不存在，或该文件没有与事实同名的产生函数）报 `D2-truth-source-stale`。
- 产生点按「事实」（频率/带宽/增益/采样率/电平）分组，只认 `setText/QLabel` 等用户可见读数，
  不按单位分组（按单位会把全仓 244 个文件算成同一个量）。

### 路径过期 ≠ 声明不实（D6）

文档引用不存在的路径时，先在同一文档里找「该模块已迁到 X」的记录（同一模块名 + 迁移词 +
一个仓内存在的 `.py` 路径，三条齐全才算）。命中就降级成 `D6-path-moved`（review，给出建议路径）；
搜不到迁移记录才是 `D6-missing-path`。**降级也要报**，只是分类不同——不许静默消失。

### 跳过账本

每个基于上下文放过的分支都记进账本，报告里单列：

```
── 检查器自述：哪些地方被跳过（没看见 ≠ 没问题）──
   A03 跳过 604 处：宽泛静默 except，但这段代码不碰持久化（需人工复核）
   A04 跳过 1 处：模块没有整文件写回路径
   A09 跳过 54 处：形态合法（条件 skip / 断言在前 / 只提仓外依赖），需人工复核
   A10 跳过 74 处：代码块里的示例 13 处；hash 紧邻处标明不是提交 56 处；`repo @ hash` 指向上游版本 5 处
   A11 跳过 35 处：带命令/日期 16 处；写的是转绿判据 14 处；标注历史且带日期 5 处
   （历史快照不再按目录跳过：105 个文件按文件登记在 docs/axioms-waivers.json）
   D6  跳过 2 处：引用的是本检查器自己的产物（按需生成）
```

一个规则报 0 条但跳过 75 处，和报 0 条跳过 0 处，含义完全不同。
「被跳过」的分支同样是判定权外移：上下文一变，跳过的那批条目就重新变成违规。

## 结果怎么读

- **应修**：检查器能直接指认的缺陷，默认最高优先。
- **复核**：事实已成立，但需要人判断是"改代码"还是"改口径"（例如同名常量是否同一语义）。
- **豁免**：命中规则但已按三要素登记（reason / review_by / approver），到期自动重新计入违规。

## 八维与对应检查

| 维度 | 可判定规范 | 检查名 |
|---|---|---|
| D1 分层与依赖 | `mbdsdr_ai` 不得 import `desktop`/Qt；`tests` 反向依赖 `desktop` 只允许在显式 UI 用例；模块顶层 import 不得成环（函数体内延迟 import 只列事实，不算环） | `D1-reverse` `D1-qt-in-core` `D1-test-reverse` `D1-cycle` |
| D2 真值源 | 同一事实只允许一个产生点；同名常量不得多值；消费点必须走 `desktop/readouts.py`；产生点由 `docs/truth-sources.json` 登记并执行 | `D2-multi-producer` `D2-truth-source-stale` `D2-dual-constant` + A01–A04/A12/A13 |
| D3 接口与命名 | 公开函数 ≥8 的模块必须有 `__all__`；同名不得不同职；同一概念不得两名 | `D3-no-contract` `D3-name-role` `D3-concept-alias` |
| D4 门禁与验收 | 守卫必须接入某个入口；门禁的文档登记必须指名**可解析**的入口；共享库必须被真的有入口的脚本引用；skip 只能表环境缺失；断言必须可成立；xfail 必须 strict | `D4-silent-gate` `D4-gate-unresolvable` `D4-unreferenced-lib` + A05–A09 |
| D5 流程与协作 | 已知红必须带转绿条件/复跑命令；文档提交号必须 `cat-file` 可见；绿必须绑定当前树；位置引用必须落到符号 | `D5-known-red-incomplete` + A10/A11/A14 |
| D6 目录与文档 | 文档引用的仓内路径必须存在；行号必须落在文件内；主题冻结声明必须与代码一致；每条 finding 的 path/line 必须落在被分析树内 | `D6-missing-path` `D6-path-moved` `D6-stale-line` `D6-color-claim` `unknown-path` `line-out-of-range` |
| D7 任务分派 | 按固定公式算优先级，不由"谁最后汇报"决定 | `priority_score` |
| D8 豁免与债务 | 任何"暂时不做"必须有理由、到期、批准人 | `D8-debt-no-expiry` |

## 14 条公理

每条都能指回本仓真实案例；"反证位置"是自检里重建该违规片段的位置，
自检要求**正例必须变红、负例必须变绿**，且删除被测逻辑后正例必须真红。

例外只有两条，且都是「不变量/稳定性」用例，不是违规检出用例：
`_locator_drift_check`（A14：行号漂移前后都必须 0 命中）与
`_cycle_representative_check`（D1：两次运行命中集合必须完全一致，因此计数为 1/1）。
其余用例仍须满足正例≥1、负例=0；这两条不适用该口径，也不得被当作「负例仍红」的违规。

| ID | 陈述 | 可判定形式 | 本仓案例 | 检查名 | 反证位置 |
|---|---|---|---|---|---|
| A01 | 用户可见事实只有一个产生点 | `desktop/` 下非 `readouts.py`/`constants.py` 的含单位 f-string 即违规 | main_window 多处就地拼 MHz/kHz | `readout_single_source` | main_window 状态条 |
| A02 | 读数保真，不得把值变成另一个值 | MHz 频率 <6 位小数 / kHz 带宽 0 位小数即违规 | 2400 Hz 显示成 2 kHz；bookmark `.4f} MHz` | `lossy_readout` | `bookmark_panel.py:342` |
| A03 | 降级必须可见且可归因 | 持久化读取外层的宽泛 except 只 pass/return None/default 即违规 | `conversation.py` 读损坏会话静默回空 | `silent_degrade` | `conversation.py` except 块 |
| A04 | 不可读的输入不得被静默覆盖 | 同模块「静默读」+ `json.dump(` 覆写，且读失败分支里没有**可达的**拷贝/改名调用即违规（不按字样跳过） | 会话/配置读损坏后继续整文件覆写 | `corrupt_no_clobber` | `conversation.py` |
| A05 | 每个守卫必须有可成立的通过态 | 顶层 `assert False`、消息自述不可能、线程体 `while True` 无 `break/return/raise`（或仓外 socket/serial 阻塞读未设超时）后断言线程已退出 | `test_network_failure_modes.py:407`（需 307s） | `impossible_assertion` | 同左 |
| A06 | 守卫必须可反证 | 非 strict xfail、吞 `AssertionError`、宽泛 except 内 skip、死断言 | K06 静默吞噬 113 处；空过的探针 patch | `vacuous_guard` | `tests/` 去掉 `strict=True` |
| A07 | 约束必须放在必经的最窄处 | 同一动作的调用方里「有数值守卫」与「无守卫且丢弃返回值」并存即违规；同文件同方法只出一条，但把其余站点的行号写进同一条 | 采样率范围只在 device_panel；增益四条路径 | `constraint_at_chokepoint` | `set_sample_rate` 的多条路径 |
| A08 | 守卫必须判对对象且可被触发 | `if`/`while` 条件完全由字面量构成即违规（不做变量传播避免误报） | `decoders.py:435 if False:`；无设备降级判错对象从未触发 | `guard_cannot_fire` | `decoders.py:435` |
| A09 | skip 只能表示环境缺失 | 看 skip 形态：在宽泛 `except` 里、或无条件且跳过前无断言（同模块无覆盖）即违规；理由文本不参与判定 | `test_web_bridge_mcp.py` 6 处「MCPToolSet 在此环境不可构造」 | `skip_means_env_only` | 同左 |
| A10 | 证据必须可复核 | 文档反引号中的 7–40 位十六进制串必须 `git cat-file -t` 为 commit | `推进报告:141` 的 `5d09bb2`（示例，非真实提交）、`COORDINATION:3430` 的 `0d7e433e`（示例，非真实提交） | `evidence_commit_exists` | `推进报告:141` |
| A11 | 绿必须绑定到被断言的那棵树 | 绿色声明无提交号且无可复核物证（命令/提交号/日期缺一）；或提交距 HEAD >50 且未标历史/修完 | `learn/phase40/ci-notes.md` 的 752 提交前绿 | `green_binds_tree` | `known-red-register.md` |
| A12 | 同名实现必须行为一致 | 函数体骨架相同、跨 ≥2 模块、含单位字面量、但口径（单位/精度）不同即违规（不看函数名） | `main_window._fmt_dbfs` vs `readouts.fmt_dbfs` | `duplicate_impl_divergence` | main_window 的 `_fmt_dbfs` |
| A13 | 身份必须唯一且不可复用 | 模块里有带 `pid/serial/start_time` 的记录，却用其中另一个同名字段判同一；比较式里没出现唯一字段即违规。**没有唯一字段记录时报 `A13-undecidable`**（无法判定，带模块名与可见字段集合），不按字样放过也不按字样定罪 | `file_claim.py:540` 只比名字；`device_manager.py:422/451` 用序号兜底 | `identity_not_reusable` | 同左 |
| A14 | 位置引用必须是符号，不是行号 | 台账/豁免里的 `path.py:N` 若没有可校验的符号名 → 定位不稳定；`name()` 必须在目标模块 AST 符号表里存在 | `axioms-waivers.json` 的 `main_window.py:8327/12761/14455` 在文件被改十几行后全部失效 | `locator_is_symbolic` | 目标文件顶部插 20 行空行 |

判同与判活是两件事：同 pid + 进程启动时间才叫"同一个进程"；同序列号才叫"同一台设备"。
名字、枚举序号、可改路径字符串都只是标签。

## 时空域量：现有公理下的不变量

时空量（过境、TLE、时区、星下点、可见性、步长）不是新的治理维度：它们要的
性质——同一事实一个产生点、值不得被换算成另一个值、降级必须可见、同名实现
行为一致——已经由 A01/A02/A03/A12 覆盖。因此这里**不新增公理**，只把六个量
逐项登记成这些公理下的具名不变量，并给出可复跑的判据；缺判据的两项本轮补上，
其余引用既有用例。

| 时空量 | 载体（符号） | 挂靠公理 | 可复跑判据 | 状态 |
|---|---|---|---|---|
| 过境起始基准 | `sat_passes.predict_passes`（排班门限 `PASS_MIN_ALT_DEG`）、`skyengine.satellites.next_rise_set(horizon_alt=)`、`PASS_COARSE_STEP_S`/`PASS_FINE_STEP_S` | A02 | `tests/test_pass_step_alignment.py`、`tests/test_grazing_pass_detection.py`、`tests/test_satellite_rise_set_precision.py` | 已兜：门限未放宽、擦边不放过、事件顺序有标记 |
| TLE 老化与未来历元 | `tle_integrity.freshness`、`time_basis.tle_epoch_report`、`tle_epoch.TLE_FUTURE_TOL_DAYS` | A01 + A12 | `tests/test_tle_epoch_single_source.py`、`tests/test_tle_age_visibility.py`、`mbdsdr_ai/tests/test_tle_freshness_predict.py` | 本轮补齐：年龄/未来判定单一产生点、容差单一常量，`freshness` 暴露 `age_days_signed` 与 `epoch_implausible` |
| 时区与夏令时 | `time_format.resolve_tz`/`resolve_tz_info`/`tz_degradation`/`local_time_note`、`time_basis.local_to_utc(fold=)` | A02 + A03 | `mbdsdr_ai/tests/test_timezone_fallback_detection.py`、`mbdsdr_ai/tests/test_time_basis.py`、`mbdsdr_ai/tests/test_time_scale_physics.py` | 已兜：歧义必须显式 `fold`、不存在时刻抛错、墙钟回跳告警；兜底固定偏移带可检测降级标记（三态 `system-rules`/`fixed-offset`/`unavailable`） |
| 星下点与地面轨迹 | `ground_track.satellite_subpoint`/`ground_track`/`satellite_footprint`、`orbit.wgs72_ecef_km_to_llh` | A01 | `tests/test_ground_track.py`、`tests/test_ellipsoid_boundary.py` | 已兜：星下点只走 WGS-72 椭球反解；覆盖圈用同一 `WGS72_A` 做球面近似（已在函数 docstring 声明，不与椭球反解混用） |
| 可见性门限 | `sat_passes.VISIBLE_MIN_ALT_DEG`、`ground_track.DEFAULT_MIN_ELEVATION_DEG` | A01 | `tests/test_visible_gate_single_source.py` | 已兜：卫星"可见"只有一个仰角常量；恒星可见性（星等/消光）是另一概念，不得共用该常量 |
| 步长误差界 | `PASS_COARSE_STEP_S`/`PASS_FINE_STEP_S`/`PASS_BISECT_TOL_S`、`skyengine.satellites.RISE_SET_BISECT_TOL_S`、`TRAIL_STEP_S`/`TRAIL_STEP_MAX_ERR_DEG`、`DEFAULT_TRACK_STEP_S`/`DEFAULT_TRACK_STEP_MAX_CHORD_KM` | A02 | `tests/test_ground_track_step_bound.py`、`tests/test_pass_step_alignment.py`、`tests/test_satellite_rise_set_precision.py` | 已兜：地面轨迹有弦高界；过境 AOS/LOS 由内联 `1.0s` 提为 `RISE_SET_BISECT_TOL_S` 并被断言（误差 ≤ 声明容差） |

**已闭合（2026-11-14）**：时区兜底的降级此前不可检测，DST 地区的用户会得到
静默的固定偏移换算。现在三种情形可区分且调用方能判定：

- `time_format.resolve_tz_info()` 返回 `TZResolution(tz, state, source, degraded, reason, note)`，
  `state ∈ {system-rules, fixed-offset, unavailable}`；
- `resolve_tz(None)` 保持返回 `tzinfo` 契约（不破坏 `astimezone` 调用点），兜底时返回
  `_FixedOffsetTZ`（`mbdsdr_degraded=True`、`mbdsdr_state="fixed-offset"`、`mbdsdr_reason`）；
- `tz_degradation(tz)` 可对任意时区对象回查同一结论，`local_time_note()` 给出
  「当前为固定偏移，不随夏令时调整（系统时区规则不可发现）」；
- `local_to_utc` 的 docstring 明示：固定偏移下 `fold=0/1` 偏移相同，夏令时歧义
  **无法检测**，调用方应先查降级状态再呈现；
- 呈现点已接同一句说明：`desktop/time_machine.py`（本地时间输入）、
  `desktop/rf_sky_view.py::format_local_time`（天空状态卡本地时钟）、
  `new_spacetime.local_display()`/`TimeInfo.local_note`（时间信息）。

判据：`mbdsdr_ai/tests/test_timezone_fallback_detection.py`（10 passed）——注入
`_probe_system_tz → (None, …)` 构造「系统时区不可发现」，断言调用方能判定降级；
正常探测时断言未降级、说明为空；并覆盖 `unavailable` 与「冻结偏移不随冬夏变化」。
反证：删掉 `_FixedOffsetTZ.mbdsdr_degraded` 后该文件 2 failed（`test_fixed_offset_is_detected`、
`test_time_basis_reexports_query_api`），恢复即绿。

## 棘轮（存量只减不增）

```bash
python scripts/axioms.py                 # 默认启用：基线存在即比对
python scripts/axioms.py --no-ratchet    # 只看测绘，不做比对
python scripts/axioms.py --accept-current --accept-reason "接受并发线的真实状态变化"
```

- 基线文件 `docs/axioms-baseline.json` **入库**（不入库的基线只在这台机器有效，对 CI 不存在）。
  它按**条目指纹**存：`sha1(规则 + 路径 + 规范化片段 + 同键序号)`，**不含行号**。
  行号会随无关改动漂移，含行号会让一次 import 变成几百条「新增 + 消失」；
  片段里内嵌的「另一个文件:行号」（如 D3 指认同名实现）也不参与指纹，否则对方文件挪一行就会报假新增。
  条目里存的是指纹的**组成部分**（规则/路径/片段/序号），比对时重算指纹——
  指纹算法改进不会把旧基线打成一片新增。
- 只把**未豁免**条目计入额度。豁免由 D8 的 `review_by` 管理：到期后该条重新
  变成应修，从而以「新增」身份触发棘轮，不会被旧基线悄悄吸收。
- **新增 / 严重度升级 → 退出 1**，并报出 `文件:行`、为什么、怎么修。
  仅「消失」不阻塞，但会提示基线可下调。
- `--accept-current` 是唯一写基线的入口：必须显式传，且会打印
  「本次接受了哪几条新增」「哪几条消失」两份清单。不能与 `--rule` 同用。
- **`--accept-current` 必须带 `--reason`（非空）**，否则直接拒绝（退出 2，不写任何文件）。
  每次接受都会把**谁**（`MBDSDR_CLAIM_OWNER` / 系统用户名）、**会话标识**、**在哪个提交上**、
  **理由**、以及**被接受/升级/移除的指纹清单**追加进 `docs/axioms-accepted.json`。
  该文件进版控、可 diff：谁在什么时候把什么存量放进了基线，一眼能查。
  接受之后再跑，这些条目按「已接受（在基线内）」入账，不再显示为「新增」，且条数与基线对得上。
- 棘轮自身有反证（自检 `R1`）：新增一处静默 `except: pass` 必红、
  删掉必绿、**同数量替换必须同时报出新增与消失**（防止按总数抵消）。
- 2026-11-13 实测点 `eb3ef686`（干净 worktree，本线最后一个提交）：基线 520 条、当前 520 条、
  新增 25 / 消失 25；接受进基线 55 条（仍存在 47、消失 8）——三个数都取自棘轮输出，是对账计数，不是对某处修复下结论。
  这 25 条**全部属于并行线的改动，未接受进基线**：`mbdsdr_ai/device_owner.py` 的
  A03/A04/D3、`mbdsdr_ai/device_manager.py` 的 A13、`known-red-register.md` 的 A14/D8、
  `desktop/control_panel.py` 的 `gain/gain_db` 别名、`command_palette.py` 的 D3、
  `scripts/check_red_accounting.py` / `guard_common.py` 的 D4、`redundancy-register.md` 的
  D6 行号漂移、`pending-patches/2026-11-13-device-session-lock-not-implemented.md` 引用的
  未创建用例。
- 归因方法（防止把自己的新增算成别人的）：拿**本轮改动前**的 `checks.py` / `survey.py`
  在同一棵树上跑，得到的 25 条新增清单与本轮改动后**逐条相同**、消失数 28 → 25。
  即本轮「A07 补认常量 + D6 路径过期降级」在真实树上引入的净新增是 **0**。
  本轮的 D2 新增 5 条、A07 暴露的 3 条已按审计流程接受并记账（见 `docs/axioms-accepted.json`）。
- 历史快照（非当前）：2026-11-13 第三轮实测点 `8d7447c8`（干净 worktree）：当时 538 条（应修 76 / 复核 462）、
  豁免 159 条（既有 54 + 本轮按文件登记的 105 个历史快照）、新增 45 / 消失 27。
  逐条归因：其中 31 条是并行线的存量（即上一条的 25 条加上其后新增的
  D4 静默门禁、known-red 台账条目等），本轮自身带来 14 条：
  **2 条 `D1-cycle`**（`Finding` 参数错位修正后才可见的真实环，见上节）+
  **12 条 `A11`**（8 个历史快照文件因混有「绑定到本仓不存在提交」或「标了历史却没日期」
  的条目而**不登记豁免**，宁可让它们重新可见——其中 4 条是应修）。
  这 14 条**未接受进基线**。

> 为什么不用「总数 272」：修掉一条、新增一条会互相抵消，总数不变但质量已退化。
> 指纹集合的差集才同时给出「新增了哪几条、消失了哪几条」。

## 派活优先级（D7）

```
score = loss*100 + freq*10 + evidence*3
        - 50（复核级）
        - 60（冻结文件，如 desktop/main_window.py）
        - 1000（已豁免）
```

| 输入 | 取值 | 含义 |
|---|---|---|
| `loss` | 1–3 | 用户可感知损失：数据丢失/显示错数=3；功能不可用=2；内部整洁=1 |
| `freq` | 1–3 | 触发频率：每条操作路径=3；常见场景=2；边角=1 |
| `evidence` | 1–3 | 证据可得性：无硬件即可静态/单测复现=3；需模拟=2；需真机=1 |

输出是按分数降序的清单（同分按路径稳定排序）。**损失永远压过整洁**，冻结件自动降权，
已豁免项排到最后但仍可见。

## 豁免规则（D8）

`docs/axioms-waivers.json` 的每一项必须同时具备：

```json
{
  "rule": "A02",
  "path": "desktop/main_window.py",
  "reason": "视觉冻结期，改动需先上报排期",
  "review_by": "2027-06-30",
  "approver": "用户（2026-11-12 决定）"
}
```

缺任一字段 → `D8-debt-no-expiry` 报违规；`review_by` 过期 → 该条自动重新计入"应修"，不再算豁免。
没有到期/批准人的"暂时不做"等于没登记。

## 首次全量跑（2026-11-13，HEAD `ab4d36df`；历史快照，非当前）

> 这是首跑快照。最新"存量"以 `docs/axioms-baseline.json` 为准（并发线会修复/新增，
> 基线随 `--accept-current` 显式下调或接受）。
>
> **口径提醒**：当时的采集层还带 10–40 条窗口截断，下表的 272 是窗口内条数。
> 同一棵树去掉截断后的真实总量是 **593 条 / 应修 119**（应修不受截断影响）。
> 截断已移除，见「计数口径」。

```
# 历史快照（非当前）：2026-11-13 首跑，采集层当时还带 10–40 条窗口截断
违规 272 条：应修 119，复核 110，豁免 43；反证自检 21 条均通过（当次；现为 33 条）
D1 5 / D2 69 / D3 33 / D4 42 / D5 40 / D6 39 / D7 0 / D8 1
```

### 立刻修（119）

| 类别 | 条数 | 为什么立刻 |
|---|---:|---|
| A03 静默降级 | 26 | 读失败与"本来就是空"不可区分，下一步就会覆盖或误判 |
| A02 读数失真 | 16 | 用户看到的数不是真实值（3–4 位小数 MHz = 1 kHz 量化） |
| A07 约束只在入口 | 15 | CLI/MCP/重连路径绕过同一条约束，后端夹值后无人提示 |
| D6 文档指向不存在的路径 | 18 | 照文档操作会直接失败（活跃文档，非历史快照） |
| A09 假 skip | 9 | 本仓依赖导不进来被记成"环境不凑巧"，长期假绿 |
| D5 已知红缺字段 | 8 | 没有转绿条件的"已知红"永远不会被销账 |
| A01 读数就地拼装 | 6 | 同一值换面板就换字符串 |
| A04 损坏后覆写 | 5 | 下一次保存永久丢数据，且无备份 |
| A06 空过守卫 | 5 | 删掉被测逻辑仍然绿 |
| D1 Qt 反向依赖 / 反向 import | 4 | 纯后端无法脱离 GUI 测试与复用 |
| A13 身份用名字/序号 | 3 | 同名进程可同时占同一设备；换 USB 口设备变两台 |
| A10 幽灵提交 | 2 | 后续排查建在不存在的提交上 |
| A05 / D2-multi-producer | 2 | 不可能通过的断言；同一读数多产生点 |

### 长期豁免（43，已三要素登记）

全部是 `desktop/main_window.py` 的 A01/A02/A12：该文件处于视觉冻结期，改动需先上报排期，
`review_by=2027-06-30`。到期后这些条目会自动回到"应修"。

### 不立即修、保留复核（110）

- **A11 绿未绑定当前树（30）**：多数是历史状态记录，需重跑后补提交号，或明确标注"历史快照"。
- **D3 命名/契约债（33）**：同名不同职、同概念两名，需改名而不是改逻辑。
- **D2 同名常量多值（12）**：先判定"同一事实"还是"同名不同职"。
- **D4 未接入入口的门禁脚本（11）**、**D6 历史快照文档的失效路径（12）**、
  **D6 颜色声明与代码不一致（7）** 等。

这些**没有**做批量豁免：豁免需要理由、到期、批准人三要素，批量豁免会把真实漂移藏起来。
它们保留在报告里，按季度复核。

## 反证自检（49 条，必须全部 PASS）

`python scripts/axioms.py --self-test-only` 会为每条规则在临时目录重建：

1. **正例**：真实违规代码片段 → 该规则必须命中 ≥1 条；
2. **负例**：修正后的等价片段 → 该规则必须命中 0 条；
3. 正例片段来自本仓历史/现存代码（见 `tools/axioms/selftest.py` 的 `provenance`）。

任一条正例 0 命中（检查失效）或负例仍红（误报）都会让命令退出 1。
这就是公理 A06 对检查器自己的约束：**守卫的反证不得空过**。
