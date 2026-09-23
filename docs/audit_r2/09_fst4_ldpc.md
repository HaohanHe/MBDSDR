# 09 · mbdsdr_ai/fst4_ldpc.py 第二轮深度审查

- 审查对象：`mbdsdr_ai/fst4_ldpc.py`（80 行，wc -l 实测）
- 参考基线：`repos/wsjtx/lib/fst4/`（fst4_params.f90 / bpdecode240_101.f90 / decode240_101.f90 / genfst4.f90 / get_fst4_bitmetrics.f90 / fst4_decode.f90 / ldpc_240_101_parity.f90）
- 执行验证：`python3 mbdsdr_ai/fst4_ldpc.py` 实跑通过（N=240 M=139，全零码字迭代=1，翻 6 位后残留错=0）
- 审查方式：只读；逐行核对常量与 Fortran 参考源码，并数值复算 parity 数组维度。

---

## 0. 结论速览

| 维度 | 结论 |
|---|---|
| LDPC 码参数 (240,101) / M=139 | ✅ 正确，与 WSJT-X 完全一致 |
| Tanner 图解析（Mn/Nm/nrw） | ✅ 维度实测吻合（720 / 834 / 140[多1]），索引无错位 |
| 编解码完整性 | ⚠️ **只有译码**，无编码器、无 CRC24、无 rvec 反卷积、无解包 |
| 与 FT8 代码复用 | ⚠️ 与 `ft8_ldpc.py` 复制粘贴了同一份 BP 内核，算法一致但双份维护 |
| 实验室绿/真机红 | 🔴 **存在**：BP 早停无 CRC24 门限 + 缺 rvec 异或，自测全绿但与 WSJT-X 不兼容 |
| 空壳/占位 | 模块顶部 docstring 自承"帧结构/波形/CRC24/解包后续再接"，属明确占位，非伪装实现 |

---

## 1. FST4 协议常量核对（对照 WSJT-X 权威源码）

| 项 | WSJT-X 参考 | 本模块 | 判定 |
|---|---|---|---|
| 码字长 N | `decode240_101.f90:10` `N=240` | `fst4_ldpc.py:13` `_N=240` | ✅ |
| 信息位 K | `decode240_101.f90:10` `K=101`（=77 用户位 + 24 CRC，`fst4_params.f90:4` KK=77） | 未显式存 K，但 N-M=101 | ✅ |
| 校验方程 M | `M=N-K=139` | `fst4_ldpc.py:30` 由 Nm 自动推得 M=139（实测 834//6=139） | ✅ |
| 每变量节点度数 | `Mn(3,N)`（`bpdecode240_101.f90:11`） | `fst4_ldpc.py:31` 每 bit 连 3 个 check | ✅ |
| 每校验节点最大度数 | `Nm(6,M)`（`bpdecode240_101.f90:10`） | `fst4_ldpc.py:29` `maxb=max(nrw)=6` | ✅ |
| 校验度分布 | 实测 nrw 取值 {5,6}（剔除尾部 `ncw=3` 后） | 同 | ✅ |
| T/R=15/30/60/120/300/900/1800s 的符号数 | **符号数恒为 NN=160**（40 sync + 120 data，`fst4_params.f90:5-7`）；随时长变化的只是 `nsps`（`fst4sim.f90:52-58`：720/1680/3888/8200/21504/66560/134400） | LDPC 与 T/R 时长无关，硬编码 (240,101) 对全部时长成立 | ✅ 设计正确 |
| 调制 | 4 频移键控，每数据符号 2 bit（Gray：00→0,01→1,11→2,10→3，`genfst4.f90:85-97`）；120 data symbol × 2 = 240 coded bit | 模块不涉及波形/符号映射（LDPC 原语层） | ✅ 职责边界内 |
| 帧结构 | s8 d30 s8 d30 s8 d30 s8 d30 s8（`genfst4.f90:12`） | 未实现 | 🔵 占位，见 §4 |

**结论：LDPC 物理层常量全部正确。** FST4 所有 T/R 时长共用同一个 (240,101) 码，本模块不需要按时长分支。

---

## 2. Tanner 图解析逐行核对（`fst4_ldpc.py:16-36`）

用 Python 实跑解析 `ldpc_240_101_parity.f90`：

- `len(Mn)=720` = 240×3 ✅（`fst4_ldpc.py:28` `N=len(mn)//3=240`）
- `len(Nm)=834` = 6×139 ✅（`fst4_ldpc.py:30` `M=len(nm)//maxb=139`）
- Fortran 列主序映射：`Nm(6,M)` → 每 check 连续 6 个数，`fst4_ldpc.py:35` `nm[maxb*c+i]` 正确；`Mn(3,N)` → 每 bit 连续 3 个数，`fst4_ldpc.py:31` `mn[3*v+k]` 正确；1-based→0-based 减 1 正确。
- 交叉验证：Mn 侧反推每个 check 出现次数 = {5,6}，与 nrw 度分布一致，图无悬挂边。

### [建议] nrw 正则多吞一个 `ncw=3`，靠"侥幸"没出错
- 位置：`fst4_ldpc.py:26`
- 现象：parity 文件尾部（第 384 行起）除 nrw(139) 外还跟着一行 `ncw=3`（实跑 `len(nrw)=140`）。`re.findall(r"\d+", txt.split("data nrw/")[1])` 把这个 `3` 也抓进来了。
- 当前为何不爆：M 由 Nm 推出（=139），循环 `for c in range(M)` 只访问 `nrw[0:139]`，第 140 个元素（=3）恰好落在圈外没被用到。代码注释 `fst4_ldpc.py:30` 自己也写了"nrw 段可能多带一个数"。
- 风险：这是脆弱的容错，不是有意设计；若未来 M 的推导改成 `len(nrw)`，立刻越界/错位。建议显式截断 `nrw = nrw[:M]`。

---

## 3. BP 译码器与 WSJT-X 参考的差异（`fst4_ldpc.py:39-68`）

### [真bug / 实验室绿·真机红] 3.1 缺 CRC24 接受门限
- 本模块：`fst4_ldpc.py:66-67` 只要 139 个校验全满足就 `break` 返回，**不做任何 CRC 校验**。
- WSJT-X：`decode240_101.f90:74-88`，校验全满足后**必须** `call get_crc24(m101,101,nbadcrc)` 且 `nbadcrc==0` 才接受；否则继续迭代/转 OSD。
- 后果：AWGN 下 BP 经常收敛到"满足校验但不是发端码字"的 miscorrection。本模块把这类码字当成功返回；WSJT-X 会丢掉。自测（全零码字，CRC 自然为 0）永远绿，真机收到噪声帧会吐出假解码——典型实验室绿、真机红。
- 附带：`fst4_decode.f90:484-487` WSJT-X 还会**主动丢弃全零码字**（`count(cw.eq.1).eq.0 → cycle`），而本模块自测 `fst4_ldpc.py:75` 把"解出全零"当成功。自测覆盖的恰恰是真机要丢的路径。

### [真bug / 实验室绿·真机红] 3.2 缺 rvec 固定异或（77 信息位）
- WSJT-X 发端：`genfst4.f90:63` `msgbits(1:77)=mod(msgbits(1:77)+rvec,2)`，rvec 是 77 位固定伪随机序列（`genfst4.f90:29-31`），异或后才算 CRC、再编码。
- WSJT-X 收端：`fst4_decode.f90:488` `write(c77,'(77i1)') mod(message101(1:77)+rvec,2)` 异或回来。
- 本模块：`fst4_ldpc.py:68` 原样返回 240 位码字，**没有 rvec、没有 CRC24**。即使 BP 完美收敛，前 77 位信息也是被 rvec 打乱的，解包后必乱。
- 这是个极易漏掉的非标变换（不是普通解包），docstring 只笼统写"解包后续再接"。建议至少把 rvec 常量和 `get_crc24` 列为硬性 TODO。

### [建议] 3.3 译码算法是 min-sum×0.75 近似，非 WSJT-X 的精确和积
- 本模块：`fst4_ldpc.py:59` 归一化 min-sum，因子 0.75。
- WSJT-X：`bpdecode240_101.f90:95-104` 用精确 log-domain 和积 `tanh(-toc/2)` → 乘积 → `platanh` → `tov=2*y`。
- 两者解同一个 H，绝大多数帧结果一致；但在译码门限附近（FST4 的卖点就是弱信号）收敛行为不同，弱帧召回率会与 WSJT-X 有偏差。可接受，但要知道这不是逐位等价译码器。
- 另缺 OSD 兜底：WSJT-X 收端 `fst4_decode.f90:477-479` 用 `maxosd=2, norder=3`，BP 失败后跑 `osd240_101`；本模块无 OSD，BP 不收敛即放弃。

### [建议] 3.4 迭代次数 25 vs WSJT-X 30
- 本模块：`fst4_ldpc.py:39` `max_iter=25`。
- WSJT-X：`decode240_101.f90:27` `maxiterations=30`。
- 差 5 轮，弱帧可能差在收敛边界上，建议对齐到 30。

### [建议] 3.5 失败/成功无区分标志
- 本模块固定返回 `(bits, it)`；不收敛时 `it=max_iter`，与"真的在第 25 轮收敛"无法区分。WSJT-X 用 `nharderror=-1 / ntype=0` 表示失败。
- `agent.py:632-634` 把 `iters` 直接抛给上层，但没有 `converged` 布尔。建议返回 `converged`（是否满足全部校验）。

### [建议] 3.6 LLR 符号约定与 WSJT-X 原始 bitmetric 相反
- WSJT-X：`get_fst4_bitmetrics.f90:167` `where(bitmetrics>=0) hbits=1`；`bpdecode240_101.f90:47` `where(zn>0) cw=1` —— **正 LLR = 偏向 bit 1**。
- 本模块：`fst4_ldpc.py:40,63` 注释"正=偏0"，`bits=0 if total>=0 else 1` —— **正 LLR = 偏向 bit 0**，恰好相反。
- 项目内 FT8 侧是自洽的：`ft8_decode.py:34,48` 显式按"正=偏0"造 LLR（`E0-E1`）。但 FST4 **目前没有任何前端**（无 bitmetrics 模块），而 `agent.py:638-643` 的 MCP 工具描述只写"240 个信道 LLR"，**没写符号约定**。一旦后续照抄 WSJT-X `bitmetrics`（正=1）直接喂进来，输出会整体按位取反——又是一个实验室绿/真机红。建议在工具描述和 docstring 里钉死约定，或在入口处统一符号。

---

## 4. 完整性：只有 LDPC，编解码栈缺大半

| 组件 | WSJT-X 文件 | 本模块 | 判定 |
|---|---|---|---|
| BP 译码（H 矩阵） | bpdecode240_101.f90 | ✅ 有（近似版） | 见 §3 |
| 编码器 | encode240_101.f90 + ldpc_240_101_generator.f90 | ❌ 无 | 🔵 占位（docstring 自认） |
| CRC24 | get_crc24.f90 | ❌ 无 | 🔴 见 3.1 |
| rvec 77-bit 异或 | genfst4.f90:29-31,63 | ❌ 无 | 🔴 见 3.2 |
| 帧/同步字/2bit-Gray 映射 | genfst4.f90:99-107, get_fst4_bitmetrics.f90 | ❌ 无 | 🔵 占位 |
| FST4W (240,74) 码 | decode240_74.f90 / ldpc_240_74_parity.f90 | ❌ 未覆盖 | 🟡 见 §5 |

定位判断：docstring `fst4_ldpc.py:5` 明说"帧结构/波形/CRC24/解包后续再接"，属于**诚实的增量占位**，不是伪装成完整实现的空壳。但 §3.1/3.2 两个"真机红"点必须在接前端之前补，否则往返自测永远绿、对真机永远错。

---

## 5. FST4W (240,74) 未覆盖

- `fst4_ldpc.py:20` 硬编码 parity 路径 `ldpc_240_101_parity.f90`，`_N=240` 注释写死 (240,101)。
- WSJT-X 中 FST4W 用 **(240,74)**（M=166，50 信息位 + CRC24），见 `genfst4.f90:82` `call encode240_74(...)` 与 `fst4_decode.f90:496`。仓库里 `repos/wsjtx/lib/fst4/ldpc_240_74_parity.f90` 已存在。
- 判定：[建议] 若产品宣称支持 FST4W 需扩展；若只做 FST4 则现状可接受，但应在 docstring 注明"仅 FST4，不含 FST4W"。

---

## 6. 与 ft8_ldpc.py 的复用/重复审查

- `ft8_ldpc.py:67-103` 与 `fst4_ldpc.py:39-68` 是**同一份 min-sum BP 内核的复制粘贴**：同样的 dict 消息图、同样的 0.75 归一化、同样的变量更新 `q = total - r`。
- 一致性：两者算法逐行对齐，**没有"重复但不一致"的算法分叉**（这是好事）。
- 差异：ft8 侧硬编码 `_N=174,_K=91,_M=83`、Nm  stride=7（`ft8_ldpc.py:61-63`）；fst4 侧自动推断 N/M/maxb（`fst4_ldpc.py:28-30`）。fst4 的写法更通用。
- 判定：[建议] 应把 BP 内核抽成共用函数（输入 check_vars/var_checks），两个码只喂不同图；否则日后修 bug（如 3.1 的 CRC 门限、3.4 的迭代数）要改两处，必然漂移。

---

## 7. 问题清单（按严重度）

| # | 级别 | 位置 | 问题 |
|---|---|---|---|
| 1 | 🔴 [真bug] | `fst4_ldpc.py:66-67` | BP 早停无 CRC24 门限，miscorrection 被当成功；与 WSJT-X `decode240_101.f90:74-88` 行为不一致（实验室绿/真机红） |
| 2 | 🔴 [真bug] | `fst4_ldpc.py:68` | 未做 rvec(77) 异或还原（WSJT-X `fst4_decode.f90:488`），BP 完美收敛后信息位仍是乱的 |
| 3 | 🟡 [建议] | `fst4_ldpc.py:26` | nrw 正则吞入尾部 `ncw=3`（实得 140 个，应为 139），当前靠 M 取自 Nm 侥幸绕过；应显式截断 |
| 4 | 🟡 [建议] | `fst4_ldpc.py:59` | min-sum×0.75 ≠ WSJT-X 精确 tanh 和积，弱信号门限召回率有差；且无 OSD 兜底（WSJT-X `maxosd=2,norder=3`） |
| 5 | 🟡 [建议] | `fst4_ldpc.py:39` | `max_iter=25`，WSJT-X 为 30（`decode240_101.f90:27`） |
| 6 | 🟡 [建议] | `fst4_ldpc.py:68` / `agent.py:633` | 不收敛与第 25 轮收敛无法区分，缺 `converged` 标志 |
| 7 | 🟡 [建议] | `fst4_ldpc.py:40,63` | LLR 正=偏0，与 WSJT-X 原始 bitmetric（正=偏1）相反；FST4 无前端、工具描述未钉约定，集成即按位取反风险 |
| 8 | 🟡 [建议] | `fst4_ldpc.py:20` | 仅 (240,101)，未覆盖 FST4W (240,74)；应注明 |
| 9 | 🔵 [占位] | 全文件 | 无编码器/CRC24/rvec/帧同步/解包；docstring 已诚实声明"后续再接"，非伪装空壳 |
| 10 | 🟡 [建议] | `fst4_ldpc.py:39-68` ≈ `ft8_ldpc.py:67-103` | BP 内核双份复制，算法一致但应抽公共函数防漂移 |

## 8. 正面确认（做对的部分）
- (240,101)、M=139、Mn(3,240)/Nm(6,139)/nrw 与 WSJT-X 逐项吻合，列主序→0-based 转换正确，图无悬挂边。
- parity 文件路径解析健壮（`lru_cache`、`os.path.normpath`），实跑 N=240 M=139 正确。
- FST4 各 T/R 时长共用同一 LDPC 码这一事实判断正确，没有错误地按时长分支。
- dict 消息图 BP 实现干净，无下标错位；自测可跑通。
