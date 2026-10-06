# Phase 55 块2+块3：Costas 状态透出 + 多普勒补偿三通道桥接

HEAD = cfb5e02。构建目录 cpp/build，Qt 6.8.2，offscreen。所有 file:line 为本轮实测。

## 块2：C++ Costas 状态透出

### 落点
- `cpp/src/dsp/vfo_manager.h`：新增 `DigitalLockStatus digitalLockStatus() const` 只读快照（选中通道的 Costas 锁定状态）。
- `cpp/src/dsp/vfo_manager.cpp`：实现——选中通道有 digitalDemod 时返回 `lockStatus`，否则返回默认 `DigitalLockStatus{carrierLocked=false,...}`（诚实空态，不伪造锁定）。
- `cpp/src/dsp/spectrum_engine.h/.cpp`：新增 `DigitalLockStatus digitalLockStatus() const` forwarder（持 sourceMutex_）。
- 三通道透出：
  - Agent `get_status`（`agent_tools.cpp` execGetStatus）：加 `carrier_locked / symbol_locked / evm_percent`。
  - ControlHub `cmdGetStatus`（`control_hub.cpp`）：同样加三字段。
  - HTTP GET `/status` 委托 ControlHub get_status，自动覆盖。

### 诚实状态
非 BPSK/QPSK 数字模式（选中通道 digitalDemod 为 null）时，engine 返回全 false 的默认锁状态——不伪造载波锁定。

## 块3：多普勒补偿三通道桥接

### 设计（干净解耦）
新增抽象接口 `cpp/src/dsp/doppler_control_surface.h`：
```cpp
class DopplerControlSurface {
public:
    virtual void setDopplerCompensationEnabled(bool on) = 0;
    virtual bool isDopplerCompensationEnabled() const = 0;
    virtual bool isDopplerCompensationAvailable() const = 0;  // station+captured
};
```
- engine 持 `DopplerControlSurface* dopplerSurface_ = nullptr`（默认 null），有 setter/accessor。
- MainWindow 继承 `public dsp::DopplerControlSurface`，在 engine 创建后 `engine_->setDopplerControlSurface(this)` 注册。
- 接口方法委托到 dopplerCompChk_：`setChecked(on)` 触发 toggled → onDopplerCompToggled，后者自带 stationSet_/capturedIdx_ 前置检查（不满足则反选）。
- 无 UI 面（headless/测试）时 surface 为 null，工具诚实返回 unavailable。

### 三通道接入
- **Agent**：新工具 `set_doppler_compensation`（write，手动门控拦截）。executor `execSetDopplerCompensation`：surface 为 null → `ok=false, available=false`；否则调 setEnabled 并回读 enabled/available。get_status 加 `doppler_available / doppler_enabled` 字段。
- **ControlHub**：新命令 `set_doppler_compensation`（`cmdSetDopplerCompensation`），get_status 加 doppler_available/doppler_enabled。
- **HTTP**：POST /command 委托 ControlHub；GET /status 委托 ControlHub get_status。

### 工具计数
C++ 桌面 Agent 工具数：**35 → 36**（新增 set_doppler_compensation，write）。

### 手动门控
set_doppler_compensation 标 `write=true`，走既有手动模式门控（gatedToolResult），无需新门控逻辑。

## 测试
- `cpp/tests/test_agent.cpp`：新增 2 个用例
  - `testDigitalLockStatusHonestEmpty`：fresh engine 的 digitalLockStatus 全 false、EVM=0。
  - `testDopplerSurfaceNullHonestUnavailable`：fresh engine surface 为 null，set_doppler_compensation 返回 ok=false/available=false。
- `cpp/tests/test_tool_registry.cpp`：硬计数 35→36，last tool 断言改为 set_doppler_compensation。
- `cpp/tests/test_agent.cpp` testToolParse：35→36。

## ctest 全量（offscreen）
```
100% tests passed, 0 tests failed out of 129
128 passed + e2e_smoke Skipped (SKIP_RETURN_CODE 77)
```
基线 129 不回归（新测试在 test_agent 目标内运行）。

## 文件清单
- 新：`cpp/src/dsp/doppler_control_surface.h`
- 改：`cpp/src/dsp/vfo_manager.{h,cpp}`、`cpp/src/dsp/spectrum_engine.{h,cpp}`
- 改：`cpp/src/ai/tool_schema.cpp`、`cpp/src/ai/agent_tools.cpp`
- 改：`cpp/src/control/control_hub.{h,cpp}`
- 改：`cpp/src/ui/main_window.{h,cpp}`
- 改：`cpp/tests/test_agent.cpp`、`cpp/tests/test_tool_registry.cpp`
- 文档：`docs/learn/phase55/performance-baseline.md`（本文件）

## 未解决项
- 多普勒补偿的真实开/关效果需在桌面端（有 UI、站址+捕获过境）手动验证；offscreen 测试仅覆盖 surface 为 null 的诚实路径。
- Costas EVM 目前仅在 get_status 透出数值，未在 UI 状态面板显示（本轮未做 UI 改动）。
