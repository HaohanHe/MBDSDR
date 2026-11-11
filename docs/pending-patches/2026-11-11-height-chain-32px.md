# 待落库补丁：高度链 −32（天空 400→376 + 底坞 48→40）

> 提出：2026-11-11 夜；来源：裁定③
> **状态：天空 376 已落地**（2026-11-11 深夜，裁定③「批」后，
> `desktop/rf_sky_view.py` 已改为 `setMinimumSize(400, 376)`）；
> **底坞 40 未落**——`desktop/main_window.py` 归 *启动工单线/W25*（施工中），
> 需等其释放后把 `setFixedHeight(48)` 改 40。两块合计 −32 到 696 的目标在底坞
> 落地前只完成 −24。
> 验证工作树：`d:\mbdsdr\output\wt-fs`（detached @ `6e90e93b`）
> 关联：裁定④「频谱页已不在高度链上」见 `docs/research/2026-11-11-gap-layout.md` §2.5

## 一、改动（两块，一个补丁）

| # | 文件 | 现值 → 拟值 | Δ |
| --- | --- | --- | --- |
| 1 | `desktop/rf_sky_view.py`（模块常量 `SKY_MIN_H`，`setMinimumSize(400, SKY_MIN_H)`） | 400 → **376** | −24 |
| 2 | `desktop/main_window.py` `_build_bottom_dock` | `setFixedHeight(48)` → **40** | −8 |
| — | 合计 | | **−32** |

按裁定**没有**采用「天空单独压到 368」的方案。

## 二、实测（同一进程内运行时回退对照，排除环境漂移）

`hchain_probe.py`（`WA_DontShowOnScreen`，请求 1000×680 并断言实际==请求）：

| 快照 | sky 最小高 | dock 固定高 | splitter 最小高 | **窗口最小高** |
| --- | --- | --- | --- | --- |
| `reverted_400_48`（改动前） | 400 | 48 | 446 | **728** |
| `code_now`（改动后） | 376 | 40 | 422 | **696** |
| `code_again`（再改回，验证可逆） | 376 | 40 | 422 | **696** |

- 门槛 700，**余量 4px**，与裁定一致；
- 变化全部落在 splitter（446→422），两个承担控件各出 −24 / −8；
- `dock_layout_min_h = 28`，固定高 40 仍有 12px 余量（不会被压缩）；
- 另在**未改动的真实工作树**上用 `force` 模式运行时施加 376/40，同样得到 **728→696**
  （即当前树的 728 基线与本补丁的 −32 可叠加，落库后就是 696）。

## 三、落库前必须做的基线重定标（已在验证树做过一遍）

`tests/test_ui_layout_locks.py::test_bottom_dock_height_is_locked` 明确锁死底坞高度，
且底坞是 8 张截图基线的间接输入。按仓库既有流程（不是人肉反推）：

1. `scripts/ui_baseline.py` 的 `DESIGN_CHANGES` 增加声明 `narrow-bottom-dock-48-to-40`，
   `BASELINE_PROVENANCE` 补齐 5 个字段（`authorized_by` 引裁定原话、`formula` 给算式、
   `source_commit`、`updated_at`、`measured`）——补丁里已带；
2. 测试里的断言 48 → 40（补丁里已带）；
3. 重建基线（只在 100% DPI 跑，脚本自带约束）：

```powershell
python scripts/ui_layout_audit.py --update-baselines --accept-geometry-change `
  --reason "narrow-bottom-dock-48-to-40 底坞 48→40 + 天空画布最小高 400→376，窗口最小高 728→696"
```

产出会改 **8 张面板基线 + 4 份 labels.json**（`spectrum_*` / `adsb_empty_*`，
1000x680 / 1280x720 / 1600x900 / 1920x1080）。实测（旧图从 `git show HEAD:` 取）：

| 基线 | 1000x680 | 1280x720 | 1600x900 | 1920x1080 |
| --- | --- | --- | --- | --- |
| `adsb_empty_*` 高 | 531→539 (**+8**) | 469→475 (**+6**) | 460→465 (**+5**) | 456→459 (**+3**) |
| `spectrum_*` 高 | 361→368 (**+7**) | 277→281 (**+4**) | 284→288 (**+4**) | 289→291 (**+2**) |

为什么不是 +32：截图基线是 1000×680 起的窗口，本就高于窗口最小高，所以**只有底坞的
Δ8 进入面板高度**；天空的 −24 只在「压到地板」时兑现（即窗口最小高 728→696 那一侧）。
频谱是归一化图（宽固定 320），Δ = `round(320×8/W)`，与实测逐档吻合。

结果：**70 passed / 4 skipped**（重定标后全绿，含
`test_planetarium_ai_card_fits_narrow_sky_slot` 与 `test_no_widget_escapes_its_parent`）。

**基线 PNG 不随补丁走**：上面一条命令是确定性的，落库时在本机跑一次即可；跑完必须
`git diff --stat tests/baselines` 复核，8 张图与 4 份 json 之外不应有别的变化。

## 四、天空画布 376 vs 400 视觉比对（裁定③的前置条件）

脚本 `sky_shot_probe.py`（把真实 `RFSkyView` 取出、单独放进 400 宽容器，分别按
400 / 376 高渲染；夜间站址 10°N / 10°E，`jd=2461356.1667`）：

| 项 | 400 高 | 376 高 | 判定 |
| --- | --- | --- | --- |
| 星表选择数 | 1669 | 1669 | 一致（选择只与站址/时刻有关，与画布无关） |
| 方向标签 | N / E / S / W 齐全，未见被裁 | 同左 | 无退化 |
| 星座名（Cepheus / Lacerta / Aquarius / Capricornus 等） | 位置完整 | 位置完整，无新增重叠 | 无退化 |
| 投影形状 | 圆盘，上下留白 8px | 圆盘，纵向压缩 6%（半径 200→188） | 可接受 |

截图：`d:\mbdsdr\output\tmp\sky-400.png`、`sky-376.png`（不入库）。
**结论：退化不明显，继续落库。** 若后续把最小高再往下压（368 及以下），必须重跑本比对。

## 五、补丁与验证

```powershell
git apply docs/pending-patches/2026-11-11-height-chain-32px.patch
python scripts/ui_layout_audit.py --update-baselines --accept-geometry-change --reason "narrow-bottom-dock-48-to-40 ..."
python -m pytest tests/test_ui_layout_locks.py -q
```

补丁含：`desktop/rf_sky_view.py`、`desktop/main_window.py`、`scripts/ui_baseline.py`、
`tests/test_ui_layout_locks.py` 四个文件。

## 六、验收判据

1. `window_minimumSizeHint().height() == 696`（DPR1.0 default 态），且 ≤ 700；
2. `SKY_MIN_H` 与底坞高度两处同时生效（单落一处只到 704 / 720，不算过）；
3. 8 张基线 + 4 份 labels 重定标后 `test_ui_layout_locks.py` 全绿；
4. 天空 376/400 截图比对无退化（第四节）；
5. 回退反证：把两块同时改回，最小高必须回到 728。
