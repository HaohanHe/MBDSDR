# 交接单：A07 剩余 4 条（三文件，均在他线 claim 下）

> 生成：2026-11-13 02:32（A06/A07 收口线）
> **本单只出补丁文本，不改源码、不提交源码、不抢 claim。**
> 基线：工作区当前内容（`desktop/main_window.py`、`mbdsdr_ai/sdr_backend.py`
> 两文件正在被别线编辑，行号逐分钟漂移——**认文本，不认行号**）。
> 机检口径：`python scripts/axioms.py --rule A07 --no-ratchet --no-self-test`
> → 当前「违规 4 条：应修 4」。

## 0. 一句话结论（三处不是同一类问题）

| 站点 | 定性 | 是"失败被吞"吗 |
|---|---|---|
| `mbdsdr_ai/sdr_backend.py` 3049 / 3055 | **真缺陷**：同文件 HackRF、USRP 两个后端都做了 fail-fast，只有 RTLSDR 漏了 | 是（返回 `False` 被丢弃） |
| `desktop/scanner_panel.py` 119 | **真缺陷**：调谐被拒 → 返回空数组 → 用户看到「本次扫频没有发现活动段」 | 是（失败伪装成"没信号"） |
| `desktop/main_window.py` 7503 | **不是失败被吞**：失败已经进 `failures` 并由调用方展示，缺的只是"返回值可见"的形状 | 否（记账了，但藏在 lambda 里） |

前两条属于上一批 15 处处收口时同一个形状；第三条是**机检形状问题**，
改它要清楚「改的是可读性，不是行为」。

## 1. 台账：归属线与占用

| 文件 | 持有线（claim owner） | pid | 签claim时刻 | TTL | 到期 | 写作时剩余 |
|---|---|---|---|---|---|---|
| `desktop/main_window.py` | 录音回放核查线 | 33648 | 02:01:19 | 60 min | **03:01:19** | ≈ 29.5 min |
| `desktop/scanner_panel.py` | A02读数失真线/当前会话 | 29868 | 01:48:39 | 90 min | **03:18:39** | ≈ 47 min |
| `mbdsdr_ai/sdr_backend.py` | A03静默降级线/6ae85f56 | 32060 | **00:57:30** | 180 min | **03:57:30** | ≈ 86 min |

（到期时刻取 `D:\mbdsdr\.locks\claims.json` 的 `acquired_at + ttl`，实际以台账为准。
本单不续租、不 takeover、不 release。）

### 1.1 `sdr_backend.py` 这条不是排期问题

该文件上的 claim 签于 00:57:30，**明文凭据从未落盘**：`release` 需要 owner
原串、续租需要同一凭据、takeover 又要求「pid 已死 + 无在途」——三条路全堵，
而这个文件正在被大面积改（在途 diff 255 行），pid 也活着。
**这是凭据机制的历史缺口，不是有人排期占着不干；等台账线把凭据补落盘后才能收口。**
在本单里只交补丁文本，不碰那条 claim。

## 2. 三处补丁（可直接照抄）

### 2.1 `mbdsdr_ai/sdr_backend.py`：RTLSDR 初始化下发失败要断链

**位置**：`RTLSDRBackend._open_hardware`，SDR++ 对齐序列第 1 步与第 2 步。

```python
# 第 3049 行（当前文本：self.set_sample_rate(self.status.sample_rate_hz)）
            if not self.set_sample_rate(self.status.sample_rate_hz):
                return self._connect_init_failed("采样率")

# 第 3055 行（当前文本：self.set_frequency(target_freq)）
            if not self.set_frequency(target_freq):
                return self._connect_init_failed("中心频率")
```

还要在 `RTLSDRBackend` 上补一个 helper——**照抄同文件 HackRF 版**
（`sdr_backend.py:4017-4029`），只把硬件句柄换成 `self._sdr`：

```python
    def _connect_init_failed(self, what: str) -> bool:
        detail = self.status.error or "硬件拒绝下发"
        try:
            self.disconnect()
        except Exception:
            pass
        self.status.connected = False
        self.status.link_state = "failed"
        self.status.error = f"RTL-SDR 连接初始化{what}下发失败: {detail}"
        logger.warning(self.status.error)
        return False
```

**为什么**：这段注释自己写的就是「上一版 connect() 只 readback 不下发，导致棒停在
出厂默认频率/采样率（"插上收不到台"的根因）」；现在下发了，但**下发被拒不处理**，
等于把同一个根因换了个位置。HackRF（4005-4009）和 USRP（5013-5016）已经是
`if not ...: return self._connect_init_failed(...)`，RTLSDR 只是漏改。
`set_sample_rate` / `set_frequency` 返回 `bool`（`sdr_backend.py:1448`、`1335`），
失败时已把原因写进 `status.error`，helper 直接取用。

**同文件还有第三处（修完上面两条它才会露出来）**：`sdr_backend.py:6871`
`be.set_frequency(vfo["frequency_hz"])` 后面紧跟 `except Exception: pass`——
切换 VFO 时调谐失败仍然 `return True`，这是**真吞**，建议同一批收：
失败时把 `status.error` 带上并返回 `False`，别让上层以为切过去了。

### 2.2 `desktop/scanner_panel.py:119`：调谐被拒不能当成"这个频点没信号"

```python
# 替换当前 4 行（try / be.set_frequency(...) / except Exception / return 空数组）
            if be.set_frequency(float(center_hz)) is False:
                raise RuntimeError(
                    f"扫频调谐 {float(center_hz) / 1e6:.6f} MHz 未生效："
                    f"{getattr(getattr(be, 'status', None), 'error', '') or '设备拒绝该频点'}")
```

**为什么**：`psd_provider` 返回空数组等于告诉 `SweepScanner`「这一点没扫到东西」，
于是整段结果里少一块，界面落到「本次扫频没有发现超过门限的活动段」（
`empty_hint_text`，`scanner_panel.py:167-177`）——**用户会去调门限，而真实原因是频点被拒**。
抛异常则沿 `sweep()` 冒到 `scanner_panel.py:150-152` 的 `except Exception` →
`self.failed.emit(f"扫频失败: ...")`，用户看到红字与原因。
`is False` 而非 `not`：老后端无返回值时 `None` 视为成功，与
`main_window._run` 的口径一致（`main_window.py:7480`）。
这与 CLI 侧同一处修复（`controller.py:870-877`，同批已改并入库）是同一形状。

### 2.3 `desktop/main_window.py:7503`：把返回值从 lambda 里拿出来

```python
# 替换当前 1 行：_run("带宽", lambda: backend.set_bandwidth(bandwidth))
        def _set_bandwidth() -> bool:
            return backend.set_bandwidth(bandwidth)
        _run("带宽", _set_bandwidth)
```

**行为零变化**：`_run` 已经把 `False` / 异常都记进 `failures`（`main_window.py:7472-7484`），
`_apply_connect_params` 的返回值就是这份清单，调用方逐项展示。改的只是
「调用挂在 lambda 里，A07 的 `_result_used` 只看得到父节点是 `Lambda`」。

**不要**改成 `if not _run("带宽", ...): pass`——**探针证明那样仍是红的**
（lambda 遮挡没解决，见 §3）。上下的采样率（7493）和初始频率（7504）之所以
没被报，不是形状更好，而是 A07 **每文件每方法只报一条**，带宽这条被排在了前面。

**注意**：修掉 7503 后，同文件 7990 / 8374（`self._vfo.set_bandwidth(...)`）会接着被报。
那两处是 VFO 内部转发、不是硬件写入，且返回值无意义——真要清零，要么把 VFO 的
`set_bandwidth` 从 `ACTION_METHODS` 的语义里区分开（机制线裁决），要么逐处把结果
显式消费掉。**别为了清红把 `self._vfo` 改名为带 `widget|panel` 的字符**。

## 3. 探针证据（仓外脚本，不写仓库）

脚本：`%TEMP%\mbdsdr_work\probe_a07_shape2.py`（直接 import `tools.axioms.checks.scan_a07`，
在内存里打补丁后重跑，不落盘）。

| 场景 | A07 findings |
|---|---|
| 现状 | 4：`main_window:7503` / `scanner_panel:119` / `sdr_backend:3049` / `sdr_backend:3055` |
| 只补 §2.2 | 3（scanner_panel 那条消失） |
| 只补 §2.1 | 3（3049/3055 消失，**6871 冒出来**） |
| 补 §2.1 + §2.2 | 2（只剩 `main_window:7503` 与 `sdr_backend:6871`） |
| 补 §2.3 的"直觉改法"（`if not _run(...): pass`） | 4，**一条没清**（证明它不是"少个 if"） |
| 补 §2.3 的"提 lambda" | `main_window:7503` 消失，`main_window:8376` 冒出来（同形有多处） |

## 4. 边界声明

- 本单**没有**写上述三个源码文件、没有提交它们、没有 claim/release/takeover。
- 上面行号是 2026-11-13 02:3x 工作区快照；`main_window.py` 与 `sdr_backend.py`
  正在被别线编辑，**落补丁时按文本定位**。
- `docs/COMMIT-INCIDENT-CARD.md` 上另有一条他线在途改动（第 ⑬ 行「三条件全绿」→
  「三条件都满足」），本单的锚点修复提交用 `--exclude-hunk` 把它挡在提交之外，
  工作区原样保留。
