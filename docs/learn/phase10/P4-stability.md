# P4-稳定性：tests/ 大套件顺序相关问题排查

> 范围：根目录 `tests/`（Python pytest 大套件）。第八/九阶段改动的回归复查见
> [`P4-regression.md`](./P4-regression.md)；本文件只记录稳定性问题。
> 红线遵守：未扩大改动面；ctest 94 基线不破；本次仅修 1 个确定性回归用例。

## 1. 现象

`scripts/run_all_tests.sh` 一键跑全量时，其余 7 项全 PASS，唯根目录 `tests/` 一项
判 FAIL：

```
[FAIL] pytest:tests   墙钟超时 150s（疑卡死用例，见日志）: rc=124
汇总: PASS=7  FAIL=1  SKIP=0
```

（基线对照：cpp ctest 94/94、flutter test 298、flutter analyze 0、
experiments/tests 34、mbdsdr_ai/tests 78+7skip、tools/onboarding 24、
tools/hw_selfcheck 14，均 PASS。）

P4 阶段留存记录的现象：

- 全量跑到约 88–90% 时累积出现 **~9 failed / 2 error**，随后某个用例 **100% CPU
  空转**，被外层墙钟兜底杀掉（rc=124）。
- 重点嫌疑：`tests/test_user_layouts.py` 的 `TestPanelMenu::test_toggle_panel_*`；
  P4 记录称"**单独跑 ~11s 即过**"。

本批复跑观察到的两个补充事实：

1. 挂起位置**不确定**：同一命令在不同次分别停在
   `test_channelizer_fix.py::test_no_nan_any_mode`（24%）、
   `fake_rtl` 摘要打印处（39%）、以及 `test_user_layouts.py` 的第 2/5/6 个用例之间，
   并非固定某一个用例坏。
2. `test_user_layouts.py` 本批复跑**不再是"11s 通过"**，而是同样进入长耗时/挂起，
   进一步说明它是"慢+累积"的受害者，而非自身逻辑错误。

## 2. 复现

```bash
# 一键（150s 墙钟兜底，tests/ 会 FAIL）
QT_QPA_PLATFORM=offscreen bash scripts/run_all_tests.sh

# 单独复跑重 UI 文件（每次起一个 MainWindow，累积变慢）
QT_QPA_PLATFORM=offscreen python3 -m pytest -v tests/test_user_layouts.py

# 隔离 MainWindow 构造成本
cd desktop && QT_QPA_PLATFORM=offscreen python3 -c '...'  # 见下"定位尝试"
```

## 3. 定位尝试

### 3.1 单例 MainWindow 构造成本（已量化）

用 cProfile 隔离**一次** `MainWindow()` + `show()`：

```
ONE construct+show: 11.40s
  main_window.py:232 __init__ ............. 11.35s
    _build_central_widget ................  9.56s
      _init_ai_agent_from_config ........  8.07s
        ai_panel.py:374 init_agent ......  8.07s
          agent.py:97 __init__ ..........  8.07s
            amr.py:369 __init__ .......... 4.92s
              _load_builtin_training_data . 4.92s
                extract_features_from_iq ×128 → builtins.sum ×9992 = 4.55s
  setStyleSheet ×5 ......................  1.58s
  sdr_backend.py:2657 _probe_network_default (socket.create_connection ×3) = 1.50s
```

结论：单次构造 ≈ **11s**，主成本是 **AI agent 初始化里 AMR 内置训练数据的特征提取
（~5s，纯 CPU 绑定）**，其次是主题 stylesheet 与网络探测。

### 3.2 各子项是否为"无限空转"

- `mbdsdr_ai/sdr_backend.py:2657 _probe_network_default`：`socket.create_connection((host,30431), timeout=0.5)`，**有 0.5s 上限**，有界（profile 中 3 次共 1.5s 吻合），非死循环。
- `enumerate_all_sdr_devices()`：单独测 **0.5s** 返回 `[]`，不是慢点。
- `sdr_backend.py:739` 环形缓冲读循环：带 `deadline` 与条件变量 `wait`，有界。
- `faulthandler.dump_traceback_later(40, exit=True)` 未打出栈：说明主线程卡在
  **Qt/C++ 层或 CPU 绑定的 numpy 计算中**，不是某条 Python `while True`。

### 3.3 挂起为何"位置漂移"

`tests/` 根目录把**数百个快单元测试**与**大量 offscreen UI 集成测试**混跑，后者每个
都 `new` 一个完整 `MainWindow()`（≈11s、加载 AMR agent）。叠加：

- `qapp` fixture 为 **module 级单例 `QApplication`**；
- `main_window` fixture 为 **function 级**，每个用例新建 MainWindow，teardown 只
  `close()` 不 `deleteLater()` → 前序窗口的 QTimer（utc 1s / gnss 1s / sky 5s）与线程
  不被回收；
- 越往后跑，常驻 MainWindow 实例越多、CPU 绑定的特征提取/样式重算越频繁，套件整体
  被拖慢，直到撞墙钟兜底。这解释了"挂起位置随机漂移"与"单独跑 test_user_layouts 时
  每次过的用例数也不同"。

### 3.4 发现并修复的 1 个确定性回归（增益档）

全量 `-v` 日志里唯一**确定性、可在隔离中复现**的失败：

```
tests/test_benchmark_essence_defaults.py::test_rtl_first_gain_empty_table_no_crash FAILED
  AssertionError: assert 25.4 == 0.0
```

- 产品代码 `mbdsdr_ai/sdr_backend.py:1361 _maybe_apply_first_gain_midpoint()`：
  当增益表为空（未知调谐器/沙箱探测失败）时，**有意**回退到
  `_UNKNOWN_TUNER_FALLBACK_GAIN_DB = 25.4`（docstring："确保首启绝不留 0 dB 聋棒"，
  对齐 gqrx/SDR++ 初始化序列）。
- 该用例仍断言旧行为"空表保持 0.0"，与增益档新增的回退行为**直接冲突**。
- 判定：产品行为正确且文档化，**测试断言过时**。已把断言对齐到回退常量
  `_UNKNOWN_TUNER_FALLBACK_GAIN_DB`（保留"不崩溃"本意）。
- 修复后 `tests/test_benchmark_essence_defaults.py` 全文件 **13 passed**。

## 4. 结论

| 问题 | 定性 | 处理 |
|---|---|---|
| `test_rtl_first_gain_empty_table_no_crash` 断言过时 | 增益档改动引入的**确定性回归** | **已安全修复**（测试对齐文档化行为，13/13 通过） |
| 套件撞墙钟兜底 rc=124、挂起位置漂移 | **测试基建/扩展性问题**：重 MainWindow(≈11s) × 大量 UI 用例 + 单例 QApplication 不回收，非产品 bug | **未在本批改产品/大改套件**（避免扩大改动面）；墙钟兜底已在 run_all_tests.sh 就位 |
| P4 记录的"~9 failed / 2 error"其余项 | 顺序相关、跨用例 Qt 状态污染；本批因挂起不确定、且无原生栈工具（无 py-spy/gdb），**未能逐条枚举定位** | 如实记录为**待后续批次** |

一句话：`tests/` 大套件的 FAIL **不是单一坏用例**，而是"每个 UI 用例都冷启动一个 ≈11s
的 MainWindow（含 AMR 训练数据特征提取）+ 单例 Qt 状态不回收"导致的累积拖慢/偶发失败；
其中唯一可确定性复现的回归（增益档空表断言）已修。

## 5. 后续建议（待后续批次，本批不动）

1. **拆分快慢用例**：给重 UI 集成测试打 `@pytest.mark.slow`（或单独目录），CI/本地
   默认只跑快用例，`slow` 单独触发——可直接消除墙钟超时主因。
2. **给 UI 测试加 headless/无 AI agent 开关**：`MainWindow` 构造里的
   `_init_ai_agent_from_config`（AMR 训练数据 ~5s）在纯 UI offscreen 测试中可跳过，
   预计把单窗构造从 ~11s 压到秒级。
3. **fixture 回收**：UI 测试 teardown 中对 MainWindow 补 `deleteLater()` 并处理待办
   QTimer/线程，减少跨用例 Qt 状态污染。
4. **原生栈定位**：在装了 `py-spy`/`gdb` 的环境复跑，attach 那个 100% CPU 用例取
   native 栈，确认是 QTabWidget relayout 反馈还是某 timer 回调。
5. runner 上若 `tests/` 仍超时，按本文件 §5.1 拆 slow 后再纳入 CI（CI 注释已指向本文件）。
