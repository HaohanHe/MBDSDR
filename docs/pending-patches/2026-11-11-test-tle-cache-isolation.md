# 交接单：`COORDINATION.md` 测试纪律补一条「外部缓存目录必须隔离」

> 来源：卫星后端线（TLE 落盘修复，提交 `06310162`）。
> 形态：**交接单，不直接改** `docs/COORDINATION.md`——该文件当前有机质线在并。
> 目标读者：机质线 / 后续改 COORDINATION.md 的会话。

## 一、要加的纪律（建议插进 `docs/COORDINATION.md`「测试纪律」节，原文可整段贴）

```markdown
### 外部缓存目录必须在测试里隔离

被测代码只要会读写 `~/.mbdsdr/` 下的磁盘缓存（TLE 缓存
`~/.mbdsdr/tle_cache/`、其它按用户目录落盘的缓存同理），测试就必须把缓存
目录指到 `tmp_path`，不能读开发机真实缓存：

- 统一入口：`orbit._CACHE_DIR`（`tle_fetch.TleFetcher` 与
  `sat_tracker.SatelliteTracker` 都按调用时读它）。测试里用
  `monkeypatch.setattr(orbit, "_CACHE_DIR", str(tmp_path / "tle_cache"))`；
- 隔离后：断言「内置基线 / 无缓存降级 / 失败路径」的用例才与机器无关；
- 不隔离的症状：同一条用例在开发机（本地刷新过 TLE）与 CI（无缓存）读到
  不同历元，出现「本机绿、CI 红」或反向漂移；典型如
  `test_sat_tracker.py::test_tle_date_label` 这类钉「内置 2026-09-25」的
  断言。

已按此改的样例：`tests/test_sat_tracker.py`（模块级 autouse fixture）、
`tests/test_sat_tracker_persistence.py`（逐例隔离）、
`mbdsdr_ai/tests/test_offline_behavior_audit.py`
（`test_offline_sat_tracker_refresh_keeps_builtin_tle` 内隔离）。

注意：这条只管「缓存/外部状态」，不替代 `isolated_profile`（真实用户配置
隔离见 `docs/pending-patches/2026-11-11-test-isolation-handoff-block5.md`）。
```

## 二、复现证据（供审阅，不必重跑）

- 未隔离时（本机缓存历元 2026-11-09）：
  `pytest tests/test_sat_tracker.py -q` → 5 failed（断言全部钉内置
  2026-09-25，实际读到缓存 2026-11-09、tier=usable）。
- 隔离后：`pytest tests/test_sat_tracker.py -q` → 13 passed；
  `pytest tests/test_sat_tracker_persistence.py -q` → 4 passed；
  `pytest mbdsdr_ai/tests/test_offline_behavior_audit.py -q` → 21 passed。
- 反向验证：同一份缓存文件内容不变，只把 `_CACHE_DIR` 指向空 tmp 目录，
  断言回到内置值——证明差异来源是目录，不是代码随机性。

## 三、适用范围提醒

新写的卫星/TLE 相关测试默认应带隔离 fixture；若用例本身就是验证「读回
磁盘缓存」，用独立 tmp 目录构造缓存文件，**不要**依赖开发机 `~/.mbdsdr`。
