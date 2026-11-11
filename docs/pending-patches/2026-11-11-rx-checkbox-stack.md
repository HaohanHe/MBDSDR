# 待落库补丁：接收组 DC/IQ 复选框竖排（150% 档）

> 提出：2026-11-11 夜；来源：裁定②「150% 档的红治接收组：`DC 去除` 与 `IQ 平衡`
> 那对复选框换行（273→138），**不要动书签组**」
> 占用：`desktop/control_panel.py` 归 *设备缺口修复线*（pid 存活，TTL 未到）且工作树有
> 他线未提交改动 → 按形态⑮只交补丁。
> 验证工作树：`d:\mbdsdr\output\wt-fs`（detached @ `6e90e93b`）

## 一、改了什么

`control_panel.py` 接收组里 `iq_cal_row = QHBoxLayout()` → `iq_cal_box = QVBoxLayout()`，
两个 `QCheckBox` 由并排变上下两行（间距 2px）。信号连接、默认勾选、成员名全部不变。

```diff
-        iq_cal_row = QHBoxLayout()
+        iq_cal_box = QVBoxLayout()
+        iq_cal_box.setSpacing(2)
         self.dc_removal_check = QCheckBox("DC 去除")
         ...
-        iq_cal_row.addWidget(self.dc_removal_check)
+        iq_cal_box.addWidget(self.dc_removal_check)
         self.iq_balance_check = QCheckBox("IQ 平衡")
         ...
-        iq_cal_row.addWidget(self.iq_balance_check)
-        rx_layout.addLayout(iq_cal_row)
+        iq_cal_box.addWidget(self.iq_balance_check)
+        rx_layout.addLayout(iq_cal_box)
```

完整补丁：`docs/pending-patches/2026-11-11-rx-checkbox-stack.patch`。

## 二、273 → 138 的实测

`boxw_probe.py`（`mapTo(body)` 取绝对坐标，DPR1.0，DPR1.0 行高 27）：

| 档 | 改前 `DC 去除` | 改前 `IQ 平衡` | 同行宽 | 改后 `DC` | 改后 `IQ` | 行宽 |
| --- | --- | --- | --- | --- | --- | --- |
| 100% | x=36 y=1149 w=100 | x=**136** y=1149 w=92 | 192 | x=36 y=1149 w=100 | x=36 **y=1178** w=92 | **100** |
| 150% | x=36 y=1197 w=138 | x=**183** y=1197 w=126 | **273** | x=36 y=1329 w=138 | x=36 **y=1370** w=126 | **138** |

「行宽」= 第二个复选框右边界 − 第一个左边界。150% 档 273 → 138，正是裁定要的数；
100% 档 192 → 100。该组最小宽（`QGroupBox 接收`）：

| | 100% | 125% | 150% |
| --- | --- | --- | --- |
| 改前 | 249 | 284 | **321** |
| 改后 | 247 | 263 | **278** |

150% 档 −43px。`书签 / 收藏` 组一处未动。

## 三、整矩阵（①+②+③ 同时生效，default 态，DPR1.0 / DPR1.5）

`right_column.body_min_w` 对 `viewport=310` 的余量（正=有富余，负=横向滚动）：

| 字体 | DPR1.0 body / 余量 | DPR1.5 body / 余量 |
| --- | --- | --- |
| 100% | 349 / **−38** | 268 / **+42** |
| 125% | 399 / **−88** | 308 / **+2** |
| 150% | 449 / **−138** | 338 / **−28** |

**这张表不达标（要求每档 ≥4px）**，但红已经不是接收组造成的：

| 组 | 100% | 125% | 150% |
| --- | --- | --- | --- |
| **收藏**（原「书签」，本轮未动） | **325** | **375** | **425** |
| 接收（本轮已治） | 247 | 263 | 278 |
| 连接 | 339 | — | 443 |

`body_min = 收藏 + 左右外边距 24`，逐档精确对上（325+24=349、375+24=399、425+24=449）。
即：**本轮的宽红由「收藏」组单独顶宽，与 DC/IQ 无关**；裁定「不要动书签组」意味着
这张矩阵在不动它的前提下不可能转绿。

为什么它比上一版记录（285 / 345）宽了 40px：`6e90e93b` 已把「书签」组改名为「收藏」并
加了内容，`git log --oneline -3 -- desktop/control_panel.py` 可见，属他线改动；
本线未触碰。详见 `docs/pending-patches/2026-11-11-right-column-width-margin.md` §七。

## 四、高度方向的代价

竖排多占一行：接收组高 +29px（100%）/ +41px（150%）。右栏是纵向滚动列，不改变窗口最小高
（高度链由天空页 422 决定，见 `2026-11-11-height-chain-32px.md`），只让滚动更早开始。

## 五、验收判据

1. `DC 去除` / `IQ 平衡` 的 `x` 相同、`y` 相差约 29px（不是同一行）；
2. 接收组最小宽 150% ≤ 280；
3. 两个复选框的 `toggled` 仍分别接到 `_on_dc_removal_changed` / `_on_iq_balance_changed`；
4. `tests/test_ui_layout_locks.py` 全绿（不做截图基线的只有这一组，宽度不进锁）；
5. 单测：勾选/取消两个复选框，后端参数仍随动。
