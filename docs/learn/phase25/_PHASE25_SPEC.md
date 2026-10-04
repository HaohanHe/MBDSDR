# 第二十五阶段实现规格：ControlHub HTTP JSON 端点 + 移动端解码面板回补

> 基线：HEAD = ba83d4d（已推送），ctest 117 注册/116 过（e2e_smoke 环境性红不计）、flutter 309、pytest 42+。
>
> 勘察事实：ControlHub（control_hub.h）32 命令仅本地进程内可用、读写分离+写门（setWriteEnabled）；mobile 已有 rtl_tcp_client（裸 IQ TCP），无 HTTP 层。

## 1. ControlHub HTTP JSON 端点（独占 cpp/src/control/ + 测试）
- **只读**：`GET /status` → get_status 全量（五态+error_message+模式/频率/带宽/解码结果快照）；`GET /pocsag_messages`、`GET /m17_calls`、`GET /vor_radial` → 三 decoder 只读结果（复用 SpectrumEngine 冻结接口）。
- **写**：`POST /command {"tool":...,"args":{...}}` → 走 ControlHub 既有 execute()（同一写门/AI gate），返回 JSON。
- **安全**：默认仅 127.0.0.1 + 可配置端口（QSettings/env）；无鉴权但有警告文案；端口不硬编码（tokens 弹性派生）。
- **诚实空态**：无设备/无解码数据 → 明确空态（null/[]），不伪造。
- **ctest**：loopback 起服务→GET status 真实字段→POST set_frequency 引擎真实改变→手动模式 gate 拦截→无设备诚实空态。
- **冻结给移动端的 HTTP 契约**（回报时给出精确 JSON 形状）：
  - `GET /status` → `{connected, status, error_message, mode, frequency_hz, bandwidth_hz, ...}`
  - `GET /pocsag_messages` → `{count, messages:[{address,function,text,type}]}`；m17 → `{count, calls:[{src,dst,type,is_stream,crc_ok,voice_undecoded,meta,payload}]}`；vor → `{locked, radial_deg, quality, morse_id}`；无数据 → count:0/[]/locked:false。

## 2. 移动端解码面板回补（独占 mobile/lib/ + mobile/test/）
- 通过 HTTP 端点读桌面 ControlHub 三解码结果 → 面板显示真实远程结果；定位「远程只读展示」（解码在桌面引擎，移动端是查看器），UI 标注「远程引擎 · POCSAG」等来源。
- 服务不可达/无数据 → 诚实空态 + 重试；不伪造。
- Flutter 测试：mock HTTP 客户端返回真实结构→面板渲染断言；服务不可达→空态断言；analyze 0、309 不回归。

## 质量门 / 红线
- 干净室 MIT、不复制 GPL；通用非专用；不硬编码/不预存；无硬件诚实空态、合成仅测试夹具；活动参数禁入通用代码。
- A 给 ctest 全量输出（117 基线+新增）；B 给 flutter test 全量 + analyze 0；只暂存相关文件（禁 add -A）；**不自行 push**；如实报告 file:line、未解决项（MainAgent 全量复核）。
