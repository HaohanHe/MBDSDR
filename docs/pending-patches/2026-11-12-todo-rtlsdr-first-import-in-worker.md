# 待办：`import rtlsdr` 在后台池线程首次执行（不改代码，待认领）

**登记时间**：2026-11-12　**来源**：CASE-2026-11-11-01（`0xC0000005` 取证时顺带发现）
**状态**：只登记，**未改任何代码**；与 `0xC0000005` 无因果关系，不要并案修。

---

## 1. 现象

`mbdsdr_ai/sdr_backend.py` 的 RTL-SDR 枚举路径有两个「每次调用都执行」的动作：

| 位置 | 行为 |
| --- | --- |
| [sdr_backend.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sdr_backend.py#L1716-L1724) `RTLSDRBackend.list_devices()` | 每次调用先跑 `setup_rtlsdr_windows(import_test=False)`（导 DLL 目录），再执行 `import rtlsdr` / `from rtlsdr import RtlSdr`。**语句每次执行，只有首次是真导入**，之后命中 `sys.modules`。 |
| [sdr_backend.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sdr_backend.py#L5796) `enumerate_all_sdr_devices()` | 同上，会被各后端 `enumerate()` 反复调用。 |

调用方是 UI 线程之外的地方：
[module_panel.py](file:///D:/mbdsdr/MBDSDR/desktop/module_panel.py#L263) `ModulePanel.__init__` 直接调
`refresh_devices_async()`（[L503-L516](file:///D:/mbdsdr/MBDSDR/desktop/module_panel.py#L503-L516)），
后者把 `_ModuleEnumTask`（[L219](file:///D:/mbdsdr/MBDSDR/desktop/module_panel.py#L219)）
丢进 `QThreadPool.globalInstance()`。

→ **`rtlsdr` 的首次导入发生在池线程上，而不是启动期主线程。**

## 2. 为什么这算健壮性问题

PySide6 在 `import PySide6` 之后，会给「任何模块的首次导入」挂一层钩子：

```
shibokensupport/signature/loader.py:72  feature_imported
  → shibokensupport/feature.py:148      feature_imported
  → shibokensupport/feature.py:159      _mod_uses_pyside → inspect.getsource(mod)
```

`inspect.getsource` 会走 `linecache → tokenize.open`，**在调用的线程上读盘 + 分词**。
`shibokensupport` 是内存态虚拟模块（`__file__` 为相对路径），所以最终以
`OSError: could not get source code` 收场——不致命，但代价是**后台线程里的一次同步磁盘 I/O**；
如果 `rtlsdr` 的导入链上还有其它首次导入的模块，每个都要付一遍。

这正是「后台线程现 import + 现读源码」的组合：与 UI 首帧竞争磁盘和 GIL。

## 3. 与崩溃的关系（已实测，勿再怀疑）

- 只 `import rtlsdr`、不建窗口：**0/10** 崩（脚本 `scripts/repro/2026-11-11-case-exit-av-minimize.py` 格 BK）。
- 只 `import mbdsdr_ai.sdr_backend`：**0/10** 崩（格 BJ）。
- `ModulePanel` patch 掉后台枚举后崩溃率不变：**13/20 vs 14/20**（格 Y vs X）。
- `0xC0000005` 的必要条件是「`MainWindow` 已构造 **且** 主线程调 `os._exit`」，见案卷 §2.3。

结论：**这条不在崩溃因果链上**，是独立的启动健壮性待办。

## 4. 建议修法（未执行）

1. 在 `sdr_backend` 模块导入期（或 `MainWindow` 构造前的显式启动阶段）完成一次
   `setup_rtlsdr_windows()` + `import rtlsdr`，用模块级 `_RTL_READY` 标志记住结果；
   `list_devices()` 里只读标志、只做枚举。
2. 保持现有诚实语义：库缺失仍返回 `[]` + `logger.warning`（[L1742-L1750](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sdr_backend.py#L1742-L1750)），
   不要把「没装库」洗成「没插设备」。
3. `setup_rtlsdr_windows(import_test=False)` 的 DLL 目录引导若必须每次跑，至少把它移出
   「每次枚举都调」的路径，改为进程级一次。

## 5. 验证口径（接手时直接用）

- 用打桩 `inspect.getsource` 打印 `threading.current_thread().name` 与模块名，
  确认首次导入发生在 `QThreadPool` 的工作线程（不是主线程）——2026-11-11 取证时用同法确认过调用链。
- 修后复测：`scripts/repro/2026-11-11-case-exit-av-minimize.py --cells BJ,BK --runs 10`
  应仍为 0/10（证明没引入新崩溃）；另跑一次 `ModulePanel` 单建（格 X，20 次）观察崩溃率
  不发生显著变化即可（预期不变，因为本案与其无关）。
