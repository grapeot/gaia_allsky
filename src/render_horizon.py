"""地平坐标全天星图: 模拟"站在地面、地球透明、平视即地平线"看到的真实全天星空。

与 render_starmap(银道坐标)的区别: 那个以银盘为水平基准(银河永远横平);
这个以**观测者地平**为基准——银河会像真实那样斜挂天上, 倾角随纬度+时刻变化。

坐标链: Gaia 银道(l,b) → 赤道(RA,Dec) → 给定纬度+地方恒星时 → 地平(Az,Alt)
       → equirectangular 矩形(方位角横轴 0-360°, 高度角纵轴 +90 天顶 / -90 天底,
         地平线在画面正中)。VR 球面贴图直接用。

复用 render_starmap 的 mag→亮度 + B-V→星色 + HDR/SDR tonemap。
"""
import numpy as np
import render_starmap as rs


# J2000 银道(l,b)→ICRS(ra,dec) 旋转矩阵。由三个银道基向量 (0,0)/(90,0)/(0,90)
# 过 astropy SkyCoord(Galactic).icrs 反解得到，与 astropy 数值一致(差 ~1e-12°)。
# 写死避免对亿级星点调 SkyCoord 的几十秒框架开销。
_G2ICRS = np.array([
    [-0.0548756577125922,  0.4941094371927267, -0.8676661375596585],
    [-0.8734370519556160, -0.4448297212232957, -0.1980763372730006],
    [-0.4838350736167157,  0.7469821839866677,  0.4559838136873021],
])


# Bortle 1-9 天空面亮度中值 (mag/arcsec², 来自 Sky&Telescope/Wikipedia 标准表)
# 数值越小=天空越亮=光污染越重。调研来源见 docs/。
BORTLE_MU = {1: 22.0, 2: 21.9, 3: 21.8, 4: 21.1, 5: 20.0,
             6: 19.2, 7: 18.6, 8: 18.0, 9: 17.5}


# skyglow 经验标定: 面亮度 μ(每角秒²) 与本渲染像素尺度差多个量级, 故标定一个
# scale 把物理梯度(μ 相对关系)映到视觉量级。标定锚点: Bortle5(μ=20, 郊区)的辉光
# ≈ 银河带典型线性亮度(0.9), 其余等级按 μ 自动拉开(每差1等差 10^0.4≈2.5 倍)。
SKYGLOW_SCALE = 56786.2


def skyglow_level(bortle, m_ref=8.0, scale=None):
    """Bortle 等级 → additive 天空辉光线性亮度(叠加到星空线性画布的均匀基底)。

    物理: 辉光是加性背景。面亮度 μ(mag/arcsec²) → 线性 L ∝ 10^(−0.4μ)。
    μ 相对关系(梯度)是物理的; 绝对值经 SKYGLOW_SCALE 标定到视觉量级。
    """
    mu = BORTLE_MU[int(round(bortle))]
    base = rs.mag_to_luminance(mu, m_ref)
    return base * (scale if scale is not None else SKYGLOW_SCALE)


def gal_to_altaz(l_deg, b_deg, lat_deg, lst_hours):
    """银道(l,b) → 地平(az, alt), 给定观测者纬度与地方恒星时。

    只依赖 LST(地方恒星时), 不绑定具体 obstime/UTC——故用 hour angle 直算 alt/az,
    而非 astropy.AltAz 框架。这样 LST 可外部自由生成(支持随恒星时扫天的动画)。
    lst_hours: 地方恒星时(小时), 超范围自动 wrap。返回 (az 0-360, alt -90..+90)。

    银道→赤道用预导出的 J2000 旋转矩阵纯 numpy 直算, 不走 astropy.SkyCoord——
    后者对亿级星点构造对象 + 高精度框架转换要几十秒、吃几十 GB; 矩阵乘与 astropy
    数值一致(差 ~1e-12°), 快两个量级。矩阵由三个银道基向量过 SkyCoord 反解得到。
    """
    l_r = np.radians(np.asarray(l_deg, float))
    b_r = np.radians(np.asarray(b_deg, float))
    cb = np.cos(b_r)
    v = np.stack([cb * np.cos(l_r), cb * np.sin(l_r), np.sin(b_r)])
    w = _G2ICRS @ v
    ra = np.degrees(np.arctan2(w[1], w[0])) % 360.0
    dec = np.degrees(np.arcsin(np.clip(w[2], -1.0, 1.0)))
    lst_deg = (lst_hours / 24.0) * 360.0
    H = np.radians((lst_deg - ra) % 360.0)         # 时角
    dec_r = np.radians(dec)
    lat_r = np.radians(lat_deg)
    sin_alt = np.sin(dec_r) * np.sin(lat_r) + np.cos(dec_r) * np.cos(lat_r) * np.cos(H)
    alt = np.degrees(np.arcsin(np.clip(sin_alt, -1, 1)))
    # 方位角(从北 0° 起, 向东增): 标准公式
    cos_alt = np.cos(np.radians(alt))
    sin_az = -np.cos(dec_r) * np.sin(H) / np.maximum(cos_alt, 1e-9)
    cos_az = (np.sin(dec_r) - np.sin(lat_r) * sin_alt) / \
             np.maximum(np.cos(lat_r) * cos_alt, 1e-9)
    az = np.degrees(np.arctan2(sin_az, cos_az)) % 360.0
    return az, alt


def project_horizon_equirect(az_deg, alt_deg, W, H):
    """地平 equirectangular: 方位角 0-360→x(横), 高度角 +90..-90→y(天顶上,天底下)。
    地平线(alt=0)在画面垂直正中。返回 (px, py, inside)。"""
    az = np.asarray(az_deg, float) % 360.0
    alt = np.asarray(alt_deg, float)
    px = (az / 360.0 * W).astype(int)
    py = ((90.0 - alt) / 180.0 * H).astype(int)   # alt=+90→y=0(顶), alt=-90→y=H(底)
    inside = (px >= 0) & (px < W) & (py >= 0) & (py < H)
    return px, py, inside


def render_horizon_map(l, b, mag, bv, lat_deg, lst_hours, W, H,
                       m_ref=8.0, psf_px=1.0, gain=1.0):
    """渲染地平坐标全天星图 → (H,W,3) 线性画布。地平线在画面正中。"""
    az, alt = gal_to_altaz(l, b, lat_deg, lst_hours)
    L = rs.mag_to_luminance(mag, m_ref) * gain
    cols = rs.bv_to_rgb(bv)
    px, py, inside = project_horizon_equirect(az, alt, W, H)
    canvas = rs.accumulate_stars(H, W, px, py, inside, L, cols, psf_px)
    return canvas, az, alt
