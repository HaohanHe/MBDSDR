# 常见问题

## 能力矩阵里的条目是哪来的？

由 `build/sync_from_repo.py` **只读解析原仓库代码**生成：域名与工具来自
`mbdsdr_ai/core/mcp_server.py` 的 `FROZEN_DOMAIN_SUMMARIES` /
`PYTHON_EXTENSION_DOMAIN_SUMMARIES` / `_TOOL_DOMAIN`，设计令牌来自 `desktop/tokens.py`，
ControlHub 端口来自 `cpp/src/core/tokens.h`，时间倍率来自 `desktop/rf_sky_view.py`。
不是手抄设计稿——`docs/mcp_tools.md` 的自动生成标记区和 README 的域数都已经滞后。
详见 [原仓库对齐说明](../09-repo-alignment.md)。

## 为什么有的域写着「该面无工具」？

因为原仓库在该工具面上确实没有工具（VNA 测量、记忆编排），源码注释明确要求
「诚实空态」。产品保留空域、不塞假工具凑数。

## 为什么 6DOF / AR / VR 的开关点不动？

它们是**规划 / 未验证**能力：07 设计稿的 §9 Roadmap 与 `docs/audit_r2/27_gimbal_pose.md`
审计结论表明位姿闭环层是空壳、头显接入不存在。按 `AGENTS.md §2` 诚实文化，
能力矩阵里标徽标并禁用开关，不包装成已上线功能。

## 为什么专业模式的频谱是空的？

对齐 `desktop/spectrum_widget.py`：无 SDR / 无 IQ 时**只画网格 + 「未连接 SDR」提示**。
早期版本预填充过合成瀑布历史，属于伪造数据，已删除。接上 ControlHub 后谱行来自
`read_spectrum` 的真实回执；内核没返回数据就继续显示空态。

## 默认站点为什么是 `0.0, 0.0`？

`AGENTS.md §1` 身份红线禁止真实地理位置默认值。默认站址是中性示例且
`trusted=false`，复刻 `mbdsdr_ai/station.py` 的守卫：站址不可信时拒绝锁定与过境预报。
两份数据别混：`decoders.BUILTIN_TLE` 是历史凑整占位值（`builtin_tle_meta.py` 策略：禁止精确过境）；产品卫星天球轨迹用的是 `sat_tracker.py::_BUILTIN_TLES`，`BUILTIN_TLE_DATE` 标注的 Celestrak 真实抓取快照。轨迹按真实 SGP4 传播，但仍是**静态快照 + 地心方向**：未做站址视差，不给升落/过境结论。

## 状态栏为什么不再显示 Viterbi / RS / ASM 数字？

那些是仓库真录金样的历史口径，不是页面正在解码的实时值；把它们摆在状态栏
等于暗示实时链路，也违反「不硬编码能力数字」。需要看金样结果请查文档与可复跑命令。

## 怎么连上真实内核？

- **ControlHub**：桌面引擎启动后访问 `dist/index.html?hub=1`（或设 `window.MBDSDR_USE_HUB`），
  UI 走 `POST http://127.0.0.1:50732/command`。注意该通道只绑回环、无鉴权；
  从 `file://` 直连可能被浏览器 CORS 拦，正式部署建议由引擎同源托管或走宿主注入桥。
- **宿主注入桥**：Qt/WebEngine 注入 `window.mbdsdrBridge`（`invoke` / `satellites` 等）即可。
- **MCP**：外部 agent 走 `tools/list` / `tools/call`。

写工具的确认门**不在前端**：ControlHub 由 C++ 宿主写开关 `writeEnabled_` 决定，Python 内核只认外部确认通道签发的一次性 `confirm_token`（别名 `tx_token`）。**模型自报 `arm_manual` 明确无效**，前端会剥离它；缺 token 时返回 `manual_gate_required` / `gated:true`，UI 显示「未获确认（gated · 非失败）」，与执行失败严格区分。前端不签发 token、不自行放行。

## 为什么界面上找不到「定轨」？

因为**定轨不是功能**。用户明确要求：UI 上不得出现任何按钮、能力位、工具入口或推销话术。
唯一触发方式是**在对话里跟模型说**（AI 抽屉底部输入框 / 专业模式协作栏）。对话流里的
工具调用不算功能面。见 [01-product-spec.md](../01-product-spec.md) 铁律 4。

## 地景到底是动还是不动？

- **随相机**：拖拽方位、缩放、俯仰，地景都跟着动（长在世界坐标上）。
- **随时间**：不动。星空是 ICRF，随周日视运动转；地景固定在 OBSERVED 地平系。
- 时间倍率按钮（⏸ / ▶ / ×60 / ×300 / ×1800）来自桌面端同一套档位，点两下就能看清区别。

## 能不能直接用 Stellarium Web Engine 的代码？

不能。它是 AGPL-3.0，我们产品是 MIT：**只学机制、不引代码**。地景坐标系、FOV 缩放、
grab-the-sky、加法大气、呼吸指针都是思想层的数学与几何，全部独立重写；贴图/星表也未复用。
详见 [06-compliance.md](../06-compliance.md)。

## 语言切换后为什么有些文档还是中文？

文档有**独立**语言钮。主文档以中文为主，仅 README 提供英文/日文译本；缺译时文档中心
显示中文原文并明确标注「暂无该语言译本」，不假装已本地化。界面文案与对话模板是三语全量。
