# D1 收口：21 块结论、原语下沉、跨模块私有名判据（设计稿）

> 日期：2026-11-13；形态：**结论 + 设计稿，规则未落地**（`tools/axioms/*` 被
> 公理棘轮线持有，剩 ~82 min）。落地时以当时工作区为准。

## 一、21 块：不再整体拆（结论）

当前全图（模块级 + 函数体内 import）SCC：**20 模块**与 **5 模块**
（调用点迁移前为 21；`openapi_integration` 的离线门改指 `net_policy` 后
不再依赖 `orbit`，已退出该块）。

- 20 块 = SDR 后端（`sdr_backend`/`spyserver_client`/`rtltcp_client`/
  `bladerf_params`/`limesuite_params`/`device_identity`/`error_handler`/
  `sdr_tools`）+ 解码（`decoders`/`decode_router`）+ 轨道/时间
  （`orbit`/`new_spacetime`/`new_spacetime_tle`/`tle_integrity`/
  `sat_passes`/`sat_tracker`/`sat_illumination`/`doppler_link`）+
  `time_basis`/`builtin_tle_meta`。
- 5 块 = `tool_registry` / `fengyun_sat` / `orbit_determination` /
  `orbit_dynamics` / `orbit_perturbation`。

最小割（顶点割，站点 = 跨刀 import 行数）：

| 刀数 | 割点 | 结果 | 站点 |
|---|---|---|---|
| 1 | `sdr_backend` | 剩 1 个 9 模块环 | 22 |
| 1 | `decoders` | `[8, 7]` | 14 |
| 1 | `new_spacetime` | `[11, 6]` | 27 |
| 1 | `orbit` | `[16, 2]` | 15 |
| 2 | `orbit + sdr_backend` | `[3,2,2]` | 37 |
| 3 | `decode_router + orbit + sdr_backend` | `[2,2,2]` | 43 |

2026-11-13 04:0x 复测（调用点迁移后，块内 20 模块）：割 1 `orbit` 站点
15→**11**、`new_spacetime` 27→**24**；割 2 `orbit+sdr_backend` 37→**33**；
割 3 `decode_router+orbit+sdr_backend` 43→**39**；其余不变。

**没有第二个 `tool_registry` 级门面。** 门面判据是「入边极多 + 几乎只取几个
叶子符号 + 可原位再导出」；块内入边最多的 `sdr_backend` 只有 8 个来源 /
14 站点，且一半取真业务类（`FileIQBackend`/`SDRBackend`/
`SDRBackendManager`）；`new_spacetime` 的 19 个站点全是同一个
`get_time_engine` 访问器，拆不出叶子。**结论：21 块不值得整体再拆。**

剩下的环都是真业务依赖，**要再切只能改 API**，不是搬 import：

| 环 | 性质 | 只能怎么改 |
|---|---|---|
| `orbit ↔ new_spacetime` | 时间引擎与轨道计算互相需要 | 把时间引擎做成注入参数，或抽 `ITimeSource` 接口 |
| `decoders ↔ decode_router` | 路由器按名字调用解码器 | 解码器注册表由调用方注入，而不是反向 import |
| `sat_passes ↔ sat_illumination` | 过境与光照共享几何 | 抽出公共几何函数到叶子（不是类型，是纯函数） |
| `new_spacetime_tle ↔ tle_integrity` | TLE 历元解析与完整性校验 | `BUILTIN_SATS` 下沉叶子常量模块，或校验器接收 `sat_map` |
| `sdr_backend ↔ spyserver_client` | 客户端继承后端基类 | 基类下沉 `sdr_backend_base`，两侧都指它 |

## 二、原语下沉（已落地 3 处，1 处阻塞）

判据：公共原语长在业务模块里，导致「只要一个小函数就依赖整块模块」；下沉到
**零 mbdsdr_ai 依赖的叶子**，原模块原处再导出（同名别名，identity 不变），
既有调用方一行不改。

| 原语 | 叶子 | 提交 | 验证 |
|---|---|---|---|
| `format_local`（+`_as_utc`/`_resolve_tz`） | `mbdsdr_ai/time_format.py` | `84f5a98d` | `mbdsdr_ai/tests/test_time_basis` 14 例 |
| `read_wav_audio`（原 `_read_wav_audio`） | `mbdsdr_ai/audio_io.py` | `3dd83c12` | identity + 合成 wav 读取 + `decode_router` import |
| `network_disabled`（原 `_network_disabled`） | `mbdsdr_ai/net_policy.py` | `90062b6d` | identity + 禁网开关两态 + `tests/test_orbit_dynamics` 11 例 |

**阻塞**：`sdr_backend` 的 6 个叶子符号（`DEFAULT_SAMPLE_RATE_HZ`、
`format_sample_rate`、`acquire/release_hardware_owner`、`device_identity_key`、
`SDRStatus`，11 站点）——该文件被 `A03第一批线` 持有（剩 88 min）且有在途
改动，按规矩跳过。

**注意（代价）**：按「不动调用方」执行，调用点仍指旧模块，所以 21 块与私有名
存量**本轮都不减**。要减，需下一轮把 `mbdsdr_ai` 内部调用点改指叶子（涉及
`agent.py`、`decode_router.py` 等被占文件），可一次性消掉约 25 个站点。

**调用点迁移（2026-11-13 04:0x，已兑现一部分）**：上段「本轮都不减」只对
下沉那一笔成立；随后单独做了调用点迁移，一文件一笔：
`261221ce` model_manager ×3、`2bc1bdfe` new_spacetime ×2（离线门 + 本地
显示）、`97ca23c7` new_spacetime_tle、`f11ab517` openapi_integration、
`ade9380c` sat_tracker、`df7bd534` time_sync、`caf318c8` sdr_tools
（本地显示）。

私有名存量（口径：跨模块 import 语句条数）**137 → 129 → 123**（两次实测
复扫）。第二批：`b8d6b570` solar_system、`d4c8dc2d` agent、
`198cd49b` decode_router；至此 `orbit._network_disabled`、`decoders.`
`_read_wav_audio` 的跨模块引用均归零。

别名在 `baba826a` 删除，**与「把 `tests/test_tle_failure_visibility.py:143`
的 patch 目标改成 `net_policy.network_disabled`」同一笔**，不留「别名已删、
测试仍 patch 旧名」的中间红态。`time_basis.format_local` 的再导出**仍保留**：
唯一 import 方 `desktop/sat_track_panel.py` 被异步不落地线持有；它是公共名、
不计存量，等释放后与 sat_track_panel 迁移同一笔删除。

## 三、判据：跨模块导入私有名必须报（D1-private-import）

### 3.1 规则正文

`mbdsdr_ai` 内部，**import 语句**里出现下划线开头（非 dunder）的符号名 →
报。理由：用别的模块的私有符号只有两种可能——它其实是公共原语、放错了地方，
或者你越界了。这条判据不依赖命名风格或注释，只依赖 Python 自己的可见性约定。

**不算**：私有类方法/属性访问（`obj._x`、`Cls._y()`），那是封装内正常使用；
只算 `ImportFrom`/`Import` 语句里的名字。

### 3.2 机检判据

1. 扫 `mbdsdr_ai/`（`desktop/` 只看 facts，不出 finding）。
2. 对每个 `ast.ImportFrom`，解析目标模块（相对 `level` 按模块自身包解析；
   `__init__.py` 的相对基准是自身；`from .. import x` 解析成父包下的 `x`，
   **不是父包本身**——这里踩过坑，见 §五）。
3. 取 `alias.name`（**不是 `alias.asname`**）：`from .leaf import read_wav_audio
   as _read_wav_audio` 导入的是公开名，不算。
4. 命中条件：`name.startswith("_") and not name.startswith("__")`。
5. 排除：目标模块 == 当前模块；`__init__.py` 再导出**同包**子模块的私有名
   （包内私有是正常封装）。
6. 输出：`D1-private-import`，detail 写 `src -> target :: _name`。
7. **第二形态（import 语句扫不到）**：测试夹具直接改别的模块的私有名。
   `setattr(M, "_x", ...)` / `monkeypatch.setattr(M, "_x", ...)` /
   `patch.object(M, "_x", ...)` / `mock.patch.object(M, "_x", ...)`，判据：
   - 首参是 `ast.Name`，且该名字在本文件中由 `import`/`from ... import`
     绑定为**模块**（用「`pkg/name.py` 或 `pkg/name/__init__.py` 是否存在」
     解析，不靠命名猜测）；
   - 第二参是字符串字面量，`startswith("_") and not startswith("__")`。
   - **不算**：首参是 `self` / 局部变量 / 类对象（`patch.object(SomeClass,
     "_method")` 属类封装内部，按 §3.1 同口径放行）。
   - 输出 rule id 复用 `D1-private-import`，detail 标 `setattr:M._x`。
   - 为什么必须加：`.monkeypatch.setattr(orbit, "_network_disabled", ...)` 在
     别名删除后会 AttributeError，而只用 import 判据的守卫看不见它——守卫
     看不见的地方正是最容易出事的地方。

### 3.3 棘轮与反证

- 先全量跑，`--accept-current` 把存量收进 `docs/axioms-baseline.json`，之后只减不增。
- 自检 Case（正例必须命中）：
  ```python
  # pos：mbdsdr_ai/a.py
  from .b import _secret
  # neg：同文件改成
  from .b import secret          # 公开名
  ```
- **结构反证**：在任一模块里临时加一行 `from .orbit import _bounded_urlopen`
  → 必须红；删掉 → 必须绿。只删注释不能变绿，否则规则退化。
- **第二形态反证（样本已消费）**：`tests/test_tle_failure_visibility.py:143`
  原为 `monkeypatch.setattr(orbit, "_network_disabled", lambda: False)`，判据
  必须报 1 条；该行已在 `baba826a` 与别名删除同笔改成
  `monkeypatch.setattr(net_policy, "network_disabled", lambda: False)`，按设计
  变绿。selftest 里正例改用**内联代码片段**（等价形态，不依赖工作区现状），
  负例：`monkeypatch.setattr(obj, "_x", ...)`（`obj` 是局部变量）不报。

### 3.4 现存量（2026-11-13 03:5x，口径：`mbdsdr_ai/` 全部 `.py`）

- **137 处 / 70 个文件 / 97 个（目标模块, 符号）对**（口径：跨模块 import
  语句条数；上一版写的 138 无法复现，按实测 137 为准）。
- 2026-11-13 04:0x 迁完第一二批调用点后复扫：**123 处**（137→129 减 8，
  129→123 减 6；两批都全部来自 `_network_disabled` / `_read_wav_audio`）。
- `orbit._network_disabled` 与 `decoders._read_wav_audio` 已归零，别名已删；
  剩余头部：`mcp_server._TOOL_DOMAIN` 10、`orbit._state_from_satrec` 4、
  `orbit._bounded_urlopen` 3、`orbit_dynamics._rk4_step` 3、
  `sat_passes._parse_tle` 3、`sat_passes._range_and_rate` 3、
  `sdr_tools._sky_view_visible` 3、`mcp_server._build_tool_table` 3。
- 其中 `orbit` 一个模块贡献 8 处（两批迁移后；原 19）——它仍是块里
  「公共原语最密集」的地方，与 §一 的割点分析互相印证。
- 落地时**必须重新扫一遍**再收基线；本清单只用于判断规模与优先级。

### 3.5 第二形态现存量（2026-11-13 04:1x）

- **147 处 / 71 个文件**（口径：AST 调用点，逐条计；扫描范围全仓 `.py`，
  排除 `.git/.venv/__pycache__/build/dist/node_modules`）。原 148 处，
  `test_tle_failure_visibility.py:143` 改公共名后按设计减 1。
- 集中区：`orbit._CACHE_DIR` / `orbit._TLE_CACHE` / `orbit._TLE_FETCHER`
  （测试隔离缓存）、各模块 `_backup_corrupt*`（损坏恢复用例）、
  `_SD_AVAILABLE` / `_HAS_QT`（可选依赖桩）、`_bounded_requests_get`。
- 这条判据会把这些**全部**报出来——它们多数是「测试为了隔离去拨模块级
  全局」，不是越界借 API。棘轮按 148 收基线，之后只减不增；真正的病灶
  （如 `_network_disabled`）由「原语下沉 + 改调用点」自然消失，不靠改
  import 名字绕过。
- 已知覆盖缺口（诚实标注）：`monkeypatch.setattr("pkg.mod._x", ...)` 字符串
  形态、`del module._x`、`getattr(module, "_x")` 均未纳入；落地时若发现新的
  绕过手法，先补判据再加例外。

## 四、落地步骤（照抄）

1. `tools/axioms/survey.py`：加 `collect_d1_private()`，rule id
   `D1-private-import`，按 §3.2（**含 §3.2.7 的 setattr 第二形态**）。
2. `tools/axioms/selftest.py`：加 §3.3 的正例/负例/结构反证三个 Case，
   外加第二形态反证 Case（`test_tle_failure_visibility.py:143` 必须命中）。
3. `docs/axioms.md`：D1 行追加检查名，贴 §3.1 规则正文。
4. `python scripts/axioms.py --rule D1` → 确认当前存量全报 → `--accept-current`
   （落地时重扫，基线以当时实测为准，不要照抄本文件的 129）。
5. 之后每轮清账只减不增；清账顺序按「原语下沉」优先（下沉后改调用点，
   私有名自然消失），不要直接改 import 名字绕过。

## 五、取证纠错记录（防再犯）

1. **扫描器曾凭空造环**：一版解析器把 `from .. import x` 记成「导入父包」，
   导致凭空出现 3 模块环 `satellite/{__init__,decoders,tracker}`。修正后仍是
   `[21, 5]`。**检测器看不见**与**检测器看见不存在的东西**，两个方向都要防：
   任何新判据先跑一次「已知无环的子图」确认不误报。
2. **伪环口径**：92 条延迟边是「在环上」的边，按定义全是真环，伪环为 0；
   伪环只能出现在其余 840 条里。写了「避免循环」理由的 9 条中，6 条不成环
   （已清 4：`goes_lrit`×2、`rtklib_adapter`、`rtl433_decoder`）。
3. **`from .. import x`** 与 **import 别名** 这两条解析规则，已写进本文件；
   实现新检查时不要重写第三遍。

## 六、2026-11-13 D1 环收口实测（D1环收口线）

> **口径现状（以 HEAD `238da1c7` 为准）**：`D1-cycle` 只把**模块顶层执行**的
> import 算成环边；函数体内的延迟 import 不进环，单列 facts
> 「延迟 import 强连通组」。**但顶层/延迟互指仍报**：A→B 是顶层 import、B→A
> 只出现在函数体内时照样报 `D1-cycle`（severity 复核）——延迟 import 不能成为
> 真环的藏身处。实测：
> - 模块级 import 环 **0**；顶层/延迟互指对 **3**（均在 SDR 子系统，见 6.4，
>   非本批改动引入）；延迟组 **1 块 / 15 成员 / 37 条内部边**；
> - 本节 6.1/6.2 的 SCC 与割数是**含延迟边的历史测量**，用于说明「再往下是
>   业务耦合」，不代表模块级违规；
> - 6.3 的 `D1-cycle` 豁免登记不再需要，但 6.4 那 3 条互指对是现行违规。

### 6.1 5 块已清零（3 刀，均行为不变）

| 刀 | 落点 | 做法 | 站点 |
|---|---|---|---|
| 1 | `fengyun_sat → tool_registry` | `ToolResult` 改引零依赖叶子 `mbdsdr_ai/tool_results.py`；`tool_registry` 原位再导出，identity 不变 | 2 |
| 2 | `orbit_determination → tool_registry` | `ToolResult`/`clean_exc` 同上 | 1 |
| 3 | `orbit_dynamics → orbit_determination` | 删掉函数体内 `pseudorange_rate_and_jacobian` 反向 import；`orbit_dynamics` 本地的同名观测模型加可选 `receiver_velocity`（`None` = 原 2 参路径），两参/三参共用一份公式 | 1 |

实测：`pytest tests/test_orbit_dynamics.py tests/test_orbit_determination.py`
15 passed；`test_fy4_iq_chain/test_fengyun/test_fengyun_dvbs2/test_tool_result_contract/test_tool_result_honesty`
82 passed。观测模型与 `orbit_determination` 三参版本 200 组随机样本
`max abs diff = 6.5e-19`（双精度舍入，源于把两项等价公式统一实现）。
旧口径下 D1-cycle 由 2 条 → 1 条，5 块整体消失（现行模块级口径下本来就是 0）。

> 落库前复验（2026-11-13 19:04，收尸线）：上述两条命令按原文复跑，
> `15 passed`、`82 passed` 与记录一致（记录里 81 passed / 1 failed 的那条
> `test_sdr_list_devices_never_fabricates` 转绿靠的是**工作区在途、不属本批**
> 的 `mbdsdr_ai/sdr_backend.py` 里 `MBDSDR_NO_HW` 短路；该文件归另一条线，
> 需在其落库后复跑这条用例确认。等价性另取 200 组随机
> 样本（`numpy.random.default_rng(7)`）复得 `max abs diff = 8.7e-19`，
> 与记录值同一量级；两参路径与三参 `None` 路径逐组差为 0。

### 6.2 16 块：本批安全抽叶子 3 刀，残量 15 成员 / 37 边 / 拆环最少删 6 条边

安全刀（均「原语下沉到零内部依赖叶子 + 原模块原位再导出」，identity 不变）：

| 刀 | 落点 | 新叶子 | 站点 |
|---|---|---|---|
| 1 | `time_basis → new_spacetime_tle` | `mbdsdr_ai/tle_epoch.py`（`parse_tle_epoch` + `_is_leap_year`，仅 datetime） | 1 |
| 2 | `tle_integrity → new_spacetime_tle` | 同上 | 1 |
| 3 | `sat_illumination → sat_passes` | `mbdsdr_ai/skyfield_compat.py`（`_require_skyfield`/`make_timescale`/skyfield 可选依赖块） | 1 |

`time_basis` 因此退出该块。**切前实测：16 成员 / 45 条内部边**
（45 条为去重后的 (src,dst) 对；k≤6 全枚举无解，即拆环最少要删 7 条边）。
**切后实测：15 成员 / 37 条内部边，拆环最少删 6 条边**
（子集 DP 全枚举：|E| − 可保留无环边 = 37 − 31 = 6）。
注意 `survey` facts 里打印的「最小割」是 **min over s≠t maxflow**（单点对最小
割），SCC 内只要有一个成员出度 1 就是 1，不能当「拆掉整个环要删几条边」用。
成员：`builtin_tle_meta`、`decode_router`、`decoders`、`doppler_link`、
`new_spacetime`、`new_spacetime_tle`、`orbit`、`rtltcp_client`、
`sat_illumination`、`sat_passes`、`sat_tracker`、`sdr_backend`、`sdr_tools`、
`spyserver_client`、`tle_integrity`。

**其余 37 条边逐条初筛：没有第二组「零行为变化抽叶子」的刀。**
剩余边全是真业务耦合：SDR 后端基类（`rtltcp_client`/`spyserver_client`/
`sdr_tools → sdr_backend`）、解码器注册表（`decoders ↔ decode_router`）、
时间引擎访问器（`new_spacetime` 19 站点同一入口）、TLE 管理器
（`builtin_tle_meta → decoders/sat_tracker/new_spacetime_tle`）、
观测几何（`orbit → sat_illumination` 的 `evaluate_illumination` 依赖
skyfield/时间引擎，不是纯原语）、`new_spacetime_tle.__getattr__` 兼容转发。
**结论：再往下要改 API（注册表/基类注入），不属于本批。**

- owner：公理棘轮线（D1 收口）
- 到期：2026-11-27
- 转绿条件：延迟组只是 facts，当前不设转绿；若口径改回「延迟边也算环」，
  15 成员 / 37 边 / 拆环最少 6 条边按 API 改造清零。

### 6.3 登记结论：不需要已知红/豁免

口径改为「只认模块顶层 import」后，15 模块残量只是
**facts「延迟 import 强连通组」**，不是违规，因此：

1. ~~`docs/axioms-waivers.json` 追加 D1-cycle 豁免~~ —— **撤销，不再需要**。
   若日后口径改回「延迟边也算环」，再按 6.2 的 15 成员 / 37 边 / 拆环最少 6 条边
   补登记（owner：公理棘轮线，到期 2026-11-27）。
2. `path` 存在性不变式（`unknown-path` / `line-out-of-range`）已由
   `42df5fe3` 落地（`tools/axioms/survey.py` + `scripts/axioms.py` +
   `selftest.py` + `docs/axioms.md`），本批无需重复实现。
3. `docs/axioms.md` D1 小节的现行口径应为「D1-cycle 只认模块顶层 import；
   函数体内延迟 import 单列 facts，不算环；但顶层/延迟互指仍报」——与旧口径
   「函数体内 import 也算边」相反，落笔前须以当时 `survey.py` 为准。

### 6.4 现行 3 条顶层/延迟互指（`D1-cycle`，severity 复核）

实测（HEAD `238da1c7` 判据，`python scripts/axioms.py --rule D1`）：

| 对 | 顶层方向 | 延迟方向 |
|---|---|---|
| `rtltcp_client ↔ sdr_backend` | `rtltcp_client → sdr_backend` | `sdr_backend → rtltcp_client`（函数体内） |
| `sdr_tools ↔ sdr_backend` | `sdr_tools → sdr_backend` | `sdr_backend → sdr_tools`（函数体内） |
| `spyserver_client ↔ sdr_backend` | `spyserver_client → sdr_backend` | `sdr_backend → spyserver_client`（函数体内） |

这三条**不是本批引入**（本批未改 SDR 子系统任何文件），是判据新增「互指」
检测后暴露的存量。修法同 6.2：把其中一个方向下沉到共同下层，或改回调/
依赖注入——属于「再往下要改 API」，不在本批范围。
