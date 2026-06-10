"""在银道 (l,b) 平面上直接渲染裂隙天区，对比 G<13 与 G<20 缝有没有变。

不经地平投影：l→x, b→y 直投影，让裂隙是水平带、最直观。复用正式渲染器的
成像链（统一 PSF + 饱和溢出 + 截断补偿）和显示链（sky floor + 软肩 + chroma），
只换投影。这样看到的缝形状不受观测者视角扭曲，纯粹反映星表数据本身。

用法：
  python src/render_rift_region.py --data data/raw/rift_region_g20.npz \
      --out outputs/_rift_region_g20.png --faint-gain 1.0
"""
import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import render_starmap as rs
import render_bortle_eye_grid as beg

# 天区范围（与 build_deep_rift_npz.py 一致），l 绕 0 点用 [-35,10] 显示
L_DISP_LO, L_DISP_HI = -35.0, 10.0   # 即 l∈[325,360]∪[0,10]，画时 l>180 减 360
B_LO, B_HI = -8.0, 10.0


def lb_project(l, b, width, height):
    """l→x, b→y 直投影。l 绕 0：>180 的减 360。"""
    lw = np.where(l > 180.0, l - 360.0, l)
    inside = (lw >= L_DISP_LO) & (lw <= L_DISP_HI) & (b >= B_LO) & (b <= B_HI)
    px = ((lw - L_DISP_LO) / (L_DISP_HI - L_DISP_LO) * width).astype(int)
    # b 向上为正，图像 y 向下，翻转
    py = ((B_HI - b) / (B_HI - B_LO) * height).astype(int)
    px = np.clip(px, 0, width - 1)
    py = np.clip(py, 0, height - 1)
    return px, py, inside


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--width", type=int, default=1600)
    p.add_argument("--height", type=int, default=640)
    p.add_argument("--bortle", type=int, default=1)
    p.add_argument("--value", type=float, default=0.0)
    p.add_argument("--psf-core-px", type=float, default=0.6)
    p.add_argument("--faint-gain", type=float, default=1.0,
                   help="深星表已含真实暗星，默认不加截断补偿增益（1.0）。")
    p.add_argument("--faint-mag-min", type=float, default=11.0)
    p.add_argument("--sat-over-sky", type=float, default=6.0)
    p.add_argument("--target-sky", type=float, default=0.012)
    p.add_argument("--star-contrast", type=float, default=6.0)
    p.add_argument("--chroma", type=float, default=1.8)
    p.add_argument("--ext-threshold", type=float, default=0.0,
                   help="弥散光阈值默认关（看缝的原始对比，不被人眼阈值抹掉）。")
    p.add_argument("--limiting-contrast", type=float, default=0.5)
    args = p.parse_args()

    d = np.load(args.data)
    l, b, g = d["l"], d["b"], d["g"]
    bv = np.nan_to_num(d["bp_rp"], nan=0.7)
    print(f"加载 {l.size} 星, G<{g.max():.1f}")

    W, H = args.width, args.height
    px, py, inside = lb_project(l, b, W, H)
    print(f"天区内 {int(inside.sum())} 星")
    cols = rs.bv_to_rgb(bv)

    L = beg.visual_luminance_for_mags(g, args.bortle, args.value, args.limiting_contrast)
    sky = beg.rh.skyglow_level(args.bortle)
    sat_level = (args.sat_over_sky * sky * beg.gain_for_mag_delta(args.value)
                 if args.sat_over_sky > 0 else None)
    canvas = beg.accumulate_uniform_psf_stars(
        H, W, px, py, inside, g, L, cols,
        args.psf_core_px, args.faint_gain, args.faint_mag_min, sat_level)
    if args.ext_threshold > 0:
        canvas = beg.apply_extended_visibility_threshold(canvas, sky, args.ext_threshold, 8.0)
    canvas = beg.add_skyglow(canvas, args.bortle)
    ad = beg.adapt_sky_floor(canvas, args.target_sky, 25.0, 6.0)
    stretch = beg.signal_stretch_for_adapted(ad, args.target_sky, 99.5, args.star_contrast)
    rgb = beg.finish_sky_adapted(ad, args.target_sky, 2.2, args.star_contrast, stretch, args.chroma)
    from PIL import Image
    arr = rgb if rgb.dtype == np.uint8 else np.clip(rgb * 255, 0, 255).astype(np.uint8)
    Image.fromarray(arr).save(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
