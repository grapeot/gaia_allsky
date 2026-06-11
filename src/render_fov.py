"""亿级星表的并行渲染入口（广州 FOV 深星表）。

瓶颈定位（profiling G<20 6.16 亿星）：逐星坐标变换 gal_to_altaz(52s)+project(24s)
占 68% 时间且是内存峰值(114G)来源，全是单线程 O(N)。本脚本把整条"逐星处理 →
累加到画布"的管线分段并行（32 核），每个 worker 处理一段星、各自累加到自己的
线性画布，主进程求和后再做 PSF 卷积 + 显示链（只一次）。

正确性保证：并行只到"线性画布累加"为止——这一步是可加的（不同星累加到同一画
布，分段求和等价于全量）。PSF 卷积、饱和溢出、tone/软肩/chroma 这些非线性、全局
的显示操作留在主进程对合并画布做一次，复用正式渲染器函数，数值与单进程一致。
（教训：早先手搓的 render_fov_parallel 把显示链也搬进 worker 复刻错了导致过曝，
已废弃。显示链绝不进 worker。）

linear-canvas 缓存：--save-linear 把 PSF 卷积后、显示链前的线性画布存成 .npy，
之后专调 tone mapping 只读它、零点几秒迭代，不必重渲 6 亿星。

用法：
  python src/render_fov.py --data data/raw/fov_g20.npz --out outputs/fov_g20.png \
      --faint-gain 1.0 --workers 28 --save-linear outputs/fov_g20_linear.npy
"""
import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import render_horizon as rh
import render_starmap as rs
import render_bortle_eye_grid as beg

# worker 共享的只读大数组（模块级，fork 时 copy-on-write，不走 pickle）
_G = {}


def _init(data_path, params):
    """worker 初始化：mmap 加载星表列 + 缓存渲染参数。"""
    d = np.load(data_path, mmap_mode="r")
    _G["l"] = d["l"]; _G["b"] = d["b"]; _G["g"] = d["g"]; _G["bp_rp"] = d["bp_rp"]
    _G["p"] = params


def _render_chunk(rng):
    """worker：处理 [lo,hi) 段星，返回累加到的线性画布 H×W×3（未卷积）。"""
    lo, hi = rng
    p = _G["p"]
    l = np.asarray(_G["l"][lo:hi], float)
    b = np.asarray(_G["b"][lo:hi], float)
    g = np.asarray(_G["g"][lo:hi], float)
    bv = np.nan_to_num(np.asarray(_G["bp_rp"][lo:hi], float), nan=0.7)

    px, py, inside = beg.project_guangzhou_fov(
        l, b, p["lat"], p["lst"], p["W"], p["H"], p["az_w"], p["max_alt"], "horizontal")
    cols = rs.bv_to_rgb(bv)
    L = beg.visual_luminance_for_mags(g, p["bortle"], p["value"], p["lim_contrast"])
    faint = g >= p["faint_mag_min"]
    if p["faint_gain"] != 1.0:
        L = L.copy(); L[faint] *= p["faint_gain"]
    # psf_px=0：worker 只累加，不卷积（卷积是全局操作，留主进程做一次）
    return rs.accumulate_stars(p["H"], p["W"], px, py, inside, L, cols, psf_px=0.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=28)
    ap.add_argument("--chunk", type=int, default=25_000_000)
    ap.add_argument("--save-linear", default=None,
                    help="把 PSF 卷积后、显示链前的线性画布存为 .npy（用于专调 tone）。")
    ap.add_argument("--bortle", type=int, default=1)
    ap.add_argument("--value", type=float, default=0.0)
    ap.add_argument("--faint-gain", type=float, default=1.0)
    ap.add_argument("--faint-mag-min", type=float, default=11.0)
    ap.add_argument("--psf-core-px", type=float, default=0.6)
    ap.add_argument("--sat-over-sky", type=float, default=6.0)
    ap.add_argument("--target-sky", type=float, default=0.012)
    ap.add_argument("--star-contrast", type=float, default=6.0)
    ap.add_argument("--chroma", type=float, default=1.8)
    ap.add_argument("--ext-threshold", type=float, default=0.035)
    ap.add_argument("--lim-contrast", type=float, default=0.5)
    ap.add_argument("--lat", type=float, default=23.13)
    ap.add_argument("--lst", type=float, default=17.76)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--az-width", type=float, default=90.0)
    ap.add_argument("--max-alt", type=float, default=75.0)
    args = ap.parse_args()

    import time
    n = int(np.load(args.data, mmap_mode="r")["g"].shape[0])
    print(f"{n:,} 星，{args.workers} worker", flush=True)
    params = dict(lat=args.lat, lst=args.lst, W=args.width, H=args.height,
                  az_w=args.az_width, max_alt=args.max_alt, bortle=args.bortle,
                  value=args.value, lim_contrast=args.lim_contrast,
                  faint_gain=args.faint_gain, faint_mag_min=args.faint_mag_min)
    ranges = [(i, min(i + args.chunk, n)) for i in range(0, n, args.chunk)]

    t = time.time()
    canvas = np.zeros((args.height, args.width, 3), np.float64)
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init,
                             initargs=(args.data, params)) as ex:
        for fut in as_completed([ex.submit(_render_chunk, r) for r in ranges]):
            canvas += fut.result()
    print(f"并行累加完成 {time.time()-t:.1f}s（{len(ranges)} 块）", flush=True)

    # 以下全局操作主进程做一次。复刻 render_panel_canvas 后半 + normalize_panel，
    # 保证与正式渲染器逐像素一致（之前手搓显示链导致过曝，已弃）。worker 返回的
    # 是纯线性累加画布（psf=0、无 sat/ext/skyglow），全局步骤全在这里：
    #   PSF 卷积 → saturate_and_bloom → ext_threshold → add_skyglow → normalize_panel
    from scipy.ndimage import gaussian_filter
    canvas = canvas.astype(np.float32)
    if args.psf_core_px > 0:
        for c in range(3):
            canvas[..., c] = gaussian_filter(canvas[..., c], args.psf_core_px)
    sky = rh.skyglow_level(args.bortle)
    if args.sat_over_sky > 0:
        sat = args.sat_over_sky * sky * beg.gain_for_mag_delta(args.value)
        canvas = beg.saturate_and_bloom(canvas, sat, (3.0, 9.0), (0.65, 0.35))
    canvas = beg.apply_extended_visibility_threshold(canvas, sky, args.ext_threshold, 8.0)
    canvas = beg.add_skyglow(canvas, args.bortle)
    if args.save_linear:
        np.save(args.save_linear, canvas)
        print(f"linear canvas saved -> {args.save_linear}", flush=True)
    # 与正式渲染器同一个显示函数；signal_stretch=None（单图自适应白点）
    arr = beg.normalize_panel(
        canvas, "sky_median", 99.7, 2.2, args.target_sky, 99.5, 25.0,
        args.star_contrast, 2.0, None, args.chroma)

    from PIL import Image
    Image.fromarray(arr).save(args.out)
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
