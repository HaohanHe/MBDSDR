# 交接单：`desktop/main_window.py` 六处「真软件」缺陷（只出单，不代改）

> 收件方：当前持有 `desktop/main_window.py` 的那条线（claim 表里 2026-11-06 06:52
> 起为 `操作逻辑线/6ae85f56`，TTL 45 min，取证时已过期；工作树有未提交改动）。
> 提出方：WorkBuddy 线；提出时间：2026-11-06 夜。
> 本单**没有**改动 `main_window.py` 一个字节；下方每条都给了锚点与修法，
> 合并时行号会漂移，以锚点文本为准。

## 0. 先说来源与可信度

这六条来自一次只读审计（Explore 子代理，逐条给 path:line），随后由本线**抽样
复验**了能离线复验的部分（例如 `_poll_sdr_iq` 全仓只有 `stop()` 没有 `start()`；
`_apply_gimbal_result(ctx, {}, 0.0)` 与函数签名 `(task, …)` 不匹配）。凡标
「未复验」的，请在动手前自查一次。

## 1. 【P0】`_poll_sdr_iq` 从未启动 → ADS-B 列表面板与地图页签永久「等待数据」

- 证据：`main_window.py:~944` 只创建定时器并 `connect`，全仓 grep `_poll_sdr_iq`
  只有定义与 `stop()`（~6333），**没有 `start()`**；真正在跑的是
  `ReceivePipeline`（~5903 `pipe.start()`）。
- 后果：`digital_adsb_panel.feed_iq`（~6468）、`iq_bus.publish`
  （`spectrum_widget.py:2520`，仅由 `set_iq_data` 调用，~6460）都挂在这条没启动
  的路径上 → 右栏「ADS-B」页签与 1090 MHz 地图页签**永远是空态**，即使真的
  解出 ADS-B 帧也一样；`AdsbMapPanel._poll_iq_bus`（`adsb_map_panel.py:298`）
  每 200 ms 空转。
- 建议：在 pipeline 的 `frame_ready` 槽（`_on_pipeline_frame_ready`，~6210）
  里补一次 `iq_bus.publish(iq, sr, cf)`，并按 `isVisible()` 决定是否
  `digital_adsb_panel.feed_iq`；或彻底删掉死路径并把注释改成实。
- 复验方法：`grep -n "_poll_sdr_iq" desktop/main_window.py`（只有两处命中即证实）。

## 2. 【P0】解调回调在后台线程直接改 Qt 控件

- 证据：`main_window.py:5911` 把 `pipe._demod1.audio_tap` 设为 `_tap_demod_audio`
  （定义 ~6751）；该回调在 `receive_pipeline.py:357` 的 `DemodWorker.run()` 线程
  里执行，内部调用 `anr_p.set_snr_improvement()`（~6784）与 `ap.feed_audio()`
  （~6797）→ `aprs_panel.py:175` 的 `self._refresh_table()` 直接
  `QTableWidget.setItem`。
- 后果：`QTableWidget/QLabel/QImage` 只能在 GUI 线程改。这里每 6.8 ms 一次
  （2.4 MS/s ÷ 16384），APRS 一旦有数据就可能在非 GUI 线程重建表格行 →
  随机崩溃或渲染错乱。这是**可复现的竞态**，不是理论风险。
- 建议：tap 改成 `Signal(np.ndarray)` + `Qt.QueuedConnection` 连回 GUI 线程槽；
  槽里只做 UI，ANR 与 AFSK 解调留在 worker。

## 3. 【P1】调谐失败回滚漏掉频谱状态 → 谱轴/瀑布/悬停读数停在请求值

- 证据：谱面拖动先改 `gen.center_freq_hz` 再发信号
  （`spectrum_widget.py:3220-3235` → `_emit_tuned` 2910）；
  `main_window.py:5662-5667` 失败只调 `_revert_tune_ui()`（4751），后者只回写
  `freq_label / status_freq / control_panel`，**不调** `spectrum.set_center_freq()`。
- 后果：顶栏显示 98.5 MHz，谱面刻度、悬停频率读数、瀑布频率锚点却停在请求的
  100 MHz；`_update_rssi_from_iq` 还会按错位的 `gen.center_freq_hz` 取 bin，
  RSSI 跟着错。典型「显示值与真实后端分叉」。
- 建议：`_revert_tune_ui()` 里补 `spectrum.set_center_freq(last_hz / 1e6)` 与
  `_vfo_center_hz` 复原；谱面自身把「请求值」与 generator 状态分开存。

## 4. 【P1】云台死区抑制分支的记账参数嵌套错层 → 统计恒不写

- 证据：`main_window.py:~9339` 调 `self._apply_gimbal_result(ctx, {}, 0.0)`（传
  内层 `ctx`），而函数签名要 `task`，内部 `ctx = task.get("ctx") or {}`
  （~8025）→ `cmd=None`、`effective=None` → 早退（~8062）；外层 ~9341 还有
  `except Exception: pass`。
- 后果：死区/限速抑制的拍子本应按注释「仍按指令侧滞后记账」，实际一条都不写，
  `PointingErrorRecorder` 只统计真正下发的拍 → 过境误差系统性低估，异常也被静默。
- 建议：改为 `self._apply_gimbal_result({"ctx": ctx}, {}, 0.0)`；去掉裸
  `except: pass`（至少 `_log.warning`）；补一条「cmd=None 也 +1 条 cmd_error」单测。
- 顺带：`~8057`、`~8066` 的 `elapsed_s=0.5` 是硬编码，应传本拍真实
  `t_cycle − 上次 t_cycle`，否则 `elapsed_total_s` 恒等于「样本数 × 0.5」。

## 5. 【P1】NTP 授时跑在 GUI 线程，最坏单次冻结 ~15 s

- 证据：`main_window.py:~9469` `get_time_info(prefer_ntp=do_ntp)` 由 5 s 的
  `_sky_update_timer` 驱动、每 30 拍触发；`mbdsdr_ai/new_spacetime.py:381-393`
  顺序试 5 台服务器，`get_ntp_time(timeout=3.0)`（:309）且含 DNS 解析。
- 后果：断网或 UDP/123 被挡时，界面每 2.5 分钟卡死 ~15 s（DNS 超时还要叠加），
  期间点击无响应。
- 建议：NTP 放 `QThreadPool` / 后台线程，结果用信号回填；或只发一次异步请求、
  失败即把时间源标成 system。

## 6. 【P1】频谱重绘被三路驱动叠加到 ~146 帧/s

- 证据：`receive_pipeline.py:47` 块长 16384、2.4 MS/s → 146 帧/s；每帧
  `frame_ready` → `main_window.py:~6218` `_refresh_status_labels()` →
  `spectrum_widget.py:2671` `_redraw()`（两次 `update()`）。同时还有三个驱动源：
  `_ensure_repaint_timer` 33 ms（~6276）、FFT 控件自建 50 ms 定时器
  （`spectrum_widget.py:3599`）、瀑布 100 ms（3654）。
- 后果：单帧绘制实测 25 ms（`spectrum_widget.py:1372` 自注 900×400 45.8→25.0 ms），
  40 帧/s × 25 ms ≈ 一个核跑满，事件循环被饿死。
- 建议：`_refresh_status_labels` 从逐帧改为 5–10 Hz 节流（SNR 不需要 146 Hz）；
  只留 33 ms 一个驱动源。

## 7. 可复用给别的线的经验

- 这次审计里最容易漏的一类缺陷形态是「**死路径仍在被阅读**」：定时器没
  `start()`、回调挂在没启动的管道上、参数传错层导致函数早退后又用裸
  `except: pass` 兜住。它们在单测里都不会红（没人驱动那条路径），只有把
  "谁启动了这个定时器 / 这个返回值谁消费" 追一遍才会露出来。
- 建议 `main_window.py` 合并后加一条守卫：所有 `QTimer()` 创建点必须有对应
  `start(` 调用或显式注释「按需启动，由 X 触发」，可用 AST 扫描实现（本线可以
  写，但需要先把这份文件放行）。
