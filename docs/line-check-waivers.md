# 第 4 步（行号内容校验）白名单：在途豁免

> 校验命令：`python scripts/verify_claims.py --check-lines --strict-lines`
> 闸门规则与四态定义见 `docs/evidence-standard.md §7.7`。

**这里只允许挂「他线正在改、本线这轮不能动」的条目。** 每条必须给 owner 与到期日，
到期未清即判失败；缺 owner／缺原因／日期格式不对／重复，一律判失败。

条数上限写死在 `scripts/verify_claims.py` 的 `MAX_WAIVERS`（当前 1），**只减不增**：
新增豁免必须改这个常量，属于评审可见的 diff——不许往表里悄悄加行再放行。
白名单清零后 `--check-lines` 的严格模式自动生效（不需要有人记得加 `--strict-lines`）。

| 文档 | target | owner | 到期 | 原因 |
|---|---|---|---|---|
| docs/COORDINATION.md | mbdsdr_ai/tool_registry.py:525 | 机制线（COORDINATION 维护方） | 2026-11-14 | 该文件有他线 49 行在途改动，本轮不得插入；`:136` 两条补充（挂 pending）待其落库后一并改，届时本条应随之为绿并删除 |
