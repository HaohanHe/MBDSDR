# device_ui drop 停摆复现固证（phase79，2026-10-24）

## 触发经过

- 动作：`make test_device_ui` 重建（phase79 慢路径告警版）后，**隔离单跑第一轮**
  （`QT_QPA_PLATFORM=offscreen ./test_device_ui`）即复现——此前 11/11 按需全绿。
- 本机有 CJK 字体、无额外负载；同命令概率性触发（非必现）。

## 原始证据（phase77 自证插桩输出，逐字固证）

```
drop-wait detail: optionsEmpty=0 comboDisabled=0 waited=18405 ms
  (window 8000; slowThreshold=1500; everOptionsEmpty=0 everComboDisabled=0;
   each poll processEvents(AllEvents, 60))
FAIL: drop: combo back to empty + disabled after device pull
```

固证纪律教训（升级 phase76）：本次用 `grep` 过滤输出，只留了匹配行，完整
stdout 丢失。后续复现运行一律整体重定向到样本文件，不过滤。

## 数据解读（三条，每条都改变认知）

1. **waited=18405ms > window=8000ms**：`waitFor` 的循环体
   `QCoreApplication::processEvents(QEventLoop::AllEvents, 60)` 的 60ms 是
   「最长处理时长」而非「保证返回时长」——单次 processEvents 被某事件槽阻塞
   ~10.4s 时，整个等待被拖到 18.4s。**「超 8s 窗口」的表象自此有了解释：
   不是窗口不够，是事件循环发生了 ~10s 级停摆。**
2. **everOptionsEmpty=0 且 everComboDisabled=0**：整个等待期间两腿从未满足
   过——不是「刷清后又回填」（那条路径 ever 会为 1），而是 UI 状态在整个
   停摆期内一次都没刷新到目标态。
3. **停摆掩盖了断连链路本身**：readIQ EOF → 20 帧 × 20ms → dropSourceLocked
   → installIdleSourceLocked → sourceChanged → MainWindow 延迟 0 定时器刷新
   （理论 ~0.5s）全部排在事件队列里干等。链路预算从来不是瓶颈。

## 与历史三见的统一

phase72/74/76 三见均报「Phase 3 超 8s 窗口」但无现场；本次自证数据把形态
从「窗口可能不够」精确为「事件循环 ~10s 停摆」。**phase40 不加宽窗口的决议
因此更加正确——加宽窗口只会让停摆被更久地掩盖。**

## 停摆源（假设排查台账，phase79 进行中）

| # | 假设 | 状态 | 证据 |
|---|---|---|---|
| 1 | pred() 内 harness getter 持锁卡住 | **排除** | harnessSampleRateOptions/Enabled/DeviceName 全是纯 UI 控件读取，无锁无引擎访问（main_window.cpp:5583-5599） |
| 2 | UI 等引擎 sourceMutex_（readIQ 持锁饿死） | **基本排除** | RtlTcpSource::readIQ 内 doPoll 带 50ms 超时（rtl_tcp_source.cpp:290），引擎持锁 ≤ ~50ms+处理；UI 不可能等 10s |
| 3 | reconnectRequested queued 到 UI 做 TCP 重连 | **排除** | 槽首行 `if (!reconnectPending_ \|\| !autoReconnect_.load()) return;`（spectrum_engine.cpp:798），测试未开自动重连 |
| 4 | refreshDeviceCapabilities 卡 capsMutex_ | **排除** | sourceCapabilities 走独立 capsMutex_ 快照，设计上就为「never take sourceMutex_」（spectrum_engine.cpp:182-187, h:971-977） |
| 5 | onSourceChanged 同步段 refreshSpacetimeView 重活 | **排除** | 全是 spTile*/setText 轻量更新（main_window.cpp:6837-6892） |
| 6 | QMediaDevices 探测触发 PulseAudio autospawn 挂起（phase40 原始登记机制） | **本环境排除** | audio_probe 实测：`defaultAudioOutput: 0 ms, isNull=1`，PulseAudioService 连接快速失败无 spawn 握手；本机装着 pulseaudio 二进制但无 PA 进程、无 dbus session，autospawn 不起 |
| 7 | 1Hz telemetry 槽重活 | 排查中 | onSourceChanged 之外的事件队列常客 |
| 8 | 系统级停顿（cgroup/调度/换页） | 待现场 | 若 gdb 栈显示多线程同卡在 futex/无业务栈，即此因；1/53 概率 + 10s 量级与之相容 |

下一步：ctest -j4 并行套件循环猎杀（历史三见的共同现场），单轮超 12s 即 gdb
全线程栈；失败轮立即归档 LastTest.log（防覆盖）。

## 样本积累

复现样本（含完整 stdout）存于 artifact 目录 deviceui-samples/，随本轮工作
提交前汇总统计复现率。
