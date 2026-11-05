# 桌面端补丁（交操作逻辑线）：1090 MHz 请求失败必须说人话，ADS-B 不许替设备吹牛

> 目标文件：`desktop/main_window.py`、`desktop/adsb_panel.py`、
> `desktop/capability_catalog.py`、`desktop/bandplan.json`
> （`desktop/` 归操作逻辑线，本文件**不直接改**；由 desktop owner 应用）。
> 生成时间：2026-11-06；执行人：解码流水线 P5。
> 依赖：`mbdsdr_ai/sdr_backend.py` 的同批改动（`get_frequency_bounds()`、
> 越界预检、`sdr_frequency_out_of_range` 文案）。

## 1. 为什么需要这个补丁（真机证据）

在 FC0012 上把频率设成 1090 MHz（真实 `MainWindow` + 真实 `RTLSDRBackend`，
offscreen）实际观察到的用户可见行为：

| 观察项 | 修复前 |
| --- | --- |
| 频率主标签 | `1090.000 MHz`，看起来已生效 |
| 状态栏消息 | `已连接 RTL-SDR #0`，**没有任何失败提示** |
| 弹窗 | 无 |
| 后端真实回读 | `0 Hz`（硬件没调到 1090） |
| 保存配置 | `frequency_hz` 被写成 1090 MHz |
| 设备状态 | `connected=True`，随后该句柄不可恢复（再写触发 access violation，须重连） |

根因有两层：后端 `_apply_frequency` 重试耗尽仍 `return True`（已在
`mbdsdr_ai/sdr_backend.py` 修掉）；桌面 `_apply_hw_action` 只捕获异常、
忽略 `fn()` 的 `False` 返回值，于是 `_on_tune_sdr` 继续写 UI 并落盘。

## 2. 补丁内容

1. `_apply_hw_action` 尊重 `False` 返回值：把 `backend.get_status().error`
   的可读文案显示到状态栏，不弹窗、不继续写 UI/配置。
2. `AdsbPanel` 新增 `set_tuner_max_hz()`：当前设备真实上限 < 1090 MHz 时，
   空态显示「当前设备上限 948.5 MHz，无法接收 1090 MHz；需覆盖 1090 MHz 的
   前端」，而不是让用户对着「等待 1090MHz 数据」空等。
3. `MainWindow._sync_adsb_tuner_limit()` 在三条连接成功路径 + 面板构建后
   把设备上限推给 ADS-B 面板。
4. 能力目录把 ADS-B 面板标为 `conditional` 并写清原因；ADS-B 解码条目标注
   「对拍用 dump1090 官方 OTA 实录，不代表本机前端覆盖 1090 MHz」。
5. `bandplan.json` 的 ADS-B 1090 描述加「需覆盖 1090 MHz 的前端」。

## 3. 应用与验证

```powershell
# 1) 从本文件第 4 节复制 diff 到 d:\tmp\desktop-adsb-1090.patch（保持 LF）
git apply --check d:\tmp\desktop-adsb-1090.patch   # 已在本仓库 HEAD 上验证通过
git apply d:\tmp\desktop-adsb-1090.patch
# 2) 定向测试（有桌面的既有用例 + 能力目录守卫）
$env:MBDSDR_NULL_AUDIO='1'; $env:MBDSDR_NO_HW='1'; $env:QT_QPA_PLATFORM='offscreen'
python -m pytest tests/test_discoverability_help.py tests/test_main_window_hw_truthfulness.py -q
```

预期行为：在 FC0012 上请求 1090 MHz 时，频率标签保持上次成功值，状态栏显示
「设置频率未生效：当前调谐器 FC0012 可调范围 22.000–948.500 MHz，无法调谐到
1090.000 MHz；未向硬件下发该请求。界面与已保存配置保持上次成功值。」，
`settings.frequency_hz` 不被改写；ADS-B 面板连接后直接显示设备上限提示。

## 4. 统一 diff（可直接 `git apply`，已 `--check` 通过）

```diff
diff --git a/desktop/main_window.py b/desktop/main_window.py
index 26ed813..9b7fe60 100644
--- a/desktop/main_window.py
+++ b/desktop/main_window.py
@@ -3776,6 +3776,8 @@ class MainWindow(QMainWindow):
         self._worker = None
         self._active_sdr_backend = backend
         self._panels_set_sdr_connected(True)
+        # 面板入口连上 ≠ 设备收得到 1090；上限提示必须在连接成功路径同步。
+        self._sync_adsb_tuner_limit()
         try:
             self.conn_label.setText(f"  状态: {backend.device.name}  ")
             self.conn_label.setStyleSheet(self._conn_label_qss(True))
@@ -3882,6 +3884,7 @@ class MainWindow(QMainWindow):
             return False
         self._active_sdr_backend = previous
         self._panels_set_sdr_connected(True)
+        self._sync_adsb_tuner_limit()
         try:
             name = previous.device.name
         except Exception:
@@ -4582,6 +4585,8 @@ class MainWindow(QMainWindow):
             self._link_degraded_code = ""
             # S9：硬件真正就绪才启用调谐/录音等控件（连接中一直保持置灰）
             self._panels_set_sdr_connected(True)
+            # 设备变了 → ADS-B 面板的上限提示跟着设备走（FC0012 收不到 1090）
+            self._sync_adsb_tuner_limit()
             # 硬件就绪：把设备报告的工具 schema 注册进 AI agent
             self._sync_ai_mcp_tools()
         else:
@@ -4640,14 +4645,53 @@ class MainWindow(QMainWindow):
         )
 
     def _apply_hw_action(self, action: str, fn) -> bool:
-        """执行硬件写入；失败只提示并返回 False，不抛、不提交 UI。"""
+        """执行硬件写入；失败只提示并返回 False，不抛、不提交 UI。
+
+        ``fn`` 返回 False 同样算失败：真实后端在频率越界/写频失败时用返回
+        值报告失败，并在 ``status.error`` 留一句人话（例：「当前调谐器
+        FC0012 可调范围 22.000–948.500 MHz，无法调谐到 1090.000 MHz」）。
+        旧实现只看异常，会把 False 当成功继续写 UI 并落盘。
+        """
         try:
-            fn()
+            result = fn()
         except Exception as e:
             self._report_hw_failure(action, e)
             return False
+        if result is False:
+            detail = ""
+            backend = getattr(self, "_active_sdr_backend", None)
+            try:
+                detail = str(getattr(backend.get_status(), "error", "") or "")
+            except Exception:
+                detail = ""
+            detail = detail.rstrip("。")
+            self.statusBar().showMessage(
+                f"{action}未生效：{detail or '设备返回失败（无更多说明）'}。"
+                "界面与已保存配置保持上次成功值。",
+                MSG_IMPORTANT,
+            )
+            return False
         return True
 
+    def _sync_adsb_tuner_limit(self) -> None:
+        """把当前设备真实可调上界交给 ADS-B 面板。
+
+        低于 1090 MHz 的设备（本机 FC0012 实测上限 948.5 MHz）收不到
+        ADS-B，面板必须如实说明，而不是一直显示「等待 1090MHz 数据」。
+        """
+        backend = getattr(self, "_active_sdr_backend", None)
+        panel = getattr(self, "digital_adsb_panel", None)
+        if backend is None or panel is None:
+            return
+        try:
+            hi_hz = float(backend.get_frequency_bounds()[1])
+        except Exception:
+            return
+        try:
+            panel.set_tuner_max_hz(hi_hz)
+        except Exception:
+            pass
+
     def _revert_tune_ui(self) -> None:
         """把频率/模式显示拉回最后一次真实成功值（失败后不许留在新值）。"""
         last_hz = float(getattr(self, "_backend_center_hz", 0.0) or 0.0)
@@ -7171,6 +7215,7 @@ class MainWindow(QMainWindow):
                     "ADS-B", self.digital_adsb_panel, "adsb_dock")
             except Exception:  # noqa: BLE001
                 self.digital_adsb_panel = None
+            self._sync_adsb_tuner_limit()
 
         # ---- APRS 数据包（右栏页签）----
         if AprsPanel is not None:
diff --git a/desktop/adsb_panel.py b/desktop/adsb_panel.py
index 58c4814..7323fb7 100644
--- a/desktop/adsb_panel.py
+++ b/desktop/adsb_panel.py
@@ -62,6 +62,13 @@ _STATION = QColor(_t.COLORS["station_blue"])
 
 # 空态文案（测试据此断言面板进入“无数据”状态）。
 ADSB_WAIT_TEXT = "等待 1090MHz 数据"
+ADSB_CENTER_HZ = 1_090_000_000
+#: 当前设备真实可调上界低于 1090 MHz 时的空态：不许让用户对着
+#: 「等待 1090MHz 数据」空等一台物理上收不到的设备。
+ADSB_UNREACHABLE_TEXT = (
+    "当前设备上限 {max_mhz:.1f} MHz，无法接收 1090 MHz；"
+    "需覆盖 1090 MHz 的前端（如 R820T/R828D）"
+)
 NO_STATION_TEXT = "未设置地面站位置（请在 ~/.mbdsdr/config.json 设置 ground_station_lat/lon）"
 
 # 飞机超时 30 秒未更新即移除。
@@ -226,6 +233,8 @@ class AdsbPanel(QWidget):
         super().__init__(parent)
         self._tracker = AircraftTracker()
         self._sdr_connected = False
+        # 当前设备真实可调上界（Hz）；None = 未知，不替设备下结论。
+        self._tuner_max_hz: Optional[float] = None
         self._station: Tuple[Optional[float], Optional[float]] = _load_station()
         self._build_ui()
 
@@ -270,6 +279,11 @@ class AdsbPanel(QWidget):
         self._sdr_connected = bool(connected)
         self._refresh_header()
 
+    def set_tuner_max_hz(self, max_hz: Optional[float]) -> None:
+        """当前设备真实可调上界；低于 1090 MHz 时如实说明收不到。"""
+        self._tuner_max_hz = float(max_hz) if max_hz else None
+        self._refresh_header()
+
     def feed_iq(self, iq, sample_rate: float) -> None:
         """喂一段 1090MHz 复基带 IQ；内部解码并合并到跟踪表。"""
         if not _HAS_ADSB:
@@ -310,9 +324,15 @@ class AdsbPanel(QWidget):
         if n:
             self.header.setText(f"在视飞机 {n} 架"
                                 + ("" if self._sdr_connected else "（未连接后端）"))
+        elif not self._sdr_connected:
+            self.header.setText("未连接（等待 1090MHz 数据）")
+        elif (self._tuner_max_hz is not None
+              and self._tuner_max_hz < ADSB_CENTER_HZ):
+            # 设备上界 < 1090：物理上收不到，别让用户对着「等待数据」空等。
+            self.header.setText(
+                ADSB_UNREACHABLE_TEXT.format(max_mhz=self._tuner_max_hz / 1e6))
         else:
-            self.header.setText(ADSB_WAIT_TEXT
-                                if self._sdr_connected else "未连接（等待 1090MHz 数据）")
+            self.header.setText(ADSB_WAIT_TEXT)
 
     def _refresh_table(self) -> None:
         acs = self._tracker.all()
diff --git a/desktop/capability_catalog.py b/desktop/capability_catalog.py
index 5f1ce5a..7573c70 100644
--- a/desktop/capability_catalog.py
+++ b/desktop/capability_catalog.py
@@ -136,12 +136,21 @@ def _panel_area_text(area: str) -> str:
 def _panel_capabilities() -> List[Capability]:
     out: List[Capability] = []
     for spec in _panels().list():
+        status, note = "available", ""
+        if spec.id == "adsb":
+            # 面板入口存在 ≠ 当前设备收得到：ADS-B 在 1090 MHz，FC0012 类
+            # tuner 实测上限 948.5 MHz，需覆盖 1090 的前端才可用。
+            status = "conditional"
+            note = ("需覆盖 1090 MHz 的前端；本机 FC0012 类 tuner 实测上限约 "
+                    "948.5 MHz，在该设备上收不到（面板会如实显示无法接收）")
         out.append(Capability(
             category=PANEL_CATEGORY.get(spec.id, "接收"),
             title=spec.name,
             description=spec.summary or f"{spec.name}面板",
             trigger=f"「面板」菜单 → {spec.name}（勾选显示/隐藏，位置：{_panel_area_text(spec.area)}）",
             source=f"panel_registry:{spec.id}",
+            status=status,
+            note=note,
         ))
     return out
 
@@ -165,6 +174,11 @@ def _digital_capabilities() -> List[Capability]:
     for key in sorted(DIGITAL_MODE_CAPABILITIES):
         cap = DIGITAL_MODE_CAPABILITIES[key]
         note = _EVIDENCE_NOTE.get(cap.evidence, _DEFAULT_EVIDENCE_NOTE)
+        if key == "adsb":
+            # 对拍数据来自 dump1090 官方 OTA 实录：只证明解码/重采样路径，
+            # 不构成「本机前端能覆盖 1090 MHz」的能力证据。
+            note += ("；解码对拍用 dump1090 官方 OTA 实录，只证明重采样/解码"
+                     "路径等价，不代表本机前端能覆盖 1090 MHz")
         status, trigger = "available", ""
         if cap.dependency:
             trigger = (f"命令行：python -m mbdsdr_ai --decode-file <文件> "
diff --git a/desktop/bandplan.json b/desktop/bandplan.json
index 0cd95ea..867e64e 100644
--- a/desktop/bandplan.json
+++ b/desktop/bandplan.json
@@ -33,6 +33,6 @@
     {"label": "公众对讲机", "start_hz": 409750000, "stop_hz": 409987500, "category": "pmr", "modulation": "FM", "desc": "409 MHz 免执照 20 信道"},
     {"label": "70cm 业余", "start_hz": 430000000, "stop_hz": 440000000, "category": "amateur", "priority": "high", "modulation": "FM/SSB", "desc": "含 435–438 卫星子带"},
     {"label": "业余卫星下行", "start_hz": 435000000, "stop_hz": 438000000, "category": "satellite", "modulation": "FM/SSB/CW", "desc": "CubeSat 下行子带"},
-    {"label": "ADS-B 1090", "start_hz": 1089000000, "stop_hz": 1091000000, "category": "aviation", "modulation": "1090ES", "desc": "机载应答机下行"}
+    {"label": "ADS-B 1090", "start_hz": 1089000000, "stop_hz": 1091000000, "category": "aviation", "modulation": "1090ES", "desc": "机载应答机下行；需覆盖 1090 MHz 的前端（FC0012 类上限约 948.5 MHz，收不到）"}
   ]
 }
```
