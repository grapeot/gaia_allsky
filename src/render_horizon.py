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

    用 astropy 做精确变换。lst_hours: 地方恒星时(小时, 0-24), 决定天空朝向。
    返回 (az_deg 0-360, alt_deg -90..+90)。
    """
    from astropy.coordinates import SkyCoord, AltAz, EarthLocation
    from astropy.coordinates import Galactic
    import astropy.units as u
    from astropy.time import Time

    gal = SkyCoord(l=l_deg * u.deg, b=b_deg * u.deg, frame=Galactic)
    eq = gal.icrs  # 赤道 RA/Dec

    # 用一个已知 obstime+经度组合, 使其 LST == 目标 lst_hours。
    # LST = GMST + 经度。取 obstime=J2000 历元附近, 经度反解出目标 LST。
    # 简化: 直接用 hour angle 公式手算 alt/az(不依赖具体 UTC, 只需 LST)。
    ra = eq.ra.deg
    dec = eq.dec.deg
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
    canvas = np.zeros((H, W, 3), np.float32)
    np.add.at(canvas, (py[inside], px[inside]), L[inside, None] * cols[inside])
    if psf_px > 0:
        from scipy.ndimage import gaussian_filter
        for c in range(3):
            canvas[..., c] = gaussian_filter(canvas[..., c], psf_px)
    return canvas, az, alt
