# 交接单：浮窗「归位」的 C++/Python 生命周期（B3，未改代码）

**登记时间**：2026-11-12　**归属**：卫星线（`desktop/main_window.py`）
**状态**：只出单，**未动 `main_window.py` 一个字节**；本条不做修改，等卫星线认领。
**引用位置**：用户给的 `main_window.py:4740-4747` 在当前工作树已漂移到
[main_window.py](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4794-L4824) `_return_panel()`；
`_pop_out_tab()` 在 [L4744-L4792](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4744-L4792)。

---

## 1. 现状

归位链路的收尾（[L4812-L4819](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4812-L4819)）：

```python
ph = info["placeholder"]
ph.setParent(None)
ph.hide()
panel.hide()
panel.setParent(None)
# 不用 deleteLater()：on_return 闭包还持有 panel 的 Python 引用，
# 强删会让 C++ QVBoxLayout 比 Python 包装先走，退出时抛
# "Internal C++ object already deleted"。交给 GC 按引用计数收。
```

即：占位 `placeholder` 与浮层 `panel` 都先 `setParent(None)`，随后**不持有任何
Python 引用**，靠引用计数归零回收。

## 2. 为什么不交给 GC（准确版）

结论先说：**GC 这条路目前能工作，但它正确的唯一原因是「调用栈返回后引用恰好清零」**，
不是 Qt 的父子关系在保证顺序。

`FloatingPanel.request_return()`（[floating_panel.py:267-269](file:///D:/mbdsdr/MBDSDR/desktop/floating_panel.py#L267-L269)）在
按钮 `clicked` 信号里被调用，链路是：

```
_return_btn.clicked → FloatingPanel.request_return（栈上持有 self=panel）
  → self._on_return() → MainWindow._return_panel（栈上持有 info / panel 局部变量）
```

所以在 `_return_panel` 还在执行时，`panel` 至少有 3 份 Python 引用：
`request_return` 的 `self`、`_return_panel` 的局部 `panel`、`info["panel"]`。
`setParent(None)` 只是把 C++ 侧的父子关系摘掉，**Python 引用一个没少**；
真正让对象死掉的是 `_return_panel` 返回、`info` 字典与局部变量离开作用域之后，
引用计数归零 → 包装析构 → shiboken 删 C++ 对象。

这也解释了注释里的历史故障：一旦中间还有别的引用（闭包、成员、队列里的信号），
顺序就会反过来——C++ 先被 Qt 删掉，Python 包装还在，随后任何访问都抛
`Internal C++ object already deleted`。

另一个细节：`take_payload()` 只做 `self._payload.setParent(None)`，
**不清 `self._payload`**；`FloatingPanel._host`、`_on_return` 也一直握到对象析构。
这些引用本身不致命，但会让「panel 何时归零」变得依赖 GUI 栈帧，而不是显式规则。

## 3. 可选修法（按推荐顺序）

### 方案 A（推荐）：显式断链 + `deleteLater()`

1. 给 `FloatingPanel` 加 `release()`：清 `_on_return = None`、`_payload = None`、
   `_host = None`，并 `self.setParent(None)`；
2. `_return_panel` 尾部改成：
   ```python
   ph.setParent(None); ph.deleteLater()
   panel.hide()
   panel.release()          # 先断 Python 侧引用
   panel.deleteLater()      # 再把 C++ 生命周期交给 Qt
   ```
3. 理由：**所有权显式交给 Qt**（`deleteLater` 在返回事件循环后删 C++），
   同时保证删除发生前 Python 侧已无引用——这正是注释里缺的那一步。
   不写 `del`：`del` 只作用于当前作用域，管不到 `_on_return`/`_payload` 这类成员。

### 方案 B：池化复用，不销毁

`_float_windows` 里同 key 的 `FloatingPanel` 只创建一次，归位时隐藏而不销毁，
再次弹出时复用。代价：所有面板常驻内存（含各自的 QVBoxLayout、样式表），
而浮窗按设计是「用户想看就弹」，常驻会让内存与首帧成本都变差。不推荐。

### 方案 C：维持 GC，但把「引用清零」写成不变量

在 `_return_panel` 里显式 `info.clear()`、并把 `panel`/`ph` 用一个内部函数隔离作用域。
最小改动、不改语义，但仍然依赖 CPython 引用计数（PyPy/子解释器下不成立），
且下次谁加一个成员引用就会复现故障。可作为 A 的过渡，不建议作为终态。

## 4. 改之前必须先看：`on_return` 闭包持有了谁

`_pop_out_tab` 传入的是（[L4774](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4774)）：

```python
on_return=lambda k=key: self._return_panel(k)
```

它捕获的是 **`self`（MainWindow）与 `key`，不捕获 `panel`**。
`FloatingPanel.__init__` 把它存成 `self._on_return`（[floating_panel.py:83](file:///D:/mbdsdr/MBDSDR/desktop/floating_panel.py#L83)）。

因此：
- 现在**不存在** `panel → _on_return → panel` 的环；注释里说的「闭包还持有 panel」
  在当前代码不成立（这很可能就是当年 `deleteLater` 失败的真实原因并非闭包，
  而是 `request_return` 栈帧 + `info` 字典尚未退出）；
- 但只要有任何人把它写成 `lambda: self._return_panel(key, panel=panel)`，
  就立刻形成自引用环（Python 的循环 GC 能收，但**收的时机不确定**），
  `deleteLater()` 后「C++ 已删、Python 还在」的窗口就出现了。
- 所以方案 A 的 `release()` 要清 `_on_return`；否则修法对「未来写法」没有免疫力。

## 5. 验收清单（改的时候照这条跑）

1. `shiboken6.isValid(panel)` 在归位返回后为 `False`（方案 A），且
   `shiboken6.isValid(payload)` 仍为 `True`（payload 已回页签，不能误删）。
2. 弹出→归位循环 20 次，不出现 `Internal C++ object already deleted`；
   进程正常退出码 0（不是 `0xC0000005`）。
3. `placeholder` 不残留：`tabs.count()` 与归位前一致，页签 `tabData` 逐项不变。
4. 跑 UI 交互守卫（`scripts/ui_interaction_audit.py`）确认「⤶」仍是真动作，
   状态栏回执文案不变。
