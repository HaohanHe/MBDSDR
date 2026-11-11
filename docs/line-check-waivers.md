# 行号断言白名单（`scripts/verify_claims.py --check-lines`）

> 只收「他线正在改、本线这轮不能动」的条目；每条必须带 owner + 到期日 + 原因。
> 条数上限写死在 `MAX_WAIVERS`（当前 1），**只减不增**：新增豁免必须改常量，形成评审可见的 diff。
> 到期、缺 owner、缺原因、重复、超上限，一律判失败。白名单为空时 `--check-lines` 自动转严格。

| 文档 | target | owner | 到期 | 原因 |
|---|---|---|---|---|
