"""直渲 HiPS pipeline——点源直接 splat 到 HEALPix 瓦片，绕过 hipsgen 重投影。多 order 并行。

每个 order 各渲：cone_search 出该 order 的 HEALPix cell 列表 → 每个 cell 直渲一个 512² 瓦片
（星→sub-healpix→瓦片像素 (x,y) 经 healpy.pix2xyf，col/row 转置对齐 HiPS 写盘约定）→
复用 bloom/立体角归一化/calib tone → 写 NorderK/DirD/NpixN.jpg。最后补 properties+Allsky+
index.html。无 TAN 中间产物、无 hipsgen 重投影。

用法：python render_hips_direct_pipeline.py <npz> <lc> <bc> <half> <orders> <out> [workers]
"""
import numpy as np, sys, os, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import render_starmap as rs, render_bortle_eye_grid as beg, render_tan_wcs as tw
import healpy as hp
from astropy_healpix import HEALPix
from astropy.coordinates import Galactic, ICRS, SkyCoord
import astropy.units as u
from PIL import Image
import multiprocessing as mp

TILE, SUBBITS = 512, 9

# worker 共享只读（fork）
_S = None


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


def _render_tile(job):
    """直渲一个 (korder, npix) 瓦片，写盘，返回 1/0。"""
    korder, npix = job
    s = _S
    nside_sub = 2 ** (korder + SUBBITS)
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
    # 所有 cone 内星映到本瓦片局部坐标 (lx,ly)（相对本 cell；邻瓦片的星会落在 [0,512) 外）。
    # 用 sub-healpix 的 face 坐标减本 cell 偏移——同 face 才有意义；不同 face 的星 lx/ly 会很大，
    # 自然被各自的范围判定排除。
    nside_sub = 2 ** (korder + SUBBITS)
    ssub_all = hp.ang2pix(nside_sub, rra, rdec, nest=True, lonlat=True)
    sx_all, sy_all, sf_all = hp.pix2xyf(nside_sub, ssub_all, nest=True)
    x8, y8, f8 = hp.pix2xyf(2 ** korder, npix, nest=True)
    lx_all = sx_all - x8 * TILE; ly_all = sy_all - y8 * TILE
    same_face = (sf_all == f8)
    # 暗星 + 主体：严格落本瓦片内（中心在 [0,512)）才 accumulate 锐点。
    inside = same_face & (lx_all >= 0) & (lx_all < TILE) & (ly_all >= 0) & (ly_all < TILE)
    if not np.any(inside):
        return 0
    # —— 逆剪切矩阵 A（不动 canvas 几何，把剪切吸进每颗星的核——无 affine、无接缝）——
    # HEALPix 瓦片像素网格球面非正交（x/y 夹角随位置~78°，等面积不保角，见 docs/Primer §3）。
    # 星 splat 斜网格 + 画圆核 = 球面斜椭圆。算瓦片像素(row=x,col=y)→切平面雅可比 J，所有点源
    # 的核都画 A=J/√det 的逆椭圆 → 球面恢复正圆。每颗星画在真实位置 + 椭圆核，邻瓦片各画各的、
    # 重叠区一致（同 bloom 跨瓦片那套），无 affine 整图重采样、无黑缝/接缝。
    def _vec(gx, gy):
        p = hp.xyf2pix(nside_sub, int(gx), int(gy), int(f8), nest=True)
        return np.array(hp.pix2vec(nside_sub, p, nest=True))
    o0 = _vec(x8*TILE + 256, y8*TILE + 256)
    erow = _vec(x8*TILE + 266, y8*TILE + 256) - o0   # +row(=+x=lx)
    ecol = _vec(x8*TILE + 256, y8*TILE + 266) - o0   # +col(=+y=ly)
    uu = erow / np.linalg.norm(erow)
    vv = ecol - np.dot(ecol, uu) * uu; vv = vv / np.linalg.norm(vv)
    J = np.array([[np.dot(erow, uu), np.dot(ecol, uu)],
                  [np.dot(erow, vv), np.dot(ecol, vv)]]) / 10   # 列序 (row,col)
    A = J / np.sqrt(abs(np.linalg.det(J)))                       # 去缩放、只剪切+旋转

    arcsec_px = cdelt * 3600.0
    margin = int(np.ceil(5.0 * tw.BLOOM_WING_ARCSEC / arcsec_px))
    # 暗星（G>FAINT，十亿颗）：锐点 psf 0.6，0.6px 椭圆肉眼无差异，仍走 accumulate（快）。
    coli = np.clip(ly_all, 0, TILE-1).astype(int)
    rowi = np.clip(lx_all, 0, TILE-1).astype(int)
    faint = inside & (gs > tw.BLOOM_G_FAINT)
    canvas = rs.accumulate_stars(TILE, TILE, coli, rowi, faint, Ls, colss, psf_px=psf)
    canvas = canvas * (tw.REF_OMEGA / cdelt ** 2)
    # 亮星（G≤FAINT）：核 + 翼都走 _bright_star_wings 的椭圆核（shear=A）——这样饱和大盘也是
    # 球面圆（之前 accumulate 圆盘 + 圆核翼，饱和中心还椭）。纳入邻区修跨瓦片截断。
    bright = (same_face & (gs <= tw.BLOOM_G_FAINT)
              & (lx_all >= -margin) & (lx_all < TILE + margin)
              & (ly_all >= -margin) & (ly_all < TILE + margin))
    if np.any(bright):
        bcol = np.clip(ly_all + margin, 0, TILE + 2*margin - 1).astype(int)
        brow = np.clip(lx_all + margin, 0, TILE + 2*margin - 1).astype(int)
        wings = tw._bright_star_wings(TILE, bcol[bright], brow[bright],
                                      Ls[bright], colss[bright], gs[bright], cdelt,
                                      margin=margin, shear=A)
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
        cells = hpk.cone_search_lonlat(cgal.ra, cgal.dec, half*1.5*u.deg)
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
