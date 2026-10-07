# Phase50 性能基线：按需解调吞吐（Debug vs Release）

日期：2026-10-07 · 环境：云端 Linux，g++ 11.4，Qt 6.8.2 gcc_64，-j4

## 测量方法（真实、确定性、非硬件合成）
`cpp/tests/test_phase50_throughput.cpp`：合成确定性宽带 FM IQ（双载波 @+100kHz/-300kHz，
1.0/2.0kHz 调制音，dev 3kHz），源采样率 2.4 MHz，**10 秒实时样本**喂入
`VfoManager::process`（channelizer + 模拟解调），按稳态墙钟给出实时倍率
（= 喂入样本时长 / 处理墙钟；>1 即快于实时）。场景覆盖 #50 的核心问题：
活动 VFO 数（selected + armed）对解调 CPU 的影响，以及 idle/orphan VFO 是否零成本。

## 实测数字（独立复跑一致）

| 场景 | Debug | Release(-O2) | Release/Debug |
|---|---|---|---|
| 单 VFO（selected，按需解调基线） | **0.51x**（10s→19.7s） | **8.52x**（10s→1.17s） | 16.7× |
| 4 VFO（selected+3 armed） | 0.17x（10s→59.2s） | 2.79x（10s→3.58s） | 16.5× |
| 9 VFO（selected+8 armed） | 0.06x（10s→157.9s） | 1.05x（10s→9.52s） | 17.1× |
| 1-of-9（8 idle，无 armed） | 0.51x（10s→19.7s） | 8.23x（10s→1.22s） | 16.1× |

## 结论
1. **Release 单通道 8.5× 实时，9 个活动 VFO 仍 1.05× 实时**——吞吐能力已满足
   实机需求，**channelizer 缓冲复用优化无必要**（诚实结论：不做无谓优化）。
2. **解调 CPU 随活动 VFO 数近似线性增长**（1→4→9：8.5→2.8→1.1×），与"每个
   active 通道独立 channelizer+demod"的设计一致。
3. **idle/orphan VFO 零成本**（1-of-9 ≈ 单 VFO，8.23x vs 8.52x）——#50 按需解调
   生效的实证：不 armed 的 VFO 不进 channelizer/demod 热路径。
4. Debug 慢于实时（0.51x）是 QtWidgets 桌面 Debug 构建的预期状态；发布构建走
   Release（-O2）。注：此前"Debug 单通道约 3× 实时"的留档基于不同的测量配置
   （引擎整体 / 较低源采样率），本次为 VfoManager::process 纯解调链、2.4MHz 源，
   以本次数字为准。

## 验证
- Release(-O2) 全量 ctest：133 基线全过 + 新增 phase50_throughput 通过
  （device_ui 在 ctest -j4 并行下有 1 次间歇失败，串行单跑 Passed 47.45s——
  并行端口/时序冲突，非 Release 回归）。
- 测试目标 `test_phase50_throughput`（AUTOMOC OFF，仅链接 mbdsdr_core）已在
  CMakeLists 注册为 `phase50_throughput` 测试。
