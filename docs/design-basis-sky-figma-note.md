# §1 补充（天空线提交）：Figma 与 index.html 的基准边界

> 用途：供 UX 线并入 `docs/design-basis.md` §1。结论均可按文末命令复跑。
> 核查日期：2026-11-05；核查对象：`D:\mbdsdr\.figma`（30 个节点文件夹）、
> `D:\mbdsdr\output\mbdsdr-product\dist\index.html`。

## 1. 结论（三条）

1. **`.figma` 是 CarWith 车机 HMI 设计稿，只能当「视觉语言参考」**：密度、对比、
   层级、数据块排布、状态呈现方式可以参考；**不能当屏幕结构依据**。
2. **天空屏的结构依据只有 `index.html`**（原型页面内有真正在跑的 `#skyCanvas`
   天空渲染脚本，实际实现 恒星 / DSO / 卫星 / 星座线 / 标签 / 大气 / 地景）。
   Figma 里没有天空屏。
3. **月相与行星绘制，两份基准都没有对应物**：`index.html` 没有画月亮/行星，
   `.figma` 没有天空节点。因此 `806f207` / `5332140` 的月相亮区与行星圆面
   填亮区属**纯自拟**，保留/修正/回退待 design-basis 表裁决。

## 2. `.figma` 为什么定性为车机 HMI（证据）

- **文件构成**：30 个节点文件夹，每个 `index.jsx` + `index.module.scss`；
  另有 36 个 `.png`、37 个 `.svg` 资源，总计约 9.5 MB。画布固定 **1920 × 1200**
  （多个 `index.module.scss` 首行 `width: 1920px`，如 `284_644`、`284_76`、
  `284_643`、`284_92`），顶栏高 100px、纯黑 `#000000`、字体 MiSans
  —— 典型横屏车机/座舱 HMI 版式。
- **字符串证据（逐条可复核）**：
  - `284_586/index.jsx:16-18`：`<p className={styles.starboy}>Starboy</p>`、
    `<p className={styles.text}>音乐</p>`、`<p className={styles.theWeeknd}>The Weeknd</p>`
    —— 音乐播放卡片。
  - `284_644/index.jsx:11-12`：`<p className={styles.a235}>23.5</p>` +
    `<p className={styles.a}>°</p>`（温度 23.5°，在 JSX 里拆成两段，所以全文搜
    `23.5°` 不命中，搜 `23.5` 命中）；同文件 30-31 行 `playPause` / `track`，
    39 行 `vol` —— 顶栏温度 + 播放控制。
  - `284_76/index.jsx:12-13`：`<p className={styles.a468Km}>468km</p>` +
    `<p className={styles.text}>预估</p>` —— 续航预估；同文件 9 行 `20:36`
    时钟、23-25 行消息图标。
  - `284_643` 同样含 `468km`。
  - 仓库自身也把它命名为 CarWith：`desktop/tokens.py` 文件头写
    「MBDSDR 桌面端 CarWith 设计体系 Token 单例 … 把 Figma 设计稿
    （`docs/design_ref/carwith_figma/`，画板 284_75 / 284_410 / 284_456 / 284_566，
    画布 1920x1200）从 SCSS 提取的精确设计令牌收敛到一处」。
- **没有天空内容**：全目录搜 `星空` / `天空` / `望远镜` / `频谱` / `卫星` /
  `月球` / `月相` / `SDR` 均为 **0 命中**；搜 `Star` 只命中歌曲名 `Starboy`
  （`284_586`、`284_644`、`284_92`），不是天文恒星。

## 3. `index.html` 能提供什么、不能提供什么

- **能提供（结构依据）**：`#skyCanvas` 脚本（约 36 KB）实际实现恒星（HYG v4.1，
  25,792 颗，V≤7.5）、星座线、DSO、卫星（示意轨道）、标签避让、大气/地景、
  方位基点；原型文档 §5 给出的完整层序为「清屏 → 背景巡天 → 星/行星 →
  星座线 → 大气(加法) → 雾带 → 地景 → 方位基点 → 标签(2D 重投影) → 选中指针」；
  FOV `1.5–160`（初始 160，入场收至 100）。注意：该层序里的「行星」只在文档
  清单中出现，**脚本内 `planet` 0 次**，不能据此认为原型画了行星。
- **不能提供（无对应物）**：全文 `月相` 0 次、`月亮` 0 次；`moon` 仅 1 处
  （`h_moon`，只用于地平亮度模型）；`planet` 6 次全部是 i18n 文案里的
  “Planetarium Mode / 星象仪模式”，`#skyCanvas` 脚本内 `planet` / `moon` /
  `sun` 各 **0 次**；`sun`（不区分大小写）19 次全部用于昼夜亮度与大气，
  **没有日面绘制**。
- 因此：月相亮区（亮缘半圆 + 晨昏线半椭圆）、月面朝向、未知月相占位、
  行星窄视场圆面填亮区、行星识别色，**均无设计稿依据**。

## 4. 使用边界（给实现线）

| 用途 | 允许 | 说明 |
|---|---|---|
| 颜色 / 圆角 / 字号 / 间距 / 动效 | 可以（作为视觉语言） | 走 `desktop/tokens.py` 的 CarWith 令牌，不散落魔法数字 |
| 数据块排布、卡片密度、对比层级、状态呈现 | 可以（作为视觉语言） | 只参考排布与层级，不照搬车机屏结构 |
| 天空屏布局、图层顺序、天体符号形状 | 不可以 | 结构依据只认 `index.html` |
| 月相/行星绘制的几何与外观 | 不可以 | 两份基准都没有对应物，须单独裁决 |

## 5. 复跑命令

```powershell
# Figma 车机字符串
Select-String -Path D:\mbdsdr\.figma\*\index.jsx -Pattern "Starboy|23\.5|468km|音乐"
# Figma 无天空内容（应无输出）
Select-String -Path D:\mbdsdr\.figma\* -Pattern "星空|天空|望远镜|频谱|卫星|月球|月相|SDR"
# index.html 无月亮/行星绘制（应输出 0）
$all=[IO.File]::ReadAllText('D:\mbdsdr\output\mbdsdr-product\dist\index.html')
foreach($k in '月相','月亮'){ "$k`t$([regex]::Matches($all,$k).Count)" }
$i=$all.IndexOf('#skyCanvas'); $end=$all.IndexOf('</script>',$i); $seg=$all.Substring($i,$end-$i)
foreach($k in 'planet','moon','sun'){ "$k`t$([regex]::Matches($seg,$k).Count)" }
```

## 附：30 个节点文件夹

`284_76 284_88 284_89 284_90 284_91 284_92 284_119 284_120 284_121 284_123
284_124 284_125 284_126 284_127 284_128 284_129 284_130 284_131 284_177 284_178
284_567 284_586 284_599 284_612 284_613 284_614 284_618 284_633 284_643 284_644`

## 6. 三个未识别节点：可核实清单（待用户裁定）

> 只写 `.figma/<node>/index.jsx` 与 `index.module.scss` 里**直接读得到**的事实。
> **这三个节点的导出物都不含画布坐标、父节点引用或兄弟顺序**，因此
> 「位于哪个区域」「与已识别节点的相邻关系」在本目录内**不可核实**——需要
> 用户在 Figma 里看，或另给带 `absoluteBoundingBox` / `parent` 的节点 JSON。
> 下表的尺寸、透明度、圆角、子结构、类名均逐字来自 SCSS/JSX；「推算」列
> 仅按 SCSS 字面声明做算术，不等于渲染实测。

| 节点 | 尺寸 | 装饰属性（可核实） | 子结构（可核实） | 文案 | 区域 / 相邻关系 | 猜测（未核实） |
| --- | --- | --- | --- | --- | --- | --- |
| `284_130` | 1175×79 | `background:#d9d9d9`、`opacity:0.5` | 单 div（类名 `rectangle1077`），**无子节点** | 无任何文字节点 | 不可核实 | — |
| `284_131` | 1175×694 | `overflow:hidden`、flex 行容器 | `frame137` → 5 个列容器（第 1 个 `autoWrapper` 无左距，后 4 个 `autoWrapper2` 各 `margin-left:80px`）→ 每列 3 个单元（`frame122` + 2×`frame127`，`frame127` 各 `margin-top:49px`）→ 每单元 = `ellipse19`（171×171、`border-radius:50%`、`opacity:0.2`）+ `rectangle1081`（89×42、`margin-top:20px`、`opacity:0.23`）。即 **5 列 × 3 行 = 15 个同构「圆 + 条」单元**；宽度 5×171+4×80=1175；按 CSS 声明推算内容高 3×233+2×49=797 > 694，底部约 103px 被裁剪 | 无任何文字节点 | 不可核实 | — |
| `284_177` | 334×334 | `background:#d9d9d9`、`opacity:0.2`、**`border-radius:8px`** | 单 div（类名 `rectangle1083`），**无子节点** | 无任何文字节点 | 不可核实 | — |

**可核实的唯一跨节点数值关系**：`284_130` 的宽度 1175 px 与 `284_131`
的 `frame137` 宽度 1175 px 相同（两者都等于 5×171+4×80 的列排布宽度）。
除此之外，三个节点与其余 27 个节点之间在本目录内**没有可核实的坐标或父子关系**。

**对「334×334 大圆」这一描述的更正**：`284_177/index.module.scss` 写的是
`border-radius: 8px`，是一个 334×334 的**圆角方形**，不是 `50%` 的圆；
类名 `rectangle1083` 也指向矩形。若用户据「大圆」的说法裁决，需先确认。

复跑：

```powershell
Get-Content D:\mbdsdr\.figma\284_130\index.module.scss
Get-Content D:\mbdsdr\.figma\284_131\index.jsx, D:\mbdsdr\.figma\284_131\index.module.scss
Get-Content D:\mbdsdr\.figma\284_177\index.module.scss
```

