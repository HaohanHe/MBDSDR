# LRPT 云图渲染研究 + ASTER 仿真帧（第④步·线 B）

> 日期：2026-10-10  作者：MBDSDR 工程轮（phase63）
> 配套：`lrpt-step1/2/3.md`（解调→FEC→端到端 IQ）
> 本步范围：LRPT 图像格式研究 + 仿真云图渲染最小集（纯 Python）。

---

## 1. 落地文件

| 文件 | 说明 |
|------|------|
| `mbdsdr_ai/lrpt_render.py` | 图像格式常量 + 段/虚拟通道数据结构 + 仿真云图生成 + PNG 渲染 |
| `mbdsdr_ai/tests/test_lrpt_render.py` | 12 个确定性 pytest |
| `experiments/output/lrpt_render/ch{0,1,2}_sim_{640,960,1920}w.png` | 仿真云图 PNG 输出（9 张） |

---

## 2. 格式研究结论（file:line 证据）

### 2.1 CADU → 虚拟通道 → APID → 通道

| 机制 | SatDump 位置 | 自述 |
|------|-------------|------|
| APID 64..69 → 通道 0..5 | `lrpt_msumr_reader.cpp:37-48` | 6 个通道，常用 3 个（可见光 1/2 + 红外） |
| CCSDS 包 → Segment | `lrpt_msumr_reader.cpp:52-53` | packet.payload → Segment 构造 |
| MCUN / 14 → 行号 | `lrpt_msumr_reader.cpp:59` | MCUN 是段计数器，除以 14 得行号 |

### 2.2 Segment 内部结构

| 机制 | SatDump 位置 | 自述 |
|------|-------------|------|
| 14 字节段头 | `segment.cpp:30-44` | day_time(2)+ms_time(4)+us_time(2)+MCUN(1)+QT(1)+DC/AC(1)+QFM(2)+QF(1) |
| 14 个 DCT 块/段 | `segment.cpp:79` | 每段 14 × 8×8 = 896 像素 |
| 像素布局 | `segment.cpp:165` | `lines[x/8][(i*8)+(x%8)]` → 8 行 × 112 列 |
| Huffman 解码 | `segment.cpp:70,87,111` | 比特流 → DC/AC 系数 |
| IDCT 反变换 | `segment.cpp:152` | 64 个频域系数 → 64 像素 |
| 像素归一化 | `segment.cpp:158-163` | +128 offset，clamp 0..255 |

### 2.3 整幅图像拼接

| 机制 | SatDump 位置 | 自述 |
|------|-------------|------|
| 每行 14 段横向拼接 | `lrpt_msumr_reader.cpp:198` | 8 × 14 × 14 = 1568 列宽 |
| 行传输周期 43 包 | `lrpt_msumr_reader.cpp:76` | 14 段 × 3 通道 + 1 遥测 = 43 |
| 段纵向堆叠成图 | `lrpt_msumr_reader.cpp:209-244` | 按 mcu_count 排序，逐行逐段填像素 |

---

## 3. 数据路径设计（payload → 像素）

```
CADU 字节流
  → CCSDS 包解包（VCDU 头 → APID）
  → APID 64..69 → 通道 0..5
  → 每包 payload → Segment 构造（14 字节头 + Huffman 数据）
  → Huffman 解码 → DC/AC 系数
  → 反量化 × IDCT → 8×8 像素块
  → 14 块横向拼 → 8×112 段
  → 14 段横向拼 → 8×1568 行
  → N 行纵向堆叠 → (H, 1568) 灰度图
  → image_enhance.py 后处理 → PNG
```

**本轮简化**：跳过 Huffman+IDCT，直接操作"已解码像素"域——即仿真云图生成器直接输出 8×112 段像素，验证段→图像→PNG 管线。Huffman+IDCT 留待后续轮。

---

## 4. 渲染落地 file:line

| 功能 | 位置 | 说明 |
|------|------|------|
| 格式常量 | `lrpt_render.py:37-44` | 段 8×112、行 14 段、宽 1568 |
| APID→通道映射 | `lrpt_render.py:47-54` | 64..69 → 0..5 |
| LrptSegment 数据结构 | `lrpt_render.py:62-69` | channel + mcu_count + pixels(8,112) |
| LrptVirtualChannel | `lrpt_render.py:74-96` | 段缓存 + to_image() 拼接 |
| 仿真云图生成 | `lrpt_render.py:103-135` | 渐变 + 高斯云斑，固定 seed |
| image_to_segments | `lrpt_render.py:148-163` | 整图 → 段列表（round-trip 测试用） |
| save_png | `lrpt_render.py:138-145` | Pillow L 模式 + 等比缩放 |

---

## 5. 测试结论（pytest 12/12 通过）

```
12 passed, 1 warning in 2.11s
```

| 测试类 | 用例数 | 结论 |
|--------|-------|------|
| TestFormatConstants | 2 | 段尺寸/行宽/APID 映射正确 |
| TestCloudSimulation | 3 | 固定 seed 可复现；3 通道；shape/dtype/范围正确 |
| TestSegmentRoundTrip | 2 | 已知像素→段→拼成图像逐字节一致 |
| TestPngRender | 3 | PNG 落盘成功；640/960/1920 三档缩放；空图抛错 |
| TestHonestEmpty | 2 | 无段→空图；缺段→填黑不伪造 |

---

## 6. PNG 输出路径

```
experiments/output/lrpt_render/
├── ch0_sim_640w.png   (21 KB)
├── ch0_sim_960w.png   (40 KB)
├── ch0_sim_1920w.png  (101 KB)
├── ch1_sim_640w.png   (22 KB)
├── ch1_sim_960w.png   (42 KB)
├── ch1_sim_1920w.png  (103 KB)
├── ch2_sim_640w.png   (21 KB)
├── ch2_sim_960w.png   (40 KB)
└── ch2_sim_1920w.png   (98 KB)
```

**诚实声明**：这些是**仿真云图**（渐变 + 高斯斑），非真实 LRPT 接收云图。

---

## 7. 诚实边界

1. **仿真数据非真实云图**：`generate_cloud_simulation()` 是确定性合成（渐变 + 高斯斑），仅用于验证渲染管线。
2. **跳过 Huffman+IDCT**：本轮直接操作"已解码像素"域，未实现 SatDump 的 Huffman 解码 + IDCT 反变换。真实 LRPT 的图像段是压缩的，解压留待后续轮。
3. **未接 CCSDS 包解包**：本轮直接构造 Segment 对象，未实现 VCDU→APID→包载荷的解包逻辑。
4. **三通道未合成 RGB**：本轮只存单通道灰度 PNG，未做 ch0/ch1/ch2 → RGB 伪彩色合成。
5. **C++ 侧渲染留待后续**：本轮纯 Python 验证；C++ 侧图像渲染管线不碰。

---

## 8. 红线与纪律

- **干净室**：lrpt_render.py 自写 MIT，未复制 SatDump 图像解码代码；file:line 仅作机制证据。
- **不预置呼号**：仿真数据无呼号。
- **不发射**：纯离线渲染。
- **不 git add/commit/push**：仅写文件。
- **隔离文件勿动**：未碰 `cpp/*`、`ui_diag_freeze.cpp`、`cpp/scratch/*`。
- **无 cpp 改动** → 双构型基线 D98/R98 139/139 保持有效（如实标注）。
