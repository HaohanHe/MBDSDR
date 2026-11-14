# 文档断言白名单（存量豁免，只减不增）

> 门禁：`tests/test_claims_guard.py`。`docs/` 下任何「已修复/已提交/已落地」类断言，
> 四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 不完整即红；
> 本表只登记**建表时 HEAD 已提交**的未证实断言，逐条写「为什么暂不补证」。
> 销账方式：补四元组（变可证实）或删断言 → 从本表删行并把 `waived-total` 同步调小；
> **不许新增行**（只减不增）。新建/改写断言一律按红处理，先补齐四元组再提交。
>
> 指纹 = sha256(`文档\0断言原文`)[:12]；断言原文改动即指纹变化 = 按新增判红。
> 在途未提交的 docs 改动不享本白名单——那正是门禁要拦的「修改后无证据」。

<!-- waived-total: 12 -->

建表口径：HEAD 断言 144 条 = 可证实 2 + 文档过期 46 + 无 hash 96；白名单只覆盖后两类。

> 证据等级 E4：本段数字未验证，引用前须复核（判据见 docs/evidence-standard.md）。

| # | 文档 | 指纹 | 断言摘要 | 暂不补证理由 |
|---|------|------|----------|--------------|
| 1 | docs/COORDINATION.md | c7357a8b2b36 | Gardner 环修复版已改进 lrpt_modem.py（中点插值+归一+钳制+热启动， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 2 | docs/COORDINATION.md | 6e4913bfc99d | 已改为**让产品从原仓库代码长出来**：`build/sync_from_repo.py` 只读 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 3 | docs/COORDINATION.md | c8503b24ac33 | 已改：`31-celestial.js` 换「相机在球心 → 视图空间(X/Y/Z) → 全屏平面投影」， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 5 | docs/COORDINATION.md | 33211081caac | GPScacodes.py 裁决更正（标准码的反极性版本，原"非标准"判词有误）； | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 6 | docs/COORDINATION.md | ad3e2bc78eab | 扫描器自指命中（HEAD 干净，`git grep` 无命中）；本条已改写为不引字 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 8 | docs/COORDINATION.md | b1f0ec5d9d1f | 现予以更正）： | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 16 | docs/COORDINATION.md | d8cb2fdab266 | 95 passed，agent 采样相关 21 passed。文档 §10-3 已改为"已落地"。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 47 | docs/design-basis-sky-figma-note.md | 3eea4e7e7446 | **对「334×334 大圆」这一描述的更正**：`284_177/index.module.scss` 写的是 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 52 | docs/hardcoded-parameters.md | 707ff33e1312 | - **A11 调谐步进**：**更正**——C++ 调谐步进本就是 10 kHz（§D-2 撤回）， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 64 | docs/limitations-register.md | 3ca1335288b3 | enum 校验先于 handler），**真红、已修**； | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 93 | docs/reference-mechanisms.md | 815c7035b8d7 | > **2026-11-05 更正**：`reference/` 下不存在 `decoders`、`blackmagic` 目录；`satdump` 已在本轮补读。见 §8。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 96 | docs/reference-mechanisms.md | a8b23ef93460 | > - 「决定」三态：**照做**（机制已按参考思路落地，保持）／**改良后做**（路线不同或只落一半，已落地或已排给某条线）／**明确不做**（给理由与可追踪的挂账/验收门）。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
