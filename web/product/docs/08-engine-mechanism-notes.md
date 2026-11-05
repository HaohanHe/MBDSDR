> 来源：原型阶段的引擎机制研究，现已并入产品文档体系。
> 许可证纪律见 [06-compliance.md](06-compliance.md)：只记数学、几何与算法，
> 不含 AGPL 源码原文；产品代码全部独立重写。

# Stellarium Web Engine 机制研究笔记（学机制、自研实现）

> 来源：D:/mbdsdr/reference/stellarium-web-engine/（AGPL-3.0）源码精读。
> 许可证纪律：只记数学/几何/算法思路，不含源码原文；产品（MIT）不引 AGPL 代码，
> 按本笔记**独立重写**。核心文件：src/modules/{landscape,atmosphere,movements,
> pointer,labels}.c、src/{navigation,observer,uv_map}.c、src/render_gl.c、
> data/shaders/*.glsl、资源样例 apps/test-skydata/landscapes/guereins/。

## 0. 总体架构
- 万物皆 obj_t，按 render_order 升序渲染：银河5→巡天6→星20→星座25→行星30→
  卫星31→坐标线34→**大气35**→**地景40**→方位基点50→标签100→**选中指针199**。
- 统一画笔 paint_quad：参数曲面细分 → UV→3D方向→视图系→投影→NDC。
- 帧变换链 ICRF(赤道) ↔ OBSERVED(地平: z=天顶) ↔ VIEW(GL y-up)；
  ro2v = Rz_gl · Rx(roll) · Ry(pitch) · Rz(-yaw) · ro2m。
- 投影可插拔：默认 stereographic（fov≤360°），另有 perspective/Mercator/
  Mollweide/Hammer；投影函数 CPU/GPU 双份（裁剪/拾取/着色）。

## 1. 地景（landscape）——"地景是动的"的机制
- 资源=HiPS 球面巡天（12×512px webp 带 alpha，天空区 alpha=0；Allsky 192×256
  占位图）。球面（HEALPix）投影，非圆柱。
- **at_infinity（w=0）**：地景在无穷远，只随视线旋转、无视差。
- **固定在 FRAME_OBSERVED**：星空（ICRF）随时间绕天极转，地景纹丝不动——
  物理正确。改 yaw/pitch → ro2v 变 → 地景随拖拽整幅移动（同一矩阵）。
- **缩放=改 fov**，地景同其他天体一起拉近拉远；LOD 公式
  render_order = round(log2(π·f·win_h/(4√2·w)))，f=1/tan(fovy/2)。
- 透明度规则：alpha_fov = smoothstep(1°,20°,fov)（放大到 1° 内淡出防糊）；
  alpha_down = smoothstep(0°,-45°,视线 alt)（俯视地下渐隐一半）。
- 地平过渡四层：①贴图 alpha 抠天空 ②雾带 alpha=0.15·smoothstep(0.2,0,|sin alt|)
  （±11.5° 高度内衰减）③昼夜亮度 b=1.5·(sin(h_sun+8°)+1/15)₊+0.2·phase·
  (sin(h_moon+8°)+1/15)₊ ④地景层豁免 hide_below_horizon。
- **Canvas 2D 复刻（没有全景图时的正确画法）**：地景 = 地平以下整块遮挡。用
  **逐屏幕列**求 `alt(y) = h(az)` 的零点（同一列里 alt 对 y 单调，二分必收敛），
  得到每列边界后一次性填到屏幕底——相机抬头时该列整列是天、俯视时整列是地，自动正确。
  **不要用"方位×高度分块拼多边形"**：地平以下的球冠在水平视角下会跨过相机平面
  （天底方向 Z≈0），被裁掉的块会在画面上留下阶梯状空洞（本产品第一版就是栽在这）。
- 有真实全景图时另说：等距柱状贴图 + 列切片（每 2-4px 竖条按 az 取源）或
  水平偏移 `offsetX = -yaw/2π·imgW`（fov<60°）；顶沿羽化 + 雾带 + globalAlpha。

## 2. 相机/视图
- 姿态=yaw(方位)+pitch(俯仰,±90°)+roll；**缩放改 fov 不推拉**；
  初始 az=0, alt=30°, fov=50°。
- **拖拽=grab-the-sky**：按下点反投影 (az0,alt0)，移动中 yaw+=az0-az_cur、
  pitch+=sal-dal——按下点的天体钉在光标下（非线性灵敏度）。
- 滚轮 fov/=k（clamp [1/3600°,120-185°]）+ zoom-to-cursor 反补 yaw/pitch；
  连续缩放指数式 fov*=(1+0.05·(-zoom))^(60dt)；键盘转速 ∝ fov（广角快转）。
- **投影 = 视图空间 → 平面；裁剪 = 窗口矩形**。`projection.c` 的 `project_to_win()`
  把 NDC 乘 `window_size` 得到像素；`painter.c` 里只判 x,y 是否落在 `[0,w]×[0,h]` 内。
  **整条链路没有任何圆形遮罩**——相机在球心，看到的是平面窗口里的一块球面影像。
- 投影可插拔二选一：`proj_perspective.c`（`out=v` 原样，只乘 4×4 透视矩阵，
  `max_ui_fov=120°`）与 `proj_stereographic.c`（`x'=2x/(1-z)`，`max_ui_fov=185°`）。
  大视场从透视切到立体，是引擎的既定策略，不是二选一的风格偏好。
- **Canvas 2D 复刻**：透视 `f=(min(W,H)/2)/tan(fov/2)`，`x=cx+X/Z·f`、`y=cy−Y/Z·f`；
  立体 `ppr=(min(W,H)/2)/(2·tan(fov/4))`，`k=2/(1+Z)`，`x=cx+X·k·ppr`。
  屏幕外的点直接丢；跨相机平面（`Z ≤ NEAR`）的线段必须先截断再投影，
  否则投影分母趋零，会在画面上拉出一条横穿全屏的假线。
- 拖拽=钉点式；fov 指数插值；zoom-to-cursor 用同一套反投影做前后对比补 yaw/pitch。

## 3. 选中指针/标签
- 纯 2D 屏幕层（render 199），**每帧重新投影**（不烘焙进场景矩阵）。
- 4 段 8px 短划线（相隔 90°，宽 3px），半径 r=max(r+5,8)+0.4·(sin(2πt/2s)+1.1)
  ——**周期 2s 呼吸动画**；正上方有标签则跳过那条划线。
- 标签：每帧 3D→2D 重投影；锚点偏移按对齐推离中心 radius 像素；
  重叠 >8px 淡出；被遮挡淡出；远→近绘制。默认对齐=天体正上方。

## 4. 大气/地平辉光
- 非贴图：**Preetham 解析模型**逐顶点算色（色度只依赖 θ,θS,浑浊度 T）+
  Garstang 类夜天光亮度（含光污染 0.0004·(bortle-1)^2.1）。
- 感知映射：中间视觉蓝移（Jensen）+ Schlick 对数色调映射（p≈2.2, lwmax=5000）
  →"夜里天变深蓝"。
- 粗网格（48 tile × split4）顶点算色、fragment 插值；下半球镜像。
- **加法混合 (ONE,ONE)** 画在星空后——白天压星、夜里增辉。
- 地平辉光=模型自然产物（天顶暗地平亮、日落方位暖红）+ 白雾带强化。
- Canvas 2D 近似：低分辨率色块+渐变插值；globalCompositeOperation='lighter'。

## 5. WebGL 天球渲染最小架构清单
渲染顺序：清屏→背景巡天→星/行星→星座线→**大气(加法)**→**雾带(alpha)**→
**地景(alpha, w=0, OBSERVED 系)**→方位基点→标签(2D 重投影)→选中指针(2D)。
必备：yaw/pitch/roll/fov、view 矩阵、f=1/tan(fovy/2)、Px/Py[5]+kx/ky、
sunDir、色调映射 p/lwmax/exposure、地景 alpha_fov/alpha_down/brightness、
雾带 smoothstep、指针呼吸相位。绘制原语四件：球面网格/点精灵/折线/2D 文本，
共用"方向→视图系→投影→NDC"管线。
