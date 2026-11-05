# 场景 · 科学研究

> 可追溯数据链、金样对拍与误差留痕

## 目标

数据从采样到帧都要能复算：时间戳基准、软判决质量、FEC 纠错数、失败原因都要留痕，未验证项写进 not_verified。

## 可用的能力组合（真实工具域）

[SDR 调谐/VFO](../wiki/capabilities/sdr-vfo.md)、[系统设备](../wiki/capabilities/system.md)、[导出演制](../wiki/capabilities/export.md)、[解码器长尾](../wiki/capabilities/decoder-tail.md)

**能力而非流程**：以上是该场景常用的能力位，不是执行顺序。模型现场问设备，按回答装配任务；
同一个场景在不同现场会长成不同的样子。

## 可出示的证据

外部真值金样（WSJT-X / dump1090 / Meteor-M2 真录）、C++/Python 跨实现对拍、工具可用性审计台账。

## 模型可能调用的工具

模型可能调用 `get_status`、`start_recording`、`read_iq`、解码链工具；按观测目标动态装配。

## 诚实边界

- 无 TLE / 站址未校准时，不给出可信过境结论；
- 内核未连接时工具返回离线失败回执；
- 规划 / 未验证项逐条留档，不包装成已验证；
- 能力用可复跑命令证明，不用数字标语。

## 相关

- [产品规格](../01-product-spec.md) · [能力百科](../wiki/README.md)
- [原仓库对齐说明](../09-repo-alignment.md) · [路线图](../07-roadmap.md)
