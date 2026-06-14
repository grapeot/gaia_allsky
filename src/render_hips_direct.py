"""直渲 HiPS 瓦片——点源直接 splat 到 HEALPix 瓦片像素，绕过 TAN 中间产物 + hipsgen 重投影。

为什么（点源专属，对通用图像不成立）：hipsgen 是为"已有像素图重投影"设计的，对每个输出
HEALPix 像素反向映射 + 双线性 rasterize，源 tile 多时慢（全广州 N8 5.5h）。但我们是点源，
渲染过程本身就是投影——把每颗星直接 splat 到它所在的 HEALPix 瓦片像素，根本不需要 rasterize。
render 直接产出最终 HiPS 瓦片，128 核线性并行，没有 hipsgen 那道重投影瓶颈。

映射（已验证 = healpy 权威 pix2xyf）：NorderK 瓦片(512²)=一个 NorderK cell，512²子像素=
Norder(K+9) 子 cell（nested 连续）。子像素在瓦片的 (x,y) 由 nested z-order 解交织得到
（healpy.pix2xyf 给 face 内坐标，减 cell 偏移 = 瓦片局部 (lx,ly)）。

复用 render_tan_wcs 的 accumulate/bloom/立体角归一化/tone——只把投影从 gnomonic 换成 HEALPix。
"""
import numpy as np
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import render_starmap as rs
import render_bortle_eye_grid as beg
import render_tan_wcs as tw

TILE = 512          # HiPS 瓦片边长（标准）
SUBBITS = 9         # 512 = 2^9


def hips_tile_canvas(npix, korder, ls_gal, bs_gal, Ls, colss, gs,
                     ra_sub, dec_sub, hp_sub, x_cell, y_cell, f_cell,
                     bortle=1, psf_core_px=0.6):
    """直渲一个 NorderK HiPS 瓦片的 raw canvas（tone 前）。
    star (l,b)gal 已转 ra/dec；映射到瓦片像素 (lx,ly)，accumulate + 亮星翼。复用 tw 的 bloom。"""
    import healpy as hp
    nside_sub = 2 ** (korder + SUBBITS)
    cdelt = (58.6323 / 2 ** korder) / TILE          # 该层瓦片像素角分辨率（度）
    # 星 → sub healpix → 落本瓦片的 + 瓦片局部 (lx,ly)
    ssub = hp.ang2pix(nside_sub, ra_sub, dec_sub, nest=True, lonlat=True)
    in_tile = (ssub // (4 ** SUBBITS)) == npix
    if not np.any(in_tile):
        return None
    sx, sy, sf = hp.pix2xyf(nside_sub, ssub[in_tile], nest=True)
    lx = sx - x_cell * TILE
    ly = sy - y_cell * TILE
    inside = (lx >= 0) & (lx < TILE) & (ly >= 0) & (ly < TILE)
    if not np.any(inside):
        return None
    # healpy pix2xyf 的 (x,y) 与 HiPS 瓦片写盘的 (col,row) 差一个转置——实测亮星质心
    # 直渲(117,408) 转置(408,117)≈hipsgen(416,110)。所以 accumulate 的 col/row 用 (ly,lx)
    # 而非 (lx,ly)（等价转置），让 Aladin 显示朝向与 hipsgen 一致。
    lxi = np.clip(ly, 0, TILE - 1).astype(int)   # col ← healpy y
    lyi = np.clip(lx, 0, TILE - 1).astype(int)   # row ← healpy x
    Lin = Ls[in_tile]; cin = colss[in_tile]; gin = gs[in_tile] if gs is not None else None
    # 暗星锐点 accumulate（复用）
    canvas = rs.accumulate_stars(TILE, TILE, lxi, lyi, inside, Lin, cin, psf_px=psf_core_px)
    canvas = canvas * (tw.REF_OMEGA / cdelt ** 2)
    # 亮星翼（复用 tw._bright_star_wings，扩边消边缘截断）
    if gin is not None:
        arcsec_px = cdelt * 3600.0
        margin = int(np.ceil(5.0 * tw.BLOOM_WING_ARCSEC / arcsec_px))
        bright = inside & (gin <= tw.BLOOM_G_FAINT)
        if np.any(bright):
            lxm = (lxi + margin); lym = (lyi + margin)
            wings = tw._bright_star_wings(TILE, lxm[bright], lym[bright], Lin[bright],
                                          cin[bright], gin[bright], cdelt, margin=margin)
            canvas = canvas + wings * (tw.REF_OMEGA / cdelt ** 2)
    canvas = beg.add_skyglow(canvas, bortle)
    return canvas
