"""TAN(gnomonic)天球投影渲染 + 输出 WCS，喂 Aladin hipsgen。

地平投影没法写标准 WCS（地平坐标随时间变、非天球固定位置）。要喂 Aladin
必须用天球投影。这里用 gnomonic(TAN)以银心为切点把星投到平面，输出 PNG +
同名 .hhh（FITS WCS header），hipsgen 读 (in=dir color=png) 自动按 WCS 拼 HiPS。

小 PSF 锐星（高分辨率本就该是分解的单星，乳光交给金字塔降采样涌现，见
working.md 双 bug 确诊）。tone 每张图自己做（统计带 signal_mask）。

WCS（银道 TAN）：
  CTYPE = GLON-TAN / GLAT-TAN ; CRVAL = 切点(lc,bc) ; CRPIX = 切点像素
  CDELT = 每像素度数（决定视场）。

用法（最小验证）：
  python src/render_tan_wcs.py --data data/raw/fov_g20.npz --out outputs/tan_test \
      --lc 0 --bc 0 --fov-deg 40 --size 1024
"""
import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import render_starmap as rs
import render_bortle_eye_grid as beg


def gnomonic(l, b, lc, bc):
    """银道 (l,b)→ TAN 标准平面坐标 (xi, eta)，单位弧度。切点 (lc,bc)。
    返回 xi(东向), eta(北向), 以及前半球可见掩码。"""
    lr, br = np.radians(l), np.radians(b)
    l0, b0 = np.radians(lc), np.radians(bc)
    dl = lr - l0
    cosc = np.sin(b0) * np.sin(br) + np.cos(b0) * np.cos(br) * np.cos(dl)
    # cosc>0 为切点同半球（gnomonic 只在半球内有定义）
    vis = cosc > 1e-6
    xi = np.cos(br) * np.sin(dl) / np.maximum(cosc, 1e-9)
    eta = (np.cos(b0) * np.sin(br) - np.sin(b0) * np.cos(br) * np.cos(dl)) / np.maximum(cosc, 1e-9)
    return xi, eta, vis


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True, help="输出前缀（生成 .png + .hhh）")
    ap.add_argument("--lc", type=float, default=0.0, help="切点银经")
    ap.add_argument("--bc", type=float, default=0.0, help="切点银纬")
    ap.add_argument("--fov-deg", type=float, default=40.0, help="图的角宽度（度）")
    ap.add_argument("--size", type=int, default=1024, help="图边长像素（方图）")
    ap.add_argument("--psf-core-px", type=float, default=0.6)
    ap.add_argument("--faint-gain", type=float, default=1.0)
    ap.add_argument("--bortle", type=int, default=1)
    ap.add_argument("--target-sky", type=float, default=0.012)
    ap.add_argument("--star-contrast", type=float, default=6.0)
    ap.add_argument("--chroma", type=float, default=1.8)
    ap.add_argument("--target-white", type=float, default=2.5,
                    help="高光膝点。银心 TAN 构图用 2.5（1.0 是压裂隙高光的特殊值，"
                         "会把整图压暗）。")
    args = ap.parse_args()

    S = args.size
    scale_rad = np.radians(args.fov_deg) / S   # 弧度/像素
    cdelt = args.fov_deg / S                    # 度/像素

    with np.load(args.data) as d:
        l, b, g = d["l"][:], d["b"][:], d["g"][:]
        bv = np.nan_to_num(d["bp_rp"][:], nan=0.7)

    xi, eta, vis = gnomonic(l, b, args.lc, args.bc)
    # 平面坐标 → 像素：切点在图中心 (S/2)。xi 东向→ -x（天文东在左），eta 北向→ -y
    px = (S / 2.0 - xi / scale_rad)
    py = (S / 2.0 - eta / scale_rad)
    inside = vis & (px >= 0) & (px < S) & (py >= 0) & (py < S)
    pxi = px.astype(int); pyi = py.astype(int)
    pxi = np.clip(pxi, 0, S - 1); pyi = np.clip(pyi, 0, S - 1)
    print(f"切点(l,b)=({args.lc},{args.bc}) fov={args.fov_deg}° size={S} "
          f"画面内星 {int(inside.sum()):,}", flush=True)

    cols = rs.bv_to_rgb(bv)
    L = beg.visual_luminance_for_mags(g, args.bortle, 0.0, 0.5)
    canvas = rs.accumulate_stars(S, S, pxi, pyi, inside, L, cols, psf_px=args.psf_core_px)
    # 立体角归一化：星光是 flux 语义，每像素值随像素角面积 ∝ Ω 变化，不同
    # fov/分辨率/投影下同样的星给出不同的每像素亮度（这正是"TAN 图比广州地平
    # 暗"的真因，而非 tone 问题）。除以像素立体角 → 面亮度（radiance），与
    # 分辨率/投影无关，一套 tone 通用，且金字塔 sum 池化后仍自洽。参考立体角
    # 取广州正式图的像素当量(0.083°)²，让归一后数值落在 tone 习惯范围。
    REF_OMEGA = 0.083 ** 2
    canvas = canvas * (REF_OMEGA / cdelt ** 2)
    # tone（单张图，带 signal_mask）
    sky = beg.rh.skyglow_level(args.bortle)
    sat = 6.0 * sky * beg.gain_for_mag_delta(0.0)
    canvas = beg.saturate_and_bloom(canvas, sat, (3.0, 9.0), (0.65, 0.35))
    canvas = beg.add_skyglow(canvas, args.bortle)
    y = canvas.sum(-1); mask = y > (float(y.min()) + 0.004)
    ad = beg.adapt_sky_floor(canvas, args.target_sky, 25.0, args.star_contrast, signal_mask=mask)
    st = beg.signal_stretch_for_adapted(ad, args.target_sky, 99.5, args.target_white, signal_mask=mask)
    rgb = beg.finish_sky_adapted(ad, args.target_sky, 2.2, args.target_white, st, args.chroma)

    from PIL import Image
    arr = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    png = args.out + ".png"
    Image.fromarray(arr).save(png)

    # WCS .hhh（FITS header 文本，每行 80 列，hipsgen 认 PNG+同名 .hhh）
    hdr = [
        f"SIMPLE  = T",
        f"BITPIX  = 8",
        f"NAXIS   = 2",
        f"NAXIS1  = {S}",
        f"NAXIS2  = {S}",
        f"CTYPE1  = 'GLON-TAN'",
        f"CTYPE2  = 'GLAT-TAN'",
        f"CRVAL1  = {args.lc}",
        f"CRVAL2  = {args.bc}",
        f"CRPIX1  = {S/2.0}",
        f"CRPIX2  = {S/2.0}",
        f"CDELT1  = {cdelt}",
        f"CDELT2  = {cdelt}",
        f"END",
    ]
    hhh = args.out + ".hhh"
    with open(hhh, "w") as f:
        f.write("".join(f"{line:<80}" for line in hdr))
    print(f"wrote {png} + {hhh}", flush=True)


if __name__ == "__main__":
    main()
