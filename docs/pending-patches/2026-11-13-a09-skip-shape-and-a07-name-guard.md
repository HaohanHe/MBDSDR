# 待修：A09 skip 形状判据 + A07 调用参数守卫判据

> 生成：2026-11-13（A03/A09 收口线）
> **本单只写判据修法与反证设计，不改 `tools/axioms/*`、不改 `scripts/axioms.py`。**
> 归属：**公理棘轮线**。
> 占用：`scripts/axioms.py`、`tools/axioms/{survey,checks,selftest,ratchet}.py`
> 当前均由 `公理棘轮线` 持有（`file_claim.py status` 可复核）；`tools/axioms/report8.py`
> 与 `model.py` 的 claim 已过期但凭据仍在原线，按协议不抢。
> 基线口径：`python scripts/axioms.py --rule <A09|A07> --no-ratchet --no-self-test`

## 1. A09：skip 形状判据对装饰器结构性误报 18/19

### 1.1 现象

`--rule A09 --no-ratchet` 当前「应修 19」。逐条判完：**真缺陷 1、误报 18**。
18 条全是 `@pytest.mark.skipif(...)` 装饰器 + 可核对外部缺失（参考数据、C++ 源码
树、`.bsp`、RTL 硬件、Windows API），函数体在条件成立时确有断言。

### 1.2 两条成因（缺一条都解释不完）

1. **`unconditional` 只沿语句父链找条件。**
   现实现只把 `If` / `While` / `Try` / `For` / `With` 当「有前置条件」。
   但 `@pytest.mark.skipif(cond)` 的条件写在**装饰器**里：
   Call 节点的父链是 `decorator_list → FunctionDef`，没有 `If`。
   于是**任何 skipif 装饰器都被判成「无条件 skip」**。
2. **`has_assert` 只数 `node.lineno` 之前的 assert。**
   装饰器的行号在函数体所有 assert 之前，`lineno < 装饰器行号` 恒为假。
   于是**装饰器形态的 skip，`has_assert` 恒 False**。

两条叠加：只要 `_covered_elsewhere` 没兜住，`@pytest.mark.skipif(...)` **100% 被报**。
这对装饰器不是「偶尔误判」，是稳定误报。

### 1.3 修法

- **装饰器形态单独取条件表达式**：若 skip/skipif Call 位于 `decorator_list`，
  取其第一个位置参数作为条件，不要再用父链判 `unconditional`。
- **常量条件优先**：条件能被 `ast.literal_eval` / `const_eval` 求值时，直接按真假判——
  `skipif(False)` 不报，`skipif(True)` 报。
- **条件要能对上「外部缺失」**：条件里应出现可核对的探测器，例如
  `Path.exists()` / `is_file()` / `importlib.util.find_spec` / `sys.platform` /
  `os.name` / `os.environ.get(<显式开关>)`。取不到这类锚点时仍按违规报（人工 opt-out
  另见 §4）。
- **`has_assert` 覆盖整个函数体**：对装饰器形态，统计**该函数（类装饰器则统计类体）**
  内的全部 `assert`，不再用 `lineno` 单调比较。同步修正 `fn is None`（类装饰器）
  时直接放弃统计的问题——应回退到该类的方法体。

### 1.4 反证设计（每条都要能复现红/绿）

合成文件（仓外临时目录，不进仓库）逐条跑 `scan_a09([Unit(...)])`：

| 场景 | 期望 | 现状 |
|---|---|---|
| `@pytest.mark.skipif(False, reason="x")` + 体内 assert | **不报** | 报（误报） |
| `@pytest.mark.skipif(True, reason="x")` + 体内 assert | **报** | 报 |
| `@pytest.mark.skipif(not Path(p).exists(), reason="x")` + assert 在装饰器之后 | **不报** | 报（误报） |
| 类级 `skipif`，类内 method 有 assert | **不报** | 报（`fn=None` → 误报） |
| `@pytest.mark.skip`（无条件）+ 体内 assert | **报** | 报 |
| `try: ... except Exception: pytest.skip(...)` | **报** | 报 |

同时钉住一条**不得放宽**的：`@pytest.mark.skip(reason=...)` 无条件跳过的用例，
即使体内有 assert 也必须继续报——这正是本批唯一真缺陷
（`tests/test_spectrum_menu_tab_chain.py:97`，已改为直接运行）。

## 2. A07：`_guards_call_arg` 只认数字字面量，认不出 `ast.Name`

### 2.1 成因

`tools/axioms/checks.py` 的 `_guards_call_arg()` 只接受两种守卫：

- `if` 里被测名字是 `ast.Name`，且与传入实参名相同；
- `if` 里出现 `ast.Constant` **数值字面量**。

于是 `if not (FREQ_MIN_HZ <= f <= FREQ_MAX_HZ): ...` 这种**把约束放在常量里**的
正确写法不被识别；唯一能变绿的是在调用点抄 `10_000.0` / `6.0e9`——**用魔数换绿**，
与 `desktop/constants.py` 的立项目标正相反。

### 2.2 修法

- 守卫表达式里的数值边界同时接受 `ast.Constant`、`ast.Name`、`ast.Attribute`
  （`constants.FREQ_MIN_HZ`），并对 `Name` 做**模块级常量解析**（同文件赋值 /
  从 `desktop.constants` import 的别名），解析到常量字面量后按现有逻辑判。
- 比较支持链式（`a <= x <= b`）与 `and`/`or` 组合里的子表达式。
- **不要**放宽成「只要 `if` 里出现任意标识符就算守卫」——那会让
  `if foo: self.set_frequency(f)` 也变绿，等于把判据废掉。

### 2.3 反证设计

| 场景 | 期望 |
|---|---|
| `if not (FREQ_MIN_HZ <= f <= FREQ_MAX_HZ): raise` + `set_frequency(f)` | **不报** |
| `if not (10_000.0 <= f <= 6.0e9): raise` + 同上 | **不报**（现状已通过） |
| `if not (FREQ_MIN_HZ <= other <= FREQ_MAX_HZ): ...` + `set_frequency(f)` | **报**（名字对不上） |
| `if enabled: set_frequency(f)` | **报**（不是数值边界） |

注意 `scan_a07` 有 `if not guarded[method]: continue`：合成用例必须**至少造一个
被守卫的站点**，否则整条规则不出任何条目，反证会假绿。

### 2.4 与既有交接单的关系（别把行为缺陷一起"修没"）

`docs/pending-patches/2026-11-13-handoff-a07-remaining-4.md` 对
`mbdsdr_ai/sdr_backend.py` 的两处 `set_sample_rate` / `set_frequency` 另有定性：
**返回值被丢弃**——`set_frequency` 拒绝越界频点后 `connect()` 仍继续，最后报
`connected=True`，棒停在上一次频率（「插上收不到台」）。
这与「约束有没有下沉」是两件事：

- 判据修好后这两条会**自动变绿**（因为 `set_frequency` 内部已有区间检查）；
- 但「下发被拒仍宣告连接成功」的行为缺陷**不会**因此消失。

所以棘轮线修 `_guards_call_arg` 时，请在 `sdr_backend.py` 那两条上保留人工复核标记，
或把它转成 A03/A07 的行为型条目，**不要当作已修**。该文件当前由
`D1原语下沉线/6ae85f56` 持有并在重构，补丁文本见上述交接单。

## 3. 不在本单范围

- `tools/axioms/*`、`scripts/axioms.py`、`docs/axioms-baseline.json`：归棘轮线，本线不碰。
- `docs/axioms-accepted.json`：**当前被 `公理棘轮线` 持有**（`file_claim.py status` 可见），
  所以下面 §4 的豁免理由暂记在本单，等持有者转写进 accepted 记录的 `reason` 字段。

## 4. A09 豁免记录（裁定 + 两条约束）

| 条目 | 规则 | 裁定 | 约束 |
|---|---|---|---|
| `mbdsdr_ai/tests/test_star_catalog_packaging.py:68` | A09 | **误报，不改跳过** | ① `-rs` 必须看到理由；② CI 不得设开关 |
| 同文件 `:91` | A09 | **误报，不改跳过** | 同上 |

**裁定理由**：`MBSDSR_SKIP_WHEEL_BUILD=1` 是模块 docstring 明确写过的
**人工 opt-out**（分钟级真构建，纯逻辑调试时用），不是「环境缺失」。按 A09 字面
可算违规，但删掉开关会让每次调试都跑分钟级构建；且它**不是默认值**，不设就不跳。
故记误报。

**两条约束已落成测试**（`7467dc5d`）：

- `test_skip_markers_carry_visible_reason`：两个 `skipif` 的 `reason` 必须非空且写明
  触发开关——`pytest -rs` 里必须看得到「为什么没跑」。
- `test_ci_never_sets_skip_wheel_build`：扫 `.github/workflows/*.yml|yaml`、
  `.gitlab-ci.yml`、`azure-pipelines.yml`、`Jenkinsfile`、`.travis.yml`、
  `appveyor.yml`；出现该变量即红。新增 CI 系统时要把文件加进
  `_CI_CONFIG_GLOBS`，否则约束看不到它。

反证：`reason` 置空 → 前者红；`ci.yml` 里加该变量 → 后者红；还原后均绿。

## 5. 边界声明

- 本单**没有**改 `tools/axioms/*`、`scripts/axioms.py`、`docs/axioms-baseline.json`、
  `docs/axioms-accepted.json`。
- 本单只记录判据缺陷与修法，不代替棘轮线裁决「哪些条目算应修」。
- 代码侧本线本批只动了 1 条真缺陷（`test_spectrum_menu_tab_chain.py:97`），
  其余 18 条 A09 一条没改——包括这 2 条已裁定的豁免。
