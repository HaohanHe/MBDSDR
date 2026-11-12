# W92 降级路径实测（2026-11-12）

> 结论：四条降级路径 + 只读/不可读缓存实测都**不会让信息卡变空表**，人工 8 星
> 9 条链路照常可用。实测同时抓到 4 个真实问题（写失败谎报成功、并发丢星、
> 临时文件残留、读冲突被 memo 化），已在 `mbdsdr_ai/sat_links_fetch.py`
> 修复并补回归测试；并发极端场景仍有低频「单轮写失败」，现在会如实报
> `write_failed` 并保留旧缓存。
>
> 证据级别：E2（可复跑的命令 + 入库原始输出）。原始件：
> - `docs/evidence/w92-degrade-paths-20261112.json`
>   SHA256 `CCC0C435BC7DA56B2286722E5A49C2B84BDFD8FDA9C2618919596EB6B73651D4`
> - `docs/evidence/w92-concurrency-20261112.json`
>   SHA256 `47B6FB25C470F2F6F757C6882F92E1F16BA0CD5051FDD59F6610A488FF81B50E`
>
> 采集条件：Windows 10、本机 Python、`MBDSDR_OFFLINE` 未置位；
> 网络场景用代理黑洞模拟，未真拔网线。

## 1. 模拟方式（不是推理）

| 场景 | 怎么做的 |
| --- | --- |
| 缓存不存在 | `MBDSDR_CACHE_DIR` 指向空目录 |
| 坏 JSON | 往缓存文件写 `{ this is not json` |
| 结构不符 | 写 `{"_schema":"someone-elses-v9","satellites":"not-a-dict"}` |
| 缓存过期 | 先假 opener 写一份有效缓存，再把 `fetched_at` 改成 `now-30d`（每星记录同步改老） |
| 网络不通 | `HTTPS_PROXY=HTTP_PROXY=ALL_PROXY=http://127.0.0.1:9`、`NO_PROXY=''`，真跑 `refresh_machine_links()`；代理端口无人监听 → WinError 10061「目标计算机积极拒绝」 |
| 缓存目录不可创建 | 用同名**文件**占住 `MBDSDR_CACHE_DIR` 的父路径 |
| 缓存文件只读 | `os.chmod(cache, stat.S_IREAD)` 后刷新 |
| 缓存文件不可读 | `icacls <cache> /deny "<whoami>:(R)"`，`finally` 里 `/remove:d` 恢复 |

网络模拟说明：这是**连接被拒绝**路径，不是 DNS 黑洞/丢包。8 颗星串行失败，
实测总耗时 16.5 s（每颗约 2 s），全程在后台/同步工具调用内完成，不碰首帧。

## 2. 四条降级路径（修复后实测）

| 场景 | 状态 | 人工 8 星 | 取到的频点 | 异常 |
| --- | --- | --- | --- | --- |
| 1 无缓存 | `missing`，文案「未取到（无本地缓存），沿用人工目录」 | 8 颗/9 条 | NOAA-15 137.62 MHz；137.1 MHz 反查命中 NOAA-19 + METEOR-M2 | 无 |
| 2a 坏 JSON | `invalid`，文案「缓存不可用：内容不是合法 JSON…」 | 8 颗/9 条 | NOAA-18 137.9125 MHz | 无 |
| 2b 结构不符 | `invalid`，文案「缓存结构不符：_schema 不符…」 | 8 颗/9 条 | ISS 2 条（145.8 / 145.825） | 无 |
| 3 缓存过期 30 天 | `ok` + `stale=true`，文案「30.0 天前…已超 168h 未刷新」 | 8 颗/9 条 | NOAA-15 仍 137.62；机器 `low_hz=137.62 MHz`、`status=inactive` **不覆盖人工值** | 无 |
| 4 网络不可达 | `failed`，`written=false`，8 颗全 `network_unreachable` | 8 颗/9 条 | NOAA-15 137.62；旧缓存字节**逐字节未变** | 无 |

场景 4 的状态文案仍是「机器缓存 0 分钟前（1 颗），7 颗最近拉取失败」——因为
本轮没写盘，磁盘状态没变。刷新结果本身如实是 `failed`；界面那行要不要补一句
「本次刷新失败」属于展示层，本轮未改（`desktop/rf_sky_view.py` 正被另一条线占用）。

## 3. 原子写盘在并发下（实测）

原始件：`docs/evidence/w92-concurrency-20261112.json`。

**修复前实测**（无跨进程锁）：

- 6 进程 × 25 轮同时刷新，150 次写里 **85 次 `os.replace` 失败**
  （WinError 5 拒绝访问；读者/写者共享冲突），`refresh()` 却仍返回
  `state=ok`，只有 `written=false`；磁盘留下 2 个 `.tmp` 文件。
- 双进程 barrier 交叉：A 只写 25338、B 只写 33591，双方都报 `written=true`，
  最终缓存**只剩 33591**，25338 被覆盖丢失。
- 读者持有缓存文件时写盘：`written=false`，旧缓存完好，但仍报 `state=ok`。

**修复后实测**：

- 6 进程 × 25 轮：入库那轮 **150/150 写成功**，最终 JSON 合法、无半截、
  无 `.tmp` 残留；复跑一次为 **149/150**（有 1 轮被读者短占导致 3 次退避后仍
  失败，如实报 `write_failed`，旧缓存保留，没有写坏）。
- 双进程 barrier 交叉：最终 `['25338','33591']` 都在，**丢星 0**。
- 并发读：原始 `read_bytes()` 仍有 5 次瞬时 PermissionError；走应用真实读路径
  `load_cache()` 为 **921 次 ok / 0 次 invalid**（读盘 OSError 退避重试 3 次，
  仍失败不写进程内 memo，下次自动恢复）。
- 读者持有文件时写盘：`state=write_failed`、`written=false`、旧缓存未变。

现状与残余风险（不假装安全）：

1. 锁是**建议性文件锁**（`sat_links_machine.json.lock`，Windows
   `msvcrt.locking` / POSIX `flock`），只罩「重读 + 合并 + 写盘」，不罩网络；
   锁文件会留在缓存目录（1 字节，无数据）。
2. 拿锁最多等 10 s；超时**不阻断刷新**，结果里 `lock_acquired=false`，
   退化为「后写者覆盖」——但仍是原子替换，不会写出半个 JSON。
3. 读者不参与锁；Windows 上读者持有文件的瞬间，写者 3 次退避仍可能失败，
   表现为单轮 `write_failed`（如实上报、旧缓存保留），下一轮自愈。
4. 进程崩溃时临时文件可能残留（本次未人为制造崩溃）；失败路径已统一清理。

## 4. 缓存权限/属主（实测）

| 操作 | 结果 |
| --- | --- |
| 缓存目录被同名文件占住（无法建目录） | 刷新 `state=write_failed`，`written=false`，原因 `FileNotFoundError/WinError 3`；人工 8 星照常 |
| 缓存文件只读 | 刷新 `state=write_failed`，旧缓存字节未变；人工 8 星照常 |
| 缓存文件 deny R | 读取 `state=invalid`「缓存不可用：Permission denied」；人工 8 星照常，**不抛异常** |
| 目录 ACL deny W（只读目录） | 读路径 `state=ok`；刷新 `state=write_failed`；`exceptions=none`，人工 8 星照常 |

结论：**启动读取路径不会因为只读/不可读缓存抛异常**，与「不阻塞首帧」不冲突；
写失败也不会清空或改坏人工目录。之前会「静默成功」的写失败，现在状态如实。

## 5. 修复内容（已提交）

| 提交 | 内容 |
| --- | --- |
| `0a379c1d` | `refresh()` 写盘失败 → `state=write_failed` + 原因；`.tmp` 清理；跨进程锁（锁内重读合并）；读盘 OSError 重试 3 次且不 memo 化；坏 JSON 文案改「缓存不可用」 |
| `f812eb87` | 回归测试：写失败上报、坏 JSON 文案、读冲突重试后恢复 |

## 6. 复跑

```powershell
cd D:\mbdsdr\MBDSDR
python scripts/repro/2026-11-12-w92-degrade-probe.py        # 四种降级 + 权限
python scripts/repro/2026-11-12-w92-concurrency-probe.py    # 并发/原子/锁/只读目录
python -m pytest mbdsdr_ai/tests/test_sat_links_machine.py -q
```

两个探针把结果写到系统临时目录（`w92-degrade-results.json` /
`w92-concurrency-results.json`），不覆盖入库证据。并发轮次的写成功数会有
±1 抖动（取决于读者/写者抢占），但下列断言稳定：最终 JSON 合法、丢星 0、
`load_cache` 不出现 `invalid`、失败必报 `write_failed`。

## 7. 剩余边界：单轮 `write_failed` 仍可能出现

### 7.1 什么条件下会

1. **读者不参与锁**：`load_cache()`（UI 线程/信息卡）不拿 `_CacheLock`。
   Windows 上 `os.replace` 要求目标文件允许 delete 共享，而 Python 普通
   `open()` 不带 `FILE_SHARE_DELETE`；读者恰好打开缓存文件的瞬间，写者的
   `os.replace` 会 WinError 5。
2. **极端抢占**：写者已退避重试 3 次（约 0.15 s 总窗口），仍撞上持续读者
   （例如连续重绘反复读同一文件、杀软扫描）时，本轮就失败。6 进程 × 25 轮
   的复跑里出现过 1/150。
3. 目录只读/文件只读/磁盘满等**持续性**权限问题不属于「偶发」，它们每轮
   都失败，但同样只报 `write_failed`，不会伪装成功。

### 7.2 发生时用户看到什么、数据会怎样

- **数据**：旧缓存**原样保留**（失败发生在 `os.replace` 之前或替换失败，
  临时文件被清理），不会丢旧记录、不会写半个 JSON；人工 8 星/9 条链路不受
  影响。下一轮刷新成功即自愈。
- **状态**：`refresh()` 返回 `state=write_failed` + 具体原因；后台路径可从
  `last_refresh_result()` 读到。**当前界面状态行仍显示旧缓存的数据龄**——
  因为磁盘状态确实没变，而展示层还没接这个字段（见 §7.3，等
  `rf_sky_view.py` 干净后接线）。
- 不会弹窗、不阻塞首帧；只多一次日志 WARNING。

### 7.3 要不要再往前一步彻底消除

| 方案 | 成本 | 收益 | 判断 |
| --- | --- | --- | --- |
| A. 读者也参与锁（共享锁） | 中：`load_cache` 每次读盘都要开锁文件 + 等待；信息卡重绘频繁时会拉长 UI 线程 | 消除读/写共享冲突 | 暂不做：把 UI 读路径变成可能阻塞，违背「不阻塞首帧」 |
| B. 继续加长 rename 重试（如 10 次 / 1 s） | 低：改一个常量 | 把偶发窗口压小，但极端读者下仍可能失败 | 可做，但不是根治；当前 3 次已覆盖常规抢占 |
| C. 写前探测读者（重命名旧文件再写新文件） | 中高：Windows 语义差异大，易在只读/权限场景引入新分支 | 仍不能保证原子性的跨平台一致 | 不做 |
| D. 保持现状（重试 + 如实上报 + 旧缓存保留） | 零 | 最坏是「本轮没更新」，不会坏数据/丢星 | **当前选择**；若后续真有用户遇到，再按 A/B 升级 |

结论：这是**可用性降级**而非数据安全缺陷；先不动，等展示层把
`write_failed` 显示出来，用户能看见「本轮没更新」即可。

### 7.4 反证测试（旧实现对照）

`mbdsdr_ai/tests/test_sat_links_machine.py` 里保留了修复前的无锁直写副本：

- `test_concurrent_refresh_keeps_both_stars`：修复版跑两进程交叉刷新，
  断言两颗星都在、双方 `written=true` → 绿；
- `test_legacy_unlocked_write_loses_a_star`：同一场景换成无锁直写副本，
  断言最终只剩 1 颗星且至少一方写成功 → 证明旧实现确实丢星。
  若有人把修复回退成无锁直写，第一条会红。
