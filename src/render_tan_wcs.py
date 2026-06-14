"""TAN(gnomonic)天球投影渲染 + 输出 WCS，喂 Aladin hipsgen。

地平投影没法写标准 WCS（地平坐标随时间变、非天球固定位置）。要喂 Aladin
必须用天球投影。这里用 gnomonic(TAN)以切点把星投到平面，输出 PNG + 同名
.hhh（FITS WCS header），hipsgen 读 (in=dir color=jpeg) 自动按 WCS 拼 HiPS。

两种模式：
  - 单图：--lc/--bc/--fov-deg/--size 渲一张图（最小验证、单张分享）。
  - 瓦片（标准 HiPS 做法）：--tiles 把天区切成网格，每格一张小图 + 各自 WCS，
    多进程并行渲，全部丢进一个目录交 hipsgen 拼金字塔。大分辨率（24K→10 亿
    像素）走这条：worker 数与 tile-size 不变则内存恒定（与 tile 总数无关），
    凑更高分辨率只需更多格。

小 PSF 锐星（高分辨率本就该是分解的单星，乳光交给金字塔降采样涌现）。每像素
立体角归一化把 flux 转面亮度，一套 tone 通用（见 working.md / rfc.md）。

手性：像素映射 xi(东)→ +x，由 WCS 的 CDELT1<0 表达"经度向左增"。两处只处理
一次手性，否则 Aladin 里图会左右镜像。

用法：
  # 单图
  python src/render_tan_wcs.py --data data/raw/fov_g20.npz --out outputs/tan_gc \
      --lc 0 --bc 0 --fov-deg 40 --size 1024
  # 瓦片（全 FOV，8 进程并行）
  python src/render_tan_wcs.py --data data/raw/fov_g20.npz --out outputs/tiles \
      --tiles --l-range=-41,79 --b-range=-31,43 --tile-fov 6 --tile-step 5 \
      --tile-size 2048 --workers 8
  # 再拼 HiPS（需 openjdk@11；color=jpeg 输出小，target 放 FOV 中心）
  java -jar AladinBeta.jar -hipsgen in=outputs/tiles out=outputs/hips \
      color=jpeg "target=271.672 -25.873"
"""
import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import render_starmap as rs
import render_bortle_eye_grid as beg

# 立体角归一化参考：广州正式图像素当量 (0.083°)²，让面亮度落在 tone 习惯范围
REF_OMEGA = 0.083 ** 2

# 瓦片全局固定 white-point stretch（不 per-tile 自适应，避免接缝；见 render_tile 注释）。
# hero +6mag 下背景已满，per-tile stretch 基本 clamp 到 ~1.0，固定 1.0 既消白点接缝又
# 符合"接受 +6mag 的满"的观感。如需更亮白点可整体调高，但必须所有 tile 一致。
TILE_STRETCH = 1.0

# —— 亮星散射翼（bloom）：光晕大小随星等 G 连续变化 ——
# 物理：恒星都是点源，"显大"来自饱和外溢 + 散射翼，两者强度 ∝ 通量、可见半径 ∝ log(通量)
# ∝ (m_lim - G)。所以光晕大小**连续**随亮度：越亮越大，从最暗的亚像素点到最亮的大盘平滑渐变
# （这才是星图"疏密有致、有大有小"的来源）。
# 实现分两层（10.6 亿星不可能逐颗卷）：
#   - 暗星（G≥BLOOM_G_FAINT，十亿颗）：accumulate 统一小 PSF（它们光晕都接近下限、差异肉眼无所谓）。
#   - 亮+中亮星（G<BLOOM_G_FAINT，全天 ~6.3 万颗、单 tile 个位~几十颗）：每颗按 σ(G) 渲翼，
#     σ 连续随 G（G≤BLOOM_G_BRIGHT 满 σ、到 BLOOM_G_FAINT 收到核尺度）。按 σ 分细档（每档一次
#     卷积，省时）逼近连续。这给出连续渐变 + 亮星气场，且密集暗星不进、银带不糊。
BLOOM_G_BRIGHT = 2.0          # 此星等及以下：满 σ 大翼（全天 65 颗）
BLOOM_G_FAINT = 8.0           # 此星等以上：不单独渲翼（走 accumulate 小 PSF）。G<8 全天 ~6.3 万颗
BLOOM_WING_ARCSEC = 80.0      # G≤BLOOM_G_BRIGHT 的散射翼大翼 σ（角尺寸，与分辨率无关；240→80 缩 3×）
BLOOM_CORE_FRAC = 1.0 / 3.0   # 小翼 = 大翼 × 此比例（承自旧 (3,9)px 的 1:3）


def gnomonic(l, b, lc, bc):
    """银道 (l,b)→ TAN 标准平面坐标 (xi, eta)，单位弧度。切点 (lc,bc)。
    返回 xi(东向), eta(北向), 以及前半球可见掩码。"""
    lr, br = np.radians(l), np.radians(b)
    l0, b0 = np.radians(lc), np.radians(bc)
    dl = lr - l0
    cosc = np.sin(b0) * np.sin(br) + np.cos(b0) * np.cos(br) * np.cos(dl)
    vis = cosc > 1e-6  # gnomonic 只在切点同半球有定义
    xi = np.cos(br) * np.sin(dl) / np.maximum(cosc, 1e-9)
    eta = (np.cos(b0) * np.sin(br) - np.sin(b0) * np.cos(br) * np.cos(dl)) / np.maximum(cosc, 1e-9)
    return xi, eta, vis


def write_hhh(path, S, lc, bc, cdelt):
    """写 FITS WCS header（80 列卡片）。hipsgen 认 PNG + 同名 .hhh。

    手性必须与像素映射自洽（astropy WCS 验证）：像素用 +xi（银经大落右），则
    CDELT1 必须 > 0。若 CDELT1 取负，Aladin 会沿经度方向把瓦片镜像错位（表现
    为银河带先 gap 再出现）。两处用同一手性，只处理一次。"""
    hdr = [
        "SIMPLE  = T", "BITPIX  = 8", "NAXIS   = 2",
        f"NAXIS1  = {S}", f"NAXIS2  = {S}",
        "CTYPE1  = 'GLON-TAN'", "CTYPE2  = 'GLAT-TAN'",
        f"CRVAL1  = {lc}", f"CRVAL2  = {bc}",
        f"CRPIX1  = {S / 2.0}", f"CRPIX2  = {S / 2.0}",
        f"CDELT1  = {cdelt}", f"CDELT2  = {cdelt}", "END",
    ]
    with open(path, "w") as f:
        f.write("".join(f"{line:<80}" for line in hdr))


def write_fits_tile(out_prefix, rgb, S, lc, bc, cdelt):
    """把 tone 后的 float rgb（H×W×3）写成一块 RGB 彩色 float FITS（NAXIS3=3）+ WCS。

    这是给 PixInsight 调色的中间产物：在 float 域保留全动态范围（不 8-bit clip），
    用户在 PixInsight 里当一张彩色图打开、调色温（Curves R/G/B 一起看）+ SCNR，导出
    回 RGB float FITS。之后 split_rgb_fits 工具把它拆成 R/G/B 三套单通道喂 hipsgen
    三通道 RGB 建树（float 域逐层池化保星点/乳光，合成才量化 JPEG，部署只留 JPEG）。

    FITS 轴序 (NAXIS3=3, NAXIS2=y, NAXIS1=x)，把 (H,W,3) → (3,H,W)。手性同 write_hhh
    （CDELT1>0，与 +xi 像素映射自洽）。"""
    from astropy.io import fits
    cube = np.moveaxis(rgb, -1, 0).astype(np.float32)  # (3,H,W)
    hdu = fits.PrimaryHDU(cube)
    h = hdu.header
    h["CTYPE1"], h["CTYPE2"] = "GLON-TAN", "GLAT-TAN"
    h["CRVAL1"], h["CRVAL2"] = float(lc), float(bc)
    h["CRPIX1"], h["CRPIX2"] = S / 2.0, S / 2.0
    h["CDELT1"], h["CDELT2"] = float(cdelt), float(cdelt)
    hdu.writeto(out_prefix + ".fits", overwrite=True)


def _fast_saturate_and_bloom(canvas, sat_level, wing_sigmas, wing_weights, ds_thresh=40.0):
    """与 beg.saturate_and_bloom 等价，但大 σ 翼用降采样卷积——大 σ 高斯天然低频，在低分辨率
    做再上采样，视觉无损（实测差 0.24%）却快 ~36×。bloom 是渲染最大瓶颈（6× bloom 的 σ=160px
    高斯在 1536² 上单 tile 7.2s，占 96% 时间；降采样后 0.2s，全天 61h→~7h）。"""
    from scipy.ndimage import gaussian_filter, zoom
    H, W = canvas.shape[:2]
    y = canvas.sum(-1)
    over = y > sat_level
    if not np.any(over):
        return canvas
    scale = np.ones_like(y)
    scale[over] = sat_level / y[over]
    core = canvas * scale[:, :, None]
    excess = canvas - core
    wings = np.zeros_like(canvas)
    for sig, w in zip(wing_sigmas, wing_weights):
        for c in range(3):
            if sig > ds_thresh:
                f = max(2, int(sig / 12))             # 降采样因子（保 ~12px σ 精度）
                sm = gaussian_filter(zoom(excess[..., c], 1.0 / f, order=1), sig / f)
                up = zoom(sm, f, order=1)
                if up.shape[0] < H or up.shape[1] < W:
                    up = np.pad(up, ((0, max(0, H - up.shape[0])),
                                     (0, max(0, W - up.shape[1]))), mode="edge")
                wings[..., c] += up[:H, :W] * w
            else:
                wings[..., c] += gaussian_filter(excess[..., c], sig) * w
    return core + wings


def _bright_star_wings(S, pxi_b, pyi_b, Lb, colsb, gb, cdelt, margin=0):
    """对亮星（G≤BLOOM_G_FAINT）按 G 分级画散射翼，返回 wings 层 (S,S,3)。
    σ 随 G 线性：G≤BLOOM_G_BRIGHT 用大翼 BLOOM_WING_ARCSEC、到 BLOOM_G_FAINT 收到 0。
    每颗星的翼是它通量的一部分铺成高斯——暗星（已被 G 排除）不进来，不糊密集星场。
    亮星少（全天 ~700 颗、单 tile 通常个位数），直接高斯卷积（不降采样）——降采样的 zoom
    双线性上采样在大 σ 会把圆高斯采成方格 artifact（亮星核出现方块）。

    margin>0：在 (S+2margin)² 的扩边画布上画翼再裁回中心 S×S。pxi/pyi 已是相对扩边
    画布的坐标（含 margin 偏移），可落在 [0,S+2margin)。这样**中心落在 tile 外、但翼伸
    进 tile** 的亮星也能贡献（修 tile 边缘亮星被截断）——卷积在扩边画布上做，翼跨过原
    tile 边界连续，裁剪只丢真正在视野外的部分，相邻 tile 各算各的扩边、重叠区一致。"""
    arcsec_px = cdelt * 3600.0
    Sp = S + 2 * margin
    wings = np.zeros((Sp, Sp, 3), np.float64)
    if len(gb) == 0:
        return wings[margin:margin + S, margin:margin + S]
    # 光晕大小连续随 G：sig_frac∈[~0.06,1]，G≤BRIGHT→1（满 σ），G≥FAINT→核尺度（最小）。
    # 线性插（log 通量近似线性于 G）。每颗星的翼 σ = BLOOM_WING_ARCSEC × sig_frac(G)。
    sig_frac = np.clip((BLOOM_G_FAINT - gb) / (BLOOM_G_FAINT - BLOOM_G_BRIGHT), 0.0, 1.0)
    sig_frac = np.maximum(sig_frac, BLOOM_CORE_FRAC * 0.2)   # 下限：最暗有翼星也比纯点略大
    # 翼能量 ∝ 通量。按 sig_frac 分细档（0.08 一档 ~13 档）逼近连续 σ。
    edges = np.linspace(sig_frac.min(), sig_frac.max() + 1e-6, 14)
    # —— 局部窗口叠加，不做全画布卷积 ——
    # 翼是局部的（5σ 半径）；对每颗亮星只在它的小窗口里贴一个预计算的归一化高斯核 × 通量，
    # 物理上等价于 conv(delta·flux)，但避免在整张 (S+2margin)² 上卷（空旷 tile 几颗星也曾
    # 触发 13档×全画布卷积 → 单 tile 170ms，是全天渲染的真瓶颈）。同档星共用一个核（核只
    # 算一次）。加速 ~60×，结果与全画布卷积逐像素一致。
    # truncate=5：高斯核截断是方形支撑（半宽 5σ），极亮星（Antares L≈中位 2900 万倍）在 3σ
    # 残值仍高于天光底会渲成方块；5σ 处残值 e^-12.5≈4e-6 没入背景。窗口取 ±5σ 即此截断。
    TRUNC = 5.0

    def _kernel(sig):
        """归一化 2D 高斯核（峰值=1，非积分归一——我们要的是"通量铺成的亮度分布"），半径 5σ。"""
        r = int(np.ceil(TRUNC * sig))
        ax = np.arange(-r, r + 1)
        g1 = np.exp(-(ax ** 2) / (2.0 * sig * sig))
        k = np.outer(g1, g1)
        return k / k.sum(), r

    def _stamp(cy, cx, amp_rgb, ker, r):
        """把 ker×amp_rgb 叠加到 wings 的 (cy,cx) 窗口，处理边界裁剪。"""
        y0, y1 = cy - r, cy + r + 1
        x0, x1 = cx - r, cx + r + 1
        ky0 = max(0, -y0); kx0 = max(0, -x0)
        y0c = max(0, y0); x0c = max(0, x0)
        y1c = min(Sp, y1); x1c = min(Sp, x1)
        if y1c <= y0c or x1c <= x0c:
            return
        ksub = ker[ky0:ky0 + (y1c - y0c), kx0:kx0 + (x1c - x0c)]
        wings[y0c:y1c, x0c:x1c, :] += ksub[:, :, None] * amp_rgb[None, None, :]

    for k in range(len(edges) - 1):
        m = (sig_frac >= edges[k]) & (sig_frac < edges[k + 1])
        if not np.any(m):
            continue
        sf = 0.5 * (edges[k] + edges[k + 1])         # 该档代表 σ_frac
        big = max((BLOOM_WING_ARCSEC * sf) / arcsec_px, 4.0)
        # 核心小翼 σ ≥3px：太小则亮星中心是尖峰、tone clip 成方形平顶；≥3 保证圆顶过渡。
        sml = max(big * BLOOM_CORE_FRAC, 3.0)
        idx = np.nonzero(m)[0]
        we = Lb[idx] * 0.5                            # 翼分走一半通量（核留一半，连续显大）
        for sig, w in [(sml, 0.65), (big, 0.35)]:
            ker, r = _kernel(sig)
            for j, i in enumerate(idx):
                amp = (we[j] * w) * colsb[i]         # RGB 三通道幅度
                _stamp(int(pyi_b[i]), int(pxi_b[i]), amp, ker, r)
    return wings[margin:margin + S, margin:margin + S]


def render_tile_canvas(l, b, cols, L, lc, bc, fov_deg, S, psf_core_px, bortle,
                       buckets=None, g=None):
    """渲一块 TAN 瓦片的 raw canvas（tone 之前）：候选星筛选 → gnomonic 投影 → accumulate
    （暗星，psf_core_px 锐点）→ 亮星散射翼（按 G 分级，g 提供时）→ 立体角归一化 → add_skyglow。
    返回 HxWx3 float canvas，或 None（无星）。render_tile 和 calibrate 共用。
    g=None 时退回旧的整图 saturate_and_bloom（标定调它、不需亮星分级）。"""
    scale_rad = np.radians(fov_deg) / S
    cdelt = fov_deg / S
    rad_deg = fov_deg * 0.7071 * 1.3  # 半对角线 + 30% 余量（粗筛半径，宁松勿紧）

    if buckets is not None:
        import astropy.units as u
        hp = buckets["hp"]; start = buckets["start"]; count = buckets["count"]
        pix = hp.cone_search_lonlat(lc * u.deg, bc * u.deg, rad_deg * u.deg)
        pix = pix[count[pix] > 0]
        if len(pix) == 0:
            return None
        segs = ([], [], [], [], [])
        for p in pix:
            s = int(start[p]); c = int(count[p])
            segs[0].append(l[s:s + c]); segs[1].append(b[s:s + c])
            segs[2].append(L[s:s + c]); segs[3].append(cols[s:s + c])
            if g is not None:
                segs[4].append(g[s:s + c])
        ls = np.concatenate(segs[0]); bs = np.concatenate(segs[1])
        Ls = np.concatenate(segs[2]); colss = np.concatenate(segs[3])
        gs = np.concatenate(segs[4]) if g is not None else None
    else:
        cos_rad = np.cos(np.radians(min(rad_deg, 180.0)))
        lr, br = np.radians(l), np.radians(b)
        l0, b0 = np.radians(lc), np.radians(bc)
        cosd = np.sin(b0) * np.sin(br) + np.cos(b0) * np.cos(br) * np.cos(lr - l0)
        near = cosd >= cos_rad
        if not np.any(near):
            return None
        ls, bs, Ls, colss = l[near], b[near], L[near], cols[near]
        gs = g[near] if g is not None else None
    xi, eta, vis = gnomonic(ls, bs, lc, bc)
    px = S / 2.0 + xi / scale_rad
    py = S / 2.0 - eta / scale_rad
    inside = vis & (px >= 0) & (px < S) & (py >= 0) & (py < S)
    if not np.any(inside):
        return None
    pxi = np.clip(px.astype(int), 0, S - 1)
    pyi = np.clip(py.astype(int), 0, S - 1)
    # 暗星 + 全部星正常 accumulate（锐点）——这是图的主体，银带保持锐利。
    canvas = rs.accumulate_stars(S, S, pxi, pyi, inside, Ls, colss, psf_px=psf_core_px)
    canvas = canvas * (REF_OMEGA / cdelt ** 2)

    if gs is not None:
        # 亮星散射翼（按 G 分级）：只 G≤BLOOM_G_FAINT 的星给翼，暗星不给（不糊密集星场）。
        # 扩边渲翼：中心落在 tile 外 margin 内、但翼伸进 tile 的亮星也要算（否则 tile 边缘
        # 的亮星翼被硬截、相邻 tile 拼起来有断口）。margin = 最大翼 5σ（truncate=5）的像素半径。
        arcsec_px = cdelt * 3600.0
        margin = int(np.ceil(5.0 * BLOOM_WING_ARCSEC / arcsec_px))
        # 扩边画布坐标：中心可落在 [-margin, S+margin) → 加 margin 偏移到 [0, S+2margin)。
        pxm = px + margin
        pym = py + margin
        bright = (vis & (gs <= BLOOM_G_FAINT)
                  & (pxm >= 0) & (pxm < S + 2 * margin)
                  & (pym >= 0) & (pym < S + 2 * margin))
        if np.any(bright):
            pxmi = np.clip(pxm.astype(int), 0, S + 2 * margin - 1)
            pymi = np.clip(pym.astype(int), 0, S + 2 * margin - 1)
            wings = _bright_star_wings(S, pxmi[bright], pymi[bright], Ls[bright],
                                       colss[bright], gs[bright], cdelt, margin=margin)
            canvas = canvas + wings * (REF_OMEGA / cdelt ** 2)
    else:
        # 标定路径（g=None）：用旧整图 saturate_and_bloom（小 σ，不影响 anchor 统计）。
        sat = 6.0 * beg.rh.skyglow_level(bortle) * beg.gain_for_mag_delta(0.0)
        canvas = _fast_saturate_and_bloom(canvas, sat, (3.0, 9.0), (0.65, 0.35))
    canvas = beg.add_skyglow(canvas, bortle)
    return canvas


def render_tile(l, b, cols, L, out_prefix, lc, bc, fov_deg, S, psf_core_px,
                bortle, target_sky, star_contrast, chroma, target_white, out_fits=False,
                calib=None, buckets=None, color_procs=None, g=None):
    """渲一块 TAN 瓦片（切点 lc,bc）。out_fits=False 出 PNG+.hhh；True 出 float FITS。
    返回画面内星数（0 不输出）。

    calib=None：sky_anchor 用物理天光底 ×3、white-point stretch 用 TILE_STRETCH=1.0。
    calib={"sky_level":.., "stretch":..}：用全天联合标定的冻结值（见 calibrate_alltile_tone.py），
      复刻 hero 整图的对比观感且块间一致。全量渲染走这条路。
    buckets=None：l/b/cols/L 是全星表，对全表做角距粗筛（内存 ~15GB/进程，仅小规模可用）。
    buckets={...}：HEALPix 分桶模式（memory-aware 根治，高分辨率全天必走）——l/b/cols/L 是
      按 Norder<order> 像素排序的全表，buckets 含 start/count/order/hp。只取 tile 覆盖的邻桶
      星做投影（几十万 vs 6 亿），内存几十 MB、快几百倍。见 build_healpix_bucketed.py。
    """
    from PIL import Image
    cdelt = fov_deg / S
    # raw canvas（候选星筛选→投影→accumulate→立体角归一化→saturate→skyglow）抽成共用函数，
    # calibrate_alltile_tone 也调它——保证标定 anchor 与实际渲染 canvas 同源。
    canvas = render_tile_canvas(l, b, cols, L, lc, bc, fov_deg, S, psf_core_px, bortle,
                                buckets=buckets, g=g)
    if canvas is None:
        return 0
    n_in = 1  # 有星（render_tile_canvas 非 None 即画面内有星）
    # 瓦片 tone 必须用全局固定标定，不能 per-tile 自适应。tone_adapted 的 sky-floor
    # 和 white-point 都按本张 tile 的分位估计（25%/99.5%），含银河带多的 tile 标定不同，
    # 同一片天在相邻两张里被映射到不同亮度 → 拼接出沿银河方向的接缝条纹（实测重叠区差
    # 32%）。改用物理天光底作 sky_anchor（块间同一 floor）+ 固定 stretch（块间同一白点），
    # 重叠区差归零。raw canvas 几何/累积层本来就块间一致，artifact 全在 tone 链。
    # sky_anchor 的单位必须与 adapt_sky_floor 内部的 y=canvas.sum(-1)（三通道和）一致：
    # add_skyglow 给每个通道各加 additive_skyglow_level，所以暗空背景的 sum 是它的 3 倍，
    # anchor 必须 ×3。漏乘 3 会把黑场锚高 3×（scale 偏大），整图背景被向上推、暗空发灰。
    # calib 提供全天标定的冻结 sky_anchor + star_contrast + stretch（复刻 hero 对比、块间
    # 一致，见 calibrate_alltile_tone.py）；calib 的 sky_anchor 是用相同 fov/size 实测的暗空
    # canvas sum 底（依赖归一化 norm，必须配套）。无 calib 时退回保守路径：物理天光底 ×3 作
    # floor + 入参 star_contrast/TILE_STRETCH（无接缝但不复刻 hero 对比）。
    if calib is not None:
        sky_anchor = float(calib["sky_anchor"])
        tile_sc = float(calib["star_contrast"])
        tile_stretch = float(calib["stretch"])
    else:
        sky_anchor = beg.rh.additive_skyglow_level(bortle) * 3.0
        tile_sc = star_contrast
        tile_stretch = TILE_STRETCH
    adapted = beg.adapt_sky_floor(canvas, target_sky, 25.0, tile_sc,
                                  sky_anchor=sky_anchor)
    rgb = beg.finish_sky_adapted(adapted, target_sky, 2.2, target_white,
                                 tile_stretch, chroma)
    # Python 版 PixInsight 调色（色温/去绿微调）——渲完直接在 worker 里做，免 PI 依赖、
    # 天然随渲染多进程并行、省一次读写。与真 PI 逐像素 eval mean≈3.6/255 等价（见 pi_curves_scnr）。
    if color_procs is not None:
        import pi_curves_scnr as pcs
        rgb = pcs.apply_xpsm(np.clip(rgb, 0.0, 1.0), color_procs)
    if out_fits:
        # FITS 域金字塔：存 tone 后、8-bit clip 前的 float rgb 为 R/G/B 三套单通道
        # FITS（hipsgen 对多面 FITS 当灰度，须分通道）。各通道 float 域逐层池化保住
        # 星点锐度/乳光对比（zoom-out 质量根因），RGB action 合成时才量化到 JPEG。
        write_fits_tile(out_prefix, np.clip(rgb, 0.0, None).astype(np.float32),
                        S, lc, bc, cdelt)
    else:
        Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).save(out_prefix + ".png")
        write_hhh(out_prefix + ".hhh", S, lc, bc, cdelt)
    return n_in


# 瓦片 worker 共享只读数据（fork copy-on-write）
_SHARED = None


def _tile_worker(job):
    prefix, lc, bc = job
    s = _SHARED
    return render_tile(s["l"], s["b"], s["cols"], s["L"], prefix, lc, bc,
                       s["tile_fov"], s["tile_size"], **s["tile_kw"],
                       out_fits=s.get("out_fits", False), buckets=s.get("buckets"),
                       color_procs=s.get("color_procs"), g=s.get("g"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True,
                    help="单图模式：输出前缀；瓦片模式：输出目录")
    # 单图模式
    ap.add_argument("--lc", type=float, default=0.0, help="切点银经")
    ap.add_argument("--bc", type=float, default=0.0, help="切点银纬")
    ap.add_argument("--fov-deg", type=float, default=40.0, help="单图角宽度（度）")
    ap.add_argument("--size", type=int, default=1024, help="图边长像素")
    # 瓦片模式
    ap.add_argument("--tiles", action="store_true",
                    help="分块瓦片模式（标准 HiPS 做法：多小图 + 各自 WCS）")
    ap.add_argument("--l-range", default="-41,79", help="银经范围 lo,hi（wrap）")
    ap.add_argument("--b-range", default="-31,43", help="银纬范围 lo,hi")
    ap.add_argument("--tile-fov", type=float, default=20.0, help="每格角宽（度）")
    ap.add_argument("--tile-step", type=float, default=16.0,
                    help="格中心步长（度）；< tile-fov 让相邻格重叠，拼接无缝")
    ap.add_argument("--tile-size", type=int, default=2048, help="每格像素边长")
    ap.add_argument("--workers", type=int, default=8,
                    help="瓦片并行进程数（各格独立，fork 共享只读星表；worker 数 + "
                         "tile-size 不变 → 内存恒定，与 tile 总数无关）")
    # 共用显示参数
    ap.add_argument("--psf-core-px", type=float, default=0.6)
    ap.add_argument("--bortle", type=int, default=1)
    ap.add_argument("--value", type=float, default=0.0,
                    help="敏感度 +N mag（delta_mag，进 visual_luminance）。0=裸眼亮度；"
                         "hero 同款用 6（+6mag，暗星增益 ~250×，配 --target-sky 0.038）。")
    ap.add_argument("--target-sky", type=float, default=0.012)
    ap.add_argument("--star-contrast", type=float, default=6.0)
    ap.add_argument("--chroma", type=float, default=1.8)
    ap.add_argument("--target-white", type=float, default=2.5)
    ap.add_argument("--fits", action="store_true",
                    help="出 float FITS 瓦片（tone 后、未 8-bit clip）而非 PNG。供 hipsgen "
                         "TILES 在真值域逐层池化、JPEG 从 float 导显示层，改善 zoom-out 质量。")
    ap.add_argument("--resume", action="store_true",
                    help="断点续传：跳过已渲出（.png/.fits 已存在）的 tile，只补未渲的。"
                         "大全天 job 中断后重跑加这个。")
    ap.add_argument("--color-xpsm", default=None,
                    help="PixInsight process icon (.xpsm)，渲完在 worker 里用 Python 复现做色温/"
                         "去绿调色（pi_curves_scnr，免 PI 依赖、随渲染并行）。默认 skills/"
                         "batch_process_frames.xpsm；传 'none' 跳过调色。")
    ap.add_argument("--calib", default=None,
                    help="全天 tone 标定 JSON（calibrate_alltile_tone.py 产出）。提供则用其冻结的"
                         " sky_anchor/star_contrast/stretch 复刻 hero 对比且块间一致。标定的"
                         " tile_fov/tile_size 必须与本次渲染一致。")
    args = ap.parse_args()

    # Python 版 PixInsight 调色：默认用 batch_process_frames.xpsm（渲完在 worker 里做，免 PI
    # 依赖、随渲染并行）。--color-xpsm none 跳过、给别的 xpsm 路径则用那个。
    color_procs = None
    cx = args.color_xpsm if args.color_xpsm is not None else os.path.join(
        ROOT, "skills", "batch_process_frames.xpsm")
    if str(cx).lower() != "none" and os.path.isfile(cx):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import pixinsight_batch as _pb
        color_procs = _pb.parse_xpsm(cx)
        print(f"Python 调色（pi_curves_scnr）: {cx} → {len(color_procs)} process", flush=True)
    elif str(cx).lower() != "none":
        print(f"⚠ color-xpsm 不存在，跳过调色: {cx}", flush=True)

    calib = None
    if args.calib:
        import json
        with open(args.calib) as f:
            calib = json.load(f)
        if (abs(calib.get("tile_fov", args.tile_fov) - args.tile_fov) > 1e-6
                or calib.get("tile_size", args.tile_size) != args.tile_size):
            raise SystemExit(f"calib 的 tile_fov/tile_size ({calib.get('tile_fov')}/"
                             f"{calib.get('tile_size')}) 与渲染 ({args.tile_fov}/{args.tile_size}) "
                             f"不一致——sky_anchor 依赖归一化 norm，必须重新标定。")
        print(f"用全天标定 {args.calib}: sky_anchor={calib['sky_anchor']:.3f} "
              f"sc={calib['star_contrast']} stretch={calib['stretch']}", flush=True)

    # 分桶星表（build_healpix_bucketed.py 产出，含 bucket_start/count/order）自动启用 memory-aware
    # 分桶模式：每 tile 只读邻桶。否则全表角距粗筛（仅小规模可用）。
    d = np.load(args.data)
    l, b, g = d["l"][:], d["b"][:], d["g"][:]
    bv = np.nan_to_num(d["bp_rp"][:], nan=0.7)
    cols = rs.bv_to_rgb(bv)
    L = beg.visual_luminance_for_mags(g, args.bortle, args.value, 0.5)
    buckets = None
    if "bucket_start" in d.files:
        from astropy_healpix import HEALPix
        from astropy.coordinates import Galactic
        order = int(d["order"])
        buckets = dict(hp=HEALPix(nside=2 ** order, order="nested", frame=Galactic()),
                       start=d["bucket_start"][:], count=d["bucket_count"][:])
        print(f"分桶模式（Norder{order}，memory-aware）：每 tile 只读邻桶", flush=True)
    tile_kw = dict(psf_core_px=args.psf_core_px, bortle=args.bortle,
                   target_sky=args.target_sky, star_contrast=args.star_contrast,
                   chroma=args.chroma, target_white=args.target_white, calib=calib)

    if not args.tiles:
        n = render_tile(l, b, cols, L, args.out, args.lc, args.bc,
                        args.fov_deg, args.size, **tile_kw, out_fits=args.fits,
                        buckets=buckets, color_procs=color_procs, g=g)
        print(f"单图 切点({args.lc},{args.bc}) fov={args.fov_deg}° size={args.size} "
              f"画面内星 {n:,} -> {args.out}.png", flush=True)
        return

    os.makedirs(args.out, exist_ok=True)
    llo, lhi = map(float, args.l_range.split(","))
    blo, bhi = map(float, args.b_range.split(","))
    lcs = np.arange(llo, lhi + 1e-6, args.tile_step)
    bcs = np.arange(blo, bhi + 1e-6, args.tile_step)
    jobs = []
    for i, lc in enumerate(lcs):
        for j, bc in enumerate(bcs):
            # 文件名用网格索引 i_j 保唯一（亚度 step 时 %.0f 整数度会碰撞——361 tile 塌成
            # 132 个同名互相覆盖，高分辨率致命 bug）。hipsgen 靠 .hhh 的 WCS 定位，不靠文件名
            # 度数，所以索引命名安全。度数附在名里仅供人读/调试。
            tag = f"tile_i{i:04d}_j{j:04d}_l{lc % 360:.2f}_b{bc:.2f}".replace("-", "m")
            jobs.append((os.path.join(args.out, tag), float(lc % 360), float(bc)))
    # 断点续传：跳过已渲出（.png 已存在）的 tile。大全天 job 中断后重跑只补未渲的。
    if args.resume:
        ext = ".fits" if args.fits else ".png"
        before = len(jobs)
        jobs = [j for j in jobs if not os.path.exists(j[0] + ext)]
        print(f"--resume：已渲 {before - len(jobs)}/{before} 跳过，本次渲 {len(jobs)}", flush=True)
        if not jobs:
            print("全部已渲，无需续跑。", flush=True)
            return
    print(f"瓦片网格 {len(lcs)}×{len(bcs)}={len(jobs)} 格，{args.workers} 进程并行，"
          f"每格 fov={args.tile_fov}° size={args.tile_size}", flush=True)

    global _SHARED
    _SHARED = dict(l=l, b=b, cols=cols, L=L, tile_fov=args.tile_fov,
                   tile_size=args.tile_size, tile_kw=tile_kw, out_fits=args.fits,
                   buckets=buckets, color_procs=color_procs, g=g)
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import multiprocessing as mp
    # macOS 默认 spawn，worker 不继承 _SHARED；用 fork 让 worker 继承 + numpy
    # 大数组 copy-on-write 共享（只读不复制）。
    ctx = mp.get_context("fork")
    # 每张瓦片 worker 自己即时写盘（崩了只丢未渲的，已渲留盘）。进度逐瓦片打印
    # （含空瓦片，计数真实单调到 total），方便实时看进度、定位卡在哪格。
    done, nonempty, total_in = 0, 0, 0
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as ex:
        futs = {ex.submit(_tile_worker, j): j for j in jobs}
        for fut in as_completed(futs):
            _, lc, bc = futs[fut]
            n = fut.result()
            done += 1
            if n > 0:
                nonempty += 1
                total_in += n
            print(f"  [{done}/{len(jobs)}] l={lc:.0f} b={bc:.0f}: {n:,} 星"
                  f"{'（空，跳过）' if n == 0 else ''}", flush=True)
    print(f"瓦片完成：{nonempty} 张非空 / {len(jobs)} 总格（累计落点 {total_in:,}）-> {args.out}/", flush=True)


if __name__ == "__main__":
    main()
