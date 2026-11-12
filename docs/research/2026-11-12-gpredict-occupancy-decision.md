# gpredict 占用态（第三档）决策页（2026-11-12）

> 对应 parity §2.2.1 专项③、§4 第 33 条。本文只回答三件事：现状、两个选项的代价、
> 不选会怎样。**显示方案仍待决策**；数据源已落地（见文末「已做」）。

## 1. 现状：我们没有第三档

parity 里说的「三档」：

1. **始终可用**：不看设备状态，参数随时能改。
2. **随数据禁用**：没数据就禁用（我们也没采用这一档）。
3. **占用即只读**（gpredict）：设备会话期间参数控件置灰、值保留；退出占用时 `setconfig()` 写回。

我们当前是第 1 档。证据（对 HEAD 核）：

- 会话期间被 `setEnabled` 切换的控件是一个**封闭集**——`HEAD:desktop/device_panel.py:489-506`
  `def set_connected(self, connected: bool, dev_label: str = "")` 内只有四行：

  ```python
  self.connect_btn.setEnabled(False)      # :499 连接后
  self.disconnect_btn.setEnabled(True)    # :500
  self.connect_btn.setEnabled(bool(self._devices))   # :505 断开后
  self.disconnect_btn.setEnabled(False)   # :506
  ```

  `sr_edit` / `gain_edit` / `ppm_edit` **不在该集合内**，会话期间仍可编辑。
- 更糟的是会话期间还会被**反向覆盖**：`HEAD:desktop/device_panel.py:436-455`
  `_render_request` 从 store 拉值写进输入框（`:441` `req = _req_store.request_for(ident.key)`，
  `:443-450` 三处 `setText`），**没有 `if not self._connected` 门**。另一面板在会话中改了
  store，本面板输入框会被拉成新值——用户看到的数字与后端实际下发值脱钩，且没有任何提示。

## 2. 数据从哪来

| 事实 | 现有唯一来源 | 位置（HEAD） |
|---|---|---|
| 每台设备的连接参数意图 | `connect_request_store._requests` | `HEAD:desktop/connect_request_store.py:162` `def request_for(key)`、`:167` `def update(...)` |
| 连接载荷 | `fill_payload(payload, key)` | `:228` |
| 是否在会话中 | `DevicePanel._connected` | `HEAD:desktop/device_panel.py:489-506` |

缺的是**「本次会话实际生效值」**这一份：现在只有「请求（想要什么）」，没有
「会话快照（这次实际下发了什么）」。所以任何第三档显示（置灰理由、只读徽标、
「下次连接生效」提示）都无处取数。

## 3. 两个可选项及代价

### 选项 A：gpredict 原样——会话中置灰 + 退出时写回

- 做法：`set_connected(True)` 冻结快照并 `setEnabled(False)` 三个参数框；
  `set_connected(False)` 恢复可编辑并把快照写回 store。
- 代价：
  - 面板侧一处开关 + 两个生命周期调用（数据源已具备，见 §5）。
  - **失败路径必须先定**：断开写回失败是回滚到会话前的值、重试、还是只告警？
    gpredict 没处理，照抄会把它带进我们的代码。
  - 测试要覆盖「会话中改不了」「断开后值与连接时一致」「写回失败不静默」三组。
- 真正的产品取舍：**监听中调增益/采样率是常见操作**，置灰意味着必须先断开。
  这是选项 A 唯一无法用工程量抵消的代价。

### 选项 B：只显示占用态，不夺走编辑权

- 做法：会话中显示「占用中 · 本次生效 2.048 MS/s」，控件保持可编辑；改动明确标注
  「下次连接生效」，或在 store 写入口拦住并提示。
- 代价：
  - 需要同时展示「当前生效值」与「下次连接值」两套数字/两句文案，否则编辑即生效的
    直觉会继续骗人；文案与交互要定（属于要决策的部分）。
  - 若不做写入拦截，仍存在「改完以为已生效」的静默偏差——所以 B 必须二选一：
    拦写入，或显式标注。
- 优点：不动「监听中可调参」的工作流，数据层零风险（快照已具备）。

## 4. 不选会怎样

维持现状不是「中性」：

- 会话期间另一面板改 store，本面板输入框会被覆盖（`HEAD:desktop/device_panel.py:436-455`），
  用户无法从界面判断哪个数字才是后端正在用的。
- 「占用中」这一状态在数据层不存在，后续无论做置灰、徽标还是提示，都要各自再造
  一份判断 → 同一事实多个来源，正是本轮削冗余要消灭的形态。
- 风险等级：中（不会崩，但会持续误导；真机验收时无法用界面数字解释实际采样率）。

## 5. 已做 / 未做

**已做（数据源，非决策部分）**

- `desktop/connect_request_store.py` 新增会话占用态：
  `begin_session(key)`（冻结首次生效值，重复调用不覆盖） /
  `end_session(key)`（清占用并返回快照，不写回请求） /
  `session_values(key)` / `is_occupied(key)`；`reset()` 一并清理。
- 证据：`tests/test_connect_request_session.py`（7 用例）。反证：把 `session_values`
  改成返回 `request_for`（活引用）后，快照冻结用例立即变红。

**未做（按协调规则阻塞，不是遗漏）**

- 生产者接线点：`device_panel.set_connected(True/False)` 各调一次
  `begin_session` / `end_session`；模块面板的连接/断开入口同。两处所在文件当前有
  他线在途改动与 claim（`desktop/device_panel.py` 被「接入层资源线」claim），
  按 COORDINATION §6「未提交 hunk 不得追加」本笔不碰。
- 显示方案：选项 A / B 待决策。

## 6. 判定口径

- 选 A：会话中三个参数框只读，断开后值与连接时一致；写回失败有显式提示。
- 选 B：会话中可编辑，但界面同时给出「当前生效值」；改动被标注或拦截。
- 两案共用同一数据源：`is_occupied` + `session_values`，不各自再造判断。
