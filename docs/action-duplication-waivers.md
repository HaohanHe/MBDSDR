# 命令/QAction 重复注册闸门白名单：在途豁免

> 校验命令：`python scripts/check_action_duplication.py --gate`
> 规则与判据见脚本头部 docstring 与 `scripts/release_check.py` 的 `action_dup` 步。

**这里只允许挂「他线正在改、本线这轮不能动」的条目。** 每条必须给 owner 与到期日，
到期未清即判失败；缺 owner／缺原因／日期格式不对／重复 target，一律判失败。

条数上限写死在 `scripts/check_action_duplication.py` 的 `MAX_WAIVERS`，**只减不增**：
新增豁免必须改这个常量，属于评审可见的 diff——不许往表里悄悄加行再放行。
白名单清零后闸门自然不再报违规（这是判据描述，不是当前树实跑记录），不需要额外开关。

target 两种写法：
- 命令标识（如 `shortcut:tools.record`）——对应「多实例驱动同一命令」；
- 条目显示名（如 `星象仪模式`）——对应「同名但命令标识不同」。

| target | owner | 到期 | 原因 |
|---|---|---|---|
