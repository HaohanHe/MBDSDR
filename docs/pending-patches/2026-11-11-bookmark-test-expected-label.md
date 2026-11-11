# 交接单：`tests/test_bookmark_builtin_label.py` 期望按钮名跟随术语统一

- 日期：2026-11-11
- 目标文件：`tests/test_bookmark_builtin_label.py`（当前由测试隔离线持有，工作区有未提交的
  `window(qtapp, isolated_profile)` fixture 改动）
- 为什么交接：控制面板术语统一已提交（`2a07f344`：按钮「收藏当前频率」→「存为书签」），
  该测试的期望值没跟，现在会红。文件里有他线在途改动，按纪律不整文件提交，故交接。

## 需要的改动（一行）

```python
# tests/test_bookmark_builtin_label.py:45
-_EXPECTED_BUILTIN_NOTE = "收藏当前频率"
+_EXPECTED_BUILTIN_NOTE = "存为书签"
```

## 触发条件

`test_builtin_bookmark_delete_is_refused_with_next_step`：删内置书签时，弹窗下一步提示
必须是当前按钮名。控制面板按钮已改为「存为书签」（`desktop/control_panel.py:514`），
提示文案同步改了（`:1206`），测试常量仍是旧名。

## 反证

```powershell
python -m pytest tests/test_bookmark_builtin_label.py::test_builtin_bookmark_delete_is_refused_with_next_step -q
# 期望：1 failed，断言文本里是「存为书签」而常量找「收藏当前频率」
```

## 落地方式

1. 持有线提交自己的 `isolated_profile` fixture 改动（或确认可整文件提交）。
2. 改上述一行。
3. `python scripts/safe_commit.py --files tests/test_bookmark_builtin_label.py --message-file <msg>`
4. 删除本交接单。
