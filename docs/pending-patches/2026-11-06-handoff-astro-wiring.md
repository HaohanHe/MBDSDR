# 交接单：天文框架接线四处（run_all / scaffold / index.html / verify_product）

> 生成：2026-11-06 数据面线（**只出交接单，不改别线文件**）
> 基线：HEAD `962c2d82`；目标四文件在工作区均为 `M`（接线条目已在工作区，
> HEAD 里还没有）。本单不代改、不代提交，落库后由所属线回填验证记录。
> 前置产物：`build/astro-golden.json` 已由 `f4ef0d13` 入库；前端与 selftest
> 已由 `3d0735e5` 入库。

## 1. 现状（必须分清「工作区已有」与「HEAD 已有」）

| 文件 | 工作区 | HEAD |
|---|---|---|
| `web/product/build/run_all.py` | 已有金标准步骤与 selftest 步骤 | **无** `astro` 引用 |
| `web/product/build/scaffold.py` | 脚本清单已含 `14-astro-frame.js` | **无** `14-astro` |
| `web/product/index.html` | 已含 `<script src="src/14-astro-frame.js">` | **无** `14-astro` |
| `web/product/tests/verify_product.py` | 已有 §9.6 astro 检查块 | **无** `astro` 引用 |

因此现在 HEAD 上「一条命令跑全链」**不成立**；工作区可以跑，但四文件未落库前
**不得宣称已接线**。下文行号均为工作区当前行号，落库后若行号漂移，以锚点文本为准。

## 2. 四处接点

### 2.1 `build/run_all.py`（两处，顺序是关键）

| 接点 | 位置 | 内容 | 前置 / 失败 |
|---|---|---|---|
| 金标准生成 | 第 4 步，`run_all.py:31` | `("天文框架金标准", [PY, "build/gen_astro_golden.py"])` | 必须在 selftest 之前；失败即该步 FAIL |
| 跨语言自检 | 第 10 步，`run_all.py:37` | `("天文框架跨语言自检", [NODE, "tests/astro_selftest.js"] if NODE else None)` | 放在「单文件构建」(34) /「静态守卫」(35) 之后，让构建先暴露 DOM/产物问题；**`astro-golden.json` 必须已由第 31 行那步生成** |

- `node` 可用性：`run_all.py:23` 用 `shutil.which("node")` 探测；缺失时该步
  `cmd` 为 `None`，`run()`（`run_all.py:44-46`）返回 `False` 且尾部输出
  `依赖缺失（node 未找到）` → 该步 **FAIL 并停链**，不是跳过。
- 非 0 传播：任一步失败 → `run_all.py:166-170` 汇总 `FAILED: ...` 并
  `return 1`；全通过才打印 `RESULT: ALL PASS`。
- 报告：`run_all.py:143` 在 `VERIFY-REPORT.md` 的 not_verified 段写
  「由 `build/gen_astro_golden.py` 与 `tests/astro_selftest.js` 跨语言对拍」。

### 2.2 `build/scaffold.py`（生成源的唯一真源）

- 接点：`scaffold.py:285-293` 的 `scripts` 列表，把 `"14-astro-frame.js"`
  放在 `"13-sat-tracks.js"` 之后、`"20-i18n.js"` / `"31-celestial.js"` 之前。
  同处注释（`scaffold.py:286-287`）已写明「14 必须在 31 之前（31 读 MB.astro）」。
- 前置：`src/14-astro-frame.js` 必须在位；缺文件时 scaffold 仍会生成标签，
  但运行期 `31-celestial.js:84` 会抛
  `31-celestial requires 14-astro-frame.js loaded first`，且静态守卫会红。
- 失败非 0：scaffold 本身遇缺文件不报错，由下游
  `verify_product.py:364-371` 的 `fail()` 收口（最终 `sys.exit(1)`，见 `:455`）。
- **不要手改 `index.html`**：它由 `scaffold.py:306` 整份重写，手改会被覆盖。

### 2.3 `index.html`（生成物，只验收不手改）

- 期望形态：`index.html:232-243` 的标签序列里，`src/14-astro-frame.js`
  紧接 `src/13-sat-tracks.js`，且在任何 `31-celestial.js` / `30-sky.js` 之前。
- 验证方式：改完 `scaffold.py` 重跑 `python build/scaffold.py`，检查
  `index.html:237` 这一行仍在、顺序未变；不要直接编辑此文件。
- 失败非 0：顺序错 → `verify_product.py` 的 astro 段或运行时抛错，链停。

### 2.4 `tests/verify_product.py`（静态守卫，插在 §9.5 之后）

- 接点：`verify_product.py:359-361` 的「<9.5> 全天星象仪」打印之后、
  `:401` 的卫星轨迹检查之前；工作区 `:363-399` 就是该检查块。
- 断言内容：
  - `:364-371` `src/14-astro-frame.js` 必含 `precessionMatrix` /
    `nutationMatrix` / `nutationAngles` / `gmstDeg` / `frameMatrices` / `Q` /
    `QT` / `IAU 2006` / `IAU 2000B`；
  - `:372-387` `31-celestial.js` 不得回退到旧 Meeus GMST（`280.46061837`）、
    不得走 J2000 直算 LST、热点必须走总矩阵、太阳 of-date 必走
    `dateToHorizon`；
  - `:389-397` `build/astro-golden.json` 必须存在、`caseCount ≥ 6` 且
    `meta.source` 含 `celestial_geometry.py`（缺产物时提示先跑生成器）；
  - `:398-399` `tests/astro_selftest.js` 必须在位。
- 失败非 0：`fail()`（`verify_product.py:31`）只累积错误，脚本末尾
  `sys.exit(1)`（`:455`）统一非 0；不会在第一处失败就中断，便于一次看全。

## 3. 依赖顺序（接错顺序会假红/假绿）

```text
gen_astro_golden.py  →  astro-golden.json
        ↓（必须先于 selftest）
scaffold.py（scripts 含 14-astro-frame）→ index.html / dist 构建
        ↓
verify_product.py（静态检查文件、golden、selftest 在位）
        ↓
node tests/astro_selftest.js（读同一份 golden 做数值对拍）
```

- `selftest` **不能**放在 `gen_astro_golden.py` 之前：`astro_selftest.js:8-10`
  直接读 `build/astro-golden.json`，缺失即抛错。
- `node` 是 selftest/perf/proj/runtime 四步的硬前置；没有 node 时
  `run_all.py` 会 FAIL 而不是跳过。
- 本检查纯本地：不需要网络、不需要浏览器；Edge 只影响截图步骤。

## 4. 落库后的验收（一条命令 + 预期输出形态）

在 `web/product/` 下：

```powershell
python run_all.py
```

应看到（顺序与名称按 `run_all.py:27-41`）：

```text
  OK   天文框架金标准
  OK   骨架与样式
  OK   单文件构建
  OK   静态守卫
  OK   天文框架跨语言自检
...
RESULT: ALL PASS
```

其中 selftest 一步的末行输出应为：

```text
astro-frame cross-language OK · 24 cases / 144 vectors / worst 3.33e-16 (horizon 2461500/40.7 ra=300[2])
```

失败形态：该步打印 `  FAIL 天文框架跨语言自检` + 末行原因，随后
`FAILED: ...` 且进程退出码 `1`；缺 node 时原因是 `依赖缺失（node 未找到）`。
单独复跑同一断言：

```powershell
node tests/astro_selftest.js   # 退出码 0 = 24 case / 144 向量全部在容差内
```

落库后请把上述实际输出贴回本单「验证记录」，**未贴回前不得宣称全链跑通**。

## 5. 禁止事项

1. 不要手改 `index.html`；只改 `scaffold.py` 的 `scripts` 列表并重跑。
2. 不要把 `astro_selftest.js` 挪到 `gen_astro_golden.py` 之前。
3. 不要把 `verify_product.py` 的 astro 块拆到 `:401` 之后（卫星轨迹检查依赖
   同一套坐标链口径）。
4. 本单不改、不提交四个目标文件；它们归当前工作区所属线。

## 6. 验证记录（落库后由所属线回填）

- [ ] 四文件同批落库（或分别落库但注明顺序）
- [ ] `python run_all.py` 退出码 0，贴末段输出
- [ ] `node tests/astro_selftest.js` 退出码 0，贴 worst 行
- [ ] `git status` 中四文件干净
