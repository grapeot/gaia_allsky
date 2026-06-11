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
    # 星场亮度锚在 scene_ref_bortle、value=0：visual_luminance_for_mags 会把
    # k(B)=skyglow(B)·10^(0.4·NELM(B)) 烘进线性画布，若用 swept bortle，星场就
    # 跟观察者的光污染纠缠在一起（sweep 模式的核心 bug）。单图路径默认
    # scene_ref_bortle=bortle、value=p["value"]，行为与历史完全一致。
    L = beg.visual_luminance_for_mags(g, p["scene_ref_bortle"], p["scene_value"], p["lim_contrast"])
    faint = g >= p["faint_mag_min"]
    if p["faint_gain"] != 1.0:
        L = L.copy(); L[faint] *= p["faint_gain"]
    # psf_px=0：worker 只累加，不卷积（卷积是全局操作，留主进程做一次）
    return rs.accumulate_stars(p["H"], p["W"], px, py, inside, L, cols, psf_px=0.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=None,
                    help="并行 worker 数。单图路径默认 28；sweep 路径默认 16（OOM 安全，"
                         "616M 星数组只加载一次，但仍把峰值压住）。")
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
    ap.add_argument("--ext-threshold", type=float, default=0.04,
                    help="Weber 可见度阈值（占天空亮度比例）。配合 sky-floor 物理锚 + "
                         "ext-softness=0.5，B7 银河淡到近不可见、B5 仍可见、B1 majestic、"
                         "且高 bortle 无硬斑（物理路定标，见 validate_bortle_series.py）。")
    ap.add_argument("--ext-knee", type=float, default=0.12,
                    help="Weber 阈值软膝宽度（threshold*sky 的比例）；0=硬阈值。"
                         "软膝消除阈值附近的硬边补丁感。仅 ext-softness=0 时生效。")
    ap.add_argument("--ext-softness", type=float, default=0.5,
                    help="Weber 对比域 sigmoid 软化宽度（log 对比 e-folds）；>0 启用对比域"
                         "软化路径，消除空间硬边补丁（高 bortle 银河柔和渐隐而非等高线硬块）。"
                         "0=退回减法/软膝路径。默认 0.5：B7 软淡入黑、无硬斑。")
    ap.add_argument("--ext-sigma-frac", type=float, default=8.0 / 1080.0,
                    help="Weber 局部面亮度估计的高斯尺度，按画幅宽度自适应"
                         "（sigma_px = ext_sigma_frac * width）。默认 8/1080，即 1080 宽下 8px、"
                         "540 宽下 4px。增大它会把'银河带局部亮度'摊到更大空间，硬斑边缘更宽更柔。")
    ap.add_argument("--save-linear-clean", default=None,
                    help="存 Weber/skyglow 之前的干净星点画布（供诊断脚本）。")
    ap.add_argument("--global-stretch-ref", type=int, default=1,
                    help="用哪个 bortle 作为 global signal_stretch 参考（白点补偿固定锚，"
                         "所有 bortle 单图共用）。默认 1（暗空）。<0 退回逐图自适应（旧 bug 行为）。")
    ap.add_argument("--target-white", type=float, default=1.0,
                    help="高光软肩膝点；G<20 真星亮云必须压到 ~1.0 才不过曝"
                         "（working.md 第189行）。旧默认 2.0 会让亮部过曝。")
    ap.add_argument("--lim-contrast", type=float, default=0.5)
    ap.add_argument("--lat", type=float, default=23.13)
    ap.add_argument("--lst", type=float, default=17.76)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--az-width", type=float, default=90.0)
    ap.add_argument("--max-alt", type=float, default=75.0)
    ap.add_argument("--sweep-bortles", default=None,
                    help="渲一次星场、扫观察者：CSV bortle 列表（如 1,2,...,9）。给定时进入"
                         "sweep 分支——星场只渲一次（616M 星只跑一遍并行累加），随后对同一张"
                         "float32 画布循环跑显示链，每个 bortle 出一张 bortle_<N>.png。不给则走"
                         "单图 main 路径（行为不变）。")
    ap.add_argument("--scene-ref-bortle", type=int, default=1,
                    help="sweep 模式下星场亮度锚定的固定参考 bortle。星场必须 bortle 无关，"
                         "故 visual_luminance_for_mags 用这个常量参考（非 swept bortle），"
                         "避免把 k(B) 烘进星场画布。默认 1（暗空）。")
    ap.add_argument("--sweep-out-dir", default=None,
                    help="sweep 模式下每个 bortle 单图的输出目录（写 bortle_<N>.png）。")
    args = ap.parse_args()

    # sweep 模式：星场锚在固定参考 bortle、value=0（眼睛敏感度是观察者属性，扫到显示链
    # 里；本 PR 每个 bortle 单一敏感度即 value=0）。单图模式 scene_ref_bortle=本图 bortle、
    # scene_value=本图 value，与历史逐像素一致。
    sweep_bortles = None
    if args.sweep_bortles:
        sweep_bortles = [int(x) for x in args.sweep_bortles.split(",") if x.strip()]
        if not args.sweep_out_dir:
            ap.error("--sweep-bortles 需要同时指定 --sweep-out-dir")

    # workers 默认值随路径而异：单图 28、sweep 16（OOM 安全）。用户显式传值时尊重。
    if args.workers is None:
        args.workers = 16 if sweep_bortles else 28

    import time
    n = int(np.load(args.data, mmap_mode="r")["g"].shape[0])
    print(f"{n:,} 星，{args.workers} worker", flush=True)
    # 星场锚定：sweep 用固定 scene_ref_bortle + value=0；单图沿用本图 bortle/value。
    scene_ref_bortle = args.scene_ref_bortle if sweep_bortles else args.bortle
    scene_value = 0.0 if sweep_bortles else args.value
    params = dict(lat=args.lat, lst=args.lst, W=args.width, H=args.height,
                  az_w=args.az_width, max_alt=args.max_alt, bortle=args.bortle,
                  value=args.value, lim_contrast=args.lim_contrast,
                  faint_gain=args.faint_gain, faint_mag_min=args.faint_mag_min,
                  scene_ref_bortle=scene_ref_bortle, scene_value=scene_value)
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
    pre_sat = canvas  # PSF 后、sat 前的星点画布；sat 随 bortle 变，参考图要从这里重建。

    def _apply_sat_weber_skyglow(base, bortle):
        sky_b = rh.skyglow_level(bortle)
        out = base
        if args.sat_over_sky > 0:
            sat = args.sat_over_sky * sky_b * beg.gain_for_mag_delta(args.value)
            out = beg.saturate_and_bloom(out, sat, (3.0, 9.0), (0.65, 0.35))
        ext_sigma_px = max(args.ext_sigma_frac * args.width, 1.0)
        out = beg.apply_extended_visibility_threshold(out, sky_b, args.ext_threshold, ext_sigma_px,
                                                      knee=args.ext_knee, softness=args.ext_softness)
        return beg.add_skyglow(out, bortle)

    # ===== sweep 分支：渲一次星场、扫观察者 =====
    # 616M 星只跑了上面那一次并行累加；pre_sat 是 bortle 无关的星场画布（亮度锚在
    # scene_ref_bortle、value=0，k(B) 没被烘进去）。这里只对同一张 float32 画布循环跑
    # 廉价的显示链。内存只持有一张 scene canvas，每个 bortle 的 display-chain 工作在
    # copy 上、出图后即 del 释放，不囤 9 张面板。
    if sweep_bortles is not None:
        import time as _t
        from PIL import Image
        os.makedirs(args.sweep_out_dir, exist_ok=True)

        # 共享 signal_stretch：整条 sweep 算一次，用参考 bortle 的显示链标定（复用上面
        # 的 global-stretch 逻辑）。这是保留真实 B1→B9 光污染冲刷的关键——否则每张图
        # 各自归一，B9 会被自适应拉成 majestic、看起来像 B1。固定锚默认 global_stretch_ref
        # （默认 1）；<0 退回逐图自适应（旧 bug 行为，不建议 sweep 用）。
        # 物理 sky-floor 锚：add_skyglow 给三个通道各加 skyglow_level(b)，故在
        # y=canvas.sum(-1) 单位下，该 bortle 注入的天空底 = 3 * skyglow_level(b)。
        # 把这个物理量当 sky_anchor 直接锚定 sky floor，bortle 间对比就由物理决定
        # （B1 亮、B9 冲白），弥散带在高 bortle 自然淡出，无需对比预算 hack。
        def _sky_anchor(bortle):
            return 3.0 * rh.skyglow_level(bortle)

        sweep_stretch = None
        if args.global_stretch_ref >= 0:
            ref_b = args.global_stretch_ref
            ref = _apply_sat_weber_skyglow(pre_sat.copy(), ref_b)
            ref_adapted = beg.adapt_sky_floor(ref, args.target_sky, 25.0, args.star_contrast,
                                              sky_anchor=_sky_anchor(ref_b))
            sweep_stretch = beg.signal_stretch_for_adapted(ref_adapted, args.target_sky, 99.5,
                                                           args.target_white)
            print(f"sweep 共享 signal_stretch (ref Bortle {ref_b}) = {sweep_stretch:.3f}", flush=True)
            del ref, ref_adapted

        for b in sweep_bortles:
            ts = _t.time()
            # 每个 bortle 的显示链跑在 pre_sat 的 copy 上：sat（该 bortle 天空）→ Weber
            # → skyglow → normalize_panel（共享 stretch，物理 sky_anchor）。出图后 del 释放。
            disp = _apply_sat_weber_skyglow(pre_sat.copy(), b)
            arr = beg.normalize_panel(
                disp, "sky_median", 99.7, 2.2, args.target_sky, 99.5, 25.0,
                args.star_contrast, args.target_white, sweep_stretch, args.chroma,
                sky_anchor=_sky_anchor(b))
            out_path = os.path.join(args.sweep_out_dir, f"bortle_{b}.png")
            Image.fromarray(arr).save(out_path)
            print(f"  wrote {out_path}  ({_t.time()-ts:.1f}s)", flush=True)
            del disp, arr
        print(f"sweep 完成：{len(sweep_bortles)} 张 -> {args.sweep_out_dir}", flush=True)
        return

    sky = rh.skyglow_level(args.bortle)

    # GLOBAL signal_stretch：白点/信号拉伸补偿必须用一个固定锚（默认 B1 暗空参考）
    # 算一次，所有 bortle 共用，否则逐图自适应把微弱的 B7/B9 银河各自拉成 majestic，
    # 光污染差异被抹平（B9 看起来像 B1）。详见 working.md。关键：参考图必须从 pre_sat
    # 用 *参考 bortle 自己的 sat* 重建——sat 随 bortle 变，不能复用当前 bortle 的 clean
    # （那会让"global" stretch 随 bortle 漂移，是已修的 bug）。sky_floor 归一仍逐图
    # （眼睛适应天空背景）。--global-stretch-ref<0 时退回逐图自适应（旧行为）。
    signal_stretch = None
    if args.global_stretch_ref >= 0:
        ref_b = args.global_stretch_ref
        ref = _apply_sat_weber_skyglow(pre_sat.copy(), ref_b)
        ref_adapted = beg.adapt_sky_floor(ref, args.target_sky, 25.0, args.star_contrast)
        signal_stretch = beg.signal_stretch_for_adapted(ref_adapted, args.target_sky, 99.5,
                                                        args.target_white)
        print(f"global signal_stretch (ref Bortle {ref_b}) = {signal_stretch:.3f}", flush=True)

    if args.sat_over_sky > 0:
        sat = args.sat_over_sky * sky * beg.gain_for_mag_delta(args.value)
        canvas = beg.saturate_and_bloom(canvas, sat, (3.0, 9.0), (0.65, 0.35))
    if args.save_linear_clean:
        np.save(args.save_linear_clean, canvas)
        print(f"clean canvas saved -> {args.save_linear_clean}", flush=True)

    canvas = beg.apply_extended_visibility_threshold(canvas, sky, args.ext_threshold, 8.0,
                                                     knee=args.ext_knee, softness=args.ext_softness)
    canvas = beg.add_skyglow(canvas, args.bortle)
    if args.save_linear:
        np.save(args.save_linear, canvas)
        print(f"linear canvas saved -> {args.save_linear}", flush=True)
    arr = beg.normalize_panel(
        canvas, "sky_median", 99.7, 2.2, args.target_sky, 99.5, 25.0,
        args.star_contrast, args.target_white, signal_stretch, args.chroma)

    from PIL import Image
    Image.fromarray(arr).save(args.out)
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
