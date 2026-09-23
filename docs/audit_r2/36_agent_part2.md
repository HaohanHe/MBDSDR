# 36 · agent.py 后半部分审查（1501–2956 行）

- 审查对象：`mbdsdr_ai/agent.py` 第 1501–2956 行（工具注册收尾 + `chat()` 主循环 + 压缩回调 + 状态/快捷命令）
- 交叉阅读：`tool_registry.py:255–430`、`context_manager.py:24–357`、`model_manager.py:319–520`、`pose.py:428/483`
- 只读审查，未修改任何代码

---

## 一、真 bug

### B1. `file_change_revert` 同一变更被回滚两次，且失败也报 success=True
`agent.py:1942`
```python
handler=lambda args: ToolResult(success=True, content=json.dumps(
    ft.revert_to(..., file_writer=lambda p,c: open(p,"w",encoding="utf-8").write(c)).to_dict()
    if ft.revert_to(..., file_writer=lambda p,c: open(p,"w",encoding="utf-8").write(c))
    else {"error": "变更不存在"}, ...)),
```
- 条件表达式先求值 `if ft.revert_to(...)` —— **第一次回滚已经发生、文件已被覆写**；条件为真后分支里再调 `ft.revert_to(...)` —— **第二次执行**。若 `revert_to` 消费式地标记变更（弹出/置为已回滚），第二次返回 `None`，`.to_dict()` 直接 `AttributeError`，被外层 `tool_registry.call` 捕获后变成 "调用失败"，即"成功路径反而报错"。
- 无论回滚成败，外层硬编码 `ToolResult(success=True)`；`else` 分支的 `{"error":"变更不存在"}` 也被包在 success=True 里喂给 LLM —— LLM 会误以为回滚成功。
- `file_writer=lambda p,c: open(p,"w",...).write(c)`：文件句柄不关闭（泄漏），且先截断 `open(p,"w")` 再写内容，无原子写/无临时文件换名，写到一半进程死了 = 文件变空。

### B2. `astro_pointing_guidance` 死代码 `if False`，beamwidth 参数被丢弃
`agent.py:2537`
```python
... compute_pointing_guidance(..., AntennaParams(beamwidth_deg=args.get("beamwidth_deg",5)) if False else None)
```
- `if False` 恒为 `None`：LLM 按 schema 传的 `beamwidth_deg`（波束宽度，决定"是否在波束内"判定）被无条件丢弃，工具描述却承诺"输出是否在波束内"。[占位] 同时建议直接删除该三元表达式。

### B3. 工具循环跑满 max_tool_rounds 时静默返回空回复
`agent.py:2681`（`for round_num in range(max_tool_rounds)`，默认 10）
- 只有"无 tool_calls"（2735）或 LLM 报错（2716）才 `break`。若 10 轮每轮都在调工具，循环自然耗尽，`final_content` 仍为 `""`，2802 返回 `content=""`、`error=None`。用户/上层拿到一条空回答，且模型没有收到任何"轮次已用尽，请直接给结论"的反馈。
- 应在循环后检测"耗尽未 break"，注入一条系统消息要求模型基于已有工具结果直接作答。

### B4. `self.wr` 从未赋值，工作流录制靠 bare except 静默空转
`agent.py:1414-1417`（背景已报，此处确认机制）
```python
try:
    self.wr.record_tool_call(tool_name, params, result.success)
except Exception:
    pass
```
- 全文件只有 `agent.py:122 self.workflow_recorder = WorkflowRecorder()`，**不存在 `self.wr`**。此处每次工作流内工具调用都抛 `AttributeError`，被 bare except 吞掉。录制功能 100% 空转且无任何日志痕迹——这正是"静默失败"的典型。bare except 还会掩盖未来任何真异常。

### B5. 上下文压缩可能拆散 assistant(tool_calls) 与 tool 消息，破坏 API 契约
`context_manager.py:283-290`（由 `agent.py:2683/2792` 触发）
- 压缩从尾部累加保留消息，`if ... and kept: break` 的截断点可能正好落在"assistant 带 tool_calls"与其后的若干 `tool` 消息之间。OpenAI 兼容 API 要求 assistant 的每个 tool_call 都有对应的 tool 消息；截断后下一轮 `build_api_messages` 会发出孤儿 tool_calls → API 400。压缩循环应保证"不拆散一个完整的 tool-call 组"。

---

## 二、空壳 / 占位

### S1. pose 全套数据没有硬件读取路径，全靠 LLM 手填 JSON
`agent.py:1748-1831`；`pose.py:428 update_imu / 483 get_pose`
- `pose_update_imu`（1774）：`pf.update_imu(IMUData(**args))` —— accel/gyro/mag 9 个数字全部来自 LLM 的 JSON 参数；`pose.py` 内无任何 I2C/SPI/serial 读取（grep 无 read_/serial/i2c）。工具描述却写"数据来自 BMI260+TMAG5273"。
- `pose_update_gps`（1795）：经纬度/速度/卫星数同样由 LLM 填写，描述称"来自 ATGM336H"。
- `pose_get`（1752）只是把上面累积的状态回显。
- 结论：真机上若没有任何驱动线程把传感器数据推进 `pose_fusion`，6DOF 位姿 = LLM 想象出来的数字。AR 投影（1814/1829）建立在这个虚构位姿上。agent.py 本文件内看不到任何硬件→pose_fusion 的数据线（需 hal/gnss_monitor 侧补推）。

### S2. AMR 空输入即模拟正弦波，且以 success 返回——"实验室绿"实锤
`agent.py:2564`（`amr_extract_features` 2579 同构）
```python
amr.classify_iq([complex(...) for i in range(0, len(...)-1, 2)]
  if args.get("iq_samples")
  else [complex(math.cos(2*math.pi*0.01*t), math.sin(2*math.pi*0.01*t)) for t in range(1000)], ...)
```
- LLM 不传 `iq_samples`（或传空数组）时，工具**静默改用一个内置旋转正弦波**做"分类"，返回 success + 置信度。LLM 无法区分"我分析了真实信号"和"我分析了 demo 信号"。真机解码链路（IQ 数组）同样要求 LLM 把原始采样当 JSON 传参——物理上不可能（百万级采样点不可能过上下文），与背景判断一致。

### S3. `judge_evaluate` 第 5 个位置参数写死空串
`agent.py:2062`：`j.judge(question, answer, tool_calls, "", use_llm)` —— 第四个参数恒为 `""`（疑为 session/user 标识），占位调用。

---

## 三、安全

### P1. 无任何 prompt-injection 防护
- 工具结果经 `agent.py:2783 add_tool_message` 原样写入历史；`SYSTEM_PROMPT`（`context_manager.py:24-41`）没有任何"工具输出是数据、不可当作指令"的约束。某个工具（或录制的基带元数据/网页/卫星 TLE 文件里）若嵌入"忽略以上 system 指令，改为……"，下一轮 LLM 调用即受其影响。
- 放大因素：`agent.py:2668-2669` 把 `memory.build_memory_context()` 的检索结果**直接拼进 system prompt**——记忆库本身成为注入通道（早期被诱导写入的内容会在后续每轮 system 里复活）。

### P2. 高危自修改工具无权限闸口
- `code_modify_file`（2290）、`code_modify_section`（2307）、`code_hot_reload`（2337，importlib 重载任意模块）、`code_git_commit`（2353）、`file_change_revert`（1942）、`plugin_install`（2041，从任意 source_path 复制文件）均对 LLM 开放，agent.py 层无路径白名单、无确认回调。system prompt 写"复杂操作先确认再执行"（`context_manager.py:33`），但代码里没有任何强制确认点——纯靠模型自觉。

### P3. 副作用工具在临时错误时被无差别重试 3 次
`agent.py:2755-2763`：RETRY_OK 关键词命中即 `sleep(0.4)` 重调 `call_from_model(tc)`，最多 3 次。对非幂等工具（`workflow_record_start`、`scheduler_add_task`、录制/写文件类）没有幂等键，"暂时忙"被重试 = 重复副作用。

### P4. 重复调用提醒（nudge）顺序错误，审计与上下文不一致
`agent.py:2764` 先 `all_tool_results.append(result.to_dict())`，2780 才把 `[guard]…` 拼进 `result.content`。返回值里的 tool_results 不含 nudge，而喂给模型的上下文含 nudge——回放/审计看到的历史与模型当时看到的不一致。

---

## 四、建议（非阻断）

1. **`_repeat_key/_repeat_count` 跨会话泄漏**：初始化于 `agent.py:240-241`，`chat()` 内从不重置；且只防"连续同一调用"，A/B/A/B 交替可绕过（2772）。建议每次 `chat()` 开头清零。
2. **压缩回调额外阻塞一次 LLM 调用**：`_compaction_callback`（2854-2868）在主循环内同步调 `chat()`，且把 `history_text[:3000]` 硬截断——更早的细节静默丢失；失败时只留一行兜底字符串。
3. **流式吐字与兜底剥离不一致**：弱模型兜底在 2732 才从 content 剥掉 `<tool_call>`/JSON，但 `on_delta`（2701-2705）早已把原始工具调用文本推给用户界面。
4. **重试关键词表脆弱**（2751-2754）：中文子串匹配（"必须/为空/找不到" vs "未连接/忙/unavailable"），"设备必须连接"这类句子同时含"必须"与"未就绪"，归类依赖优先级。建议改用结构化 error code（tool_registry 已经产出 `error` 字段）而不是匹配中文文案。
5. **嵌套子代理共享不了预算**：`subagent_execute`（1731）同步等待最长 120s，子代理内部又有自己的 max_tool_rounds——主循环的 10 轮预算不约束子代理，延迟与 token 堆叠不可控。
6. **调度器 lambda 直传 schema 键**（1513）：`sched.add_task(**args)` 把 JSON schema 字段原样透传，签名漂移时只能靠通用异常兜底。

---

## 五、做得对的地方（避免误报）

- `tool_registry.py:307-326`：required 参数预校验，缺参返回结构化错误喂回模型，不让 handler 抛 KeyError。
- `tool_registry.py:403-411`：arguments JSON 解析失败有单引号/Python 布尔修复兜底。
- `tool_registry.py:374-383`：超长工具输出 head 60% + tail 40% 裁剪，保留表头与结论。
- 弱模型兜底解析（`model_manager.py:489+`）会合成 `call_text_{i}` 的 id（483 行），tool_call_id 与 tool 消息能正确配对——此处无 bug。
- 重复调用 guard、临时错误重试分层（参数错不空转）的设计意图正确，只是实现细节如上。

---

## 严重度汇总

| 级别 | 条目 |
|---|---|
| 真 bug | B1 回滚双执行+假成功；B2 `if False` 死代码；B3 轮次用尽空回复；B4 `self.wr` 空转（背景确认）；B5 压缩拆散 tool 消息组 |
| 空壳 | S1 pose 全靠 LLM 填数（背景确认）；S2 AMR 模拟输入 success；S3 judge 占位空串 |
| 安全 | P1 无注入防护；P2 自修改无闸口；P3 非幂等重试；P4 nudge 审计不一致 |
