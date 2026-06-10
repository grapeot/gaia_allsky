"""判别实验：裂隙"缝灰"是物理边界，还是 tone mapping 显示层的产物？

审稿人主张：我们犯了量纲错误，拿照片的非线性 JPEG 灰度比(~0.05)去比我们的
线性光通量比(~0.29)。真实线性缝/云对比本来就 0.1-0.3，照片的 0.05 是 tone
curve 产物，我们的渲染线性上是对的。

本脚本做四件事，每件报告数字：
 1. 同纬度、0.5°半径的干净对照窗（缝/云星数比、光通量比）。
 2. 视差分解：缝窗的光是不是前景星(d<300pc)主导。
 3. 渲染管线拉伸前(线性域 canvas)vs 拉伸后(显示RGB)的缝/云像素中位比。
 4. 关掉饱和(sat-over-sky 0)对线性比的影响。

用法：
  python src/disc_rift_tonemap.py [--step N]   # N=1/2/3/4，不给则全跑
"""
import argparse
import csv
import gzip
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from probe_rift_depth import (angsep_deg, window_to_healpix8, shards_for_indices,
                              load_manifest, _ecsv_header_line)

SHARD_DIR = os.path.join(ROOT, "data", "raw", "flatiron_gaia_source_fov_gz")
RADIUS = 0.5  # 小窗，避免大窗把致密暗核平均掉

# ---- 干净对照窗（同纬度 b=4.5, 0.5°半径），由 STEP1 密度图定位 ----
# 数据 footprint 只覆盖两个 2° 老窗周边，银道面 b=0 没数据。裂隙暗带与亮云
# 本身就处在不同纬度（暗带在 b=4-6.5，亮云核在 b=-2.5），这是真实银河结构。
# 为了「同纬度、去掉银盘密度梯度」最干净的做法：在裂隙 blob 内部、同一个
# b=4.5 上取暗带核(lw=1.5)与邻近经度的亮云(lw=3.0)。两窗 b 完全相同，
# 只差经度，彻底消除纬度方向的密度梯度。
RIFT_WIN = (1.5, 4.5)      # (l, b) 暗带核
CLOUD_WIN = (3.0, 4.5)     # (l, b) 同纬度邻近亮云


def flux(g):
    return np.power(10.0, -0.4 * g)


# ======================================================================
# STEP 1: 同纬度 0.5° 干净对照
# ======================================================================
def find_clean_windows():
    """用 rift_region_g20.npz 的星数密度图，在 b≈0±1 银道面上找：
    - 缝窗：l 接近 0 的真实暗带里恒星面密度局部极小处
    - 云窗：同 |b| 附近、邻近经度、离开暗带的高密度处
    返回 (rift_lb, cloud_lb)。
    """
    d = np.load(os.path.join(ROOT, "data", "raw", "rift_region_g20.npz"))
    l, b = d["l"], d["b"]
    lw = np.where(l > 180, l - 360, l)  # 绕 0：[-32,7]

    # 限制到银道面带 b∈[-1,1]，看面密度随 l 的变化（0.5° bin）
    band = (b >= -1.0) & (b <= 1.0)
    lb, bb = lw[band], b[band]

    # 在 l∈[-12, 6] 范围扫 0.5° 半径圆窗的星数（面密度），步长 0.5°
    l_grid = np.arange(-12.0, 6.01, 0.5)
    counts = []
    for lc in l_grid:
        sep = angsep_deg(lb, bb, lc, 0.0)
        counts.append(int(np.sum(sep <= RADIUS)))
    counts = np.array(counts)

    print("=== STEP1 银道面 b≈0 面密度扫描 (0.5°窗, b∈[-1,1]) ===")
    print(f"{'l(wrap)':>8} {'N(0.5°)':>9}")
    for lc, c in zip(l_grid, counts):
        print(f"{lc:>8.1f} {c:>9d}")

    # 缝窗：l 接近 0 的暗带里局部极小。限制 l∈[-3,3] 找最小
    near0 = (l_grid >= -3.0) & (l_grid <= 3.0)
    i_rift = np.argmin(np.where(near0, counts, counts.max() + 1))
    rift_l = float(l_grid[i_rift])

    # 云窗：邻近经度的高密度处。在缝窗左右 ±10° 内找最大密度
    near_rift = (np.abs(l_grid - rift_l) <= 10.0) & (np.abs(l_grid - rift_l) >= 1.0)
    i_cloud = np.argmax(np.where(near_rift, counts, -1))
    cloud_l = float(l_grid[i_cloud])

    rift_lb = (rift_l % 360.0, 0.0)
    cloud_lb = (cloud_l % 360.0, 0.0)
    print(f"\n推荐 缝窗 (l_wrap={rift_l:.1f}, b=0) N={counts[i_rift]}")
    print(f"推荐 云窗 (l_wrap={cloud_l:.1f}, b=0) N={counts[i_cloud]}")
    return rift_lb, cloud_lb


def window_stats_from_npz(lc, bc, radius, gmax=None):
    """从 rift_region_g20.npz 取窗内星，返回 (N, flux_sum, g_array)。"""
    d = np.load(os.path.join(ROOT, "data", "raw", "rift_region_g20.npz"))
    l, b, g = d["l"], d["b"], d["g"]
    sep = angsep_deg(l, b, lc, bc)
    inw = sep <= radius
    gg = g[inw]
    if gmax is not None:
        gg = gg[gg < gmax]
    return gg.size, float(flux(gg).sum()), gg


def step1():
    rift_lb, cloud_lb = RIFT_WIN, CLOUD_WIN
    print("=== STEP1 干净对照（0.5°半径，同纬度 b=4.5）===")
    print("注：data footprint 只覆盖两 2° 老窗周边，银道面 b=0 无数据；暗带与亮云")
    print("    本就处不同纬度（暗带 b≈4-6.5，亮云核 b≈-2.5），这是真实结构。")
    print("    为彻底去掉纬度密度梯度，取裂隙 blob 内同一 b=4.5 上的暗带核与邻近亮云。")
    for label, gmax in [("G<13", 13.0), ("G<20", None)]:
        rN, rF, _ = window_stats_from_npz(*rift_lb, RADIUS, gmax)
        cN, cF, _ = window_stats_from_npz(*cloud_lb, RADIUS, gmax)
        gtag = "G<20" if gmax is None else gmax
        print(f"\n[{label}]")
        print(f"  缝窗 (l={rift_lb[0]:.1f},b={rift_lb[1]:.1f}): N={rN}, flux={rF:.4e}")
        print(f"  云窗 (l={cloud_lb[0]:.1f},b={cloud_lb[1]:.1f}): N={cN}, flux={cF:.4e}")
        print(f"  星数比 缝/云 = {rN/max(cN,1):.4f}")
        print(f"  光通量比 缝/云 = {rF/max(cF,1e-30):.4f}")
    print("\n对比：旧的(纬度不同的2°窗)光通量比 ~0.29")
    return rift_lb, cloud_lb


# ======================================================================
# STEP 2: 视差分解
# ======================================================================
def read_shard_window_plx(path, lc, bc, radius):
    """读分片，返回窗内星的 (g, parallax)。"""
    import pandas as pd
    skip = _ecsv_header_line(path)
    df = pd.read_csv(path, skiprows=skip,
                     usecols=["l", "b", "phot_g_mean_mag", "parallax"],
                     compression="gzip", low_memory=False)
    l = df["l"].to_numpy(dtype=float)
    b = df["b"].to_numpy(dtype=float)
    g = df["phot_g_mean_mag"].to_numpy(dtype=float)
    plx = df["parallax"].to_numpy(dtype=float)
    ok = np.isfinite(l) & np.isfinite(b) & np.isfinite(g)
    l, b, g, plx = l[ok], b[ok], g[ok], plx[ok]
    sep = angsep_deg(l, b, lc, bc)
    inw = sep <= radius
    return g[inw], plx[inw]


def step2(rift_lb):
    print(f"\n=== STEP2 视差分解 缝窗 (l={rift_lb[0]:.1f},b={rift_lb[1]:.1f}, 0.5°) ===")
    manifest = load_manifest()
    idx = window_to_healpix8(rift_lb[0], rift_lb[1], radius_deg=RADIUS)
    shards = shards_for_indices(idx, manifest)
    print(f"覆盖 {len(shards)} 分片")
    gs, plxs = [], []
    for i, s in enumerate(shards):
        path = os.path.join(SHARD_DIR, s["name"])
        g, plx = read_shard_window_plx(path, rift_lb[0], rift_lb[1], RADIUS)
        gs.append(g); plxs.append(plx)
        print(f"  [{i+1}/{len(shards)}] {s['name']} 窗内 {g.size}", flush=True)
    g = np.concatenate(gs); plx = np.concatenate(plxs)
    print(f"缝窗总星数 {g.size}")

    f = flux(g)
    F_total = f.sum()

    # 距离 d = 1000/parallax(mas) pc。parallax<=0 或 NaN 视为不可靠（当远景）。
    good = np.isfinite(plx) & (plx > 0)
    d = np.full_like(plx, np.inf)
    d[good] = 1000.0 / plx[good]

    near = good & (d < 300)
    mid = good & (d >= 300) & (d < 2000)
    far = (~good) | (d >= 2000)   # 不可靠或远

    for label, mask in [("近景 d<300pc", near),
                        ("中景 300-2000pc", mid),
                        ("远景 >2000pc/不可靠", far)]:
        Fc = f[mask].sum()
        Nc = int(mask.sum())
        print(f"  {label:22} N={Nc:7d}  flux={Fc:.4e}  占比={100*Fc/F_total:6.2f}%")

    # 额外细分远景里 parallax 不可靠 vs 真远
    unrel = (~good)
    truefar = good & (d >= 2000)
    print(f"    其中 parallax不可靠: N={int(unrel.sum())} flux占比={100*f[unrel].sum()/F_total:.2f}%")
    print(f"    其中 真远(>2000pc): N={int(truefar.sum())} flux占比={100*f[truefar].sum()/F_total:.2f}%")

    # 暗端涌入：分 G 段看远景占比（核球红巨星在暗端）
    print("  按 G 段看远景(>2000pc/不可靠)光通量占比：")
    for lo, hi in [(0, 11), (11, 15), (15, 18), (18, 20)]:
        seg = (g >= lo) & (g < hi)
        if seg.sum() == 0:
            continue
        Fseg = f[seg].sum()
        Ffar = f[seg & far].sum()
        print(f"    G[{lo},{hi}): flux={Fseg:.3e} 远景占该段={100*Ffar/max(Fseg,1e-30):.1f}% "
              f"(占全窗={100*Fseg/F_total:.1f}%)")
    near_frac = 100 * f[near].sum() / F_total
    print(f"\n判读：前景星(d<300pc)贡献缝窗光通量 {near_frac:.1f}%。"
          f"{'前景星主导' if near_frac > 50 else '前景星不主导（审稿人对）'}")
    return g, plx


# ======================================================================
# STEP 3 & 4: 渲染管线两节点的缝/云像素比
# ======================================================================
def render_two_nodes(rift_lb, cloud_lb, sat_over_sky, tag):
    """渲 rift_region_g20.npz，在线性域(节点A)和最终RGB(节点B)各取缝/云
    像素中位亮度比。返回 (ratio_A, ratio_B, medians...)。
    """
    import render_starmap as rs
    import render_bortle_eye_grid as beg
    from render_rift_region import lb_project, L_DISP_LO, L_DISP_HI, B_LO, B_HI

    # 渲染参数（与 render_rift_region.py 默认一致）
    W, H = 1600, 640
    bortle = 1
    value = 0.0
    psf_core_px = 0.6
    faint_gain = 1.0
    faint_mag_min = 11.0
    target_sky = 0.012
    star_contrast = 6.0
    chroma = 1.8

    d = np.load(os.path.join(ROOT, "data", "raw", "rift_region_g20.npz"))
    l, b, g = d["l"], d["b"], d["g"]
    bv = np.nan_to_num(d["bp_rp"], nan=0.7)
    px, py, inside = lb_project(l, b, W, H)
    px = np.clip(px, 0, W - 1).astype(int)
    py = np.clip(py, 0, H - 1).astype(int)
    cols = rs.bv_to_rgb(bv)

    L = beg.visual_luminance_for_mags(g, bortle, value, 0.5)
    sky = beg.rh.skyglow_level(bortle)
    sat_level = (sat_over_sky * sky * beg.gain_for_mag_delta(value)
                 if sat_over_sky > 0 else None)

    # ---- 节点A：accumulate + skyglow + adapt_sky_floor 之后，finish 之前 ----
    canvas = beg.accumulate_uniform_psf_stars(
        H, W, px, py, inside, g, L, cols, psf_core_px, faint_gain,
        faint_mag_min, sat_level)
    canvas = beg.add_skyglow(canvas, bortle)
    adapted = beg.adapt_sky_floor(canvas, target_sky, 25.0, star_contrast)
    nodeA = adapted.sum(axis=-1)  # 线性域亮度 (sky floor 已加，但未 tone 拉伸/软肩)

    # ---- 节点B：finish_sky_adapted 之后的最终显示 RGB ----
    stretch = beg.signal_stretch_for_adapted(adapted, target_sky, 99.5, star_contrast)
    rgb = beg.finish_sky_adapted(adapted, target_sky, 2.2, star_contrast, stretch, chroma)
    nodeB = rgb.sum(axis=-1)  # 显示域亮度 (0..1 gamma 后)

    # 缝窗/云窗对应的像素区域：把窗中心 (l,b) 投到像素，取 0.5° 半径内的像素
    def window_pixels(lc, bc):
        # lc 可能 >180，lb_project 内部会处理 wrap；这里直接给中心点投影
        lcx = lc - 360.0 if lc > 180 else lc
        # 像素网格里每像素对应的 (l,b)
        xs = np.arange(W)
        ys = np.arange(H)
        Lgrid = L_DISP_LO + (xs + 0.5) / W * (L_DISP_HI - L_DISP_LO)   # l per col
        Bgrid = B_HI - (ys + 0.5) / H * (B_HI - B_LO)                  # b per row
        LL, BB = np.meshgrid(Lgrid, Bgrid)
        sep = angsep_deg(LL, BB, lcx, bc)
        return sep <= RADIUS

    mrift = window_pixels(*rift_lb)
    mcloud = window_pixels(*cloud_lb)

    # 中位亮度。线性域要减掉 sky floor（target_sky）才是"信号"对比；
    # 但审稿人比的是线性"亮度"，这里两种都报：含 floor 和减 floor。
    A_rift = float(np.median(nodeA[mrift]))
    A_cloud = float(np.median(nodeA[mcloud]))
    B_rift = float(np.median(nodeB[mrift]))
    B_cloud = float(np.median(nodeB[mcloud]))

    # 减 sky floor 的信号对比（floor = target_sky，nodeA 的 sky 部分）
    floor = target_sky
    A_rift_sig = max(A_rift - floor, 0)
    A_cloud_sig = max(A_cloud - floor, 0)

    print(f"\n--- 渲染 [{tag}] sat_over_sky={sat_over_sky} ---")
    print(f"  缝窗像素 {int(mrift.sum())}, 云窗像素 {int(mcloud.sum())}")
    print(f"  节点A(线性域,含floor): 缝中位={A_rift:.5f} 云中位={A_cloud:.5f} "
          f"缝/云={A_rift/max(A_cloud,1e-12):.4f}")
    print(f"  节点A(减sky floor信号): 缝={A_rift_sig:.5f} 云={A_cloud_sig:.5f} "
          f"缝/云={A_rift_sig/max(A_cloud_sig,1e-12):.4f}")
    print(f"  节点B(显示RGB): 缝中位={B_rift:.5f} 云中位={B_cloud:.5f} "
          f"缝/云={B_rift/max(B_cloud,1e-12):.4f}")

    # 中位被 sky floor 主导（窗里大部分像素是星间背景）。弥散辉光的"灰"对应
    # 面均亮度，用 mean 与高百分位更能反映视觉上的缝/云对比。
    def ratio(stat_fn, arr_r, arr_c, sub=0.0):
        r = max(stat_fn(arr_r) - sub, 0.0); c = max(stat_fn(arr_c) - sub, 0.0)
        return r, c, r / max(c, 1e-12)
    Ar, Ac = nodeA[mrift], nodeA[mcloud]
    Br, Bc = nodeB[mrift], nodeB[mcloud]
    print("  --- 面均/百分位（弥散辉光对比，更贴近视觉）---")
    for name, fn in [("mean", np.mean), ("p50", lambda x: np.percentile(x, 50)),
                    ("p75", lambda x: np.percentile(x, 75)),
                    ("p90", lambda x: np.percentile(x, 90))]:
        # 节点A 减 floor 信号
        rA, cA, ratA = ratio(fn, Ar, Ac, sub=floor)
        rB, cB, ratB = ratio(fn, Br, Bc, sub=0.0)
        print(f"    {name:4} 节点A信号 缝/云={ratA:.4f} | 节点B显示 缝/云={ratB:.4f}")

    # 存中间图（节点A 归一化 + 节点B）
    from PIL import Image
    a_vis = (np.clip(nodeA / max(nodeA.max(), 1e-9), 0, 1) ** (1/2.2) * 255).astype(np.uint8)
    Image.fromarray(a_vis).save(os.path.join(ROOT, "outputs", f"_disc_nodeA_{tag}.png"))
    rgb_vis = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(rgb_vis).save(os.path.join(ROOT, "outputs", f"_disc_nodeB_{tag}.png"))

    return {
        "A_ratio_withfloor": A_rift / max(A_cloud, 1e-12),
        "A_ratio_signal": A_rift_sig / max(A_cloud_sig, 1e-12),
        "B_ratio": B_rift / max(B_cloud, 1e-12),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--step", type=int, default=0)
    args = p.parse_args()

    rift_lb, cloud_lb = RIFT_WIN, CLOUD_WIN
    if args.step in (0, 1):
        step1()

    if args.step in (0, 2):
        step2(rift_lb)

    res_on = res_off = None
    if args.step in (0, 3):
        res_on = render_two_nodes(rift_lb, cloud_lb, sat_over_sky=6.0, tag="saton")
    if args.step in (0, 4):
        res_off = render_two_nodes(rift_lb, cloud_lb, sat_over_sky=0.0, tag="satoff")

    if args.step == 0:
        print("\n" + "=" * 60)
        print("=== 汇总判别 ===")
        print(f"缝窗 {rift_lb}  云窗 {cloud_lb}  (0.5°半径)")
        if res_on:
            print(f"[饱和开] 节点A线性比(含floor)={res_on['A_ratio_withfloor']:.4f} "
                  f"信号比={res_on['A_ratio_signal']:.4f} → 节点B显示比={res_on['B_ratio']:.4f}")
        if res_off:
            print(f"[饱和关] 节点A线性比(含floor)={res_off['A_ratio_withfloor']:.4f} "
                  f"信号比={res_off['A_ratio_signal']:.4f} → 节点B显示比={res_off['B_ratio']:.4f}")


if __name__ == "__main__":
    main()
