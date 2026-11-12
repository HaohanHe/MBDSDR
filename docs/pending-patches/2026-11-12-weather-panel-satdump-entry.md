# 交接单：气象云图面板接 SatDump 数据集目录（待新立项）

> 来源：satdump 清单线（会话 `6ae85f56`）。
> 状态：**未实现、未加控件**。本单只记录需求与验收，动手前先 claim。
> 前置已落库：
> - `mbdsdr_ai/satdump_integration.py` 的 `compose_cloud_image()` 已改为
>   清单驱动选图，返回值带 `selection`（`source` / `manifest` / `product` /
>   `satellite` / `timestamp` / `image_count` / `image_index` / `picked_by`，
>   装了 `cbor2` 时另有 `channel` / `instrument` / `type` / `abs_index` /
>   `bit_depth` / `wavenumber` / `polarization` / `bandwidth` /
>   `calibration_type` / `product_timestamp`）。
> - `mbdsdr_ai/sdr_tools.py::_satdump_compose_image` 的文本输出已如实区分
>   「按清单选取 / 清单缺失 / 清单不可读 / 清单无可用图像」。

## 1. 目标

给气象云图面板（`desktop/weather_panel.py`）增加一个「加载 SatDump 数据集目录」
入口：选一个 SatDump 输出目录（含 `dataset.json` 与产品子目录）→ 合成云图 →
用**现有** `image_label` / `image_title` / `meta_label` 展示，并在同一处用现有
标签补一行「SatDump 产物：产品 / 卫星 / 时间」。

## 2. 现状与可复用点

- 现有展示位（**不新增标签**）：
  - `desktop/weather_panel.py` 的 `_on_result()` 里 `image_title.setText(...)`、
    `meta_label.setText(...)`（当前显示 卫星 / 通道 / 时间 / 分辨率）。
  - `_show_image(path)`、`_set_placeholder(text)`。
- 现有历史产物只认 `_OUT_DIR`（`~/.mbdsdr/weather`）下 `weather_*.png`
  （`_load_history()`）；SatDump 合成图若要进历史，需要沿用该前缀或明确扩展，
  不能写回源码树。
- 面板目前只消费自家解码器结果，**从未读取 SatDump 目录**。

## 3. 改动点（建议）

1. 新增入口控件：工具栏/按钮「加载 SatDump 目录」，内部
   `QFileDialog.getExistingDirectory`。这是一个**新控件**，因此必须过布局锁。
2. 调 `compose_cloud_image(input_dir, output_file, enhance=?)`；输出落到
   `_OUT_DIR`，命名沿用历史过滤规则（例如 `weather_satdump_<时间戳>.png`），
   避免历史列表看不到产物。
3. 展示映射（只填现有标签）：
   - `image_title`：「云图 — SatDump」或沿用现有「云图 — {satellite}」。
   - `meta_label`：卫星 = `selection.satellite`，通道 = `selection.channel`
     （未装 `cbor2` 时为 `—`），时间 = `selection.timestamp`（UTC）或
     `selection.product_timestamp`，分辨率 = 合成图 `size`；
     另起一行（同一 `meta_label` 内换行，不加控件）写：
     `SatDump 产物：产品 {product} / 卫星 {satellite} / 时间 {timestamp}`。
   - 兜底来源必须如实：`selection.source == "glob"` 时写
     「清单缺失/不可读，按文件名选取」，不得让用户以为是精确选图。
4. 选图/合成失败沿用诚实空态：`_set_placeholder("无数据")` + 状态栏报错，
   绝不补假图。

## 4. 验收

- 现有面板 `minimumSizeHint().width()` 与改动前**完全相等**（改动前先记录
  基线值，断言相等，不是「差不多」）。
- 构造一个含 `dataset.json` + 两个产品、其中一个产品多张图的目录：
  - 界面显示的「产品 / 卫星 / 时间」与 `selection` 一致；
  - 未装 `cbor2` 时通道显示 `—`，且选图仍确定、可复现；
  - 无清单的旧产物目录可加载，来源文案明确写「按文件名选取」。
- 新增控件后跑现有 UI/布局相关测试（`tests/test_ui_flow_guards.py` 等），
  不得引入新的离线/无显示环境失败。

## 5. 边界

- 不动 `satdump_integration.py` 的选图策略与 `sdr_tools.py` 的文本口径。
- 不引入新的硬依赖（`cbor2` 仍是可选）。
- 不把产物写回源码树；一律写 `~/.mbdsdr/weather`。
