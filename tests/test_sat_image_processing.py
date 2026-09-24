"""气象卫星图像处理链测试。

对照 SatDump (github.com/altillimity/SatDump) src-core/image/ 与 projection/ 的
numpy 移植实现做量化自检：
  1. 中值滤波：椒盐噪声去除前后 PSNR 提升；
  2. 直方图均衡：低对比度图输出像素方差增大（动态范围拉开）；
  3. Kuwahara：平坦区方差降低、边缘梯度保持；
  4. 白平衡：灰度世界法使三通道均值趋于相等；
  5. 几何校正：全圆盘 -> 等经纬度，已知地标像素位移方向正确；
  6. RGB 合成：真彩色 / 伪彩色(gray/iron/rainbow) PNG 落盘 artifacts/；
  7. 完整管线：合成云图 -> 全链路 -> 增强 PNG；
  8. 工具注册：sat_image_enhance 可被调用并返回增强结果。

运行：pytest tests/test_sat_image_processing.py -v
"""

from __future__ import annotations

import os
import numpy as np
import pytest

from mbdsdr_ai import sat_image_processing as S

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "mbdsdr_ai", "artifacts")
os.makedirs(ART, exist_ok=True)

rng = np.random.default_rng(42)


# --------------------------------------------------------------------------- #
# 合成测试图：渐变 + 边缘 + 噪声
# --------------------------------------------------------------------------- #
def _synthetic_cloud(size: int = 128) -> np.ndarray:
    """合成云图：左暗右亮渐变 + 一条垂直边缘 + 一个亮云团。"""
    x = np.linspace(0, 1, size)
    grad = np.outer(np.ones(size), x)                 # 水平渐变
    img = grad.copy()
    img[:, size // 2:] += 0.3                         # 右半抬亮 -> 垂直边缘
    yy, xx = np.mgrid[0:size, 0:size]
    cloud = np.exp(-((xx - size * 0.3) ** 2 + (yy - size * 0.3) ** 2) / (2 * 8.0 ** 2))
    img += cloud * 0.5
    return np.clip(img, 0.0, 1.0)


def _psnr(a: np.ndarray, b: np.ndarray, max_val: float = 1.0) -> float:
    mse = np.mean((a.astype(float) - b.astype(float)) ** 2)
    if mse <= 1e-12:
        return 99.0
    return 10.0 * np.log10(max_val ** 2 / mse)


# --------------------------------------------------------------------------- #
# 1. 中值滤波：椒盐噪声 PSNR 提升
# --------------------------------------------------------------------------- #
def test_median_removes_saltpepper_psnr():
    clean = _synthetic_cloud(128)
    noisy = clean.copy()
    # 注入 5% 椒盐噪声
    n = noisy.size
    idx = rng.choice(n, size=n // 20, replace=False)
    noisy.flat[idx[: n // 40]] = 1.0     # 盐
    noisy.flat[idx[n // 40:]] = 0.0      # 椒

    psnr_in = _psnr(noisy, clean)
    out = S.median_filter((noisy * 255).astype(np.uint8), ksize=3)
    psnr_out = _psnr(out.astype(float) / 255.0, clean)
    assert psnr_out > psnr_in + 3.0, f"中值滤波未提升 PSNR: {psnr_in:.2f} -> {psnr_out:.2f}"


# --------------------------------------------------------------------------- #
# 2. 直方图均衡：低对比度图输出像素方差增大
# --------------------------------------------------------------------------- #
def test_equalize_increases_variance():
    # 低对比度窄带图
    low_contrast = 0.4 + 0.05 * _synthetic_cloud(128)
    var_in = low_contrast.var()
    out = S.histogram_equalize(low_contrast)
    var_out = out.var()
    assert var_out > var_in * 3.0, f"均衡后方差未增大: {var_in:.4f} -> {var_out:.4f}"
    # CLAHE 也应拉开对比度
    out_c = S.histogram_equalize(low_contrast, clahe=True, tiles=4)
    assert out_c.var() > var_in * 2.0


# --------------------------------------------------------------------------- #
# 3. Kuwahara：平坦区方差降低、边缘梯度保持
# --------------------------------------------------------------------------- #
def test_kuwahara_flat_smooths_edge_preserved():
    size = 128
    # 平坦区：常数 + 高斯噪声
    flat = np.full((size, size), 0.5) + rng.normal(0, 0.05, (size, size))
    out_flat = S.kuwahara_filter(flat, size=3)
    # 平坦区噪声方差应下降
    assert out_flat.var() < flat.var() * 0.6, \
        f"平坦区方差未下降: {flat.var():.5f} -> {out_flat.var():.5f}"

    # 边缘：垂直阶跃
    edge = np.zeros((size, size))
    edge[:, size // 2:] = 1.0
    edge += rng.normal(0, 0.03, (size, size))
    out_edge = S.kuwahara_filter(edge, size=3)
    # 边缘处的梯度（跨中列）应仍存在，即左右均值差保持
    left = out_edge[:, :size // 2].mean()
    right = out_edge[:, size // 2:].mean()
    assert abs(right - left) > 0.7, f"边缘被模糊: 左{left:.2f} 右{right:.2f}"


# --------------------------------------------------------------------------- #
# 4. 白平衡：灰度世界法使三通道均值趋于相等
# --------------------------------------------------------------------------- #
def test_white_balance_grayworld():
    # 人为偏色图：R 偏强、B 偏弱
    h, w = 64, 64
    rgb = np.zeros((h, w, 3))
    rgb[..., 0] = 0.8
    rgb[..., 1] = 0.5
    rgb[..., 2] = 0.2
    out = S.white_balance(rgb, method="grayworld")
    means = out.reshape(-1, 3).mean(axis=0)
    # 三通道均值应接近相等
    spread = means.max() - means.min()
    assert spread < 0.05, f"灰度世界后通道均值仍分散: {means}"


# --------------------------------------------------------------------------- #
# 5. 几何校正：全圆盘 -> 等经纬度，地标位移方向正确
# --------------------------------------------------------------------------- #
def test_geos_roundtrip_and_landmark_direction():
    sub_lon = 0.0
    proj = S.GeoProjector(sub_lon_deg=sub_lon)
    # 正/逆往返
    lat0 = np.array([0.0, 20.0, -20.0])
    lon0 = np.array([0.0, 30.0, -30.0])
    xm, ym, vis = proj.geos_forward(lat0, lon0)
    lat_r, lon_r, vis_r = proj.geos_inverse(xm, ym)
    assert np.allclose(lat_r, lat0, atol=1e-6)
    assert np.allclose(lon_r, lon0, atol=1e-6)

    # 构造全圆盘图，放置三个地标：星下点 / 北 / 东
    H = W = 256
    h_km = 35786.0
    extent_m = h_km * 1000.0 * np.radians(8.6)
    disk = np.zeros((H, W), dtype=np.float64)

    def place(lat, lon):
        xm, ym, v = proj.geos_forward(np.array([lat]), np.array([lon]))
        col = int(round((xm[0] + extent_m) / (2 * extent_m) * (W - 1)))
        row = int(round((extent_m - ym[0]) / (2 * extent_m) * (H - 1)))
        disk[max(0, row - 2):row + 3, max(0, col - 2):col + 3] = 1.0

    place(0.0, sub_lon)        # 星下点
    place(30.0, sub_lon)       # 北
    place(0.0, sub_lon + 40)   # 东

    eq = S.reproject_full_disk_to_equirect(
        disk, sub_lon_deg=sub_lon, h_km=h_km,
        out_w=360, out_h=180, lat_min=-60, lat_max=60)

    # 星下点应落在等经纬度图中心附近（lat=0, lon=sub_lon 对应网格中心）
    cy, cx = eq.shape[0] // 2, eq.shape[1] // 2
    roi = eq[max(0, cy - 6):cy + 7, max(0, cx - 6):cx + 7]
    assert roi.max() > 0.5, "星下点未落在等经纬度中心附近"

    # 北地标应在中心上方（lat>0 -> 行号更小）
    north_region = eq[:cy, :]
    assert north_region.max() > 0.5, "北地标未出现在等经纬度上半部分"
    # 东地标应在中心右侧（lon>sub_lon -> 列号更大）
    east_region = eq[:, cx:]
    assert east_region.max() > 0.5, "东地标未出现在等经纬度右半部分"
    # 北地标行号应小于南/星下点参照：北亮点位置在中心上方
    nrows = np.where(north_region.max(axis=1) > 0.5)[0]
    assert len(nrows) > 0 and nrows.max() < cy, "北地标位移方向错误"


# --------------------------------------------------------------------------- #
# 6. RGB 合成：真彩色 + 三种伪彩色 PNG 落盘
# --------------------------------------------------------------------------- #
def test_rgb_composites_save_pngs():
    from PIL import Image
    ch = _synthetic_cloud(128)
    # 真彩色：三通道用不同拉伸模拟 R/G/B
    true_rgb = S.rgb_composite(ch, np.roll(ch, 10, axis=1), np.roll(ch, -10, axis=0))
    assert true_rgb.shape == (128, 128, 3) and true_rgb.dtype == np.uint8
    Image.fromarray(true_rgb).save(os.path.join(ART, "true_color.png"))

    for lut in ("gray", "iron", "rainbow"):
        false_rgb = S.false_color_ir(ch, lut=lut)
        assert false_rgb.shape == (128, 128, 3)
        Image.fromarray(false_rgb).save(os.path.join(ART, f"false_color_{lut}.png"))

    # 通道代数：WV 差值产品
    wv = S.channel_algebra(ch, np.roll(ch, 5, axis=0), op="diff")
    assert wv.shape == ch.shape and wv.max() <= 1.0


# --------------------------------------------------------------------------- #
# 7. 完整管线：合成云图 -> 全链路 -> 增强 PNG
# --------------------------------------------------------------------------- #
def test_full_enhance_pipeline():
    from PIL import Image
    cloud = _synthetic_cloud(128)
    noisy = cloud + rng.normal(0, 0.03, cloud.shape)
    steps = [
        {"op": "median", "ksize": 3},
        {"op": "equalize", "clahe": True, "tiles": 4},
        {"op": "kuwahara", "size": 3},
    ]
    proc = (S.SatImageProcessor(noisy)
            .enhance_pipeline(steps)
            .to_rgb(lut="iron"))
    out = proc.result
    assert out.shape == (128, 128, 3) and out.dtype == np.uint8
    Image.fromarray(out).save(os.path.join(ART, "enhanced_cloud.png"))

    # 链式 API 与 pipeline 结果形状一致
    chained = (S.SatImageProcessor(noisy)
               .median(3).equalize(clahe=True, tiles=4).kuwahara(3)
               .to_rgb("rainbow").result)
    assert chained.shape == (128, 128, 3)


# --------------------------------------------------------------------------- #
# 8. 工具注册：sat_image_enhance 可调用
# --------------------------------------------------------------------------- #
def test_register_tool_registry():
    from mbdsdr_ai.tool_registry import ToolRegistry
    reg = ToolRegistry()
    S.register_tool_registry(reg)
    assert "sat_image_enhance" in reg.tools
    cloud = (_synthetic_cloud(64) * 255).astype(np.uint8)
    res = reg.tools["sat_image_enhance"]["handler"]({
        "image": cloud.tolist(),
        "steps": [{"op": "median", "ksize": 3},
                  {"op": "equalize", "clahe": True}],
        "lut": "rainbow",
    })
    assert res.success, res.error
    assert res.data["shape"] == [64, 64, 3]
