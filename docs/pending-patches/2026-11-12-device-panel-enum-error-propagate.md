# 待应用 patch：device_panel 把「枚举失败」与「没插设备」分开

- 目标文件：`desktop/device_panel.py`
- 基线：`598aea18`（本文件另有未提交改动，行号请以 rebase 后为准）
- 提出者：W92（审计线），**不直接改该文件**；请设备线 owner 应用
- 参考实现：`desktop/module_panel.py` 的 `_apply_devices(devices, enum_err)`
  （该线已修过同一 bug，注释记为「旧实现先 setText(异常) 再被『未发现 SDR 设备』盖掉」）

## 问题

同一台机器、同一次枚举失败，两个面板给用户相反结论：

| 面板 | 失败时显示 |
| --- | --- |
| `module_panel` | 「设备枚举失败 · 这不等于没插设备」+ 原始异常 |
| `device_panel` | 「未检测到 SDR 设备」 |

`device_panel.refresh_devices` 把异常吞成 `[]`（`except Exception: devices = []`），
`_on_devices_refreshed` 把后台任务带回的 `err` 也直接丢掉，
`_apply_devices([])` 只能看到「空列表」，无法区分「没有设备」和「枚举没成功」。
用户据此去拔插 USB，而真实原因可能是依赖缺失、USB open 卡死或驱动报错。

## Patch（3 处）

### 1. 同步入口：保留异常原因

```diff
     def refresh_devices(self) -> None:
         """同步枚举 SDR 设备（同步调用入口，测试与显式请求用）。"""
         if not _ENUM_OK or enumerate_all_sdr_devices is None:
-            self._apply_devices([])
+            self._apply_devices([], enum_err="设备枚举不可用（枚举依赖缺失）")
             return
         try:
             devices = list(enumerate_all_sdr_devices())
-        except Exception:  # noqa: BLE001 - 失败按空列表处理，不造假
-            devices = []
+        except Exception as e:  # noqa: BLE001 - 保留原因，不压成「没插设备」
+            self._apply_devices([], enum_err=f"{type(e).__name__}: {e}")
+            return
         self._apply_devices(devices)
```

### 2. 异步入口：把 `err` 传下去，不要丢

```diff
     def refresh_devices_async(self) -> None:
         ...
         if not _ENUM_OK or enumerate_all_sdr_devices is None:
-            self._apply_devices([])
+            self._apply_devices([], enum_err="设备枚举不可用（枚举依赖缺失）")
             return
@@
     def _on_devices_refreshed(self, devices, err: str, token: int = 0) -> None:
         ...
         self._refresh_watchdog.stop()
         if err or devices is None:
-            self._apply_devices([])
+            self._apply_devices([], enum_err=err or "枚举任务未返回设备列表")
             return
         self._apply_devices(devices)
```

### 3. `_apply_devices` 区分两种空态

```diff
-    def _apply_devices(self, devices) -> None:
+    def _apply_devices(self, devices, enum_err: str | None = None) -> None:
         """把一份已枚举到的设备列表落到控件上（不触发重新枚举）。"""
         self._devices = list(devices or [])
         ...
         if not self._devices:
             self.dev_combo.addItem(_NO_DEVICE_COMBO)
-            self.status_label.setText(_NO_DEVICE_STATUS)
-            self.status_label.setToolTip(_NO_DEVICE_TIP)
+            if enum_err:
+                # 枚举失败 != 没插设备：不要把用户赶去拔插 USB
+                self.status_label.setText("设备枚举失败 · 这不等于没插设备")
+                self.status_label.setToolTip(
+                    f"枚举设备时出错：{enum_err}\n"
+                    "设备可能已插好，只是本次枚举失败。请点「刷新」重试，"
+                    "或拔插设备后重试。")
+            else:
+                self.status_label.setText(_NO_DEVICE_STATUS)
+                self.status_label.setToolTip(_NO_DEVICE_TIP)
             self.connect_btn.setEnabled(False)
             self._last_poll_locs = set()
             return
```

## 验证（建议与实现同笔提交）

```python
def test_enum_failure_is_not_reported_as_no_device(monkeypatch):
    panel = DevicePanel()                     # 或复用现有 fixture
    def boom():
        raise OSError("USB open timeout")
    monkeypatch.setattr("device_panel.enumerate_all_sdr_devices", boom)
    panel.refresh_devices()
    assert "枚举失败" in panel.status_label.text()
    assert panel.status_label.text() != device_panel._NO_DEVICE_STATUS

def test_empty_enum_still_says_no_device(monkeypatch):
    monkeypatch.setattr("device_panel.enumerate_all_sdr_devices", lambda: [])
    panel.refresh_devices()
    assert panel.status_label.text() == device_panel._NO_DEVICE_STATUS
```

反证：把 `_apply_devices` 的 `enum_err` 分支删掉，第一条必红
（实测旧实现只会显示 `_NO_DEVICE_STATUS`）。

## 不要顺手做的事

- 不要改「热插拔轮询 2 秒」与看门狗（`_on_refresh_timeout` 已能自愈）；
- 不要动 `_NO_DEVICE_STATUS` / `_NO_DEVICE_TIP` 的现有文案与 `_NO_DEVICE_COMBO`；
- 不要在这个 patch 里合并 `module_panel` 与 `device_panel` 的整套枚举管线——
  那是更大的重构，本 patch 只修「同一个失败两个结论」。
