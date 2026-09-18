# ai-sdr Mini 接线表 v1.0（匹配 2026-09-17 立创原理图工程 + 固件 v0.6）

> 位号以你 09-17 原理图 BOM 为准（和旧 v0.7 文档不同，**已重排**）：
> U1=USB2514B Hub，U2=SI4732，**U3=AMS1117**，U4=ESP32-S3，U5=BMI260，U6=ATGM336H，
> **U7=24MHz 晶振**，**U8=自恢复保险丝**，X1=32.768kHz 晶振；
> USB1=USB-A 母座，USB2=Type-C，RF1=SMA，RF2=IPEX(u.FL)，SW1=复位按钮。
>
> 立创连法：优先用「网络标签 N」——同一网络名放同名标签即连通，不必拉满屏线。
> 下表「网络名」一列就是要放的标签。

---

## 〇、必须先补 / 先改（重要）

1. **补磁力计 TMAG5273A1QDBVR（立创 C5220660，SOT-23-6），位号给 U9；再加 1 个 100nF 去耦（位号 C24）。**
   固件 v0.6 已按 TMAG5273 地址 0x35、MAG_INT=IO13 写好驱动，当前原理图缺这颗芯片，罗盘/绝对航向无法实现。
2. **保险丝只要 1 个**：原理图里是 U8。PCB 旧布局里的 F1、U9（旧保险丝）是历史残留，更新网表后以 U8 为准，多余的删掉。
3. BOM 中 100nF 一行把 C9、C14 各重复列了一次（18 个条目、去重后 C2~C17 共 16 个）。在原理图里确认 C2~C17 **每个位号只有一个元件**；补 U9 后 100nF 总数应为 **17 个**（C2~C17 + C24）。
4. **SW1 接 EN（硬复位）**，不要接 IO0。IO0 只用 R9 上拉保证正常启动（固件把 IO0 定义为可选用户键 BOOT，当前不接按钮，留上拉即可）。

---

## 一、电源树（先连这部分）

```
USB2(Type-C) VBUS ── U8(500mA自恢复保险丝) ── +5V_USB
                                              ├─ U3(AMS1117) pin3 VIN ── C18(10µF)+C12(100nF) 到GND
                                              │      U3 pin2 VOUT = +3V3 ── C19(10µF)+C13(100nF) 到GND
                                              ├─ USB1(USB-A) pin1 VBUS
                                              └─ U1(Hub) VBUS 相关 / U1 pin27 经R18接+5V
+3V3 ── 给 U2 / U4 / U5 / U6 / U9 / U1数字脚，以及全部上拉电阻、LED阳极、L1馈电
所有 GND、连接器外壳、U1中心EP、U5散热焊盘、U6底部焊盘 ── GND（顶底双面铺铜）
```

| 网络名 | 连接点 |
|---|---|
| +5V_USB | USB2.VBUS → U8 → U3.pin3(VIN)、USB1.pin1、R18 上端 |
| +3V3 | U3.pin2(VOUT) → U2.pin14、U4.3V3、U5.VDD/VDDIO、U6.VCC、U9.pin4、U1.pin5/10/15/23/29/36、各上拉电阻、R1~R4 上端、L1 一端 |
| GND | 全部 GND 脚、pin7 RFGND、U1.pin11(TEST)、U1.EP、U5 散热盘、U6 底焊盘、U9.pin2/pin3、U3.pin1、连接器外壳、电容负端 |

---

## 二、USB（三组差分，等长短走）

| 网络名 | 一端 | 另一端 |
|---|---|---|
| USB_DM_UP | USB2 D- | U1.pin30 (USBDM_UP) |
| USB_DP_UP | USB2 D+ | U1.pin31 (USBDP_UP) |
| USB_DM2 | U1.pin3 (USBDM_DN2) | U4 IO19 (USB_D-) |
| USB_DP2 | U1.pin4 (USBDP_DN2) | U4 IO20 (USB_D+) |
| USB_DM3 | U1.pin6 (USBDM_DN3) | USB1.pin2 (D-) |
| USB_DP3 | U1.pin7 (USBDP_DN3) | USB1.pin3 (D+) |

- USB2 CC1 → R15(5.1k) → GND；CC2 → R16(5.1k) → GND（两个都接，设备模式取电）。
- USB1.pin4、USB2.GND、外壳 → GND。
- Hub 下行 port1(pin1/2)、port4(pin8/9) 预留空接（NC）。
- 差分 0.2mm 线宽/间距、等长、少过孔，下面铺完整 GND。

---

## 三、I2C 总线（3~4 个器件共用，挂 IO8/IO9）

| 网络名 | U4 ESP32 | 并接到 |
|---|---|---|
| I2C_SCL | IO8 | U2.pin11(SCLK)、U5.SCL、U9.pin1(SCL)；R5(4.7k) 上拉到 +3V3 |
| I2C_SDA | IO9 | U2.pin12(SDIO)、U5.SDA、U9.pin6(SDA)；R6(4.7k) 上拉到 +3V3 |

地址与配置脚：
- U2 SI4732：**pin10 SENB → GND**（地址 0x11）。
- U5 BMI260：**SDO → GND**（地址 0x68）、**CSB → +3V3**（选 I2C 模式）。
- U9 TMAG5273：地址固定 0x35，**pin3(TEST) → GND，不可悬空**。

---

## 四、I2S（SI4732 数字音频 → ESP32，ESP32 为主接收）

| 网络名 | U2 SI4732 | U4 ESP32 |
|---|---|---|
| I2S_BCLK | pin2 (GPO3/DCLK) | IO4 |
| I2S_LRCLK | pin1 (LOUT/DFS) | IO5 |
| I2S_DIN | pin16 (ROUT/DOUT) | IO7 |

---

## 五、UART / PPS（北斗，TX/RX 交叉）

| 网络名 | U6 ATGM336H | U4 ESP32 |
|---|---|---|
| GPS_TXD | TXD | IO17 (UART RX) |
| GPS_RXD | RXD | IO18 (UART TX) |
| GPS_PPS | PPS | IO16 |

---

## 六、控制 / 中断 / 复位

| 网络名 | U4 GPIO | 去向 | 上下拉 |
|---|---|---|---|
| SI_INT | IO10（输入） | U2.pin3 (GPO2/INTB) | — |
| SI_RST | IO11（输出） | U2.pin9 (RST，低有效) | R7(4.7k) 上拉 +3V3 |
| IMU_INT | IO12（输入，可先轮询） | U5.INT1 | — |
| MAG_INT | IO13（输入，可先轮询） | U9.pin5 (INT) | — |
| HUB_RST | IO21（输出） | U1.pin26 (RESET_N，低有效) | R10(10k) 上拉 +3V3 |
| EN_RST | U4.EN(pin3) | SW1（按下接 GND） | R8(10k) 上拉 +3V3 |
| BOOT | U4.IO0 | 不接按钮 | R9(10k) 上拉 +3V3 |

U5.INT2、U6.ON_OFF、U6.VBAT、U2.pin4(GPO1)/pin5(NC) → NC（空）。

---

## 七、LED（4 个全黄，靠闪烁模式区分）

| 网络名 | LED | 阳极 | 阴极接 U4 |
|---|---|---|---|
| LED1 | LED1 黄 | 经 R1(1k) → +3V3 | IO1（电源/运行） |
| LED2 | LED2 黄 | 经 R2(1k) → +3V3 | IO2（SDR 接收） |
| LED3 | LED3 黄 | 经 R3(1k) → +3V3 | IO3（GPS 定位） |
| LED4 | LED4 黄 | 经 R4(1k) → +3V3 | IO46（AI/录音） |

> 阴极接 GPIO、上电为高阻，灯不亮，满足 IO3/IO46 strapping 上电为低的要求。

---

## 八、晶振

**X1 = 32.768kHz（FC-135，给 SI4732，单端接法，只要 1 个负载电容）**
- X1 一端 → U2.pin13(RCLK)，该点同时接 **C1(12pF C0G) → GND**；
- X1 另一端 → GND。晶振下方不走线、周围包地。

**U7 = 24MHz（3225 四脚，给 Hub，必须）**
- U7 → U1.pin33(XTALIN)：接 **C22(18pF C0G) → GND**；
- U7 → U1.pin32(XTALOUT)：接 **C23(18pF C0G) → GND**；
- 四脚晶振外壳脚/空脚 → GND；紧贴 pin32/33（<5mm），下方不走线、包地打过孔。

---

## 九、射频前端

**RF1 = SMA（FM/AM 共用，边缘安装，中心孔压板边）**
- 中心针分两路：
  - 经 **C3(100nF) 隔直** → U2.pin6 (FMI)；
  - **直连** U2.pin8 (AMI)；
- U2.pin7 (RFGND) → GND，多打过孔；SMA 外壳 → GND。
- 50Ω 短直走线（1.5~2mm），顶层走、不打过孔、两侧地过孔。
- AM 匹配电感（旧版 68nH）已取消，不贴。

**RF2 = IPEX/u.FL（北斗有源天线）**
- 中心针 → U6.ANT；
- +3V3 经 **L1(100nH)** 馈到中心针（通直流、扼高频）；
- 中心针接 **C17(100nF) → GND** 做 π 滤波；走线短、50Ω、包地。

---

## 十、U1 USB2514B 必焊外围（缺一个 Hub 都不工作）

| 引脚 | 网络/接法 |
|---|---|
| pin14 CRFILT | C10(100nF) → GND |
| pin34 PLLFILT | C11(100nF) → GND |
| pin35 RBIAS | R17(**12kΩ ±1%**) → GND |
| pin27 VBUS_DET | R18(100k) 接 +5V、R19(100k) 接 GND，中点进 pin27（2:1 分压） |
| pin11 TEST | GND |
| pin22 / pin24 / pin25 / pin28 | 各经 R11/R12/R13/R14(10k) **下拉到 GND**（选内部默认 ROM、总线供电、端口可移除，免 EEPROM） |
| pin26 RESET_N | IO21 + R10 上拉 |
| pin5/15/23/36 VDD33、pin10/29 VDDA33 | 各接 100nF 去耦（C4~C9）→ +3V3 |
| EP 中心焊盘 | GND，多过孔 |
| pin12/13/16/17/18/19/20（各口电源使能/过流） | NC（不用外部电源开关，内部上拉） |

---

## 十一、电容分配（按最新位号，买料/贴片照此核对）

| 位号 | 值/介质 | 用途 |
|---|---|---|
| C1 | 12pF C0G 0402 | X1 / U2.pin13 单端负载 |
| C2 | 100nF | U2.pin14 VDD 去耦 |
| C3 | 100nF | SMA→U2.pin6 FMI 隔直（串联） |
| C4~C7 | 100nF | U1 VDD33：pin5/15/23/36 |
| C8~C9 | 100nF | U1 VDDA33：pin10/29 |
| C10 | 100nF | U1 pin14 CRFILT |
| C11 | 100nF | U1 pin34 PLLFILT |
| C12 | 100nF | U3.pin3 VIN 去耦 |
| C13 | 100nF | U3.pin2 VOUT 去耦 |
| C14 | 100nF | U4 ESP32 高频去耦 |
| C15 | 100nF | U5 BMI260 去耦 |
| C16 | 100nF | U6 北斗 VCC 去耦 |
| C17 | 100nF | RF2 ANT 端 π 滤波 |
| C18 | 10µF X5R 0805 | U3 VIN 输入 |
| C19 | 10µF X5R 0805 | U3 VOUT 输出 |
| C20 | 10µF X5R 0805 | U4 ESP32 电源 |
| C21 | 10µF X5R 0805 | U6 北斗 VCC |
| C22/C23 | 18pF C0G 0402 | U7 24MHz pin33/pin32 负载 |
| **C24（补）** | 100nF | **U9 TMAG5273 pin4 去耦** |

---

## 十二、ESP32-S3 strapping 与禁用脚

- IO0：上电为高（R9 上拉）= 正常 Flash 启动。
- IO3：上电为低（LED3 阴极，高阻即满足）。
- IO45：上电为低（未用，内部下拉）。
- IO46：上电为低（LED4 阴极，高阻即满足）。
- **GPIO26~GPIO32 连接模组内部 Flash/PSRAM（N8R8），禁止外接任何信号。**
- USB D-/D+ 固定 IO19/IO20。

---

## 十三、连完自检（跑原理图 DRC 必须清零）

1. 每个芯片电源脚旁都有 100nF，且连线到对应电源脚（不是只放了元件没连）。
2. I2C 全板只有两条网络 I2C_SCL / I2C_SDA，所有 SCL/SDA 并在一起，且只有 R5/R6 两个上拉。
3. USB 三组差分网络名没有串台（上行/port2/port3 各自成对）。
4. U1 的 CRFILT、PLLFILT、RBIAS(12k)、四个配置下拉、VBUS 分压一个不缺。
5. U7 24MHz 紧贴 pin32/33，X1 单端只接 C1 一个 12pF。
6. U9 TMAG5273 已补：SCL/SDA/INT(IO13)/VCC(C24)/pin2、pin3 接地。
7. EN 有 R8 上拉 + SW1 到地；IO0 有 R9 上拉；GPIO26~32 全部空着。
8. DRC「未连接引脚」逐条清零（明确 NC 的脚标 NC 或不连）。

---

## 十五、云台/舵机接口（v1.1 新增，板边预留 PH2.0-4Pin）

手自一体天线指向：AI 算卫星方位角/仰角 → 驱动云台或人工引导 → IMU+磁力计回读闭环。

**板边放一个 4Pin PH2.0 立式插座（或 4 个 2.54mm 焊盘）：**

| Pin | 网络名 | 接 U4 ESP32 | 说明 |
|---|---|---|---|
| 1 | GND | GND | 舵机地，与板子共地 |
| 2 | +5V_SERVO | +5V_USB（F1 保险丝**之后**） | 舵机电源，**禁接3V3**；并 100µF 电解 + 100nF 到 GND |
| 3 | SERVO_AZ | IO14（LEDC ch0，50Hz） | 方位舵机信号线（可串 220~470Ω 保护） |
| 4 | SERVO_EL | IO15（LEDC ch1，50Hz） | 俯仰舵机信号线 |

- 舵机脉宽 500µs(0°)~2500µs(180°)，固件 `gimbal_set` 已实现（az 0-180°、el 0-90°）。
- 板载舵机只覆盖方位 0-180°；360° 连续旋转/大型八木旋转器走软件 Hamlib rotctld（TCP 4533），不占板上资源。
- 无舵机时该插座可空，软件自动走 manual 模式（屏幕/手机 AR 指引人工转动）。
- **IO14/IO15 在本设计中原本空闲**，不与任何现有信号冲突。

---

## 十六、0918 工程位号勘误（覆盖前文旧位号，以此为准）

09-18 立创工程实际位号与本文前文（0917）不同，连线时**以下表为准**：

| 器件 | 前文(0917)位号 | **0918实际位号** |
|---|---|---|
| USB2514B Hub | U1 | U1（不变） |
| SI4732 | U2 | U2（不变） |
| TMAG5273 磁力计 | U9（待补） | **U3（已补）** |
| ESP32-S3 | U4 | U4（不变） |
| BMI260 | U5 | U5（不变） |
| ATGM336H 北斗 | U6 | U6（不变） |
| AMS1117-3.3 稳压 | U3 | **U7** |
| 24MHz 晶振 | U7 | **X2** |
| 32.768kHz 晶振 | X1 | X1（不变） |
| 自恢复保险丝 | U8（1个） | **F1（删掉多余的 U9，全板只留 1 个）** |

其他要点：
- 100nF 实际 19 个（C2~C17 + C24 + C25 + C26）：C24 给 U3 磁力计去耦；**C25 贴 ESP32 模组 3V3 脚旁、C26 贴 Type-C VBUS 旁**。
- 前文所有「U9 磁力计」字样一律改为「U3」；「U3 AMS1117」改为「U7」；「U7 24MHz」改为「X2」；「U8 保险丝」改为「F1」。

