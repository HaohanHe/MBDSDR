# 构建性能根因定位 — 全量构建从 8h+ 降到 7.2 分钟

## 结论（掌舵修正）
任务曾假设全量 Debug 构建慢（3.5–4h）的根因是"重型 TU（spectrum_engine /
vfo_manager / tle_client）被约 130 个测试目标逐目标重复编译"，并计划做静态库
重构或接 ccache。**实测推翻该假设**：真正瓶颈是**构建所在文件系统的 I/O 延迟**，
不是 CMake 的源重复结构。因此高风险的静态库重构 / ccache 接入**不做**（在快盘上
重复编译只占 7.2 分钟，收益不抵风险）——科学理性务实。

## 证据（云端实测）
1. `/home/user` 挂载类型为 **hpvs_fs**（虚拟化/网络文件系统，FUSE 类）；`/tmp`
   与 `/` 是本地 overlay（kata 块设备）。
2. 在 hpvs_fs 上构建时：`load average` 仅 **0.80**（-j4 应接近 4），4 个 cc1plus
   进程 CPU 都只有 **3.0–3.5%**、进程状态 **S（睡眠）**——编译器在等文件 I/O
   （大量小头文件 open/stat/read 的 FUSE 往返延迟），不是 CPU 算力不足。
3. 文件系统吞吐对比（dd，200MB）：
   | 位置 | 写 | 读 |
   |---|---|---|
   | /home/user (hpvs_fs) | 190 MB/s | 121 MB/s |
   | /tmp (本地 overlay) | **3.8 GB/s** | **13.7 GB/s** |
   顺序大块差异 ~20×；编译器实际是**海量小文件/元数据**操作，hpvs_fs 的每操作
   延迟惩罚远大于顺序吞吐所示（这才是 -j4 被串行化、load 0.8 的根因）。
4. **本地盘全量构建实测**：`cmake --build build -j4` → **rc=0、429s（7.2 分钟）、
   0 error、全部 133 目标建成**。对比 hpvs_fs：跑到 71% 已耗约 3 小时、全量估计
   8 小时以上。**端到端提速约 60 倍**，且此时 -j4 真正 CPU 并行。

## 正确的构建/测试方法（已脚本化）
关键约束：`/tmp` 虽快，但是**每个 Bash 调用 / 后台任务私有的、跨调用不保留**
（持久的只有 hpvs_fs /home/user）。因此必须在**单个后台实例内一气呵成**完成
"复制源码到快盘 → 配置 → 构建 → ctest"，不能把 build 和 ctest 拆到不同调用
（否则下个实例看不到 /tmp 产物）。

脚本：`tools/build_local_fast.sh`（本提交引入；仓库根自定位、Qt 路径可用 QT6_HOME 覆盖）
- rsync 源码（排除 cpp/build）到实例私有 `/tmp/MBDSWRUN`
- 在 /tmp 配置 + `cmake --build -j4`（实测 ~7 分钟）
- 同实例内 `QT_QPA_PLATFORM=offscreen ctest --output-on-failure`
- 关键结果（rc / 各阶段耗时）走 stdout；build 详细日志留私有 /tmp、仅失败时 dump

用法（后台单任务，stdout 由调用方捕获，勿再重定向到私有 /tmp）：
```
bash tools/build_local_fast.sh
```

## 诚实未完成项
- **全量 ctest 133 的 133/133 结果尚未取得**：本阶段两次单实例运行都在 build
  完成后、ctest 跑完前被 2 小时墙钟预算回收（build ~9min（含 rsync/configure）+
  ctest 全量串行（含 NAVTEX 48s、多组 soak/spectrum 长测试）合计接近/超出剩余
  预算）。下一轮应**直接**启动 `tools/build_local_fast.sh` 并给足不被打断的时间窗，
  即可一次拿到 build 429s + ctest 133 真实结果。
- 本地盘 /tmp 容量 9.7GB、单实例全量 build 产物约 7.3GB，足够；/dev/shm 仅
  225MB tmpfs，不可用于构建。
- 该结论针对云端 hpvs_fs 环境；常规 Linux 本机（本地 ext4/xfs）无此 I/O 问题，
  正常在源码树构建即可，无需本脚本。
