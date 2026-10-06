# 第五十三阶段实现规格：接收链精载波恢复 + 稳定性定位 + 性能基线

> 基线：HEAD = 5002be9（已推送）。Phase52 诚实遗留三个缺口，把项目本体做厚。
> 遗留：陡扫频（0→200Hz 线性）仅 ~21/36 MCU（分段 AFC 小窗分辨率 vs 跟踪带宽矛盾）；RSS +4.8MB/min 慢漂移未定位（无 heaptrack/valgrind、OOM 预算）；主程序启动 ~11s 无系统基线。
> 复用：Phase52 afc_correct / notch_cw / _demod_resync（ssdv_phy.py、ccsds_ssdv.py）；Phase51 stress_iq_stream.sh + soak_rss.csv（RSS 89.5→137.6MB）；P38 曾测 MainWindow 构造 ~218ms。

## 块 1（最高优先，Python 域）：二阶 PLL 精载波恢复
- 分段 AFC 粗校正基础上加**二阶锁相环相位跟踪**（公开教科书算法干净室重写）；
- 合成线性扫 **0→200Hz / 0→500Hz**、真实 LEO 多普勒曲线全链实测：**跟踪残余相位/解码成功率 vs AFC-only 对比**真实数字；
- 仍失效区间（低 SNR×陡斜率）**诚实标 FAIL 并给理论解释**（PLL 带宽-噪声权衡）；gate 具名常量。

## 块 2（C++ 域）：RSS 慢漂移定位
- 代码审计 waterfall 固定 ring、audio sink 无设备重试、QTimer/事件累积路径；
- 写**轻量 heap 分配计数探针**（无 valgrind 条件下 std::allocator 统计或每 10s 对象计数），定位候选并修复或**如实标注根因未定位+已排除项**；
- 长跑复测给**真实 RSS 曲线（对比修复前后）**。

## 块 3（C++ 域）：性能基线
- 主程序启动时间分解（Qt 样式表/网络探测/AMR 初始化各自耗时——**以实测为准，P38 已证 C++ 无 AMR**）；
- 10 分钟持续流吞吐/CPU/内存基线，落 docs/learn/phase53/performance-baseline.md；
- 优化低成本项（延迟样式表应用、网络探测超时等），**改前改后数字对比**。

## 质量门 / 红线
- 干净室 MIT 不复制 GPL；无比赛字样；活动参数禁入；不预置 TLE；诚实空态不 mock；
- 8GB OOM（-j2，如实记录）；只暂存相关文件（禁 add -A）；**不 commit/push**；
- pytest 176/7 skip、flutter 384、ctest 129 不回归（全量输出实际计数）；
- 如实报告 file:line、各场景真实数字（改前改后）、未解决项。
