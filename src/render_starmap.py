"""全天星图渲染: 星等→亮度 + B-V→星色, 投影到全天 (equirectangular / Mollweide)。

复用自 turquoise_band 的物理星场管线。每颗真实恒星按真位置投影、真星等定亮度、真色温定星色。
银河应从恒星密度自然涌现, 无需特殊处理。

SDR/HDR 两套输出。先低分辨率 SDR 验证银河涌现。
"""
import numpy as np


def mag_to_luminance(vmag, m_ref=0.0):
    """星等 → 相对亮度。L = 10^(-0.4·(m − m_ref))。m_ref=0 即以 0 等星为 1.0。"""
    return 10.0 ** (-0.4 * (np.asarray(vmag, float) - m_ref))


def bv_to_rgb(bv):
    """B-V 色指数 → 归一化 RGB 星色。蓝白(B-V<0) → 白(~0.6) → 橙红(>1.4)。

    简化色温映射(够定性看星色; 银河里蓝白年轻星 vs 橙红老星的色差能显出来)。
    """
    bv = np.asarray(bv, float)
    r = np.clip(0.72 + bv * 0.30, 0.45, 1.20)
    b = np.clip(1.22 - bv * 0.48, 0.40, 1.25)
    g = np.clip(1.05 - np.abs(bv - 0.45) * 0.20, 0.55, 1.12)
    rgb = np.stack([r, g, b], axis=-1)
    return rgb / np.maximum(rgb.max(axis=-1, keepdims=True), 1e-6)  # 归一(色相, 亮度由mag定)


def project_equirectangular(lon_deg, lat_deg, W, H):
    """等距圆柱投影: 经度(0-360)→x, 纬度(-90~90)→y。返回像素 (px, py, inside)。
    用银道坐标(lon=l, lat=b)则银河在中间水平带。"""
    lon = np.asarray(lon_deg, float) % 360.0
    lat = np.asarray(lat_deg, float)
    px = (lon / 360.0 * W).astype(int)
    py = ((90.0 - lat) / 180.0 * H).astype(int)
    inside = (px >= 0) & (px < W) & (py >= 0) & (py < H)
    return px, py, inside


def project_mollweide(lon_deg, lat_deg, W, H):
    """Mollweide 等积投影(全天椭圆, 银河带形状更真实)。返回 (px, py, inside)。"""
    lon = np.radians((np.asarray(lon_deg, float) + 180.0) % 360.0 - 180.0)  # -π..π
    lat = np.radians(np.asarray(lat_deg, float))
    # 解 2θ+sin2θ=π·sinφ (牛顿迭代)
    theta = lat.copy()
    for _ in range(8):
        theta = theta - (2 * theta + np.sin(2 * theta) - np.pi * np.sin(lat)) / \
                (2 + 2 * np.cos(2 * theta))
    x = (2 * np.sqrt(2) / np.pi) * lon * np.cos(theta)   # ∈ [-2√2, 2√2]
    y = np.sqrt(2) * np.sin(theta)                       # ∈ [-√2, √2]
    px = ((x / (2 * np.sqrt(2)) + 0.5) * W).astype(int)
    py = ((0.5 - y / (2 * np.sqrt(2))) * H).astype(int)
    inside = (px >= 0) & (px < W) & (py >= 0) & (py < H) & np.isfinite(x) & np.isfinite(y)
    return px, py, inside


def accumulate_stars(H, W, px, py, inside, luminance, rgb, psf_px=0.0):
    """星点累加到 (H,W,3) 线性画布。px/py 整型像素坐标, inside 有效标志。

    各 render 模块共用的散点累加(消除 np.add.at 多处重复)。psf_px>0 加高斯 PSF 光晕。
    """
    canvas = np.zeros((H, W, 3), np.float32)
    np.add.at(canvas, (py[inside], px[inside]), luminance[inside, None] * rgb[inside])
    if psf_px > 0:
        from scipy.ndimage import gaussian_filter
        for c in range(3):
            canvas[..., c] = gaussian_filter(canvas[..., c], psf_px)
    return canvas


def render_starmap(lon_deg, lat_deg, mag, bv, W, H, projection="mollweide",
                   m_ref=0.0, psf_px=0.0, gain=1.0):
    """渲染全天星图 → (H,W,3) 线性画布。

    lon/lat: 银道坐标(度), 银河在中间。mag: 星等。bv: B-V 色指数。
    每颗星: 投影落点 + 亮度(mag) × 星色(bv)。密集处(银河)自然累加变亮。
    """
    L = mag_to_luminance(mag, m_ref) * gain
    cols = bv_to_rgb(bv)
    proj = project_mollweide if projection == "mollweide" else project_equirectangular
    px, py, inside = proj(lon_deg, lat_deg, W, H)
    return accumulate_stars(H, W, px, py, inside, L, cols, psf_px)


def mollweide_mask(W, H):
    """Mollweide 椭圆边界 mask: 椭圆内 True, 外 False(渲染外区填黑/透明)。"""
    yy, xx = np.mgrid[0:H, 0:W]
    nx = (xx + 0.5) / W * 2 - 1     # -1..1
    ny = (yy + 0.5) / H * 2 - 1
    return (nx ** 2 + ny ** 2) <= 1.0   # 单位椭圆(画幅2:1时为椭圆)


def normalize_brightness(canvas, percentile=99.7, curve="gamma", gamma=2.2,
                         log_gain=200.0):
    """曝光归一化 + 非线性编码 → [0,1] 浮点(各 tonemap/视频共用)。

    canvas: (H,W,3) 线性亮度。按 percentile 定白点归一, 再按 curve 编码:
    - "linear": 不编码(纯线性, 暗部信息被挤压, 仅用于需要原始线性的场合)
    - "gamma":  幂律 v^(1/gamma)(SDR 常用, 温和抬暗部)
    - "log":    log1p(gain·v)/log1p(gain)(数字底片式, 暗部大幅展开、亮部 rolloff,
                17 stops 动态范围在 16bit 里信息密度均匀, 最适合后期 PS 的"底片")
    """
    Y = canvas.sum(-1)
    norm = np.percentile(Y[Y > 0], percentile) if (Y > 0).any() else 1.0
    v = np.clip(canvas / max(norm, 1e-9), 0, 1)
    if curve == "linear":
        return v
    if curve == "log":
        return np.log1p(log_gain * v) / np.log1p(log_gain)
    return v ** (1 / gamma)   # gamma


def tonemap_sdr(canvas, percentile=99.7, gamma=2.2, mask=None):
    """SDR tone map: 高百分位定标 + gamma → 8bit。mask: Mollweide 椭圆(外区置黑)。"""
    out = (normalize_brightness(canvas, percentile, "gamma", gamma) * 255).astype(np.uint8)
    if mask is not None:
        out[~mask] = 0
    return out


def tonemap_hdr16(canvas, percentile=99.97, curve="log", log_gain=200.0,
                  gamma=2.2, mask=None):
    """HDR → 16bit TIFF。curve 决定编码:

    纯 linear 在 16bit 里把暗部(恒星主体)信息挤进很窄区间, 后期一拉就断层。
    默认 "log"(数字底片式): 暗部展开、亮部 rolloff, 17 stops 信息密度均匀,
    保留足够原始信息供后期 PS/grade。也可选 "gamma"/"linear"。返回 uint16 (H,W,3)。
    """
    out = (normalize_brightness(canvas, percentile, curve, gamma, log_gain)
           * 65535.0).astype(np.uint16)
    if mask is not None:
        out[~mask] = 0
    return out
