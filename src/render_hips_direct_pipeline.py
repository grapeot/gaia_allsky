"""直渲 HiPS pipeline——点源直接 splat 到 HEALPix 瓦片，绕过 hipsgen 重投影。多 order 并行。

每个 order 各渲：cone_search 出该 order 的 HEALPix cell 列表 → 每个 cell 直渲一个 512² 瓦片
（星→HEALPix cell 内连续 dx/dy→瓦片像素 (x,y)，col/row 转置对齐 HiPS 写盘约定）→
复用 bloom/立体角归一化/calib tone → 写 NorderK/DirD/NpixN.jpg。最后补 properties+Allsky+
index.html。无 TAN 中间产物、无 hipsgen 重投影。

用法：python render_hips_direct_pipeline.py <npz> <lc> <bc> <half> <orders> <out> [workers]
"""
import numpy as np, sys, os, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import render_starmap as rs, render_bortle_eye_grid as beg, render_tan_wcs as tw
import healpy as hp
import cdshealpix as cds
from astropy_healpix import HEALPix
from astropy.coordinates import Galactic, ICRS, SkyCoord
import astropy.units as u
from PIL import Image
import multiprocessing as mp

TILE = 512

# worker 共享只读（fork）
_S = None


def _lonlat_to_vec(lon_deg, lat_deg):
    """ICRS lon/lat degrees -> unit vectors, vectorized."""
    lon = np.radians(lon_deg)
    lat = np.radians(lat_deg)
    clat = np.cos(lat)
    return np.stack([clat * np.cos(lon), clat * np.sin(lon), np.sin(lat)], axis=-1)


def _local_shear_matrices(npix, korder, dx, dy):
    """Per-position HEALPix parameter-plane shear for one HiPS tile.

    dx/dy are continuous offsets inside the order-k HEALPix cell.  The returned
    matrices map tile pixel (row=dx, col=dy) deltas to a local tangent plane,
    normalized to remove scale and keep only rotation+shear.
    """
    dx = np.asarray(dx, dtype=float)
    dy = np.asarray(dy, dtype=float)
    if dx.size == 0:
        return np.empty((0, 2, 2), dtype=float)

    eps = 10.0 / TILE
    one = np.nextafter(1.0, 0.0)
    dx0 = np.maximum(dx - eps, 0.0)
    dx1 = np.minimum(dx + eps, one)
    dy0 = np.maximum(dy - eps, 0.0)
    dy1 = np.minimum(dy + eps, one)
    # Avoid zero denominators at pathological exact boundaries.
    dx1 = np.where(dx1 <= dx0, np.minimum(dx0 + eps, one), dx1)
    dy1 = np.where(dy1 <= dy0, np.minimum(dy0 + eps, one), dy1)

    ip = np.array([int(npix)], dtype=np.uint64)
    depth = np.array([int(korder)], dtype=np.uint8)

    erow = np.empty((dx.size, 3), dtype=float)
    ecol = np.empty((dx.size, 3), dtype=float)
    for i in range(dx.size):
        lon_x1, lat_x1 = cds.healpix_to_lonlat(ip, depth, dx=float(dx1[i]), dy=float(dy[i]))
        lon_x0, lat_x0 = cds.healpix_to_lonlat(ip, depth, dx=float(dx0[i]), dy=float(dy[i]))
        lon_y1, lat_y1 = cds.healpix_to_lonlat(ip, depth, dx=float(dx[i]), dy=float(dy1[i]))
        lon_y0, lat_y0 = cds.healpix_to_lonlat(ip, depth, dx=float(dx[i]), dy=float(dy0[i]))
        erow[i] = (_lonlat_to_vec(lon_x1.deg, lat_x1.deg) - _lonlat_to_vec(lon_x0.deg, lat_x0.deg))[0] \
            / max((dx1[i] - dx0[i]) * TILE, 1e-12)
        ecol[i] = (_lonlat_to_vec(lon_y1.deg, lat_y1.deg) - _lonlat_to_vec(lon_y0.deg, lat_y0.deg))[0] \
            / max((dy1[i] - dy0[i]) * TILE, 1e-12)

    uu = erow / np.maximum(np.linalg.norm(erow, axis=1, keepdims=True), 1e-30)
    ecol_u = np.sum(ecol * uu, axis=1)
    vv = ecol - ecol_u[:, None] * uu
    vv = vv / np.maximum(np.linalg.norm(vv, axis=1, keepdims=True), 1e-30)

    J = np.empty((dx.size, 2, 2), dtype=float)
    J[:, 0, 0] = np.sum(erow * uu, axis=1)
    J[:, 0, 1] = np.sum(ecol * uu, axis=1)
    J[:, 1, 0] = np.sum(erow * vv, axis=1)
    J[:, 1, 1] = np.sum(ecol * vv, axis=1)
    det = np.linalg.det(J)
    return J / np.sqrt(np.maximum(np.abs(det), 1e-30))[:, None, None]


def _elliptical_gaussian_kernel(sig, shear, truncate=5.0):
    """Unit-sum Gaussian kernel whose pixel-space ellipse maps to a sky-circle."""
    sinv = np.linalg.inv(shear)
    cov = (sinv @ sinv.T) * (sig * sig)
    cinv = np.linalg.inv(cov)
    r = int(np.ceil(truncate * sig * np.sqrt(np.linalg.eigvalsh(sinv @ sinv.T).max())))
    ax = np.arange(-r, r + 1)
    yy, xx = np.meshgrid(ax, ax, indexing="ij")
    q = cinv[0, 0] * yy * yy + 2 * cinv[0, 1] * yy * xx + cinv[1, 1] * xx * xx
    k = np.exp(-0.5 * q)
    return k / np.maximum(k.sum(), 1e-30), r


def _bincount_bilinear(h, w, col, row, luminance, rgb):
    """Bilinear splat RGB flux at continuous pixel coordinates into an HxW image."""
    col = np.asarray(col, dtype=float)
    row = np.asarray(row, dtype=float)
    x0 = np.floor(col).astype(np.int64)
    y0 = np.floor(row).astype(np.int64)
    fx = col - x0
    fy = row - y0
    acc = np.zeros((h * w, 3), dtype=np.float64)
    weights_rgb = np.asarray(luminance)[:, None] * np.asarray(rgb)
    for dx, dy, wt in ((0, 0, (1 - fx) * (1 - fy)),
                       (1, 0, fx * (1 - fy)),
                       (0, 1, (1 - fx) * fy),
                       (1, 1, fx * fy)):
        xx = x0 + dx
        yy = y0 + dy
        m = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h) & (wt > 0)
        if not np.any(m):
            continue
        flat = yy[m] * w + xx[m]
        vals = weights_rgb[m] * wt[m, None]
        for c in range(3):
            acc[:, c] += np.bincount(flat, weights=vals[:, c], minlength=h * w)
    return acc.reshape(h, w, 3)


def _accumulate_faint_sheared(korder, npix, col, row, inside, luminance, rgb,
                              dx, dy, psf_px=0.6, block=64):
    """Accumulate faint stars with a block-local HEALPix shear-compensated PSF.

    Low-order HiPS tiles cover large sky areas, so HEALPix shear changes across one
    tile.  A single circular gaussian_filter draws the unresolved star field in
    pixel space, which Aladin then displays as a sheared sky texture.  This helper
    keeps the fast binning path, but splits the tile into small blocks and filters
    each block with the local inverse-shear Gaussian.
    """
    from scipy.ndimage import convolve

    col = np.asarray(col)[inside].astype(float)
    row = np.asarray(row)[inside].astype(float)
    lum = np.asarray(luminance)[inside]
    cols = np.asarray(rgb)[inside]
    if col.size == 0:
        return np.zeros((TILE, TILE, 3), dtype=np.float32)

    canvas = np.zeros((TILE, TILE, 3), dtype=np.float64)
    for y0 in range(0, TILE, block):
        y1 = min(TILE, y0 + block)
        for x0 in range(0, TILE, block):
            x1 = min(TILE, x0 + block)
            m = (row >= y0) & (row < y1) & (col >= x0) & (col < x1)
            if not np.any(m):
                continue
            cy = (y0 + y1) * 0.5 / TILE
            cx = (x0 + x1) * 0.5 / TILE
            shear = _local_shear_matrices(npix, korder, np.array([cy]), np.array([cx]))[0]
            ker, r = _elliptical_gaussian_kernel(psf_px, shear)

            yy0 = max(0, y0 - r); yy1 = min(TILE, y1 + r)
            xx0 = max(0, x0 - r); xx1 = min(TILE, x1 + r)
            h = yy1 - yy0; w = xx1 - xx0
            layer = _bincount_bilinear(h, w, col[m] - xx0, row[m] - yy0, lum[m], cols[m])
            for c in range(3):
                canvas[yy0:yy1, xx0:xx1, c] += convolve(layer[..., c], ker, mode="constant", cval=0.0)
    return canvas.astype(np.float32)


def _tone(canvas, calib):
    if calib:
        a = beg.adapt_sky_floor(canvas, calib['target_sky'], 25.0, calib['star_contrast'],
                                sky_anchor=calib['sky_anchor'])
        rgb = np.clip(beg.finish_sky_adapted(a, calib['target_sky'], 2.2, calib['target_white'],
                                             calib['stretch'], 1.8), 0, 1)
    else:
        rgb = np.clip(canvas, 0, 1)
    # PI 调色（色温/去绿，pi_curves_scnr）——和 TAN 路径一致，直渲之前漏了这步
    cp = _S.get('color_procs') if _S else None
    if cp is not None:
        import pi_curves_scnr as pcs
        rgb = pcs.apply_xpsm(np.clip(rgb, 0.0, 1.0), cp)
    return rgb


def _tile_search_radius(half, korder):
    """Cone radius for selecting HiPS tiles at one order, including a low-order guard band."""
    tile_fov = 58.6323 / 2 ** korder
    return max(half * 1.5, half + tile_fov)


def _render_tile(job):
    """直渲一个 (korder, npix) 瓦片，写盘，返回 1/0。"""
    korder, npix = job
    s = _S
    cdelt = (58.6323 / 2 ** korder) / TILE
    psf = 0.6
    hp8 = HEALPix(nside=2 ** korder, order='nested', frame=ICRS())
    cra, cdec = hp8.healpix_to_lonlat(npix)
    cg = SkyCoord(ra=cra, dec=cdec, frame=ICRS()).galactic
    fov = 58.6323 / 2 ** korder
    hpb = s['hpb']
    pix = hpb.cone_search_lonlat(cg.l.deg * u.deg, cg.b.deg * u.deg, fov * 0.9 * u.deg)
    pix = pix[s['count'][pix] > 0]
    if len(pix) == 0:
        return 0
    seg = [[], [], [], []]
    for p in pix:
        st = int(s['start'][p]); c = int(s['count'][p])
        seg[0].append(s['l'][st:st+c]); seg[1].append(s['b'][st:st+c])
        seg[2].append(s['L'][st:st+c]); seg[3].append(s['g'][st:st+c])
    ls = np.concatenate(seg[0]); bs = np.concatenate(seg[1])
    Ls = np.concatenate(seg[2]); gs = np.concatenate(seg[3])
    cols = s['cols']
    # cols 按 bucket 段取
    cseg = []
    for p in pix:
        st = int(s['start'][p]); c = int(s['count'][p]); cseg.append(cols[st:st+c])
    colss = np.concatenate(cseg)
    sc = SkyCoord(l=ls * u.deg, b=bs * u.deg, frame=Galactic()).icrs
    rra = sc.ra.deg; rdec = sc.dec.deg
    # 所有 cone 内星映到本瓦片局部连续坐标 (lx,ly)。cdshealpix 给的是 owning order-k cell 内
    # dx/dy∈[0,1)，用于本 tile 内星；high-order face 坐标用于跨 tile 边界的 PSF 贡献。
    depth = np.full(rra.shape, int(korder), dtype=np.uint8)
    ip_all, dx_all, dy_all = cds.lonlat_to_healpix(rra * u.deg, rdec * u.deg, depth,
                                                   return_offsets=True)
    in_cell = (ip_all == int(npix))
    lx_all = dx_all * TILE
    ly_all = dy_all * TILE

    nside_sub = 2 ** (korder + 9)
    ssub_all = hp.ang2pix(nside_sub, rra, rdec, nest=True, lonlat=True)
    sx_all, sy_all, sf_all = hp.pix2xyf(nside_sub, ssub_all, nest=True)
    xk, yk, fk = hp.pix2xyf(2 ** korder, npix, nest=True)
    lx_face = sx_all - xk * TILE
    ly_face = sy_all - yk * TILE
    same_face = sf_all == fk
    lx_psf = np.where(in_cell, lx_all, lx_face)
    ly_psf = np.where(in_cell, ly_all, ly_face)

    if not np.any(in_cell):
        return 0

    arcsec_px = cdelt * 3600.0
    margin = int(np.ceil(5.0 * tw.BLOOM_WING_ARCSEC / arcsec_px))
    # 普通星使用连续亚像素坐标，并允许中心在邻 tile 的星贡献进当前 tile 的 PSF 尾部。
    # 整数化会把 0.6px 的椭圆核采成块状/梳状 PSF。
    faint_margin = int(np.ceil(5.0 * psf * 1.5))
    faint = (same_face & (gs > tw.BLOOM_G_FAINT)
             & (lx_psf >= -faint_margin) & (lx_psf < TILE + faint_margin)
             & (ly_psf >= -faint_margin) & (ly_psf < TILE + faint_margin))
    coli = ly_psf
    rowi = lx_psf
    # 普通星也必须按 HEALPix 局部剪切预补偿。否则 zoom-in 时像素圆 PSF 会被 Aladin 显示成
    # 球面椭圆。低 order 剪切梯度大，用 64px block；高 order 梯度小，用整 tile 中心 shear，
    # 避免把 N8 性能打穿。
    block = 64 if korder <= 5 else TILE
    canvas = _accumulate_faint_sheared(korder, npix, coli, rowi, faint, Ls, colss,
                                       dx_all, dy_all, psf_px=psf, block=block)
    canvas = canvas * (tw.REF_OMEGA / cdelt ** 2)
    # 亮星（G≤FAINT）：核 + 翼都走 _bright_star_wings 的椭圆核（shear=A）——这样饱和大盘也是
    # 球面圆（之前 accumulate 圆盘 + 圆核翼，饱和中心还椭）。纳入邻区修跨瓦片截断。
    # 亮星光晕必须跨 tile：星中心在邻 tile 时，它的 wing 仍可能落进当前 tile。
    bright_seed = gs <= tw.BLOOM_G_FAINT
    bright = (bright_seed & same_face
              & (lx_face >= -margin) & (lx_face < TILE + margin)
              & (ly_face >= -margin) & (ly_face < TILE + margin))
    if np.any(bright):
        bcol = (ly_face[bright] + margin).astype(int)
        brow = (lx_face[bright] + margin).astype(int)
        # 对 tile 外亮星，局部剪切取最近的 tile 边界位置。光晕落进当前 tile 的部分由当前
        # tile 的 HEALPix 局部几何决定；这样可避免中心在邻 tile 的亮星被整块裁掉。
        sdx = np.clip(lx_face[bright] / TILE, 0.0, np.nextafter(1.0, 0.0))
        sdy = np.clip(ly_face[bright] / TILE, 0.0, np.nextafter(1.0, 0.0))
        shear = _local_shear_matrices(npix, korder, sdx, sdy)
        wings = tw._bright_star_wings(TILE, bcol, brow,
                                      Ls[bright], colss[bright], gs[bright], cdelt,
                                      margin=margin, shear=shear)
        canvas = canvas + wings * (tw.REF_OMEGA / cdelt ** 2)
    canvas = beg.add_skyglow(canvas, 1)
    rgb = _tone(canvas, s['calib'])
    D = (npix // 10000) * 10000
    dd = os.path.join(s['out'], f"Norder{korder}", f"Dir{D}")
    os.makedirs(dd, exist_ok=True)
    Image.fromarray((np.clip(rgb, 0, 1) * 255).astype('uint8')).save(
        os.path.join(dd, f"Npix{npix}.jpg"), quality=92)
    return 1


def main():
    npz, lc, bc, half = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
    orders = [int(x) for x in sys.argv[5].split()]
    out = sys.argv[6]; W = int(sys.argv[7]) if len(sys.argv) > 7 else 8
    os.makedirs(out, exist_ok=True)
    d = np.load(npz)
    l, b, g = d['l'][:], d['b'][:], d['g'][:]
    cols = rs.bv_to_rgb(np.nan_to_num(d['bp_rp'][:], nan=0.7))
    L = beg.visual_luminance_for_mags(g, 1, 6.0, 0.5)
    order = int(d['order'])
    calib = json.load(open('outputs/ant_po2/o8/calib.json')) if os.path.exists('outputs/ant_po2/o8/calib.json') else None
    # PI 调色 procs（默认 batch_process_frames.xpsm，和 TAN 路径一致）
    color_procs = None
    cx = os.path.join(ROOT, 'skills', 'batch_process_frames.xpsm')
    if os.path.isfile(cx):
        sys.path.insert(0, os.path.join(ROOT, 'tools'))
        import pixinsight_batch as _pb
        color_procs = _pb.parse_xpsm(cx)
        print(f"Python 调色（pi_curves_scnr）: {len(color_procs)} process", flush=True)
    global _S
    _S = dict(l=l, b=b, g=g, L=L, cols=cols, out=out, calib=calib, color_procs=color_procs,
              hpb=HEALPix(nside=2**order, order='nested', frame=Galactic()),
              start=d['bucket_start'][:], count=d['bucket_count'][:])
    cgal = SkyCoord(l=lc*u.deg, b=bc*u.deg, frame=Galactic()).icrs
    # 收集所有 (order, npix) job
    jobs = []
    for k in orders:
        hpk = HEALPix(nside=2**k, order='nested', frame=ICRS())
        # Select every tile that can overlap the requested cone, plus one tile-width guard band.
        # The old half*1.5 missed coarse N3/N4 neighbours near the view boundary; Aladin then
        # displayed black seams in zoom-out even though high-order tiles existed.
        search_radius = _tile_search_radius(half, k)
        cells = hpk.cone_search_lonlat(cgal.ra, cgal.dec, search_radius*u.deg)
        jobs += [(k, int(n)) for n in cells]
    print(f"直渲 {len(jobs)} 瓦片（orders {orders}），{W} 进程", flush=True)
    ctx = mp.get_context('fork')
    t0 = time.time(); done = 0
    with ctx.Pool(W) as p:
        for r in p.imap_unordered(_render_tile, jobs, chunksize=4):
            done += r
    dt = time.time() - t0
    print(f"直渲完成 {done}/{len(jobs)} 非空瓦片，{dt:.1f}s ({len(jobs)/dt:.1f} tile/s) → {out}", flush=True)


if __name__ == "__main__":
    main()
