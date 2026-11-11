# 清单：写盘/导出类用户动作的成败可见性（批次 K）

> 日期：2026-11-11；范围：`desktop/*.py` 里由用户动作触发、真正落盘/导出的
> 入口。三列判定：
> **① 异常是否被吞**（`except: pass` / 只写日志 / 返回值被丢弃）；
> **② 失败是否说人话**（用户可见、带原因/后果）；
> **③ 成功文案是否在失败路径也出现**（同一句「已保存/已导出」在写失败时仍发）。
>
> 判定口径：③ 命中 = 假成功；① 命中且无 ② = 静默失败。内部缓存（主题图、
> 天气图、OSM 瓦片）只列不判，因为它们没有面向用户的成功文案。

## 一、逐条清单

| # | 动作 | path:line | ① 异常吞 | ② 失败说人话 | ③ 成功文案也发 |
| --- | --- | --- | --- | --- | --- |
| 1 | 书签导出 CSV | `desktop/bookmark_panel.py:363-377` | 否（捕获后弹窗） | 是（`导出失败` + 异常内容） | 否（先 return） |
| 2 | 书签导入 CSV | `desktop/bookmark_panel.py:346-361` | 否 | 是（`导入失败` + 异常内容） | 否 |
| 3 | 设置导出 JSON | `desktop/settings_panel.py:1318-1327`、`1222-1237` | 否（`_atomic_write_json` 抛，调用方捕获） | 是（`导出失败` + `str(e)`） | 否（`已导出` 在写之后） |
| 4 | 设置单改值（含 AI 配置） | `desktop/settings_panel.py:907-931`、`593-598` | 部分（settings 走 `on_error`；ai_config 捕获后只 `_notify`） | **被覆盖**：ai_config 失败刚提示，`_on_changed` 末尾又发 `已保存：{label}` | **命中**（ai_config 写失败仍发「已保存」） |
| 5 | 观测站站址保存 | `desktop/main_window.py:11761-11815`、`12954-13026` | **是**（`11798` `except: pass`；`_save_gui_config` 内部只 `logger.warning`） | 否（成功文案不问持久化结果） | **命中**（写盘失败仍发「观测位置：…」成功态） |
| 6 | 顶栏切换布局 | `desktop/main_window.py:5358-5367`；`desktop/dock_layout_manager.py:589-613` | 部分（`save_state` 返回 False 被丢弃，`last_save_error` 有值但不显示） | 否（切换后静默，重启回旧布局） | 命中（UI 已切换，用户视为已保存） |
| 7 | 顶栏「保存」布局 | `desktop/main_window.py:5401-5422`；`desktop/layout_store.py:132-145` | 否（返回 False） | 部分（「无法保存布局，请重试」无原因） | 否 |
| 8 | 星象仪「存图」 | `desktop/main_window.py:3129-3167` | 否（`grab/save` 捕获置 False） | 部分（「存图失败」有路径无原因） | 否（三元互斥） |
| 9 | 云图另存 PNG | `desktop/satellite_image_panel.py:445-466` | 否 | 是（文件名 + 异常类型；11-06 已修） | 否 |
| 10 | 录音（开始/停止/侧车/关窗） | `desktop/main_window.py:9358-9720`、`12847-12952` | 否（按 A+B 收口） | 是（中止原因、sidecar 失败、关窗失败弹窗） | 否（`_stop_recording(error=…)` 以错误开头） |
| 11 | GUI 配置自动保存 | `desktop/main_window.py:12954-13026` | **是**（最外层 `except` 只 `logger.warning`） | 否（无成功文案，但用户以为窗口/站址已记住） | 否（无成功文案）→ 归入 #5 |
| 12 | 窗口几何 / splitter | `desktop/main_window.py:11019-11030` | 是（QSettings 写不检测；splitter 段 `except: pass`） | 否 | 否（无成功文案） |
| 13 | 地图离线瓦片下载 | `desktop/location_panel.py:341-401` | 否（`finished(ok,bad)` 分类） | 是（成功/失败张数） | 否 |
| 14 | AI 配置对话框保存 | `desktop/ai_panel.py:215-232`、`523-556` | 否（返回 False + `_last_save_error`） | 是（明确「绝不报已配置并保存」） | 否 |
| 15 | 人工确认审计 JSONL | `desktop/main_window.py:7830-7844` | **是**（只 `logger.warning`） | 否 | 否（审计证据静默丢失） |
| 16 | 布局预设写入 | `desktop/layout_store.py:132-145` | 否（返回 False） | 由 #6/#7 决定 | 否 |
| 17 | 站址镜像 `config.json` | `desktop/station_sync.py:115-163` | 否（返回 False） | 否（唯一调用点 `main_window.py:13020` 丢弃返回值） | 归入 #5 |
| — | 主题/图标/天气图缓存 | `desktop/themes.py:55/104/128`、`weather_panel.py:274/352` | 部分 | 不适用（内部缓存） | 无成功文案 |
| — | OSM 瓦片缓存 | `desktop/osm_map.py:291-306` | 部分（缓存写失败不影响当屏） | 不适用 | 无成功文案 |

## 二、最误导的三处（修）

1. **#4 AI 配置写失败被「已保存」覆盖**：`_store_value` 先报失败，随后
   `_on_changed` 无条件 `已保存：{label}` 把失败消息冲掉。用户重启后发现
   API Key/参数没保存。
2. **#5 站址保存失败仍报「观测位置：…」**：`persist=True` 时 `_save_gui_config`
   的异常在 `11798` 被 `except: pass` 吞，站址只在本次会话有效，重启回到旧
   站址，而状态栏给的是成功态。
3. **#6 切换布局失败无提示**：`save_state()` 的 False 与 `last_save_error`
   被丢弃，布局当次生效、重启回旧布局，全程无提示。

## 三、其余判定（不修，留档）

- #7/#8 属于「说了失败但没说原因」，不是假成功，属文案改进，排后。
- #11/#12/#15 属「静默失败但无成功文案」，用户不会有被欺骗的完成感，
  但会误以为已持久化；其中 #11 由 #5 修复路径顺带覆盖（`_save_gui_config`
  返回值被上层使用）。
- #10 录音族已在 2026-11-11 早前按 A+B 收口，本轮只复核不重复改。

## 四、被占文件的修复设计（#5 / #6，等 `main_window.py` 释放后执行）

`desktop/main_window.py` 目前被 `星象仪取证线/6ae85f56` 持有**未过期 claim**
（且工作区已出现未提交改动，`_refresh_sat_panel` 引用暂不可用）。按纪律不抢，
设计与验收先落在这里。

### #5 站址保存失败仍报成功

1. `_save_gui_config()`（`:12954-13026`）改为返回 `bool`：最外层 `except`
   保留日志（`logger.warning("GUI 配置写入失败: %s", exc)`），把原因存到
   `self._gui_config_last_error`，返回 False；成功返回 True。**不抛**（关闭
   窗口时调用，抛了会更糟）。
2. `set_observer_site()`（`:11761-11815`）在 `persist=True` 时接收返回值：
   - 成功：维持 `观测位置：{lat}, {lon}（来源）`；
   - 失败：`showMessage("观测位置已更新（仅本次会话）：配置写入失败：{原因}，
     重启后会恢复旧站址", MSG_IMPORTANT)`。
   坐标本身照常应用（不改行为，只改可见性）。
3. 测试 `tests/test_observer_site_persist_honesty.py`：
   - monkeypatch `_save_gui_config` 返回 False（`_gui_config_last_error="磁盘只读"`）
     → 调 `set_observer_site(31.2, 121.5)` 后状态栏含「仅本次会话」「磁盘只读」，
     且 `_observer_lat == 31.2`（功能不受影响）；
   - 返回 True → 状态栏不含「写入失败」。
4. 变异反证：把 `set_observer_site` 改回忽略返回值 → 第一条用例必红；
   把 `_save_gui_config` 改回 `except: pass` 且无返回值 → 第一条必红。

### #6 切换布局失败无提示

1. `_on_layout_preset_changed`（`:5358-5367`）接收 `dlm.save_state()` 的 bool：
   `False` 时 `showMessage(f"布局已切换，但保存失败：{dlm.last_save_error or '未知原因'}
   （重启后会回到旧布局）", MSG_IMPORTANT)`。
2. 顶栏「保存」布局（`:5401-5422`）的失败文案补原因：把
   「无法保存布局，请重试」改为带 `dlm.last_save_error`。
3. 测试 `tests/test_layout_persist_honesty.py`：把 `dlm.save_state` 打成返回
   False + `last_save_error="disk full"` → 切换预设后状态栏含「保存失败」
   「disk full」，且当前布局确实已应用。
4. 变异反证：丢弃返回值 → 用例必红。
