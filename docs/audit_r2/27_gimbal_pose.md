# 第二轮深度审查：gimbal.py（云台控制）与 pose.py（位姿估计）

- 审查范围：`mbdsdr_ai/gimbal.py`（427 行）、`mbdsdr_ai/pose.py`（672 行）
- 关联文件：`mbdsdr_ai/hal.py`（690 行）、`mbdsdr_ai/orbit.py`、`mbdsdr_ai/sdr_tools.py`、`mbdsdr_ai/sdr_backend.py`、`mbdsdr_ai/agent.py`、固件 `ai-sdr-mini-kicad/ai_sdr_mini_firmware_v0.5_WebOTA.ino`
- 审查方式：只读，逐行核对 + 全仓调用方追踪

---

## 0. 结论速览

| 模块 | 是否被真实调用 | 是否真接硬件 | 评级 |
|---|---|---|---|
| gimbal.py | **是**（sdr_tools 适配层 7 个 MCP 工具 + workflow_engine 编排） | rotctld 是真实 TCP；board_pwm 经 WebSocket MCP 真下发到 ESP32 固件；**但 IMU 闭环回读从未接线** | 协议层可用，闭环层是空壳 |
| pose.py | **是**（agent.py 注册 4 个 MCP 工具） | **否**。无任何串口/NMEA/IMU 读取线程，数据全靠 LLM 当参数填；Madgwick 磁力计修正实际未生效 | 算法层有真 bug，硬件接入层是空壳 |

核心判断：**gimbal.py 是"半真半假"——电机驱动链路真，IMU 闭环链路假；pose.py 是"算法演示+占位"——补滤波数学基本对，标称的 9DOF Madgwick 修正被代码自己丢掉，且没有任何硬件数据源。这正是"实验室绿、真机红"的直接成因。**

---

## 1. gimbal.py 逐项发现

### 1.1 云台控制协议真相

三种后端（`gimbal.py:40-44`）：

1. **rotctld**（`gimbal.py:107-160`）：真实 TCP 文本协议，连 `127.0.0.1:4533`，`socket.create_connection` 直连。协议帧 `P <az> <el>` / `p` / `S` 与 Hamlib rotctld 基本吻合。**这是真网络协议，非模拟。**
   - [建议] `gimbal.py:114` docstring 写停止命令为 `\chkstop`，实际代码用 `"S"`（`gimbal.py:152`），文档与实现不一致。
   - [建议] `gimbal.py:149,153` 成功判定 `any("0" in ln for ln in lines)` 过于宽松——`RPRT -10` 这类负回报也含 `"0"`，会误判成功。应精确匹配 `"RPRT 0"`。
2. **board_pwm**（`gimbal.py:167-194`）：不直接碰 ESP32 LEDC，而是通过注入的 `send_callable` 走 MCP `gimbal_set/gimbal_get` 下发。注入点真实存在：`sdr_backend.py:649-666` 在 WebSocket 连上板子后 `set_send_callable(lambda method, params: self._send_mcp(method, params))`，断开时清除。固件侧确有 `gimbal_set/gimbal_get` 实现（`.ino:996-1024`）。**链路是真的。**
3. **manual**（`gimbal.py:214-218`）：无电机，纯文本引导。

### 1.2 [真bug] board_pwm 方位角量程与固件不匹配

- `gimbal.py:186` `BoardGimbalClient.set_position` 发送 `"az": az % 360.0`（0–360°）。
- 但固件 `.ino:1007` 把 az 直接喂舵机，且固件自述/接线表均为 **az 0–180°**（`.ino:1044`：`az方位0-180度`；`ai-sdr-mini-接线表-v1.0-匹配0917工程.md:234`：`az 0-180°、el 0-90°`）。
- 后果：`point_to(270, 30)` 会把 270 直接发给 0–180° 量程的舵机，**舵机过机械限位**。`GimbalStatus.az_max=360`（`gimbal.py:79`）也与板载物理量程矛盾。代码里**没有任何 az→board 量程（0–180）的钳位或映射**。
- 对比：el 方向固件 `el*2.0`（`.ino:1012`，0–90°→舵机 0–180°），gimbal.py 发 el 0–90 是对的，唯独 az 错了一个量程。

### 1.3 [真bug / 空壳] 文档吹的"IMU+磁力计闭环"从未接线——这是 gimbal 侧最大问题

- `gimbal.py:208-209` 定义 `self._pose_provider`，`gimbal.py:235` 提供 `set_pose_provider()`。
- **全仓 grep 结果：`set_pose_provider` 除定义外零调用**（`gimbal.py:235` 是唯一命中，无任何外部模块注入 pose 回读函数）。
- 后果链：
  - `read_pose()`（`gimbal.py:244-264`）：`_pose_provider is None` → board_pwm/manual/offline 模式下直接落到 `return self.status.current or GimbalPose()`，即**恒返回 (0°N, 0°仰)**。
  - `point_to()`（`gimbal.py:292-299`）据此算 `az_error/el_error` 和 `guidance`：系统根本不知道天线实际朝哪，却一本正经地告诉你"顺时针转 135°"——**引导是相对假想正北(0,0)算的，不是相对天线真实姿态**。
  - `point_to_satellite()`（`gimbal.py:303-314`）、`sweep_azimuth()`（`gimbal.py:344`）同样盲转。
- 即：manual/board 模式的"闭环收敛到波束宽度内"在真机上是**开环盲导**，只有 rotctld 模式靠 `rotctld.get_position()`（`gimbal.py:257-262`）有真实位置回读。docstring 第 14-20 行宣称的闭环流程，一半是假的。

### 1.4 与 orbit.py 的卫星指向联动

- orbit.py 方位角计算本身正确：`orbit.py:158-159` `elevation=asin(e_up/dist)`、`azimuth=atan2(e_east,e_north)%360`，是标准 ENU 天文约定（0=北，90=东，顺时针），与 gimbal 的 `GimbalPose`（`gimbal.py:49`）约定一致，**单位均为度，无弧度/度混用**。
- 但联动是**LLM 编排级，不是代码级**：`gimbal_track_satellite` 工具（`sdr_tools.py:5902-5919`）要求 LLM 自己传入 `sat_azimuth/sat_elevation`；gimbal.py 不 import orbit、不订阅 TLE、无跟踪线程、无过境连续跟踪循环。`sdr_tools.py:5916` 自己也提示"过境期间应按 sky_view_visible 刷新方位角后重复调用本工具"。**过境连续跟踪 = 靠 LLM 反复调工具，无自动闭环。**

### 1.5 孤儿/空壳函数

| 符号 | 位置 | 状态 |
|---|---|---|
| `angle_to_servo_us()` | `gimbal.py:88-96` | **[空壳/孤儿]** 全仓零调用。板载脉宽换算在固件里做，PC 端发的是角度不是 us。 |
| `az_el_to_rotator_angles()` | `gimbal.py:408-427` | **[空壳/孤儿]** 全仓零调用。极轴式倾斜座架变换写了但没人用，普通 AZ/EL 透传分支（`:415-416`）才是唯一可达路径且都没被走到。 |
| `GimbalStatus.pwm_min_us/pwm_max_us` | `gimbal.py:75-76` | **[占位]** 仅存数据，配合上面孤儿函数使用，无人读。 |

### 1.6 数值正确性（抽查）

- `angular_error` 最短角归算 `gimbal.py:56`：`(target-self+180)%360-180` —— **正确**。
- `aligned()` 容差 `gimbal.py:60-63`、`point_to_satellite` 波束半宽 `tol=max(2, beamwidth/2)`（`gimbal.py:307`）—— 合理。
- `_guidance_text`（`gimbal.py:384-401`）：`dirs` 列表定义了 8 个罗盘方位却从未使用（死变量），实际只输出"顺时针/逆时针转 X°"。[建议]。
- `stop()` board 分支 `set_position(-1,-1)`（`gimbal.py:380`）依赖固件"负值=释放"约定，`.ino` 未见对应负值处理分支，仅见 `gimbal_writeAngle` 直写——**该约定是否真在固件生效存疑**，[建议]核对固件。

---

## 2. pose.py 逐项发现

### 2.1 位姿估计方法真相

标称"互补滤波 + 倾斜补偿罗盘 + Madgwick + 卡尔曼"（`pose.py:13-17`）。实际：

- **互补滤波** `ComplementaryFilter`（`pose.py:139-195`）：加速度计+陀螺仪，数学正确。
- **倾斜补偿罗盘** `TiltCompensatedCompass`（`pose.py:202-277`）：正确实现了 roll→pitch 旋转补偿 + `atan2(-mx_tilt, my_tilt)` 航向 + 磁偏角叠加。
- **Madgwick** `MadgwickFilter`（`pose.py:284-394`）：**名不副实，见 2.2**。
- **"卡尔曼滤波：GNSS+IMU 航位推算"**（`pose.py:17`）：**[占位/不存在]** 全文无任何卡尔曼滤波器实现。`update_gps` 只是位置字段直接覆盖 + 速度/航向简单加权（`pose.py:447-481`）。

### 2.2 [真bug] Madgwick 的加速度/磁力计修正被丢弃，9DOF 名存实亡

`pose.py:338-367` 实际执行顺序：

1. `:346` `q = q + q_dot*dt` —— 四元数只用陀螺仪积分完。
2. `:349-356` 由 q 算重力方向 `(gx,gy,gz)`，与归一化 accel 叉积出 `error`。
3. `:359` `gyro = gyro + self.beta * error` —— **改的是局部变量 `gyro`，此后再没有任何代码用它重新积分 q**。
4. `:362-364` 归一化的是第 1 步已经积分完的 q。

后果：**加速度计对重力的修正量算出来就扔掉了，对四元数零影响**。而且：

- **磁力计 `mag` 在 `:327-330` 归一化后，后续全程再未被引用**（9DOF 变 6DOF，且连 6DOF 的 accel 修正都没接回去）。
- 因为 `PoseFusion._use_madgwick=True`（`pose.py:426`，默认路径），`update_imu` 走 madgwick（`pose.py:430-433`），**yaw 等于陀螺仪 z 轴纯开环积分，无磁力计校正 → 真机上 yaw 必然随时间漂走**。这正是"合成姿态数据通过（测试只断言 `q is not None`，tests/test_full_integration.py:466-470）、真实 IMU 数据失败"的算法侧根因。

### 2.3 数值正确性（正确的部分，予以确认）

- 四元数运动学 `q_dot`（`pose.py:339-344`）：与 `0.5·q⊗ω` 标准展开逐项核对，**w/x/y/z 四项符号全部正确**。
- `get_euler` 转 ZYX 欧拉角（`pose.py:373-387`）：`atan2(2(wx+yz),1-2(x²+y²))` 等三式与标准公式一致，pitch 反正弦已做 ±1 截断（`:379-382`），yaw 归 0–360（`:388`）。**正确。**
- 互补滤波（`pose.py:177-187`）：gyro rad/s 经 `math.degrees` 转度×dt；`accel_roll=atan2(ay,az)`、`accel_pitch=atan2(-ax,√(ay²+az²))` 均为标准重力倾角公式；dt 窗口保护 `dt<=0 or dt>1`（`:173`）。**正确。**
- 罗盘倾斜补偿（`pose.py:254-269`）：先 roll 后 pitch 两次旋转矩阵展开正确；`yaw=atan2(-mx_tilt,my_tilt)` + 磁偏角（长春 -9°）真北换算符号正确。
- GNSS 速度 NED 分解（`pose.py:460-463`）：`v_n=speed·cos(course)`, `v_e=speed·sin(course)`，NED 约定正确；航向环绕处理（`:470-475`）正确。
- 角度单位：gyro 全程 rad、对外输出度，转换点都用了 `math.degrees/radians`，**未发现度/弧度混用**。

### 2.4 [空壳/占位] 没有任何硬件数据源——真机红的接入侧根因

- pose.py 全文**无串口打开、无 NMEA 解析、无 BMI260/TMAG5273/ATGM336H 驱动、无后台采样线程**。`IMUData`/`GPSData` 只是 dataclass。
- 真实入口是 agent.py 把它们包成 MCP 工具：`pose_update_imu`（`agent.py:1774`）`IMUData(**args)`——**加速度/陀螺/磁力数值由 LLM 当 JSON 参数填进来**；`pose_update_gps`（`agent.py:1795`）同理。
- 即：真机上不会有任何线程把 IMU/GNSS 数据流进来；要"喂数据"只能靠 LLM 凭空生成数字——这解释了为何合成测试全绿、接真实传感器必红。hal.py 经 grep 确认**无 Gimbal/GNSS/IMU 任何类**（690 行里 "gimbal/gnss/imu" 零命中，`serial` 仅是设备序列号字段）。
- [建议] `pose.py:443` 置信度 `confidence += 0.01` 单调递增永不衰减；`:479` `0.5+fix_quality*0.1` 固定值，与实际融合质量脱钩。

### 2.5 AR 投影（pose.py:500-672）

- `project_satellite`（`:529-588`）：只取 yaw 作水平指向、`-pitch` 作垂直指向（`:548-549`），**完全忽略 roll**；`device_elevation=-pitch` 的符号是个约定猜测。az 环绕归一（`:554-557`）正确；屏幕坐标线性映射 `0.5±diff/FOV`（`:566-567`）对小 FOV 可接受。
- `project_multiple`（`:650-672`）有被定义，但全仓仅此处自调用，无外部批量投影调用方——**接近孤儿**。

---

## 3. 与 hal.py 的关系（审查重点 3/4）

- **gimbal.py 完全绕过 hal.py**：rotctld 自己建 socket，board_pwm 走 `sdr_backend` 的 WebSocket MCP 通道注入（`sdr_backend.py:649-666`）。hal.py 里确实没有任何 Gimbal/GNSS/天线控制器（与 `docs/audit_r2/21_hal.md:39-41` 结论一致）。
- 因此"gimbal 是否真接硬件"的答案要分两半：
  - **rotctld / board_pwm 的电机指令链 = 真**（TCP / WebSocket→固件→LEDC，固件真实存在）。
  - **IMU 姿态回读链 = 假**（`set_pose_provider` 零调用，pose.py 又无硬件驱动）。所谓"IMU+磁力计闭环"两端都没接上。

---

## 4. 被谁真实调用（审查重点 6）

| 模块 | 真实调用方 |
|---|---|
| `GimbalController` 本体 | `sdr_tools.py:5829` 懒加载单例 → 7 个 MCP 工具（`gimbal_modes/connect/point/track_satellite/read_pose/stop/rssi_sweep`，注册于 `sdr_tools.py:1691-1777`）；`workflow_engine.py:202-206` 干扰源测向流程编排调用其中 4 个 |
| `RotctldClient` | 上述 + `agent.py:1035` 另起一个 `gimbal_move` 工具（直连 rotctld，绕过单例控制器） |
| `BoardGimbalClient` 通道 | `sdr_backend.py:655` 注入 send_callable |
| `PoseFusion/ARProjector` | `agent.py:118-119` 实例化，注册 `pose_update_imu/pose_update_gps/ar_project_satellite/ar_pointing_guidance`（`agent.py:1756-1824`）；`__init__.py:56` 导出 |
| 仅测试调用、生产零调用 | `ComplementaryFilter/TiltCompensatedCompass/MadgwickFilter` 直接实例化（仅 `tests/test_full_integration*.py`） |
| 孤儿（零调用） | `angle_to_servo_us`、`az_el_to_rotator_angles`、`GimbalStatus.pwm_*`、`project_multiple`、`ARMarker.size` 缩放逻辑仅展示 |

---

## 5. 问题清单汇总（按严重度）

**[真bug]**
1. `gimbal.py:186` board_pwm 发 `az%360(0-360)`，固件仅支持 0–180°（`.ino:1007,1044`），大方位角会打舵机过限位。
2. `gimbal.py:235` `set_pose_provider` 全仓零调用 → `read_pose`（`:244`）在 manual/board 模式恒返 (0,0)，"IMU 闭环"实为开环盲导。
3. `pose.py:359` Madgwick 加速度误差修正改的是局部 `gyro`，q 不再积分 → 修正零生效；`:327` 磁力计归一化后全程未用 → yaw 纯陀螺开环漂移。

**[空壳/占位]**
4. `pose.py:17` 宣称卡尔曼滤波，全文无实现（[占位]）。
5. pose.py 无任何 IMU/GNSS 硬件驱动线程，数据靠 LLM 填参（`agent.py:1774,1795`）——真机红根因之二。
6. `gimbal.py:88` `angle_to_servo_us`、`:408` `az_el_to_rotator_angles`、`pose.py:650` `project_multiple` 孤儿代码。

**[建议]**
7. `gimbal.py:149,153` rotctld 成功判定 `"0" in ln` 过宽，应精确匹配 `RPRT 0`；`:114` 停止命令文档(`\chkstop`)与代码(`S`)不一致。
8. `gimbal.py:380` board `set_position(-1,-1)` 停转约定未见固件分支核实。
9. `pose.py:443,479` 置信度模型与真实融合质量脱钩；AR 投影忽略 roll、`-pitch` 符号为约定猜测。
10. gimbal↔orbit 无代码级跟踪闭环，过境连续指向完全依赖 LLM 反复调工具。

**数值正确性结论**：四元数运动学、欧拉角换算、互补滤波、罗盘倾斜补偿、GNSS NED 速度分解、方位角最短误差归算均正确；角度单位（内部 rad/对外度）转换点齐全，未发现度/弧度混用；NED/ENU 约定自洽（orbit ENU ↔ gimbal 0北顺时针一致）。**数值层无硬错误，问题集中在"修正量被丢弃"和"硬件数据链路缺失"两处。**
