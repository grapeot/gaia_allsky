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


def render_tile(l, b, cols, L, out_prefix, lc, bc, fov_deg, S, psf_core_px,
                bortle, target_sky, star_contrast, chroma, target_white, out_fits=False):
    """渲一块 TAN 瓦片（切点 lc,bc）。out_fits=False 出 PNG+.hhh；True 出 float FITS。
    返回画面内星数（0 不输出）。"""
    from PIL import Image
    scale_rad = np.radians(fov_deg) / S
    cdelt = fov_deg / S
    xi, eta, vis = gnomonic(l, b, lc, bc)
    # xi(东)→ +x；eta(北)→ -y（图像 y 向下）。手性由 CDELT1<0 在 WCS 里表达。
    px = S / 2.0 + xi / scale_rad
    py = S / 2.0 - eta / scale_rad
    inside = vis & (px >= 0) & (px < S) & (py >= 0) & (py < S)
    n_in = int(inside.sum())
    if n_in == 0:
        return 0
    pxi = np.clip(px.astype(int), 0, S - 1)
    pyi = np.clip(py.astype(int), 0, S - 1)
    canvas = rs.accumulate_stars(S, S, pxi, pyi, inside, L, cols, psf_px=psf_core_px)
    # 立体角归一化：flux → 面亮度，与投影/分辨率/fov 无关，一套 tone 通用
    canvas = canvas * (REF_OMEGA / cdelt ** 2)
    sky = beg.rh.skyglow_level(bortle)
    sat = 6.0 * sky * beg.gain_for_mag_delta(0.0)
    canvas = beg.saturate_and_bloom(canvas, sat, (3.0, 9.0), (0.65, 0.35))
    canvas = beg.add_skyglow(canvas, bortle)
    rgb = beg.tone_adapted(canvas, target_sky, star_contrast, target_white, chroma)
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
                       out_fits=s.get("out_fits", False))


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
    args = ap.parse_args()

    with np.load(args.data) as d:
        l, b, g = d["l"][:], d["b"][:], d["g"][:]
        bv = np.nan_to_num(d["bp_rp"][:], nan=0.7)
    cols = rs.bv_to_rgb(bv)
    L = beg.visual_luminance_for_mags(g, args.bortle, args.value, 0.5)
    tile_kw = dict(psf_core_px=args.psf_core_px, bortle=args.bortle,
                   target_sky=args.target_sky, star_contrast=args.star_contrast,
                   chroma=args.chroma, target_white=args.target_white)

    if not args.tiles:
        n = render_tile(l, b, cols, L, args.out, args.lc, args.bc,
                        args.fov_deg, args.size, **tile_kw, out_fits=args.fits)
        print(f"单图 切点({args.lc},{args.bc}) fov={args.fov_deg}° size={args.size} "
              f"画面内星 {n:,} -> {args.out}.png", flush=True)
        return

    os.makedirs(args.out, exist_ok=True)
    llo, lhi = map(float, args.l_range.split(","))
    blo, bhi = map(float, args.b_range.split(","))
    lcs = np.arange(llo, lhi + 1e-6, args.tile_step)
    bcs = np.arange(blo, bhi + 1e-6, args.tile_step)
    jobs = []
    for lc in lcs:
        for bc in bcs:
            prefix = os.path.join(args.out, f"tile_l{lc:+.0f}_b{bc:+.0f}"
                                  .replace("+", "p").replace("-", "m"))
            jobs.append((prefix, float(lc % 360), float(bc)))
    print(f"瓦片网格 {len(lcs)}×{len(bcs)}={len(jobs)} 格，{args.workers} 进程并行，"
          f"每格 fov={args.tile_fov}° size={args.tile_size}", flush=True)

    global _SHARED
    _SHARED = dict(l=l, b=b, cols=cols, L=L, tile_fov=args.tile_fov,
                   tile_size=args.tile_size, tile_kw=tile_kw, out_fits=args.fits)
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
