# 桌面扫频诚实性补丁：调谐被拒不得产出「该频段无电台」伪结论

> 目标文件：`desktop/main_window.py`（操作逻辑线，本线**只交付补丁，不直接改**）
> 基线：HEAD `fc1f8076` 的 blob（该文件当前被他人占用、工作树有未提交改动，
> 合并时请 rebase 到其最新提交；本补丁只改两处，冲突面很小）
> 生成：2026-11-06，解码流水线 P5
> 配套测试：`tests/test_desktop_sweep_truthfulness.py`（已放根 `tests/`；
> 补丁未并线前该用例为红，并线后应转绿）

## 1. 问题

1. `_SweepWorker.run()` 内的 `acquire`（HEAD 约 562–586 行）只捕
   `backend.set_frequency()` 抛异常，**忽略显式返回 `False`**。设备拒绝调谐时
   仍继续 `read_samples`，把旧频率上的样本喂给 `sweep_scan`；最终
   `_on_sweep_done` 输出「该频段未检测到超过门限的活动信号（可能无电台/增益
   过低）」——这是用旧频样本编出来的伪结论。
2. `_on_sweep_done`（HEAD 约 8370–8386 行）切最强台时同样不消费返回值，
   无条件把 `spectrum.generator.center_freq_hz` 搬到目标频率。图上 marker
   基准已移动、实际仍在旧频，属状态撒谎。

## 2. 修法

- `acquire`：`set_frequency` 异常或返回 `False` 都记入 `refused`（取
  `backend.status.error` 作人话原因），**不调用 `read_samples`**；
  `run()` 在 `sweep_scan` 返回后若 `refused` 非空，发 `failed`
  （「扫频中止：…；未在旧频率继续采样，结果不可信」），不发 `finished_ok`。
  取消优先于失败（`cancelled` 先判）。
- `_on_sweep_done`：只有调谐成功才写 `spectrum.generator.center_freq_hz`；
  失败时状态栏明确「扫频完成，但未能调谐到最强台 …；频谱仍停留在原频率」，
  marker 基准保持不动。

## 3. 行为测试与反证（实测）

- 用例：`tests/test_desktop_sweep_truthfulness.py::test_sweep_never_reports_success_or_moves_marker_when_tuning_refused`
- **红（旧行为 = 本仓当前 HEAD）**：`1 failed in 2.10s`，
  断言落点 `assert failed and not ok` → `assert ([])`。
- **绿（临时 worktree 应用本补丁）**：`1 passed in 1.98s`。
- **反证**：应用后 `git checkout -- desktop/main_window.py` 撤掉补丁 → 同用例
  立即回到红；等价于「改回旧行为→红」。
- 断言内容：`failed` 非空且 `finished_ok` 为空；`backend.reads == 0`
  （拒绝后不得继续在旧频采样）；`spectrum.generator.center_freq_hz` 保持
  原值；状态栏含「未能调谐」。

## 4. 复跑命令（PowerShell，路径按本机替换）

```powershell
$env:MBDSDR_NULL_AUDIO='1'; $env:MBDSDR_NO_HW='1'; $env:QT_QPA_PLATFORM='offscreen'
$env:HOME='<稳定目录>'; $env:USERPROFILE='<稳定目录>'
$env:APPDATA='<稳定目录>\AppData\Roaming'; $env:LOCALAPPDATA='<稳定目录>\AppData\Local'
python -m pytest tests/test_desktop_sweep_truthfulness.py -q
```

预期：补丁未应用 `1 failed`（预期红）；应用后 `1 passed`。

## 5. 统一 diff（可直接 `git apply`）

````diff
diff --git a/desktop/main_window.py b/desktop/main_window.py
index 72b7b73..8126b51 100644
--- a/desktop/main_window.py
+++ b/desktop/main_window.py
@@ -569,12 +569,24 @@ class _SweepWorker(QThread):
 
         backend = self._backend
 
+        # 调谐失败（异常或显式 False）必须中止整次扫频：继续采样只会得到
+        # "旧频率上的样本"，最后产出"该频段无电台"的伪结论。
+        refused = {}
+
         def acquire(center_hz, sample_rate_hz, n):
             if self._cancel:
                 return None
             try:
-                backend.set_frequency(float(center_hz))
-            except Exception:
+                applied = backend.set_frequency(float(center_hz))
+            except Exception as e:
+                refused.setdefault(
+                    "reason", f"调谐到 {center_hz / 1e6:.3f} MHz 失败：{e}")
+                return None
+            if applied is False:
+                status = getattr(backend, "status", None)
+                reason = getattr(status, "error", "") or (
+                    f"调谐到 {center_hz / 1e6:.3f} MHz 未落到硬件（设备拒绝该频率）")
+                refused.setdefault("reason", reason)
                 return None
             # 调谐后短暂驻留，让 AGC/滤波器稳定（SDR++ scanner 换频后也会等）
             _time.sleep(0.04)
@@ -596,6 +608,10 @@ class _SweepWorker(QThread):
             return
         if self._cancel:
             self.cancelled.emit()
+        elif refused:
+            # 不能用剩余频点的"0 台"结果冒充扫频成功。
+            self.failed.emit(
+                f"扫频中止：{refused['reason']}；未在旧频率继续采样，结果不可信")
         else:
             self.finished_ok.emit(result)
 
@@ -8378,11 +8394,22 @@ class MainWindow(QMainWindow):
             pass
         # 调谐到最强活动台，让第一个 marker 立即可见
         if activities and backend is not None:
+            target_hz = float(activities[0].center_hz)
+            tune_error = ""
             try:
-                backend.set_frequency(float(activities[0].center_hz))
-                self.spectrum.generator.center_freq_hz = float(activities[0].center_hz)
-            except Exception:
-                pass
+                if backend.set_frequency(target_hz) is False:
+                    status = getattr(backend, "status", None)
+                    tune_error = getattr(status, "error", "") or "设备拒绝该频率"
+            except Exception as e:
+                tune_error = str(e)
+            if tune_error:
+                # 调谐没生效就不能把 marker 基准搬过去，否则图上标着新频率、
+                # 实际仍收旧频，是另一种状态撒谎。
+                self.statusBar().showMessage(
+                    f"扫频完成，但未能调谐到最强台 {target_hz / 1e6:.3f} MHz："
+                    f"{tune_error}；频谱仍停留在原频率", MSG_IMPORTANT)
+            else:
+                self.spectrum.generator.center_freq_hz = target_hz
         self._sweep_cleanup()
 
         # 状态栏摘要
````

## 6. 验证记录

- 本补丁在 `core.autocrlf=true` 的临时副本上 `git apply --check` `rc=0`。
- 在 `git worktree add --detach HEAD` 的干净副本里应用后运行配套用例：`1 passed`；
  撤掉补丁后 `1 failed`。
- 仅追加/替换上述两处逻辑，未改任何 UI 文案常量或其它行为。
