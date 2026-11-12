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

## 7. 未修的诚实边界

- 界面状态行在「本次刷新失败但旧缓存仍在」时只显示旧缓存数据龄，不显示
  「本次失败」；数据层已把 `state=write_failed/failed` 暴露给
  `last_refresh_result()`，展示层接线未做（该文件本轮被其他线占用，未触碰）。
- 原生 Windows 共享冲突无法被应用层完全消除，只能重试 + 如实上报；
  若要求「绝不出现单轮写失败」，需要读者也参与锁（会拉长 UI 读等待），
  本轮未做这个取舍。
