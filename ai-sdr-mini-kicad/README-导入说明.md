# ai-sdr Mini 原理图 / PCB 工程导入说明（v0.7.1）

项目：**MBDSDR**（AI 定义无线电）　硬件板名：**ai-sdr Mini**　呼号：BI4MIB
62 个元件、49 个网络、80×60 mm 双层板、全 SMT（SMA 直插除外）。
本工程为**单文件自包含**：符号库已内嵌进 `ai-sdr-mini.kicad_sch`，不依赖任何外部符号库，拷走一个文件也能打开。

---

## 一、文件清单

| 文件 | 说明 |
|---|---|
| `ai-sdr-mini.kicad_pro` | KiCad 工程文件（同名自动关联原理图+PCB） |
| `ai-sdr-mini.kicad_sch` | **原理图（本次交付，62 元件/49 网络，符号内嵌）** |
| `ai-sdr-mini.kicad_pcb` | PCB 布局参考（62 元件、80×60 板框+4 安装孔，已通过 kicad-cli Gerber/钻孔导出，尚未布线/铺地；投板以立创EDA从原理图转 PCB 为准） |
| `ai-sdr-mini.net` | KiCad legacy 网表（49 命名网络，跨工具/兜底用） |
| `BOM-MBDSDR-Mini-v0.7.1-最终下单版.xlsx` | 最终下单 BOM，19 种关键件带立创编号，含位号映射 |
| `原理图连接表-v0.7-匹配立创工程.md` | 逐引脚权威连接表（设计依据，建议通读） |
| `schematic_layout_preview.png` | 原理图布局预览图（自检用） |
| `generate_schematic.py` / `generate_pcb.py` / `gen_netlist.py` | 数据驱动生成脚本，改连接后可重生成 |
| `ai_sdr_mini_firmware_v0.5_WebOTA.ino` / `mbdsdr_protocol.h` | ESP32-S3 固件（Web OTA + MCP over WebSocket） |

---

## 二、打开 / 导入（三选一，推荐方案 A）

### 方案 A：立创EDA专业版（你正在用，可直接出 Altium/SMT，最推荐）
1. 打开**立创EDA专业版**（标准版也可，专业版对 KiCad 兼容更好）。
2. 顶部菜单：**文件 → 导入 → KiCad**。
3. 选择本目录的 `ai-sdr-mini.kicad_sch`（导入原理图）；PCB 同理选 `ai-sdr-mini.kicad_pcb`。
   - 若弹窗要求选择工程，选 `ai-sdr-mini.kicad_pro` 所在的整个文件夹。
4. 导入后即可看到 62 个元件，连接关系通过**同名网络标签（全局标签）**建立，无需手动拉线即已连网。
5. **需要 Altium 原生 `.SchDoc`**：在立创EDA专业版中 **文件 → 导出 → Altium Designer**，即可得到 `.SchDoc` / `.PcbDoc`。
6. SMT 贴片前：按 BOM 把每个元件**关联立创物料编号**（设计管理器/BOM 里逐个绑定，编号见 xlsx），再"原理图转 PCB"。

### 方案 B：Altium Designer 直接导入
- AD20 及以上：**File → Import Wizard → KiCad Design Files**，按向导选 `ai-sdr-mini.kicad_sch` / `.kicad_pcb`。
- 若你的 AD 版本 Import Wizard 里**没有 KiCad 原理图项**（部分版本只支持 PCB），请走方案 A：先用立创EDA专业版导入，再"导出 → Altium Designer"，得到的 `.SchDoc` 可在 AD 中直接打开。
- 说明：`.SchDoc` 是 Altium 闭源二进制格式，无法用脚本合法直接生成，因此本工程以开放的 KiCad 格式交付，再经立创EDA转换为 Altium 原生格式，连接关系零丢失。

### 方案 C：KiCad 原生打开（100% 兼容，免费）
1. 安装免费 **KiCad 7 或 8**。
2. 双击 `ai-sdr-mini.kicad_pro`，工程会同时载入原理图与 PCB。
3. 可直接 ERC 电气检查、转 PCB、布线、出 Gerber。

### 网表兜底
任何 EDA 若图形导入异常，可导入 `ai-sdr-mini.net`（标准 KiCad legacy 网表）：
- Altium：在 PCB 中 **Design → Netlist → Import Netlist**，或直接 File→Open 该 `.net`；
- 网表含全部 62 元件、49 网络、每个引脚归属，可据此重建连接，不会"decoding-error"。

---

## 三、重要：引脚号约定（替换模组符号前必读）

- **U1 USB2514B、U2 SI4732、U3 TMAG5273、U7 AMS1117**：符号引脚号**严格按数据手册真实物理引脚号**绘制，可直接对应封装。
- **U4 ESP32-S3-WROOM-1、U5 BMI260、U6 ATGM336H**：这三个是**模组/多脚器件**，符号引脚号采用 **GPIO/信号名**（如 `IO8`、`SCL`、`VCC`、`TXD`、`INT1`），**不是**模组物理脚序号。
  - 原因：不同封装/批次模组物理脚位可能不同，用信号名建网最不容易接错。
  - 在立创/Altium 用**官方库模组符号**替换时，**按信号名把同名网络接在一起即可**（例如官方 ESP32 符号的 GPIO8 接 `I2C_SCL`、GPIO9 接 `I2C_SDA`），网表 `ai-sdr-mini.net` 里这些引脚同样以信号名给出，逐条对应。
  - ATGM336H-5NR32-G 不同批次引脚排列可能有差异，投产前在立创库按信号名（VCC/GND/TXD/RXD/PPS/ANT）核对一次。

连接关系由**同名全局标签**决定，移动符号、重新走线都不会改变网络；这也是不会再出现"数据异常 decoding-error"的原因——网络不依赖手绘导线是否碰到引脚。

---

## 四、关键网络与设计要点（布线/投板前对照）

- **电源链**：Type-C VBUS → F1(500mA 自恢复) → +5V → AMS1117-3.3 → +3V3。C7/C8 在 **5V 侧用 0805/16V** 10μF（降额），其余 10μF 在 3.3V 侧。
- **USB 差分**：Type-C→Hub 上行（TYPEC_DM/DP）、Hub→ESP32（USB_DM/DP，IO19/20）、Hub→USB-A（USBA_DM/DP）。转 PCB 后这三对 D+/D- 走**等长、90Ω 差分、短而直**，参考地完整，不要过孔换层。
- **射频**：SMA 中心脚→SI4732 AMI(pin8) 直连为 `ANT_SMA`（AM/短波/广播宽频）；FMI(pin6) 经 **C4=100nF（严禁 33pF）** 耦合为 `FM_IN`。u.FL→ATGM336H 为 `GPS_ANT`，串 L2(100nH) 馈电、C16 接地。射频走线 **50Ω、短、直、包地过孔**。
- **SI4732 时钟**：RCLK(pin13) 接单端 32.768 kHz 晶振 X1（晶振另一脚接地）+ **1 个 C1=12pF**（单端时钟只挂一个负载电容）。
- **未用脚/后备脚（已按数据手册处理，勿悬空）**：USB2514B 的 OCS_N1/2/3(pin13/17/19) 接 **+3V3**（过流检测低有效，自供电 Hub 不用外部电源开关，接高防误关断），PRTPWR1-4 与未用端口1/4 的 D+/D- 保持 NC；ATGM336H 的 **VBAT、ON/OFF 接 +3V3**（后备电源保热启动、ON/OFF 低有效接高确保开机），nRESET/Reserved 悬空。
- **Hub 24 MHz 晶振** X2：C17/C18=18pF（负载 12pF 规格，18pF 可稳定起振，USB FS 频偏裕量大；追求极致可改 15pF）。
- **I2C**（IO8=SCL/IO9=SDA，R5/R6=4.7k 上拉）：SI4732=0x11（SENB 接地）、BMI260=0x68（SDO 接地、CSB 接 3.3V）、TMAG5273=0x35。
- **中断**：SI4732_INT→IO10、SI4732_RST→IO11（R19 上拉）、BMI260 INT1→IO12(`IMU_INT`)、TMAG INT→IO13(`MAG_INT`)；IO14/IO15 预留 NC。
- **Hub 配置**：R13–R16 为 CFG22/24/25/28 下拉配置；R12=12k±1% 为 RBIAS 偏置（精度影响 USB 眼图，务必 1%）；R17/R18=100k 分压给 VBUS_DET。
- **复位/启动**：EN 接按键 SW1 + R8(10k) 上拉；GPIO0(BOOT) 经 R11(10k) 上拉，保证正常启动。
- **LED**：LED1–4 黄色，阳极经 R1–R4=1k 接 +3V3，阴极接 ESP32 IO1/IO2/IO3/IO46（低电平点亮）。
- **去耦**：每个芯片 VCC 脚就近放 100nF（共 16 个），先大电容后小电容、过孔直接回地。
- **铺地**：顶层/底层完整地平面，射频与 USB 下方不分割；GND 共 52 个连接点。

---

## 五、已通过的校验（本版，金标准 = KiCad 7.0.11 kicad-cli）

本版已定位并修复立创EDA"**转换异常 decoding-error**"的两个根级原因：
1. **S 表达式不支持 `;` 注释**——旧文件里的 `;;` 注释被解析成非法节点（这是 decoding-error 的直接原因）。现由 `normalize_sch.py` 在生成末尾自动剥离注释并按 KiCad 官方缩进规范化。
2. **库符号局部坐标 y 轴向上、实例化到图纸时 y 取反**——旧生成器按 y-down 写库，导致竖直/非零 y 引脚上下颠倒、短线接空。已用最小复现实验锁定坐标真值并修正，引脚热点全部精确命中。

校验结果：
- kicad-cli **网表 / PDF / SVG / BOM 导出全部成功**，无任何加载错误；
- S 表达式括号严格配对、字符串闭合；62 个元件实例，位号零重复；
- **49 个命名网络全部 ≥2 个连接点，无悬空单点网络**；另有 **14 个设计 NC**（PRTPWR 开漏输出、未用端口 D 线、GPO1、真 NC、ESP IO14/15 预留、BMI INT2），均放置蓝色 no_connect 叉号，ERC 不报警；
- 数据手册复核修正：X1 晶振另一端接 GND（否则不起振）、ATGM VBAT/ON-OFF 接 +3V3、USB2514B OCS_N×3 接 +3V3；
- PCB 文件 `ai-sdr-mini.kicad_pcb` 通过 kicad-cli **Gerber 与钻孔（.drl）导出**，封装/板框/安装孔完整（已修 `(paper)`、`fp_text`、旋转角并入 `(at)`、网络名-编号自洽、晶振两脚不再短接）；
- 元件几何布局零重叠（脚本矩形检测）。

仍需人工完成（立创EDA GUI 中，脚本替代不了）：① 三个模组符号替换为官方库并按信号名对网；② 关联立创物料编号；③ 原理图转 PCB，USB 三对差分 90Ω 等长、射频 50Ω 手动布线，其余自动布线、双面铺地；④ DRC。
