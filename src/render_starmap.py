"""全天星图渲染: 星等→亮度 + B-V→星色, 投影到全天 (equirectangular / Mollweide)。

复用自 turquoise_band 的物理星场管线。每颗真实恒星按真位置投影、真星等定亮度、真色温定星色。
银河应从恒星密度自然涌现, 无需特殊处理。

SDR/HDR 两套输出。先低分辨率 SDR 验证银河涌现。
"""
import numpy as np


def mag_to_luminance(vmag, m_ref=0.0):
    """星等 → 相对亮度。L = 10^(-0.4·(m − m_ref))。m_ref=0 即以 0 等星为 1.0。"""
    return 10.0 ** (-0.4 * (np.asarray(vmag, float) - m_ref))


# ---------------------------------------------------------------------------
# 色指数 → 星色 (BP-RP 标定，白点锚定在太阳 G2V)
#
# 历史包袱：函数名沿用 bv_to_rgb，但渲染器实际喂进来的是 Gaia 的 BP-RP 色指数
# (fetch_gaia_allsky 输出 BP-RP)，不是 Johnson B-V。旧实现是手搓线性近似，没有
# 任何白点锚定——从未定义"一颗太阳色温的星该渲染成什么白"，且把 BP-RP 当 B-V
# 用（两者数值范围不同），导致银盘里大量 BP-RP∈[1,2] 的恒星被推成暖黄，叠加显示
# 层的全局拉饱和后整条银河发黄。
#
# 新实现走天文摄影标准做法 + 公开物理标定：
#   1. BP-RP → Teff：用 Pecaut & Mamajek (2013, 持续更新的 EEM 星表，Gaia EDR3
#      Bp-Rp 列) 的主序色—温度锚点做单调插值。太阳 G2V 落在 BP-RP≈0.82。
#   2. Teff → 线性 sRGB：把恒星当黑体，普朗克谱与 CIE 1931 2° 颜色匹配函数
#      (Wyman/Sloan/Shirley 2013 的解析高斯拟合，无需外部数据文件) 积分得 XYZ，
#      经标准 sRGB 矩阵转线性 RGB。
#   3. 白点锚定：把白点温度取为太阳 Teff=5772 K，对每颗星的 RGB 除以太阳 RGB
#      （gray-world / set-white-point 操作），强制 BP-RP≈0.82 的 G2V 渲染成中性
#      白 (1,1,1)。这正是 PixInsight PCC / Photoshop 设白点消除整体偏黄的原理。
#
# 标定自查（白点=太阳，max 通道归一）：BP-RP=0.0→(0.52,0.66,1.0) 蓝白、
# 0.82→(1,1,1) 中性白、1.5→(1.0,0.82,0.58) 微暖、2.5→(1.0,0.62,0.26) 橙红。
# 黑体管线与 Mitchell Charity 经典 T→sRGB 表交叉验证：5800K→(255,247,235)
# vs 表 (255,240,233)，10000K→(204,222,255) vs 表 (204,219,255)，均吻合。
#
# 返回值仍按"色相由色指数定、亮度由星等定"的约定：linear RGB 按最大通道归一
# (max=1)，下游在线性空间乘以 mag 决定的亮度。注意返回的是线性 RGB（累积/tonemap
# 在线性空间工作），不是 sRGB gamma 编码值。
# ---------------------------------------------------------------------------

# Pecaut & Mamajek 主序 BP-RP ↔ Teff 锚点 (Gaia EDR3 Bp-Rp, K)。覆盖 O/B 蓝端
# 到 M 矮星红端；太阳 G2V 锚在 0.82/5772K。
_BPRP_ANCHORS = np.array([
    -0.34, -0.20, 0.00, 0.20, 0.40, 0.55, 0.66, 0.82, 0.98,
    1.20, 1.50, 1.85, 2.20, 2.80, 3.50, 4.50])
_TEFF_ANCHORS = np.array([
    15000.0, 11500, 9700, 8200, 7200, 6500, 6000, 5772, 5300,
    4800, 4400, 3900, 3500, 3100, 2900, 2700])

_SUN_TEFF = 5772.0  # 白点温度 (太阳 G2V)


def _cie_1931(wl):
    """CIE 1931 2° 颜色匹配函数 (Wyman et al. 2013 多瓣高斯解析拟合)。wl 单位 nm。"""
    def g(x, mu, s1, s2):
        s = np.where(x < mu, s1, s2)
        t = (x - mu) * s
        return np.exp(-0.5 * t * t)
    xb = 0.362 * g(wl, 442.0, 0.0624, 0.0374) \
        + 1.056 * g(wl, 599.8, 0.0264, 0.0323) \
        - 0.065 * g(wl, 501.1, 0.0490, 0.0382)
    yb = 0.821 * g(wl, 568.8, 0.0213, 0.0247) \
        + 0.286 * g(wl, 530.9, 0.0613, 0.0322)
    zb = 1.217 * g(wl, 437.0, 0.0845, 0.0278) \
        + 0.681 * g(wl, 459.0, 0.0385, 0.0725)
    return xb, yb, zb


def _planck(wl_nm, T):
    """普朗克黑体辐射谱 (相对值即可, 常数因子归一时抵消)。wl 单位 nm。"""
    wl = wl_nm * 1e-9
    h, c, k = 6.626e-34, 2.998e8, 1.381e-23
    return 1.0 / (wl ** 5) / (np.exp(h * c / (wl * k * T)) - 1.0)


# sRGB 线性矩阵 (XYZ→linear RGB, D65) 与积分用波长网格、CMF、白点常量预计算
_SRGB_M = np.array([
    [3.2406, -1.5372, -0.4986],
    [-0.9689, 1.8758, 0.0415],
    [0.0557, -0.2040, 1.0570]])
_WL = np.arange(380.0, 781.0, 5.0)
_XB, _YB, _ZB = _cie_1931(_WL)


def _teff_to_linear_rgb(T):
    """Teff(K, 数组) → 太阳白点锚定的线性 RGB(...,3)。每颗星归一前不裁负。"""
    T = np.asarray(T, float)[..., None]              # (...,1) 便于广播波长轴
    B = _planck(_WL, T)                              # (..., n_wl)
    X = np.trapezoid(B * _XB, _WL, axis=-1)
    Y = np.trapezoid(B * _YB, _WL, axis=-1)
    Z = np.trapezoid(B * _ZB, _WL, axis=-1)
    XYZ = np.stack([X, Y, Z], axis=-1) / np.maximum(Y[..., None], 1e-30)
    rgb = XYZ @ _SRGB_M.T
    return rgb / _SUN_WHITE_RGB                       # 除太阳白点 → G2V 变中性白


def _white_rgb():
    """太阳 Teff 的线性 RGB（白点除数），用于 gray-world 锚定。"""
    B = _planck(_WL, _SUN_TEFF)
    X = np.trapezoid(B * _XB, _WL)
    Y = np.trapezoid(B * _YB, _WL)
    Z = np.trapezoid(B * _ZB, _WL)
    return (np.array([X, Y, Z]) / Y) @ _SRGB_M.T


_SUN_WHITE_RGB = _white_rgb()


def bv_to_rgb(bv):
    """色指数 (Gaia BP-RP) → 归一化线性 RGB 星色，白点锚定在太阳 G2V。

    函数名与签名沿用历史 (输入仍是渲染器喂的那个色指数数组，内部按 BP-RP 处理)。
    流程：BP-RP →(Pecaut-Mamajek 插值)→ Teff →(黑体+CIE1931+sRGB)→ 线性 RGB，
    再除以太阳 (5772K) 白点 → BP-RP≈0.82 渲染成中性白 (1,1,1)。返回按最大通道归一
    的线性 RGB (色相), 亮度由星等在下游决定。详见本文件顶部标定说明。

    蓝白星 (BP-RP<0): B>R; 太阳型 (≈0.82): R≈G≈B; 橙红星 (>1.4): R>B。
    """
    bv = np.asarray(bv, float)
    c = np.clip(bv, _BPRP_ANCHORS[0], _BPRP_ANCHORS[-1])
    # LUT 快路径：星色是 BP-RP 的一维平滑函数，逐星算黑体谱会产生 N×n_wl 的
    # 巨型中间数组（3300 万星即吃 ~90GB，6 亿星必 OOM）。改为只对 4096 个量化
    # 档算一次谱，星查表展开。量化误差远低于 8-bit 显示精度，视觉无差。
    if c.size > 100_000:
        n_lut = 4096
        grid = np.linspace(_BPRP_ANCHORS[0], _BPRP_ANCHORS[-1], n_lut)
        teff_lut = np.interp(grid, _BPRP_ANCHORS, _TEFF_ANCHORS)
        rgb_lut = np.clip(_teff_to_linear_rgb(teff_lut), 0.0, None)
        rgb_lut = rgb_lut / np.maximum(rgb_lut.max(axis=-1, keepdims=True), 1e-6)
        idx = np.clip(((c - _BPRP_ANCHORS[0]) /
                       (_BPRP_ANCHORS[-1] - _BPRP_ANCHORS[0]) *
                       (n_lut - 1)).round().astype(np.intp), 0, n_lut - 1)
        return rgb_lut[idx]
    teff = np.interp(c, _BPRP_ANCHORS, _TEFF_ANCHORS)
    rgb = _teff_to_linear_rgb(teff)
    rgb = np.clip(rgb, 0.0, None)                     # 色域外裁负
    return rgb / np.maximum(rgb.max(axis=-1, keepdims=True), 1e-6)  # 归一(色相)


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
    # bincount 替代 np.add.at：后者单线程逐点累加，对亿级星点慢一个量级且
    # 内存峰值极高（G<20 的 6 亿星会触发 OOM）。bincount 是 C 实现的桶累加，
    # 把 (py,px) 压成一维像素 index 后按通道累加，数学等价、快得多、省内存。
    # 分块累加：weights = lum×rgb 的 N×3 中间量对亿级星会吃上百 GB，按 CHUNK
    # 切批，峰值内存只跟批大小有关（与总星数无关），让 G<20 的 6 亿星也能跑。
    pxi = px[inside]; pyi = py[inside]
    lumi = luminance[inside]; rgbi = rgb[inside]
    npix = H * W
    acc = np.zeros((npix, 3), np.float64)
    CHUNK = 20_000_000
    n = pxi.size
    for lo in range(0, n, CHUNK):
        hi = min(lo + CHUNK, n)
        flat = pyi[lo:hi].astype(np.int64) * W + pxi[lo:hi].astype(np.int64)
        w = lumi[lo:hi, None] * rgbi[lo:hi]
        for c in range(3):
            acc[:, c] += np.bincount(flat, weights=w[:, c], minlength=npix)
    canvas = acc.reshape(H, W, 3).astype(np.float32)
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
