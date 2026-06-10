"""把全列星表（含视差与逐星消光）合成正式渲染缓存，按 3D 尘埃几何烘焙增益衰减。

演化史（详见 docs/working.md 2026-06-09/10）：
- v1 全柱公式 10^(-0.4·(A_col−A_G))：把分布式尘埃当薄幕重复计费，误伤亮星云。
- v2 两段式：仅前景星打折，对薄幕/分布式的区分是启发式的。
- v3（本版）真实几何：每个 1° 天区格用视差+逐星消光建 A(d) 消光-距离曲线，
  每颗星的推断光按"它身后的实测尘埃" A(∞)−A(d_star) 打折。薄幕（曲线阶跃，
  如 Aquila Rift 在 300-800pc 的跳变）前的星自动吃到整个阶跃；分布式尘埃
  （曲线缓升，如 Scutum 方向）深处的星自动几乎不折。探测实验验证三类视线
  签名分明（缝核阶跃 / 亮云缓升 / 净空平线）。

直接观测的星光永不衰减；衰减只作用于截断补偿增益推断出的不可分辨光。

用法：
  python src/build_render_cache.py     # 生成 data/raw/gaia_g13_render.npz
"""
import argparse
import os

import numpy as np
from scipy.ndimage import gaussian_filter


RAW = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
SEGMENTS = ["gaia_full_0_11.npz", "gaia_full_11_12.npz", "gaia_full_12_12p5.npz",
            "gaia_full_12p5_12p8.npz", "gaia_full_12p8_13.npz"]
OUT = os.path.join(RAW, "gaia_g13_render.npz")

# 距离壳层边界（pc）。内层细分以定位近距薄幕，外层覆盖到盘面深处。
SHELLS = np.array([0, 150, 300, 500, 800, 1200, 2000, 3500, 6000, 1e9])


def load_segments(paths):
    arrs = {k: [] for k in ["l", "b", "g", "bp_rp", "plx", "ag"]}
    for p in paths:
        d = np.load(p)
        n = len(d["g"])
        if n == 3000000:
            raise SystemExit(f"{p} 行数恰为 300 万，疑似被 Gaia 匿名查询上限截断，重取该段")
        for k in arrs:
            arrs[k].append(np.asarray(d[k], float))
    return {k: np.concatenate(v) for k, v in arrs.items()}


def cell_indices(l, b):
    li = np.clip((l % 360).astype(int), 0, 359)
    bi = np.clip((b + 90).astype(int), 0, 179)
    return li, bi


def extinction_profiles(l, b, ag, dist, min_stars=6):
    """每 1° 格 × 距离壳层的 A_G 中位数曲线，强制随距离单调不减。

    单调化的物理依据：消光是柱积分，真实 A(d) 不可能随距离下降；测量噪声
    和样本变化造成的回落用 cumulative max 抹平。空壳层由前值前向填充
    （消光在无数据段保持不变是保守假设）。
    """
    fin = np.isfinite(ag) & np.isfinite(dist) & (dist > 0)
    li, bi = cell_indices(l[fin], b[fin])
    nsh = len(SHELLS) - 1
    si = np.clip(np.searchsorted(SHELLS, dist[fin], side="right") - 1, 0, nsh - 1)
    key = (li * 180 + bi) * nsh + si
    order = np.argsort(key)
    ks, av = key[order], ag[fin][order]
    bounds = np.searchsorted(ks, np.arange(0, 360 * 180 * nsh + 1))
    prof = np.full((360 * 180, nsh), np.nan, np.float32)
    counts = bounds[1:] - bounds[:-1]
    for cidx in np.nonzero(counts >= min_stars)[0]:
        s, e = bounds[cidx], bounds[cidx + 1]
        prof[cidx // nsh, cidx % nsh] = np.median(av[s:e])
    prof = prof.reshape(360, 180, nsh)
    filled0 = np.where(np.isnan(prof[:, :, 0]), 0.0, prof[:, :, 0])
    out = [filled0]
    for k in range(1, nsh):
        layer = np.where(np.isnan(prof[:, :, k]), out[-1], prof[:, :, k])
        out.append(np.maximum(layer, out[-1]))
    prof = np.stack(out, axis=2)
    for k in range(nsh):
        prof[:, :, k] = gaussian_filter(prof[:, :, k], 1.0, mode=("wrap", "nearest"))
    return prof


def impute_ag_cellmean(l, b, g, ag, faint_mag_min=11.0):
    li, bi = cell_indices(l, b)
    fin = (g >= faint_mag_min) & np.isfinite(ag)
    s = np.zeros((360, 180)); c = np.zeros((360, 180))
    np.add.at(s, (li[fin], bi[fin]), ag[fin])
    np.add.at(c, (li[fin], bi[fin]), 1)
    sm = gaussian_filter(np.where(c > 0, s / np.maximum(c, 1), 0), 1.5, mode=("wrap", "nearest"))
    w = gaussian_filter((c > 0).astype(float), 1.5, mode=("wrap", "nearest"))
    fill = sm / np.maximum(w, 1e-6)
    return np.where(np.isfinite(ag), ag, fill[li, bi])


def build(out=OUT, faint_mag_min=11.0):
    arrs = load_segments([os.path.join(RAW, s) for s in SEGMENTS])
    l, b, g, plx, ag = arrs["l"], arrs["b"], arrs["g"], arrs["plx"], arrs["ag"]
    with np.errstate(divide="ignore", invalid="ignore"):
        dist = np.where(np.isfinite(plx) & (plx > 0.05), 1000.0 / plx, np.nan)

    prof = extinction_profiles(l, b, ag, dist)
    A_inf = prof[:, :, -1]
    li, bi = cell_indices(l, b)

    # 每颗星身后的尘埃：优先用实测距离查格曲线；无距离的星退化为用自身
    # A_G（它已穿过的柱量）作为曲线位置。
    nsh = len(SHELLS) - 1
    si = np.clip(np.searchsorted(SHELLS, np.nan_to_num(dist, nan=1e9), side="right") - 1, 0, nsh - 1)
    A_at_star = prof[li, bi, si]
    has_d = np.isfinite(dist)
    ag_eff = impute_ag_cellmean(l, b, g, ag, faint_mag_min)
    A_at_star = np.where(has_d, A_at_star, ag_eff)
    behind = np.clip(A_inf[li, bi] - A_at_star, 0.0, None)
    atten = (10.0 ** (-0.4 * behind)).astype(np.float32)

    np.savez(out, l=l, b=b, g=g, bp_rp=np.nan_to_num(arrs["bp_rp"], nan=0.7),
             proxy_atten=atten)
    faint = g >= faint_mag_min
    print(f"已保存 {out}: {len(g)} 颗星 (有距离 {has_d.mean()*100:.1f}%)")
    print(f"暗星代理衰减 p10/50/90: {np.percentile(atten[faint], [10, 50, 90]).round(3)}")
    for name, lc, bc in [("缝核", 30, 3), ("亮云", 26, -2.5), ("净空", 30, 40)]:
        m = faint & (np.abs(((l - lc + 180) % 360) - 180) < 1.5) & (np.abs(b - bc) < 1.5)
        print(f"  {name}: atten 均值 {atten[m].mean():.3f}  A_inf {A_inf[lc, 90 + int(bc)]:.2f}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default=OUT)
    p.add_argument("--faint-mag-min", type=float, default=11.0)
    args = p.parse_args(argv)
    build(args.output, args.faint_mag_min)


if __name__ == "__main__":
    main()
